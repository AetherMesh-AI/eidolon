import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import { expect, it, vi } from 'vitest'

import { RuntimeHistoryObjectiveDetail } from './runtime-history'
import type { Objective, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

const objective: Objective = {
  id: 'archived-goal', title: 'Retained board brief', description: 'Recorded work',
  source: 'runtime', status: 'completed', ownerId: 'executive', createdAt: '2026-10-09T00:00:00Z'
}

function Location() {
  const location = useLocation()

  return <output aria-label="Current route">{location.pathname}{location.search}</output>
}

function setup(query: string) {
  const snapshot: OrganizationSnapshot = {
    source: 'runtime', connection: { state: 'ready', scope: 'socket-a', ownerScope: 'owner-a' },
    objectives: [], agents: [], tasks: [], requests: [], activity: [], knowledge: [],
    runtime: { state: 'ready', capabilities: [], maxWorkers: 2, scope: 'Recorded work',
      history: { current: 0, archived: 1, currentLimit: 200, archiveLimit: 2000 } }
  }

  let resolve!: (snapshot: OrganizationSnapshot) => void
  const getHistoryObjective = vi.fn(() => new Promise<OrganizationSnapshot>(yes => { resolve = yes }))
  const mutation = vi.fn()

  const adapter: RuntimeOrganizationAdapter = {
    mode: 'runtime', getSnapshot: () => snapshot, subscribe: () => () => undefined,
    refresh: mutation, cancelObjective: mutation, retryRequest: mutation, resolveRequest: mutation,
    respondRequest: mutation, configureOrganization: mutation, createObjective: mutation,
    getEvidence: mutation, getToolReceipts: async () => [], getHistoryObjective
  }

  const view = render(<MemoryRouter initialEntries={[`/objectives/archived-goal?${query}`]}>
    <Location />
    <RuntimeHistoryObjectiveDetail adapter={adapter} objectiveId="archived-goal" snapshot={snapshot} />
  </MemoryRouter>)

  return { view, snapshot, mutation, getHistoryObjective, resolve: (value: OrganizationSnapshot) => resolve(value) }
}

it.each([
  ['/work?team=general', 'owner-a', '/work?team=general'],
  ['/messages?query=brief', 'owner-a', '/messages?query=brief'],
  ['/requests', 'owner-a', '/requests'],
  ['/home', 'owner-a', '/home'],
  ['/work', 'other-owner', '/objectives'],
  ['https://external.example/work', 'owner-a', '/objectives'],
  ['//external.example/work', 'owner-a', '/objectives'],
  ['/settings', 'owner-a', '/objectives'],
  ['/work#unexpected', 'owner-a', '/objectives'],
  ['', '', '/objectives']
])('keeps a safe owner-scoped Back target while history loads: %s (%s)', async (from, owner, expected) => {
  const query = new URLSearchParams({ from, originOwner: owner }).toString()
  const fixture = setup(query)
  const back = () => fixture.view.container.querySelector<HTMLAnchorElement>('a.eid-back')!
  expect(fixture.getHistoryObjective).toHaveBeenCalledWith('archived-goal')
  expect(back().getAttribute('href')).toBe(expected)
  await act(async () => { fixture.resolve({ ...fixture.snapshot, objectives: [objective] }) })
  expect(screen.getByRole('heading', { name: 'Retained board brief' })).toBeTruthy()
  expect(back().getAttribute('href')).toBe(expected)
  fireEvent.click(back())
  expect(screen.getByLabelText('Current route').textContent).toBe(expected)
  expect(fixture.mutation).not.toHaveBeenCalled()
})

it('opens the exact request after retained history loads and preserves the Back origin after closing', async () => {
  const query = new URLSearchParams({ from: '/messages?query=brief', originOwner: 'owner-a', request: 'retained-request', requestOwner: 'owner-a' }).toString()
  const fixture = setup(query)
  expect(screen.queryByRole('complementary', { name: 'Request details' })).toBeNull()
  await act(async () => {
    fixture.resolve({ ...fixture.snapshot, objectives: [objective], requests: [{
      id: 'retained-request', objectiveId: objective.id, type: 'work.draft', team: 'general',
      priority: 3, status: 'completed', attempts: 1, createdAt: '2026-10-09T00:00:00Z',
      requestedOutcome: 'Retained exact request'
    }] })
  })
  expect(screen.getByRole('complementary', { name: 'Request details' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Close request details' }))
  expect(screen.queryByRole('complementary', { name: 'Request details' })).toBeNull()
  const route = screen.getByLabelText('Current route').textContent!
  expect(new URLSearchParams(route.split('?')[1]).has('request')).toBe(false)
  const back = fixture.view.container.querySelector<HTMLAnchorElement>('a.eid-back')!
  expect(back.getAttribute('href')).toBe('/messages?query=brief')
  fireEvent.click(back)
  expect(screen.getByLabelText('Current route').textContent).toBe('/messages?query=brief')
  expect(fixture.mutation).not.toHaveBeenCalled()
})
