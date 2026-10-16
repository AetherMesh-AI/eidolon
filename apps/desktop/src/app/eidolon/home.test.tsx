import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { useOverlayRouting } from '../shell/hooks/use-overlay-routing'

import { Home, HomeObjective, HomeRequest } from './home'
import { homeData } from './home-data'
import type { OrganizationOutcome } from './runtime-outcome-types'
import { ownerChatSnapshot } from './runtime-owner-chat.test-support'
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

it('keeps intake mounted and its draft intact when explicitly opened and collapsed', () => {
  const original = HTMLElement.prototype.scrollIntoView
  HTMLElement.prototype.scrollIntoView = vi.fn()
  const current = { ...snapshot, connection: { scope: 'fit-test', ownerScope: 'fit-test', state: 'ready' as const } }
  const adapter = { mode: 'runtime', getSnapshot: () => current, createObjective: vi.fn() } as unknown as RuntimeOrganizationAdapter

  try {
    const view = render(<MemoryRouter><Home adapter={adapter} snapshot={current} /></MemoryRouter>)
    const disclosure = view.container.querySelector('details.eid-home-intake') as HTMLDetailsElement
    expect(disclosure.open).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'New objective' }))
    expect(disclosure.open).toBe(true)
    const input = screen.getByRole('textbox', { name: 'Objective' })
    fireEvent.change(input, { target: { value: 'Keep my unsent objective' } })
    fireEvent.click(disclosure.querySelector('summary')!)
    expect(disclosure.open).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'New objective' }))
    expect(screen.getByRole('textbox', { name: 'Objective' })).toBe(input)
    expect(input).toHaveProperty('value', 'Keep my unsent objective')
    expect(globalThis.document.activeElement).toBe(input)
    expect(adapter.createObjective).not.toHaveBeenCalled()
  } finally {
    HTMLElement.prototype.scrollIntoView = original
  }
})

it('returns from Settings to the open originating setup with its unsent draft', () => {
  const original = HTMLElement.prototype.scrollIntoView
  HTMLElement.prototype.scrollIntoView = vi.fn()
  const current = ownerChatSnapshot()
  current.connection!.ownerScope = 'home-settings-return'
  const adapter = { mode: 'runtime', getSnapshot: () => current, createObjective: vi.fn() } as unknown as RuntimeOrganizationAdapter

  function Surface() {
    const routing = useOverlayRouting()

    return routing.settingsOpen ? <button onClick={routing.closeOverlayToPreviousRoute}>Return from settings</button> : <Home adapter={adapter} snapshot={current} />
  }

  try {
    render(<MemoryRouter initialEntries={['/home']}><Surface /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: 'New objective' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Objective' }), { target: { value: 'Preserve setup context' } })
    fireEvent.click(screen.getByRole('link', { name: 'Review profile model' }))
    fireEvent.click(screen.getByRole('button', { name: 'Return from settings' }))
    expect(screen.getByRole('region', { name: 'Organization setup' })).toBeTruthy()
    expect(screen.getByRole('textbox', { name: 'Objective' })).toHaveProperty('value', 'Preserve setup context')
    expect(adapter.createObjective).not.toHaveBeenCalled()
  } finally {
    HTMLElement.prototype.scrollIntoView = original
  }
})


it('opens organization evidence from the accepted-results action without creating work', () => {
  const current = { ...snapshot, outcomes: { generation: '1', total: 1, unread: 0, hasMore: false, nextCursor: null, items: [outcome] } }
  const adapter = { mode: 'runtime', getSnapshot: () => current, createObjective: vi.fn() } as unknown as RuntimeOrganizationAdapter

  function Location() {
    const location = useLocation()

    return <output>{location.pathname + location.search}</output>
  }

  render(<MemoryRouter initialEntries={['/home']}><Home adapter={adapter} snapshot={current} /><Location /></MemoryRouter>)
  expect(screen.getByRole('heading', { name: outcome.title })).toBeTruthy()
  fireEvent.click(screen.getByRole('link', { name: 'View deliverables →' }))
  expect(screen.getByText('/artifacts?source=organization')).toBeTruthy()
  expect(adapter.createObjective).not.toHaveBeenCalled()
})


it('summarizes only current scoped work on the primary objective without inventing accepted delivery', () => {
  const current = { id: 'task', objectiveId: objective.id, title: 'Recorded work', ownerId: 'worker', status: 'completed' as const, dependsOn: [], currentRound: true }
  const record = { ...objective, phase: 'مراجعة (P2)' }
  const state = { ...snapshot, objectives: [record], tasks: [current, { ...current, id: 'old', currentRound: false }, { ...current, id: 'other', objectiveId: 'other' }] }
  const before = JSON.stringify(state)
  const view = render(<MemoryRouter><HomeObjective objective={record} snapshot={state} /></MemoryRouter>)
  expect(screen.getByText(record.phase).getAttribute('dir')).toBe('auto')
  const counts = screen.getByRole('list', { name: 'Recorded task states' })
  expect(counts.textContent).toBe('1 Completed')
  expect(screen.queryByText('Accepted deliverable')).toBeNull()
  expect(screen.getByRole('link', { name: 'View objective →' }).getAttribute('href')).toBe('/objectives/open')
  view.rerender(<MemoryRouter><HomeObjective compact objective={record} snapshot={state} /></MemoryRouter>)
  expect(screen.queryByRole('list', { name: 'Recorded task states' })).toBeNull()
  expect(screen.queryByRole('progressbar')).toBeNull()
  expect(screen.getByRole('link', { name: record.title }).getAttribute('href')).toBe('/objectives/open')
  expect(JSON.stringify(state)).toBe(before)
})


it('shows the recorded question and requester without attributing it to the assigned executor', () => {
  const value = ownerChatSnapshot()
  const worker = value.agents[0]
  value.objectives = [objective]

  const request = {
    id: 'question', objectiveId: objective.id, requesterId: worker.id, agentId: 'reviewer',
    type: 'request.question', team: 'Research', priority: 3, status: 'pending_intervention' as const,
    reason: 'Owner input required', requestedOutcome: 'Which audience should this brief address?', attempts: 1, createdAt: objective.createdAt
  }

  const view = render(<MemoryRouter><HomeRequest request={request} snapshot={value} /></MemoryRouter>)
  expect(screen.getByRole('heading', { name: request.requestedOutcome })).toBeTruthy()
  expect(screen.getByText(request.reason)).toBeTruthy()
  expect(screen.getByText(worker.name)).toBeTruthy()
  const href = new URL(screen.getByRole('link', { name: /Review request/ }).getAttribute('href')!, 'https://workspace.invalid')
  expect(href.pathname).toBe(`/objectives/${objective.id}`)
  expect(href.searchParams.get('request')).toBe(request.id)
  expect(href.searchParams.get('requestOwner')).toBe(value.connection?.ownerScope ?? value.connection?.scope)
  view.rerender(<MemoryRouter><HomeRequest request={{ ...request, requesterId: undefined, agentId: worker.id }} snapshot={value} /></MemoryRouter>)
  expect(screen.queryByText(worker.name)).toBeNull()
  expect(screen.getByText('Not recorded')).toBeTruthy()
})


it('switches compact intake modes through explicit shortcuts while preserving the manual draft', () => {
  const scroll = vi.fn()
  const original = HTMLElement.prototype.scrollIntoView
  HTMLElement.prototype.scrollIntoView = scroll
  const current = { ...snapshot, connection: { scope: 'mode-test', ownerScope: 'mode-test', state: 'ready' as const } }
  const adapter = { mode: 'runtime', getSnapshot: () => current, createObjective: vi.fn() } as unknown as RuntimeOrganizationAdapter

  try {
    const view = render(<MemoryRouter><Home adapter={adapter} snapshot={current} /></MemoryRouter>)
    const assisted = view.container.querySelector('#eid-assisted-intake') as HTMLDetailsElement
    const manual = view.container.querySelector('#eid-manual-intake') as HTMLDetailsElement
    expect(assisted.open).toBe(true)
    expect(manual.open).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'New objective' }))
    expect(manual.open).toBe(true)
    expect(assisted.open).toBe(false)
    const input = screen.getByRole('textbox', { name: 'Objective' })
    fireEvent.change(input, { target: { value: 'Retain my manual work' } })
    expect(scroll.mock.instances.at(-1)).toBe(view.container.querySelector('#eid-home-intake'))
    fireEvent.click(screen.getByRole('button', { name: 'Describe a request' }))
    expect(manual.open).toBe(false)
    expect(assisted.open).toBe(true)
    expect(scroll.mock.instances.at(-1)).toBe(view.container.querySelector('#eid-home-intake'))
    fireEvent.click(screen.getByRole('button', { name: 'New objective' }))
    expect(screen.getByRole('textbox', { name: 'Objective' })).toBe(input)
    expect(input).toHaveProperty('value', 'Retain my manual work')
    expect(globalThis.document.activeElement).toBe(input)
    expect(adapter.createObjective).not.toHaveBeenCalled()
    view.rerender(<MemoryRouter><Home adapter={adapter} snapshot={{ ...current, connection: { ...current.connection, state: 'connecting' } }} /></MemoryRouter>)
    expect(input).toHaveProperty('disabled', true)
  } finally {
    HTMLElement.prototype.scrollIntoView = original
  }
})

it('opens and focuses assisted intake after the explicit team-setup continuation', () => {
  const original = HTMLElement.prototype.scrollIntoView
  HTMLElement.prototype.scrollIntoView = vi.fn()
  const current = { ...snapshot, connection: { scope: 'first-request', ownerScope: 'first-request', state: 'ready' as const } }
  const adapter = { mode: 'runtime', getSnapshot: () => current, createObjective: vi.fn() } as unknown as RuntimeOrganizationAdapter

  try {
    const view = render(<MemoryRouter initialEntries={['/home?intake=assisted#eid-home-intake']}><Home adapter={adapter} snapshot={current} /></MemoryRouter>)
    const assisted = view.container.querySelector('#eid-assisted-intake') as HTMLDetailsElement
    const manual = view.container.querySelector('#eid-manual-intake') as HTMLDetailsElement
    expect(assisted.open).toBe(true)
    expect(manual.open).toBe(false)
    expect(globalThis.document.activeElement).toBe(assisted.querySelector('summary'))
    expect(adapter.createObjective).not.toHaveBeenCalled()
  } finally { HTMLElement.prototype.scrollIntoView = original }
})


it('makes the first request primary and keeps conversation and manual creation quiet', () => {
  const current = { ...snapshot, connection: { scope: 'actions', ownerScope: 'actions', state: 'ready' as const } }
  const adapter = { mode: 'runtime', getSnapshot: () => current } as unknown as RuntimeOrganizationAdapter
  const view = render(<MemoryRouter><Home adapter={adapter} snapshot={current} starterEntry={<section className="eid-team-entry"><button>Set up organization</button></section>} /></MemoryRouter>)
  expect(screen.getByRole('button', { name: 'Describe a request' }).getAttribute('data-variant')).toBe('default')
  expect(screen.getByRole('button', { name: 'Message organization' }).getAttribute('data-variant')).toBe('text')
  expect(screen.getByRole('button', { name: 'New objective' }).getAttribute('data-variant')).toBe('text')
  expect(view.container.querySelector('.eid-home-actions > .eid-team-entry')).toBeTruthy()
  const intake = view.container.querySelector('#eid-home-intake') as HTMLDetailsElement
  fireEvent(intake, new Event('toggle'))
  expect(globalThis.document.activeElement).toBe(screen.getByRole('button', { name: 'New objective' }))
  view.rerender(<MemoryRouter><Home adapter={adapter} snapshot={current} starterEntry={<></>} /></MemoryRouter>)
  expect(view.container.querySelector('.eid-home-actions > .eid-team-entry')).toBeNull()
  expect(screen.getByRole('button', { name: 'Describe a request' }).getAttribute('data-variant')).toBe('default')
})


it.each(['unchecked', 'warning'] as const)('shows real %s readiness copy above the request composer', status => {
  const value = ownerChatSnapshot()
  const current: OrganizationSnapshot = { ...value, runtime: { ...value.runtime!, setup: { version: 1, provider: { status, blockers: [], inheritedMembers: 1, overriddenMembers: 0 }, backgroundOptIn: false } } }
  const adapter = { mode: 'runtime', getSnapshot: () => current } as unknown as RuntimeOrganizationAdapter
  const view = render(<MemoryRouter><Home adapter={adapter} snapshot={current} /></MemoryRouter>)
  const summary = view.container.querySelector('.eid-home-setup > summary')!
  expect(summary.textContent).toContain('Organization setup')
  expect(summary.textContent).toContain(status === 'warning' ? 'Configured transport needs review' : 'Provider execution not checked')
})

it('retires a queued keyboard reveal when Home changes owner while its summary remains mounted', () => {
  let frame: FrameRequestCallback | undefined

  const requestFrame = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(callback => {frame = callback;

 return 1})

  const cancelFrame = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {})
  const current = { ...snapshot, connection: { scope: 'keyboard-a', ownerScope: 'keyboard-a', state: 'ready' as const } }
  const adapter = { mode: 'runtime', getSnapshot: () => current } as unknown as RuntimeOrganizationAdapter
  const tree = (state: OrganizationSnapshot) => <MemoryRouter><main className="eid-workspace"><Home adapter={adapter} snapshot={state} /></main></MemoryRouter>
  const view = render(tree(current))
  const workspace = view.container.querySelector<HTMLElement>('main')!
  const intake = view.container.querySelector<HTMLElement>('#eid-home-intake')!
  const summary = intake.querySelector('summary')!
  Object.defineProperty(workspace, 'clientHeight', { value: 325 })
  workspace.scrollTop = 500
  vi.spyOn(workspace, 'getBoundingClientRect').mockReturnValue({ top: 47 } as DOMRect)
  vi.spyOn(summary, 'getBoundingClientRect').mockReturnValue({ top: 40, bottom: 60, height: 20 } as DOMRect)

  try {
    fireEvent.keyDown(intake, { key: 'Tab' })
    summary.focus()
    expect(frame).toBeDefined()
    const queuedReveal = frame!
    view.rerender(tree({ ...current, connection: { scope: 'keyboard-b', ownerScope: 'keyboard-b', state: 'ready' } }))
    expect(summary.isConnected).toBe(true)
    expect(globalThis.document.activeElement).toBe(summary)
    expect(cancelFrame).toHaveBeenCalledWith(1)
    queuedReveal(0)
    expect(workspace.scrollTop).toBe(500)
  } finally {
    view.unmount()
    requestFrame.mockRestore()
    cancelFrame.mockRestore()
  }
})
