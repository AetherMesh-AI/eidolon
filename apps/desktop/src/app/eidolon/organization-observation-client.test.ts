import { afterEach, expect, it, vi } from 'vitest'

import { createOrganizationObservationClient, type ObservationScope } from './organization-observation-client'

const response = (profile = 'default', title = 'Retained objective') => ({
  capabilityVersion: 1, state: 'available', scope: { kind: 'served-profile', profile },
  snapshot: { source: 'runtime', objectives: [{ id: title, title }], agents: [], tasks: [], activity: [], knowledge: [], requests: [],
    runtime: { state: 'ready', capabilities: [], maxWorkers: 2, scope: 'fixture' } }
})

function deferred() {
  let resolve!: (value: unknown) => void
  let reject!: (error: unknown) => void
  const promise = new Promise((yes, no) => {resolve = yes; reject = no})

  return { promise, resolve, reject }
}

function harness() {
  const request = vi.fn().mockResolvedValue(response())
  let scope: ObservationScope = { key: 'connection-a:socket-1:default', profile: 'default', eligible: true, request }
  const listeners = new Set<() => void>()

  const client = createOrganizationObservationClient({
    getScope: () => scope,
    subscribeScope(listener) {listeners.add(listener);

 return () => listeners.delete(listener)}
  })

  return { client, request, listeners, change(next: Partial<ObservationScope>) {
    scope = { ...scope, ...next }; listeners.forEach(listener => listener())
  } }
}

afterEach(() => vi.useRealTimers())

it('reads only the captured observation endpoint, once for concurrent readers', async () => {
  const h = harness()
  const off = h.client.subscribe(vi.fn())

  expect(h.request).not.toHaveBeenCalled()
  const first = h.client.read()

  expect(h.client.read()).toBe(first)
  await first
  expect(h.request).toHaveBeenCalledExactlyOnceWith('organization.observe', {}, 10000, expect.any(AbortSignal))
  expect(h.client.getSnapshot().observation?.snapshot?.objectives[0].title).toBe('Retained objective')
  off(); h.client.dispose()
  expect(h.listeners.size).toBe(0)
})

it.each(['resolve', 'reject'] as const)('ignores stale %s through A → B → A and aborts the captured transport', async finish => {
  const h = harness()
  const old = deferred()

  h.request.mockReturnValueOnce(old.promise)
  const pending = h.client.read()

  await Promise.resolve()
  const signal = h.request.mock.calls[0][3] as AbortSignal

  h.change({ key: 'connection-b:socket-2:default' })
  h.change({ key: 'connection-a:socket-1:default' })
  expect(signal.aborted).toBe(true)
  expect(h.client.getSnapshot().observation).toBeNull()
  h.request.mockResolvedValueOnce(response('default', 'New request'))
  await h.client.read()

  if (finish === 'resolve') {old.resolve(response('default', 'Obsolete'))} else {old.reject({ code: 5073 })}
  await pending
  expect(h.client.getSnapshot().observation?.snapshot?.objectives[0].title).toBe('New request')
  expect(h.client.getSnapshot().error).toBeNull()
  h.client.dispose()
})

it('rejects mismatched served profiles and malformed envelopes without fallback', async () => {
  const h = harness()

  for (const value of [response('foreign'), { ...response(), capabilityVersion: 2 },
    { ...response(), snapshot: {} }, { ...response(), state: 'unconfigured' }]) {
    h.request.mockResolvedValueOnce(value)
    await h.client.read()
    expect(h.client.getSnapshot().error).toBe('invalid')
    expect(h.client.getSnapshot().observation).toBeNull()
  }

  expect(h.request.mock.calls.every(([method, params]) => method === 'organization.observe' && Object.keys(params).length === 0)).toBe(true)
  h.client.dispose()
})

it.each(['unconfigured', 'unsupported'])('retains an explicit %s result', async state => {
  const h = harness()

  h.request.mockResolvedValueOnce({ ...response(), state, snapshot: null })
  await h.client.read()
  expect(h.client.getSnapshot().observation?.state).toBe(state)
  expect(h.client.getSnapshot().error).toBeNull()
  h.client.dispose()
})

it.each([[-32601, 'unsupported'], [5073, 'storage'], [4001, 'unavailable']])('surfaces RPC %s without provisioning or reconnecting', async (code, error) => {
  const h = harness()

  h.request.mockRejectedValueOnce({ code })
  await h.client.read()
  expect(h.client.getSnapshot()).toEqual({ observation: null, busy: false, error })
  expect(h.request).toHaveBeenCalledTimes(1)
  h.client.dispose()
})

it('never reads an ineligible shared, disconnected or switching scope', async () => {
  const h = harness()

  h.change({ eligible: false })
  await h.client.read()
  expect(h.request).not.toHaveBeenCalled()
  expect(h.client.getSnapshot().error).toBe('unavailable')
  h.client.dispose()
})

it('bounds a stalled transport and permits explicit retry without late publication', async () => {
  vi.useFakeTimers()
  const h = harness()
  const stalled = deferred()

  h.request.mockReturnValueOnce(stalled.promise)
  const pending = h.client.read()

  await vi.advanceTimersByTimeAsync(10000)
  await pending
  expect(h.client.getSnapshot().error).toBe('unavailable')
  expect((h.request.mock.calls[0][3] as AbortSignal).aborted).toBe(true)
  await h.client.read()
  stalled.resolve(response('default', 'Too late'))
  await Promise.resolve()
  expect(h.client.getSnapshot().observation?.snapshot?.objectives[0].title).toBe('Retained objective')
  h.client.dispose()
})

it('disposal prevents both in-flight publication and subsequent reads', async () => {
  const h = harness()
  const old = deferred()

  h.request.mockReturnValueOnce(old.promise)
  const pending = h.client.read()

  await Promise.resolve()
  h.client.dispose()
  await pending
  old.resolve(response())
  await h.client.read()
  expect(h.request).toHaveBeenCalledTimes(1)
  expect(h.client.getSnapshot().observation).toBeNull()
  expect(h.listeners.size).toBe(0)
})
