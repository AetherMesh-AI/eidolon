import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useSyncExternalStore } from 'react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway, type OrganizationScope } from './runtime-adapter'
import type { OrganizationOutcome, OrganizationOutcomePage, OrganizationOutcomeQuery } from './runtime-outcome-types'
import { RuntimeOutcomeInbox } from './runtime-outcomes'
import type { OrganizationSnapshot } from './types'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })

  return { promise, resolve, reject }
}

function fixture(count = 26) {
  let scope: OrganizationScope = { key: 'socket-a', ownerKey: 'owner-a', connected: true }

  let notify = () => {}
  let generation = 1

  let items: OrganizationOutcome[] = Array.from({ length: count }, (_, index) => ({
    objectiveId: `goal-${index}`, revision: 1, seen: false,
    status: index === 1 ? 'legacy_completed' : index === 2 ? 'cancelled' : 'accepted',
    title: `Outcome ${index}`, summary: `Summary ${index}`, round: 1,
    deliverableId: null, acceptanceRequestId: null, evidenceIds: [], archived: index === 0,
    createdAt: '2026-10-08T00:00:00Z', updatedAt: '2026-10-08T00:00:00Z'
  }))

  let intercept: ((method: string, params: Record<string, unknown>) => Promise<unknown> | undefined) | undefined

  const page = ({ before, unreadOnly, limit = 100 }: OrganizationOutcomeQuery = {}): OrganizationOutcomePage => {
    const offset = before ? items.findIndex(item => item.objectiveId === before) + 1 : 0
    const matching = items.slice(offset).filter(item => !unreadOnly || !item.seen)
    const rows = matching.slice(0, limit)

    return { generation: String(generation), items: rows, total: items.length, unread: items.filter(item => !item.seen).length,
      hasMore: matching.length > limit, nextCursor: matching.length > limit ? rows.at(-1)!.objectiveId : null }
  }

  const snapshot = (): OrganizationSnapshot => ({ source: 'runtime', objectives: [], requests: [], agents: [], tasks: [], activity: [], knowledge: [], outcomes: page(),
    runtime: { state: 'ready', capabilities: [], maxWorkers: 2, scope: 'Submitted context' } })

  const request = vi.fn<(...args: Parameters<OrganizationGateway['request']>) => Promise<unknown>>(async (method, params) => {
    const pending = intercept?.(method, params)

    if (pending) { return pending }

    if (method === 'organization.outcomes') { return page(params) }

    if (method === 'organization.markOutcomeSeen') {
      items = items.map(item => item.objectiveId === params.id ? { ...item, seen: true } : item)
      generation++
    }

    return snapshot()
  })

  const adapter = createRuntimeAdapter({ request: request as OrganizationGateway['request'], getScope: () => scope,
    subscribeScope(listener) { notify = listener;

 return () => { notify = () => {} } } })

  function View() {
    const value = useSyncExternalStore(adapter.subscribe, adapter.getSnapshot)

    return value.outcomes ? <RuntimeOutcomeInbox adapter={adapter} key={value.connection?.ownerScope} snapshot={value} /> : null
  }

  const view = render(<MemoryRouter><View /></MemoryRouter>)

  return { adapter, request, view, page, snapshot,
    intercept(next: typeof intercept) { intercept = next },
    update(id: string, changes: Partial<OrganizationOutcome>) { items = items.map(item => item.objectiveId === id ? { ...item, ...changes } : item); generation++ },
    switchOwner() { items = []; generation++; scope = { key: 'socket-b', ownerKey: 'owner-b', connected: true }; notify() }
  }
}

const inbox = () => within(screen.getByRole('region', { name: 'Outcomes inbox' }))
const calls = (f: ReturnType<typeof fixture>, method: string) => f.request.mock.calls.filter(call => call[0] === method)

it('pages retained and archived outcomes, keeps inspection separate from acknowledgement, and reloads changed generations', async () => {
  const f = fixture()
  await waitFor(() => expect(inbox().getAllByRole('listitem')).toHaveLength(25))
  expect(inbox().getByText('Legacy completion')).toBeTruthy()
  expect(inbox().getByText('Archived history')).toBeTruthy()
  expect(inbox().getByRole('link', { name: 'Outcome 0' }).getAttribute('href')).toBe('/objectives/goal-0')
  fireEvent.click(inbox().getByRole('link', { name: 'Outcome 0' }))
  expect(calls(f, 'organization.markOutcomeSeen')).toHaveLength(0)
  const reads = calls(f, 'organization.outcomes').length
  await act(() => f.adapter.refresh())
  expect(calls(f, 'organization.outcomes')).toHaveLength(reads)
  fireEvent.click(inbox().getByRole('button', { name: 'Next inbox page' }))
  await waitFor(() => expect(inbox().getByText('Outcome 25')).toBeTruthy())
  const stale = deferred<OrganizationOutcomePage>()
  f.intercept(method => method === 'organization.outcomes' ? stale.promise : undefined)
  f.update('goal-25', { revision: 2, summary: 'Revised retained result' })
  await act(() => f.adapter.refresh())
  expect(inbox().getByRole('button', { name: 'Mark seen' }).hasAttribute('disabled')).toBe(true)
  await act(async () => stale.resolve(f.page({ before: 'goal-24' })))
  await waitFor(() => expect(inbox().getByText('Revised retained result')).toBeTruthy())
  f.intercept(undefined)
  fireEvent.click(inbox().getByRole('button', { name: 'Mark seen' }))
  await waitFor(() => expect(inbox().getByRole('button', { name: 'Seen' })).toBeTruthy())
  expect(calls(f, 'organization.markOutcomeSeen')[0][1]).toEqual({ id: 'goal-25', revision: 2 })
  fireEvent.click(inbox().getByRole('button', { name: 'Unread' }))
  await waitFor(() => expect(inbox().getAllByRole('listitem')).toHaveLength(25))
  expect(inbox().queryByText('Outcome 25')).toBeNull()
  expect(calls(f, 'organization.resolve')).toHaveLength(0)
  expect(calls(f, 'organization.respond')).toHaveLength(0)
})

it('fences repeated writes, late failures, profile changes and pages read before a newer filter', async () => {
  const f = fixture(1)
  await waitFor(() => expect(inbox().getByRole('button', { name: 'Mark seen' })).toBeTruthy())
  const oldPage = deferred<OrganizationOutcomePage>()
  f.intercept((method, params) => method === 'organization.outcomes' && !params.unreadOnly ? oldPage.promise : undefined)
  fireEvent.click(inbox().getByRole('button', { name: 'Refresh' }))
  fireEvent.click(inbox().getByRole('button', { name: 'Unread' }))
  await waitFor(() => expect(inbox().getByRole('button', { name: 'Mark seen' }).hasAttribute('disabled')).toBe(false))
  await act(async () => oldPage.reject(new Error('Obsolete filter error')))
  expect(inbox().queryByText('Obsolete filter error')).toBeNull()
  const ack = deferred<OrganizationSnapshot>()
  f.intercept(method => method === 'organization.markOutcomeSeen' ? ack.promise : undefined)
  const mark = inbox().getByRole('button', { name: 'Mark seen' })
  fireEvent.click(mark)
  fireEvent.click(mark)
  await waitFor(() => expect(calls(f, 'organization.markOutcomeSeen')).toHaveLength(1))
  const signal = calls(f, 'organization.markOutcomeSeen')[0][3]
  await act(async () => f.switchOwner())
  expect(signal.aborted).toBe(true)
  await act(async () => ack.reject(new Error('Old owner write failed')))
  await waitFor(() => expect(inbox().getByText('No outcomes yet')).toBeTruthy())
  expect(inbox().queryByText('Old owner write failed')).toBeNull()
  expect(inbox().queryByText('Outcome 0')).toBeNull()
})

it('invalidates an older unread receipt outside the snapshot window when only its generation changes', async () => {
  const f = fixture(131)
  await waitFor(() => expect(inbox().getAllByRole('listitem')).toHaveLength(25))

  for (let index = 0; index < 130; index++) { f.update(`goal-${index}`, { seen: true }) }

  await act(() => f.adapter.refresh())
  fireEvent.click(inbox().getByRole('button', { name: 'Unread' }))
  await waitFor(() => expect(inbox().getByText('Outcome 130')).toBeTruthy())
  const before = f.snapshot().outcomes!
  const changedPage = deferred<OrganizationOutcomePage>()
  f.intercept(method => method === 'organization.outcomes' ? changedPage.promise : undefined)
  f.update('goal-130', { revision: 2, summary: 'New retained evidence' })
  const after = f.snapshot().outcomes!
  expect(after.items).toEqual(before.items)
  expect(after.total).toBe(before.total)
  expect(after.unread).toBe(before.unread)
  expect(after.generation).not.toBe(before.generation)
  await act(() => f.adapter.refresh())
  expect(inbox().getByRole('button', { name: 'Mark seen' }).hasAttribute('disabled')).toBe(true)
  await act(async () => changedPage.resolve(f.page({ unreadOnly: true })))
  await waitFor(() => expect(inbox().getByText('New retained evidence')).toBeTruthy())
  expect(inbox().getByRole('button', { name: 'Mark seen' }).hasAttribute('disabled')).toBe(false)
})
