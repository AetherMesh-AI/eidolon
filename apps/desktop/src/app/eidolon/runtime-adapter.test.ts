import { afterEach, describe, expect, it, vi } from 'vitest'

import { createRuntimeAdapter, organizationErrorMessage, type OrganizationGateway, type OrganizationScope } from './runtime-adapter'
import type { Objective, OrganizationSnapshot } from './types'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => {resolve = yes; reject = no})

  return { promise, resolve, reject }
}

export function runtimeSnapshot(title?: string): OrganizationSnapshot {
  return {
    source: 'runtime', objectives: title ? [{ id: title, title, description: 'Submitted context', ownerId: 'manager', createdAt: '2026-10-04T00:00:00Z', status: 'planning', source: 'runtime' }] : [],
    agents: [], tasks: [], activity: [], knowledge: [], requests: [],
    runtime: { state: 'ready', capabilities: ['work.draft', 'work.analyze'], maxWorkers: 2, scope: 'Writing and analysis of submitted context.' }
  }
}

function harness(request = vi.fn().mockResolvedValue(runtimeSnapshot())) {
  let scope: OrganizationScope = { key: 'connection-a:default:socket-1', connected: true }
  const listeners = new Set<() => void>()

  const gateway: OrganizationGateway = {
    request,
    getScope: () => scope,
    subscribeScope(listener) { listeners.add(listener);

 return () => {listeners.delete(listener)} }
  }

  return {
    adapter: createRuntimeAdapter(gateway), request,
    change(next: OrganizationScope) {scope = next; listeners.forEach(listener => listener())},
    listenerCount: () => listeners.size
  }
}

const settle = async () => {for (let index = 0; index < 12; index++) {await Promise.resolve()}}
afterEach(() => vi.useRealTimers())

describe('runtime organization ownership and recovery', () => {
  it('polls only while subscribed, shares a single read and stops bounded failure recovery visibly', async () => {
    vi.useFakeTimers()
    const h = harness(vi.fn().mockRejectedValue(new Error('Method not found')))
    expect(h.request).not.toHaveBeenCalled()
    const off = h.adapter.subscribe(vi.fn())
    const offSecond = h.adapter.subscribe(vi.fn())
    await settle()
    expect(h.request).toHaveBeenCalledTimes(1)
    expect(h.adapter.getSnapshot().connection).toMatchObject({ state: 'error', error: 'Method not found' })
    await vi.advanceTimersByTimeAsync(100000)
    expect(h.request).toHaveBeenCalledTimes(3)
    h.request.mockResolvedValue(runtimeSnapshot('Recovered'))
    await h.adapter.refresh()
    expect(h.adapter.getSnapshot().objectives[0].title).toBe('Recovered')
    off()
    expect(h.listenerCount()).toBe(1)
    offSecond()
    expect(h.listenerCount()).toBe(0)
    await vi.advanceTimersByTimeAsync(100000)
    expect(h.request).toHaveBeenCalledTimes(4)
  })

  it('coalesces repeat admission and retains the idempotency key after an uncertain failure without fake work', async () => {
    const admission = deferred<{ objective: Objective; snapshot: OrganizationSnapshot }>()
    const h = harness(vi.fn().mockReturnValue(admission.promise))
    const first = h.adapter.createObjective('Draft memo', { description: 'Context', priority: 'P2', agentId: 'prototype-only' })
    const repeat = h.adapter.createObjective('Draft memo', { description: 'Context', priority: 'P2', agentId: 'prototype-only' })
    await settle()
    expect(h.request).toHaveBeenCalledTimes(1)
    expect(h.adapter.getSnapshot().objectives).toEqual([])
    const params = h.request.mock.calls[0][1]
    expect(params).toMatchObject({ title: 'Draft memo', description: 'Context', priority: 'P2' })
    expect(params).not.toHaveProperty('agentId')
    admission.reject(new Error('Acknowledgement lost'))
    await expect(first).rejects.toThrow('Acknowledgement lost')
    await expect(repeat).rejects.toThrow('Acknowledgement lost')
    const next = runtimeSnapshot('Draft memo')
    h.request.mockResolvedValue({ objective: next.objectives[0], snapshot: next })
    await h.adapter.createObjective('Draft memo', { description: 'Context', priority: 'P2' })
    expect(h.request.mock.calls[1][1].idempotencyKey).toBe(params.idempotencyKey)
    expect(h.adapter.getSnapshot().objectives).toHaveLength(1)
  })

  it.each(['success', 'failure'])('never lets an older poll %s overwrite a newer mutation snapshot', async outcome => {
    vi.useFakeTimers()
    const old = deferred<OrganizationSnapshot>()
    const created = runtimeSnapshot('New request')
    const h = harness(vi.fn().mockReturnValueOnce(old.promise).mockResolvedValueOnce({ objective: created.objectives[0], snapshot: created }))
    const off = h.adapter.subscribe(vi.fn())
    await settle()
    await h.adapter.createObjective('New request', {}, 'stable-intent')

    if (outcome === 'failure') {old.reject(new Error('Old poll failed'))} else {old.resolve(runtimeSnapshot())}
    await settle()
    expect(h.adapter.getSnapshot().objectives[0].id).toBe('New request')
    expect(h.adapter.getSnapshot().connection?.state).toBe('ready')
    off()
  })

  it('clears the previous connection immediately and aborts its late poll and in-flight writes', async () => {
    vi.useFakeTimers()
    const oldPoll = deferred<OrganizationSnapshot>()
    const oldWrite = deferred<{ objective: Objective; snapshot: OrganizationSnapshot }>()
    const h = harness(vi.fn().mockReturnValueOnce(oldPoll.promise).mockReturnValueOnce(oldWrite.promise).mockResolvedValue(runtimeSnapshot('B result')))
    const off = h.adapter.subscribe(vi.fn())
    await settle()
    const write = h.adapter.createObjective('A result', {}, 'same-intent')
    const rejected = expect(write).rejects.toThrow('connection or profile changed')
    await settle()
    const [pollSignal, writeSignal] = h.request.mock.calls.map(call => call[3] as AbortSignal)
    h.change({ key: 'connection-b:default:socket-2', connected: true })
    expect(pollSignal.aborted).toBe(true)
    expect(writeSignal.aborted).toBe(true)
    expect(h.adapter.getSnapshot().objectives).toEqual([])
    await settle()
    oldPoll.resolve(runtimeSnapshot('A secret'))
    const a = runtimeSnapshot('A result')
    oldWrite.resolve({ objective: a.objectives[0], snapshot: a })
    await rejected
    expect(h.adapter.getSnapshot().objectives.map(item => item.title)).toEqual(['B result'])
    off()
  })

  it('does not dispatch a queued write after a same-name profile changes connection', async () => {
    const h = harness()
    const off = h.adapter.subscribe(vi.fn())
    await settle()
    const submission = h.adapter.createObjective('Original scope', {}, 'intent')
    const assertion = expect(submission).rejects.toThrow('changed before')
    h.change({ key: 'connection-b:default:socket-2', connected: true })
    await assertion
    expect(h.request.mock.calls.every(call => call[0] === 'organization.snapshot')).toBe(true)
    off()
  })

  it('stops disconnected polling, keeps last state marked stale, and resumes on reconnection', async () => {
    vi.useFakeTimers()
    const h = harness(vi.fn().mockResolvedValue(runtimeSnapshot('Existing')))
    const off = h.adapter.subscribe(vi.fn())
    await settle()
    h.change({ key: 'connection-a:default:socket-1', connected: false })
    expect(h.adapter.getSnapshot().connection?.state).toBe('disconnected')
    expect(h.adapter.getSnapshot().objectives).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(100000)
    expect(h.request).toHaveBeenCalledTimes(1)
    await expect(h.adapter.createObjective('Offline')).rejects.toThrow('Connect')
    h.change({ key: 'connection-a:default:socket-1', connected: true })
    await settle()
    expect(h.request).toHaveBeenCalledTimes(2)
    expect(h.adapter.getSnapshot().connection?.state).toBe('ready')
    off()
  })

  it('uses a stable retry receipt after uncertain failure and a new receipt for a later confirmed attempt', async () => {
    const initial = runtimeSnapshot('Retry objective')
    initial.requests = [{ id: 'request', objectiveId: 'Retry objective', type: 'work.draft', team: 'delivery', priority: 3, status: 'pending_intervention', attempts: 2, createdAt: '2026-10-04T00:00:00Z' }]
    const retry = deferred<OrganizationSnapshot>()
    const h = harness(vi.fn().mockResolvedValueOnce(initial).mockReturnValueOnce(retry.promise))
    await h.adapter.refresh()
    const first = h.adapter.retryRequest('request')
    const assertion = expect(first).rejects.toThrow('Lost acknowledgement')
    await settle()
    const receipt = h.request.mock.calls[1][1].idempotencyKey
    retry.reject(new Error('Lost acknowledgement'))
    await assertion
    const later = { ...initial, requests: initial.requests.map(request => ({ ...request, attempts: 3 })) }
    h.request.mockResolvedValue(later)
    await h.adapter.retryRequest('request')
    expect(h.request.mock.calls[2][1].idempotencyKey).toBe(receipt)
    await h.adapter.retryRequest('request')
    expect(h.request.mock.calls[3][1].idempotencyKey).not.toBe(receipt)
    expect(h.listenerCount()).toBe(0)
  })

  it('reads full evidence and rejects mismatched or stale ownership instead of showing another artifact', async () => {
    const evidence = { id: 'proof', content: 'full content', sha256: 'digest', summary: 'Memo', createdAt: '2026-10-04T00:00:00Z', objectiveId: 'goal', taskId: 'task' }
    const h = harness(vi.fn().mockResolvedValueOnce(evidence).mockResolvedValueOnce({ ...evidence, id: 'wrong' }))
    await expect(h.adapter.getEvidence('proof')).resolves.toEqual(evidence)
    expect(h.request.mock.calls[0].slice(0, 2)).toEqual(['organization.evidence', { id: 'proof' }])
    await expect(h.adapter.getEvidence('proof')).rejects.toThrow('invalid evidence')
  })
})

it('reads only the exact request audit and fences its late result across same-name profile connections', async () => {
  const tool = { id: 'tool-1', requestId: 'request-a', toolCallId: 'call-1', toolName: 'read_file', arguments: { path: 'root0/notes.md' }, status: 'running', createdAt: '2026-10-04T00:00:00Z' }
  const later = deferred<unknown[]>()
  const h = harness(vi.fn().mockResolvedValueOnce([tool]).mockResolvedValueOnce([{ ...tool, requestId: 'request-b' }]).mockReturnValueOnce(later.promise))
  await expect(h.adapter.getToolReceipts('request-a')).resolves.toEqual([tool])
  expect(h.request.mock.calls[0].slice(0, 2)).toEqual(['organization.toolReceipts', { id: 'request-a' }])
  await expect(h.adapter.getToolReceipts('request-a')).rejects.toThrow('invalid tool execution receipts')
  const pending = h.adapter.getToolReceipts('request-a')
  const assertion = expect(pending).rejects.toThrow('connection or profile changed')
  h.change({ key: 'connection-b:default:socket-2', connected: true })
  expect(h.request.mock.calls[2][3].aborted).toBe(true)
  later.resolve([tool])
  await assertion
  expect(h.listenerCount()).toBe(0)
})


it('redacts connection credentials from surfaced error messages without hiding the recoverable reason', () => {
  const text = organizationErrorMessage(new Error('Reconnect failed for https://owner:password@example.test/path?token=secret. Authorization: Bearer abc123 api_key=private'))
  expect(text).toContain('Reconnect failed')
  expect(text).toContain('https://example.test/path')
  expect(text).not.toMatch(/password|secret|abc123|private/)
})


it('retains unresolved intent keys by logical owner across profile switches without sharing them across owners', async () => {
  const h = harness(vi.fn().mockRejectedValue(new Error('Acknowledgement lost')))
  h.change({ key: 'a', ownerKey: 'owner-a', connected: true })
  await expect(h.adapter.createObjective('same goal')).rejects.toThrow('Acknowledgement lost')
  const a = h.request.mock.calls[0][1].idempotencyKey
  h.change({ key: 'b', ownerKey: 'owner-b', connected: true })
  await expect(h.adapter.createObjective('same goal')).rejects.toThrow('Acknowledgement lost')
  const b = h.request.mock.calls[1][1].idempotencyKey
  h.change({ key: 'a-new-socket', ownerKey: 'owner-a', connected: true })
  await expect(h.adapter.createObjective('same goal')).rejects.toThrow('Acknowledgement lost')
  expect(h.request.mock.calls[2][1].idempotencyKey).toBe(a)
  expect(b).not.toBe(a)
})

it.each(['response', 'configuration'])('fences late %s mutations across profiles and retains an uncertain owner-scoped identity', async kind => {
  const pending = deferred<OrganizationSnapshot>()
  const h = harness(vi.fn().mockReturnValueOnce(pending.promise).mockResolvedValue(runtimeSnapshot('Other profile')))
  const configuration = { roster: [], max_inflight: 2, max_members: 16 }
  const submit = () => kind === 'response' ? h.adapter.respondRequest({ id: 'question', text: 'Exact answer', decision: 'answered' }) : h.adapter.configureOrganization({ configuration, expectedGeneration: 2 })
  const old = submit()
  const assertion = expect(old).rejects.toThrow('connection or profile changed')
  await settle()
  const key = h.request.mock.calls[0][1].idempotencyKey
  const signal = h.request.mock.calls[0][3]
  h.change({ key: 'connection-b:default:socket-2', connected: true })
  expect(signal.aborted).toBe(true)
  await h.adapter.refresh()
  pending.resolve(runtimeSnapshot('Old private result'))
  await assertion
  expect(h.adapter.getSnapshot().objectives[0].title).toBe('Other profile')
  h.change({ key: 'connection-a:default:socket-1', connected: true })
  await submit()
  const retry = h.request.mock.calls.findLast(call => call[0] === (kind === 'response' ? 'organization.respond' : 'organization.configure'))!
  expect(retry[1].idempotencyKey).toBe(key)
})

it('reads exact execution audits and rejects mismatched or late same-name profile responses', async () => {
  const audit = { requestId: 'review', contexts: [{ attemptToken: 'attempt', createdAt: '2026-10-04T00:00:00Z', report: { mode: 'hierarchical', status: 'complete' } }], evidencePasses: [], modelCalls: [] }
  const later = deferred<typeof audit>()
  const h = harness(vi.fn().mockResolvedValueOnce(audit).mockResolvedValueOnce({ ...audit, requestId: 'other' }).mockReturnValueOnce(later.promise))
  await expect(h.adapter.getExecutionAudit!('review')).resolves.toEqual(audit)
  expect(h.request.mock.calls[0].slice(0, 2)).toEqual(['organization.executionAudit', { id: 'review' }])
  await expect(h.adapter.getExecutionAudit!('review')).rejects.toThrow('invalid execution audit')
  const pending = h.adapter.getExecutionAudit!('review')
  const assertion = expect(pending).rejects.toThrow('connection or profile changed')
  h.change({ key: 'connection-b:default:socket-2', connected: true })
  expect(h.request.mock.calls[2][3].aborted).toBe(true)
  later.resolve(audit)
  await assertion
  expect(h.listenerCount()).toBe(0)
})


it('uses transport-owned routing metadata instead of backend-supplied connection fields', async () => {
  const forged = { ...runtimeSnapshot(), connection: { scope: 'forged', state: 'ready', ownerRoute: { connectionId: 'wrong', profile: 'wrong' } } }
  const h = harness(vi.fn().mockResolvedValue(forged))
  const route = { connectionId: 'remote-a', profile: 'research' }
  h.change({ key: 'real-socket', ownerKey: 'real-owner', ownerRoute: route, connected: true })
  const unsubscribe = h.adapter.subscribe(() => {})
  await settle()
  expect(h.adapter.getSnapshot().connection?.ownerRoute).toEqual(route)
  expect(h.adapter.getSnapshot().connection?.ownerScope).toBe('real-owner')
  unsubscribe()
})
