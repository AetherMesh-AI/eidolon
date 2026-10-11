import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { Home, HomeObjective } from './home'
import { homeData } from './home-data'
import type { OrganizationOutcome } from './runtime-outcome-types'
import type { Objective, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

afterEach(cleanup)

const objective: Objective = {
  id: 'open',
  title: 'Owner content 日本語',
  description: 'Actual scope',
  ownerId: 'executive',
  source: 'runtime',
  status: 'active',
  createdAt: '2026-10-10T00:00:00Z',
  progress: 100
}

const snapshot: OrganizationSnapshot = {
  source: 'runtime',
  objectives: [objective],
  agents: [],
  tasks: [],
  activity: [],
  knowledge: []
}

const outcome: OrganizationOutcome = {
  objectiveId: 'accepted',
  title: 'Accepted result',
  summary: 'Recorded acceptance',
  status: 'accepted',
  deliverableId: 'artifact',
  acceptanceRequestId: 'review',
  evidenceIds: ['artifact'],
  revision: 1,
  seen: false,
  round: 1,
  archived: false,
  createdAt: objective.createdAt,
  updatedAt: objective.createdAt
}

it('requires a retained acceptance and deliverable instead of inferring readiness from completed work', () => {
  const value = {
    ...snapshot,
    objectives: [
      objective,
      { ...objective, id: 'completed', status: 'completed' as const },
      { ...objective, id: 'cancelled', status: 'cancelled' as const }
    ],
    outcomes: {
      generation: '1',
      total: 99,
      unread: 99,
      hasMore: true,
      nextCursor: 'cursor',
      items: [
        outcome,
        { ...outcome, objectiveId: 'legacy', status: 'legacy_completed' as const },
        { ...outcome, objectiveId: 'cancelled', status: 'cancelled' as const },
        { ...outcome, objectiveId: 'missing', deliverableId: null }
      ]
    }
  }

  expect(homeData(value).objectives.map(item => item.id)).toEqual(['open'])
  expect(homeData(value).deliverables.map(item => item.objectiveId)).toEqual(['accepted'])
  expect(homeData(snapshot).deliverables).toEqual([])
})

it('takes recent activity without mutating ledger order or including routine worker queues as owner requests', () => {
  const events = Array.from({ length: 6 }, (_, i) => ({
    id: String(i),
    source: 'runtime' as const,
    kind: 'system' as const,
    text: `Recorded ${i}`,
    timestamp: `2026-10-10T00:00:0${i}Z`
  }))

  const requests = ['queued', 'running', 'pending_intervention', 'waiting_response', 'completed', 'cancelled'].map(
    (status, i) => ({ id: String(i), status })
  ) as OrganizationSnapshot['requests']

  const value = { ...snapshot, activity: events, requests }
  expect(homeData(value).activity.map(item => item.id)).toEqual(['5', '4', '3', '2'])
  expect(events.map(item => item.id)).toEqual(['0', '1', '2', '3', '4', '5'])
  expect(homeData(value).requests.map(item => item.id)).toEqual(['2'])
})

it('labels task completion independently of acceptance and preserves owner-authored content', () => {
  const view = render(
    <MemoryRouter>
      <HomeObjective objective={objective} snapshot={snapshot} />
    </MemoryRouter>
  )

  expect(screen.getByRole('progressbar', { name: '100% of tasks completed' })).toBeTruthy()
  expect(screen.getByRole('link', { name: objective.title }).getAttribute('href')).toBe('/objectives/open')
  expect(screen.queryByText('Accepted deliverable')).toBeNull()
  view.rerender(
    <MemoryRouter>
      <HomeObjective objective={{ ...objective, source: 'prototype' }} snapshot={snapshot} />
    </MemoryRouter>
  )
  expect(screen.getByRole('progressbar', { name: '100% · local estimate' })).toBeTruthy()
  view.rerender(
    <MemoryRouter>
      <HomeObjective objective={{ ...objective, progress: undefined }} snapshot={snapshot} />
    </MemoryRouter>
  )
  expect(screen.queryByRole('progressbar')).toBeNull()
  expect(screen.getByText('Progress not reported')).toBeTruthy()
})

it('focuses explicit intake navigation once after connection readiness without stealing focus on reconnect', () => {
  const scroll = vi.fn()
  const original = HTMLElement.prototype.scrollIntoView
  HTMLElement.prototype.scrollIntoView = scroll
  const connection = { scope: 'socket', ownerScope: 'home-focus-test', state: 'connecting' as const }
  const ready = { ...snapshot, connection: { ...connection, state: 'ready' as const } }
  const adapter = { mode: 'runtime', getSnapshot: () => ready } as RuntimeOrganizationAdapter

  const tree = (value: OrganizationSnapshot) => (
    <MemoryRouter initialEntries={['/home#eid-home-intake']}>
      <Home adapter={adapter} snapshot={value} />
    </MemoryRouter>
  )

  try {
    const view = render(tree({ ...snapshot, connection }))
    const field = screen.getByRole('textbox', { name: 'Objective' })
    expect(scroll).not.toHaveBeenCalled()
    view.rerender(tree(ready))
    expect(globalThis.document.activeElement).toBe(field)
    expect(scroll).toHaveBeenCalledTimes(1)
    field.blur()
    view.rerender(tree({ ...snapshot, connection }))
    view.rerender(tree(ready))
    expect(globalThis.document.activeElement).not.toBe(field)
    expect(scroll).toHaveBeenCalledTimes(1)
    expect(screen.getByText('This runtime has not supplied its request queue.')).toBeTruthy()
    expect(screen.getByText(/has not supplied its outcome inbox/)).toBeTruthy()
  } finally {
    HTMLElement.prototype.scrollIntoView = original
  }
})


it('opens a scoped executive chooser only after explicit Home action and never submits work', () => {
  const current = { ...snapshot, connection: { scope: 'socket', ownerScope: 'current-organization', state: 'ready' as const } }
  const adapter = { mode: 'runtime', getSnapshot: () => current, createObjective: vi.fn(), openOwnerChat: vi.fn(), sendOwnerChat: vi.fn() } as unknown as RuntimeOrganizationAdapter

  function Location() {
    const value = useLocation()

    return <output>{value.pathname + value.search}</output>
  }

  const tree = (value: OrganizationSnapshot) => <MemoryRouter initialEntries={['/home']}><Home adapter={adapter} snapshot={value} /><Location /></MemoryRouter>
  const view = render(tree(snapshot))
  expect(screen.getByRole('button', { name: 'Message organization' })).toHaveProperty('disabled', true)
  expect(screen.getByText(/Connect to the current organization before choosing/)).toBeTruthy()
  view.rerender(tree(current))
  fireEvent.click(screen.getByRole('button', { name: 'Message organization' }))
  expect(screen.getByText('/messages?recipient=executive')).toBeTruthy()
  expect(adapter.createObjective).not.toHaveBeenCalled()
  expect(adapter.openOwnerChat).not.toHaveBeenCalled()
  expect(adapter.sendOwnerChat).not.toHaveBeenCalled()
})
