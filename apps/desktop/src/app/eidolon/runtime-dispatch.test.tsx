import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useSyncExternalStore } from 'react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway } from './runtime-adapter'
import type { DispatchReceipt } from './runtime-dispatch-types'
import { RuntimeHistoryObjectiveDetail } from './runtime-history'
import type { Objective, OrganizationSnapshot } from './types'

async function fixture() {
  let profile = 'alpha'

  let notify = () => {}
  let objective = { id: 'obj-a', title: 'Retained goal', description: 'Scoped work', status: 'active', source: 'runtime', createdAt: '2026-10-10T00:00:00Z', dispatchControl: { paused: false, revision: 0, runningCount: 1 } } as Objective
  let archived = false
  let lose = false
  let deny = false
  let intercept: (() => Promise<unknown>) | undefined
  const receipts = new Map<string, DispatchReceipt>()
  const snapshot = (exact = false): OrganizationSnapshot => ({ source: 'runtime', objectives: archived && !exact ? [] : [structuredClone(objective)], agents: [], tasks: [], requests: [], activity: [], knowledge: [], runtime: { profile, state: 'ready', scope: 'Text', maxWorkers: 1, capabilities: [], history: { current: archived ? 0 : 1, archived: archived ? 1 : 0, currentLimit: 200, archiveLimit: 2000 } } })
  const reply = () => ({ receipt: [...receipts.values()].at(-1), profile, objective: structuredClone(objective), snapshot: snapshot() })

  const request = vi.fn(async (method: string, params: Record<string, unknown>) => {
    if (method === 'organization.snapshot') {return snapshot()}

    if (method === 'organization.historyObjective') {return snapshot(true)}

    if (method !== 'organization.setPaused') {throw new Error(method)}

    if (intercept) {return intercept()}
    let receipt = receipts.get(String(params.idempotencyKey))

    if (!receipt) {
      if (deny && params.paused === false) {throw new Error('Objective deadline reached')}

      if (params.expectedRevision !== objective.dispatchControl!.revision) {throw new Error('Dispatch changed; refresh and review')}
      receipt = { objectiveId: objective.id, idempotencyKey: String(params.idempotencyKey), paused: params.paused as boolean, revision: objective.dispatchControl!.revision + 1 }
      receipts.set(receipt.idempotencyKey, receipt)
      objective = { ...objective, dispatchControl: { ...objective.dispatchControl!, paused: receipt.paused, revision: receipt.revision } }
    }

    if (lose) {lose = false; throw new Error('Response lost')}

    return { ...reply(), receipt }
  })

  const gateway: OrganizationGateway = { request: request as OrganizationGateway['request'], getScope: () => ({ key: profile, ownerKey: profile, connected: true }), subscribeScope: listener => { notify = listener;

 return () => {} } }

  const adapter = createRuntimeAdapter(gateway)

  function View() {
    const value = useSyncExternalStore(adapter.subscribe, adapter.getSnapshot)

    return <MemoryRouter><RuntimeHistoryObjectiveDetail adapter={adapter} key={value.connection?.scope} objectiveId={profile === 'alpha' ? 'obj-a' : 'obj-b'} snapshot={value} /></MemoryRouter>
  }

  render(<View />)
  await screen.findByRole('button', { name: 'Pause new dispatch' })

  return { adapter, request, reply, lose: () => { lose = true }, deny: (value: boolean) => { deny = value }, intercept: (fn?: () => Promise<unknown>) => { intercept = fn },
    async archive() {
      archived = true
      objective = { ...objective, status: 'cancelled', history: { archived: true, revision: 1, updatedAt: null, canArchive: false, canRestore: true, blocker: null } }
      await act(async () => adapter.refresh())
    },
    async external(paused = true, revision = 3) { objective = { ...objective, dispatchControl: { ...objective.dispatchControl!, paused, revision } }; await act(async () => adapter.refresh()) },
    async switchProfile() { profile = 'beta'; objective = { ...objective, id: 'obj-b', dispatchControl: { paused: false, revision: 0, runningCount: 0 } }; await act(async () => { notify(); await adapter.refresh() }) }
  }
}

const calls = (request: ReturnType<typeof vi.fn>) => request.mock.calls.filter(call => call[0] === 'organization.setPaused')

it('retains the exact uncertain pause across archive omission and rejects wrong provenance', async () => {
  const f = await fixture()
  f.lose()
  const pause = screen.getByRole('button', { name: 'Pause new dispatch' })
  fireEvent.click(pause); fireEvent.click(pause)
  await screen.findByRole('alert')
  expect(calls(f.request)).toHaveLength(1)
  await f.archive()
  await screen.findByText('Archived history')
  expect(f.adapter.getSnapshot().objectives).toEqual([])
  const original = calls(f.request)[0][1]
  const good = f.reply()
  f.intercept(async () => ({ ...good, objective: { ...good.objective, id: 'wrong-objective' } }))
  fireEvent.click(screen.getByRole('button', { name: 'Retry same dispatch change' }))
  await screen.findByText(/invalid dispatch receipt/)
  f.intercept(undefined)
  fireEvent.click(screen.getByRole('button', { name: 'Retry same dispatch change' }))
  await screen.findByText('Dispatch change confirmed · Receipt revision: 1', { selector: '[role="status"]' })
  expect(calls(f.request).map(call => call[1])).toEqual([original, original, original])
  expect(screen.queryByRole('button', { name: 'Resume dispatch' })).toBeNull()
  expect(f.adapter.getSnapshot().objectives).toEqual([])
})

it('shows in-flight stages, retains denied resume, and requires review after a concurrent change', async () => {
  const f = await fixture()
  fireEvent.click(screen.getByRole('button', { name: 'Pause new dispatch' }))
  await screen.findByRole('button', { name: 'Resume dispatch' })
  expect(screen.getByText('Already claimed stages: 1')).toBeTruthy()
  f.deny(true)
  fireEvent.click(screen.getByRole('button', { name: 'Resume dispatch' }))
  await screen.findByText(/Objective deadline reached/)
  expect(f.adapter.getSnapshot().objectives[0].dispatchControl?.paused).toBe(true)
  f.deny(false)
  fireEvent.click(screen.getByRole('button', { name: 'Retry same dispatch change' }))
  await screen.findByRole('button', { name: 'Pause new dispatch' })
  expect(calls(f.request)[1][1]).toEqual(calls(f.request)[2][1])
  await f.external()
  expect(screen.getByRole('button', { name: 'Resume dispatch' }).hasAttribute('disabled')).toBe(true)
  expect(screen.getByText(/Review the current dispatch state before another change/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Review current dispatch' }))
  fireEvent.click(screen.getByRole('button', { name: 'Resume dispatch' }))
  await screen.findByText('Dispatch change confirmed · Receipt revision: 4', { selector: '[role="status"]' })
  expect(calls(f.request).at(-1)![1].expectedRevision).toBe(3)
})

it('fences an in-flight dispatch receipt after a profile switch', async () => {
  const f = await fixture()
  let resolve!: (value: unknown) => void
  f.intercept(() => new Promise(yes => { resolve = yes }))
  fireEvent.click(screen.getByRole('button', { name: 'Pause new dispatch' }))
  await waitFor(() => expect(calls(f.request)).toHaveLength(1))
  await f.switchProfile()
  await act(async () => resolve({ receipt: {}, profile: 'alpha', objective: {}, snapshot: {} }))
  expect(screen.queryByRole('alert')).toBeNull()
  expect(f.adapter.getSnapshot().runtime?.profile).toBe('beta')
  expect(f.adapter.getSnapshot().objectives[0].dispatchControl?.paused).toBe(false)
})


it('acknowledges a historical retry without replacing current state or offering stale actions', async () => {
  const f = await fixture()
  f.lose()
  fireEvent.click(screen.getByRole('button', { name: 'Pause new dispatch' }))
  await screen.findByRole('alert')
  const original = calls(f.request)[0][1]
  await f.external(false, 2)
  fireEvent.click(screen.getByRole('button', { name: 'Retry same dispatch change' }))
  await screen.findByText('Dispatch change confirmed · Receipt revision: 1', { selector: '[role="status"]' })
  expect(screen.getByText('New dispatch enabled · Dispatch revision: 2')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Resume dispatch' })).toBeNull()
  expect(screen.getByRole('button', { name: 'Pause new dispatch' }).hasAttribute('disabled')).toBe(true)
  expect(calls(f.request).map(call => call[1])).toEqual([original, original])
  fireEvent.click(screen.getByRole('button', { name: 'Review current dispatch' }))
  fireEvent.click(screen.getByRole('button', { name: 'Pause new dispatch' }))
  await screen.findByText('Dispatch change confirmed · Receipt revision: 3', { selector: '[role="status"]' })
  expect(calls(f.request).at(-1)![1].expectedRevision).toBe(2)
  await f.external(false, 4)
  expect(screen.getByText('New dispatch enabled · Dispatch revision: 4')).toBeTruthy()
  expect(screen.queryByText('New dispatch paused · Dispatch revision: 3')).toBeNull()
  expect(screen.getByRole('status').textContent).toBe('Dispatch change confirmed · Receipt revision: 3')
  expect(screen.getByRole('button', { name: 'Pause new dispatch' }).hasAttribute('disabled')).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Review current dispatch' }))
  fireEvent.click(screen.getByRole('button', { name: 'Pause new dispatch' }))
  await screen.findByText('Dispatch change confirmed · Receipt revision: 5', { selector: '[role="status"]' })
  expect(calls(f.request).at(-1)![1].expectedRevision).toBe(4)
})
