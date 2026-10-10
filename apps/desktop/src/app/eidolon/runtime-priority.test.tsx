import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useSyncExternalStore } from 'react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway } from './runtime-adapter'
import { RuntimeHistoryObjectiveDetail } from './runtime-history'
import { RuntimePriority } from './runtime-priority'
import type { PriorityChange, PriorityReceipt } from './runtime-priority-types'
import type { Objective, OrganizationSnapshot } from './types'

async function fixture(initialRevision = 0, historyRoute = false) {
  let profile = 'alpha'

  let notify = () => {}
  const objective = { id: 'obj-a', title: 'Goal', status: 'planning', source: 'runtime', priority: 'P5', priorityRevision: initialRevision, createdAt: '2026-10-10T00:00:00Z' } as Objective

  let snapshot: OrganizationSnapshot = { source: 'runtime', objectives: [objective], agents: [], tasks: [], knowledge: [], activity: [], requests: [],
    runtime: { profile, state: 'ready', capabilities: [], maxWorkers: 1, scope: 'Text', history: { current: 1, archived: 0, currentLimit: 200, archiveLimit: 2000 } } }

  let archived: Objective | undefined
  const receipts = new Map<string, PriorityReceipt>()
  let lose = false
  let intercept: (() => Promise<unknown>) | undefined

  const request = vi.fn(async (method: string, params: Record<string, unknown>) => {
    if (method === 'organization.snapshot') {return structuredClone(snapshot)}

    if (method === 'organization.historyObjective') {return structuredClone({ ...snapshot, objectives: archived ? [archived] : snapshot.objectives })}

    if (method !== 'organization.changePriority') {throw new Error(method)}

    if (intercept) {return intercept()}
    let receipt = receipts.get(String(params.idempotencyKey))

    if (!receipt) {
      const current = snapshot.objectives[0]

      if (current.priorityRevision !== params.expectedRevision) {throw new Error('Priority changed; refresh and review the current value')}
      receipt = { objectiveId: current.id, idempotencyKey: String(params.idempotencyKey), previousPriority: current.priority as PriorityReceipt['priority'], priority: params.priority as PriorityReceipt['priority'], revision: current.priorityRevision! + 1 }
      receipts.set(receipt.idempotencyKey, receipt)
      snapshot = { ...snapshot, objectives: [{ ...current, priority: receipt.priority, priorityRevision: receipt.revision }] }
    }

    if (lose) {lose = false; throw new Error('Response lost')}

    return { receipt, profile, objective: structuredClone(archived ?? snapshot.objectives[0]), snapshot: structuredClone(snapshot) }
  })

  const gateway: OrganizationGateway = { request: request as OrganizationGateway['request'], getScope: () => ({ key: profile, ownerKey: profile, connected: true }), subscribeScope: listener => { notify = listener;

 return () => {} } }

  const adapter = createRuntimeAdapter(gateway)

  function View() {
    const value = useSyncExternalStore(adapter.subscribe, adapter.getSnapshot)

    if (historyRoute) {return <MemoryRouter><RuntimeHistoryObjectiveDetail adapter={adapter} objectiveId="obj-a" snapshot={value} /></MemoryRouter>}

    return value.objectives[0] ? <RuntimePriority adapter={adapter} key={value.connection?.scope} objective={value.objectives[0]} snapshot={value} /> : null
  }

  const rendered = render(<View />)
  await screen.findByRole('combobox', { name: 'New priority' })

  return { adapter, request, rendered, lose: () => { lose = true }, intercept: (fn?: () => Promise<unknown>) => { intercept = fn },
    reply: () => ({ receipt: [...receipts.values()][0], profile, objective: archived ?? snapshot.objectives[0], snapshot: structuredClone(snapshot) }),
    async archive() {
      archived = { ...snapshot.objectives[0], status: 'cancelled', history: { archived: true, revision: 1, updatedAt: null, canArchive: false, canRestore: true, blocker: null } }
      snapshot = { ...snapshot, objectives: [] }
      await act(async () => adapter.refresh())
    },
    async terminal(status: Objective['status'] = 'completed') {
      snapshot = { ...snapshot, objectives: [{ ...snapshot.objectives[0], status }] }
      await act(async () => adapter.refresh())
    },
    async external(priority: Objective['priority'], revision: number) {
      snapshot = { ...snapshot, objectives: [{ ...snapshot.objectives[0], priority, priorityRevision: revision }] }
      await act(async () => adapter.refresh())
    },
    async switchProfile() {
      profile = 'beta'
      snapshot = { ...snapshot, objectives: [{ ...objective, id: 'obj-b' }], runtime: { ...snapshot.runtime!, profile } }
      await act(async () => { notify(); await adapter.refresh() })
    }
  }
}

const select = (priority: string) => fireEvent.change(screen.getByRole('combobox', { name: 'New priority' }), { target: { value: priority } })
const calls = (request: ReturnType<typeof vi.fn>) => request.mock.calls.filter(call => call[0] === 'organization.changePriority')

it('saves observable versions, retains exact retry intent after lost replies, and requires review after a concurrent edit', async () => {
  const f = await fixture()
  f.lose(); select('P1')
  const save = screen.getByRole('button', { name: 'Save priority' })
  fireEvent.click(save); fireEvent.click(save)
  await screen.findByRole('alert')
  expect(calls(f.request)).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: 'Retry priority save' }))
  await screen.findByText('Saved P5 → P1 (revision 1).')
  expect(calls(f.request)[0][1]).toEqual(calls(f.request)[1][1])
  expect(screen.getByText(/Current priority:/).textContent).toContain('P1')
  await f.external('P2', 2)
  select('P5'); fireEvent.click(screen.getByRole('button', { name: 'Save priority' }))
  await screen.findByRole('alert')
  expect(calls(f.request)[2][1].expectedRevision).toBe(1)
  fireEvent.click(screen.getByRole('button', { name: 'Review current priority' }))
  select('P5'); fireEvent.click(screen.getByRole('button', { name: 'Save priority' }))
  await screen.findByText('Saved P2 → P5 (revision 3).')
  expect(calls(f.request)[3][1].expectedRevision).toBe(2)
  expect(calls(f.request)[3][1].idempotencyKey).not.toBe(calls(f.request)[2][1].idempotencyKey)
  await f.terminal()
  expect(screen.queryByRole('combobox', { name: 'New priority' })).toBeNull()
  expect(screen.getByText('Completed, cancelled and archived objectives retain their priority.')).toBeTruthy()
})

it('fences an in-flight save on profile navigation and rejects receipt provenance mismatches', async () => {
  const f = await fixture()
  let resolve!: (value: unknown) => void
  f.intercept(() => new Promise(yes => { resolve = yes }))
  select('P1'); fireEvent.click(screen.getByRole('button', { name: 'Save priority' }))
  await waitFor(() => expect(calls(f.request)).toHaveLength(1))
  await f.switchProfile()
  await act(async () => resolve({ receipt: {}, snapshot: {} }))
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByText(/Current priority:/).textContent).toContain('P5')
  f.intercept(async () => ({ receipt: { objectiveId: 'obj-b', idempotencyKey: 'key', priority: 'P1', previousPriority: 'P5', revision: 1 }, snapshot: { ...f.adapter.getSnapshot(), runtime: { profile: 'alpha' } } }))
  await expect(f.adapter.changePriority!({ id: 'obj-b', priority: 'P1', expectedRevision: 0, idempotencyKey: 'key' })).rejects.toThrow('invalid priority-change receipt')
  expect(f.adapter.getSnapshot().objectives[0].priority).toBe('P5')
})


it.each(['completed', 'cap'] as const)('recovers a committed lost reply after %s without enabling a new change', async outcome => {
  const revision = outcome === 'cap' ? 99 : 0
  const f = await fixture(revision)
  f.lose(); select('P1')
  fireEvent.click(screen.getByRole('button', { name: 'Save priority' }))
  await screen.findByRole('alert')
  const original = structuredClone(calls(f.request)[0][1])
  expect(original).toMatchObject({ id: 'obj-a', priority: 'P1', expectedRevision: revision })
  expect(original.idempotencyKey).toEqual(expect.any(String))

  if (outcome === 'cap') {
    await act(async () => f.adapter.refresh())
  } else {
    await f.terminal(outcome)
  }

  expect(screen.queryByRole('combobox', { name: 'New priority' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Save priority' })).toBeNull()
  expect(screen.getByRole('alert')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Retry priority save' }))
  await screen.findByText(`Saved P5 → P1 (revision ${revision + 1}).`)
  expect(calls(f.request).map(call => call[1])).toEqual([original, original])
  expect(f.adapter.getSnapshot().objectives[0]).toMatchObject({
    priority: 'P1', priorityRevision: revision + 1, status: outcome === 'cap' ? 'planning' : outcome
  })
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.queryByRole('combobox', { name: 'New priority' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Save priority' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Retry priority save' })).toBeNull()
  expect(calls(f.request)).toHaveLength(2)
})


it('recovers an archived receipt through the real detail route while keeping the active projection empty', async () => {
  const f = await fixture(0, true)
  f.lose(); select('P1')
  fireEvent.click(screen.getByRole('button', { name: 'Save priority' }))
  await screen.findByRole('alert')
  const original = structuredClone(calls(f.request)[0][1]) as PriorityChange
  await f.archive()
  await screen.findByText('Archived history')
  expect(f.adapter.getSnapshot().objectives).toEqual([])
  expect(screen.queryByRole('combobox', { name: 'New priority' })).toBeNull()
  expect(screen.getByRole('button', { name: 'Retry priority save' })).toBeTruthy()

  // Omitted active rows require independently validated exact-objective provenance.
  const good = f.reply()

  for (const bad of [
    { ...good, profile: 'beta' },
    { ...good, objective: { ...good.objective, id: 'another-objective' } },
    { ...good, objective: undefined },
    { ...good, snapshot: { ...good.snapshot, runtime: { ...good.snapshot.runtime, profile: 'beta' } } },
    { ...good, objective: { ...good.objective, priorityRevision: 0 } }
  ]) {
    f.intercept(async () => bad)
    await expect(f.adapter.changePriority!(original)).rejects.toThrow('invalid priority-change receipt')
    expect(f.adapter.getSnapshot().objectives).toEqual([])
  }

  f.intercept(undefined)
  fireEvent.click(screen.getByRole('button', { name: 'Retry priority save' }))
  await screen.findByText('Saved P5 → P1 (revision 1).')
  expect(calls(f.request).every(call => JSON.stringify(call[1]) === JSON.stringify(original))).toBe(true)
  expect(f.adapter.getSnapshot().objectives).toEqual([])
  expect(screen.queryByRole('button', { name: 'Retry priority save' })).toBeNull()
  expect(screen.queryByRole('combobox', { name: 'New priority' })).toBeNull()
  expect(screen.queryByRole('alert')).toBeNull()
})
