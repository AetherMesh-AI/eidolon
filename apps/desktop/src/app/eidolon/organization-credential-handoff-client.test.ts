import { expect, it, vi } from 'vitest'

import { createCredentialHandoffClient } from './organization-credential-handoff-client'
import { handoffHarness, preparationFixture, targetFixture } from './organization-credential-handoff-test-support'
import type { ReplacementIntent, ReplacementStatus } from './organization-credential-handoff-types'
import { deferred, entryFixture } from './organization-credentials-test-support'

const secret = 'SYNTHETIC_OWNER_VALUE_NOT_FOR_STORAGE'

const prepare = async (h: ReturnType<typeof handoffHarness>) => {
  await h.client.loadTargets(entryFixture())

  return h.client.prepare(entryFixture(), targetFixture(), secret)
}

const committed = (intent: ReplacementIntent): ReplacementStatus => ({ operationId: intent.operationId, state: 'committed', receipt: {
  action: 'replace_value', entryId: intent.entryId, revision: intent.expectedRevision + 1, version: intent.nextVersion, operationId: `worker-${intent.operationId}`
} })

it('prepares only the exact listed generation and grant, retaining metadata without claiming a replacement', async () => {
  const h = handoffHarness()
  expect(await h.client.prepare(entryFixture(), targetFixture(), secret)).toBe(false)
  await h.client.loadTargets(entryFixture())
  expect(await h.client.prepare(entryFixture(), { ...targetFixture(), generation: 'later-generation' }, secret)).toBe(false)
  expect(await h.client.prepare(entryFixture(), { ...targetFixture(), grantRevision: 3 }, secret)).toBe(false)
  expect(await h.client.prepare(entryFixture(), targetFixture(), secret)).toBe(true)
  const view = h.client.getSnapshot()
  expect(view.status?.state).toBe('pending')
  expect(view.status?.receipt?.action).toBe('prepare_replacement')
  expect(h.request.mock.calls[1]).toEqual(['organization.credentials.prepareReplacement', {
    expectedOrganizationId: h.scope.organizationId, expectedOwnerId: h.scope.ownerId, entryId: entryFixture().id,
    expectedRevision: 3, actorId: 'identity-1', requestId: 'request-1', generation: 'generation-1', expectedGrantRevision: 2,
    operationId: view.retained!.intent.operationId, value: secret, confirmed: true
  }])
  expect(JSON.stringify([...h.values.values()])).not.toContain(secret)
  expect(JSON.stringify(view)).not.toContain(secret)
  expect([...h.values.values()][0]).not.toContain('inputHandle')
  expect(await h.client.prepare(entryFixture(), targetFixture(), 'SYNTHETIC_SECOND')).toBe(false)
  expect(h.request.mock.calls).toHaveLength(2)
})

it('retains a lost preparation identity across reload and absent status until explicit exact cancellation, without replay', async () => {
  const h = handoffHarness()
  await h.client.loadTargets(entryFixture())
  h.request.mockRejectedValueOnce(new Error(secret))
  expect(await h.client.prepare(entryFixture(), targetFixture(), secret)).toBe(false)
  const intent = h.client.getSnapshot().retained!.intent
  const restored = h.makeClient()
  restored.restore()
  expect(restored.getSnapshot().retained?.intent).toEqual(intent)
  expect(await restored.reconcile()).toBe(false)
  expect(restored.getSnapshot().status?.state).toBe('absent')
  expect(h.values.size).toBe(1)
  expect(await restored.cancel('different-operation')).toBe(false)
  expect(await restored.cancel(intent.operationId)).toBe(true)
  expect(restored.getSnapshot().status?.state).toBe('cancelled')
  expect(h.values.size).toBe(0)
  expect(h.request.mock.calls.filter(([method]) => method.endsWith('.prepareReplacement'))).toHaveLength(1)
  expect(h.request.mock.calls.at(-1)?.[1]).toMatchObject({ operationId: intent.operationId, confirmed: true })
})

it('validates exact committed revision/version and a known worker operation before releasing intent', async () => {
  const h = handoffHarness()
  await prepare(h)
  const intent = h.client.getSnapshot().retained!.intent
  const restored = h.makeClient()
  restored.restore()

  for (const malformed of [
    { ...committed(intent), operationId: 'another-owner-operation' },
    { ...committed(intent), receipt: { ...committed(intent).receipt, revision: intent.expectedRevision } },
    { ...committed(intent), receipt: { ...committed(intent).receipt, version: intent.nextVersion + 1 } },
    { ...committed(intent), receipt: { ...committed(intent).receipt, operationId: 'another-worker-operation' } }
  ]) {
    h.request.mockResolvedValueOnce(malformed)
    expect(await restored.reconcile()).toBe(false)
    expect(restored.getSnapshot().retained?.intent).toEqual(intent)
    expect(h.values.size).toBe(1)
  }

  h.statuses.set(intent.operationId, committed(intent))
  expect(await restored.reconcile()).toBe(true)
  expect(restored.getSnapshot().status?.state).toBe('committed')
  expect(h.values.size).toBe(0)
})

it('recovers committed truth after a lost prepare acknowledgment and never calls it cancelled', async () => {
  const h = handoffHarness()
  await h.client.loadTargets(entryFixture())
  h.request.mockRejectedValueOnce(new Error('lost acknowledgment'))
  await h.client.prepare(entryFixture(), targetFixture(), secret)
  const intent = h.client.getSnapshot().retained!.intent
  h.statuses.set(intent.operationId, committed(intent))
  expect(await h.client.cancel(intent.operationId)).toBe(true)
  expect(h.client.getSnapshot().status?.state).toBe('committed')
  expect(h.client.getSnapshot().retained).toBeNull()
})

it('cancels a recorded preparation even when its acknowledgment was lost', async () => {
  const h = handoffHarness()
  await h.client.loadTargets(entryFixture())
  h.request.mockImplementationOnce(async (_method, params) => {
    const intent = { ...params, nextVersion: 2 } as unknown as ReplacementIntent
    h.statuses.set(intent.operationId, { operationId: intent.operationId, state: 'pending', receipt: preparationFixture(intent) })
    throw new Error('Acknowledgment lost after preparation')
  })
  await h.client.prepare(entryFixture(), targetFixture(), secret)
  const record = h.client.getSnapshot().retained!
  expect(record.preparation).toBeNull()
  expect(await h.client.cancel(record.intent.operationId)).toBe(true)
  expect(h.client.getSnapshot().status).toMatchObject({ state: 'cancelled', receipt: { action: 'prepare_replacement', operationId: record.intent.operationId } })
  expect(h.values.size).toBe(0)
})

it('keeps unavailable, malformed and failed cancellation outcomes recoverable', async () => {
  const h = handoffHarness()
  await prepare(h)
  const record = h.client.getSnapshot().retained!
  h.statuses.set(record.intent.operationId, { operationId: record.intent.operationId, state: 'unavailable', receipt: record.preparation })
  expect(await h.client.reconcile()).toBe(false)
  expect(h.client.getSnapshot().status?.state).toBe('unavailable')

  for (const result of [
    { operationId: record.intent.operationId, state: 'cancelled', receipt: null },
    { operationId: record.intent.operationId, state: 'pending', receipt: record.preparation },
    { operationId: 'wrong-operation', state: 'cancelled', receipt: record.preparation }
  ]) {
    h.request.mockResolvedValueOnce(result)
    expect(await h.client.cancel(record.intent.operationId)).toBe(false)
    expect(h.values.size).toBe(1)
  }

  h.request.mockRejectedValueOnce(new Error(secret))
  expect(await h.client.cancel(record.intent.operationId)).toBe(false)
  expect(JSON.stringify(h.client.getSnapshot())).not.toContain(secret)
  expect(await h.client.cancel(record.intent.operationId)).toBe(true)
})

it('never accepts a preparation receipt for a different reviewed grant, generation, or version', async () => {
  for (const changed of [{ grantRevision: 3 }, { generation: 'new-generation' }, { nextVersion: 3 }]) {
    const h = handoffHarness()
    await h.client.loadTargets(entryFixture())
    h.request.mockImplementationOnce(async (_method, params) => ({ ...preparationFixture({ ...params, nextVersion: 2 } as unknown as ReplacementIntent), ...changed }))
    expect(await h.client.prepare(entryFixture(), targetFixture(), secret)).toBe(false)
    expect(h.client.getSnapshot().error).toBe('uncertain')
    expect(h.client.getSnapshot().retained).not.toBeNull()
  }
})

it('keeps newer same-owner durable intent when an old cancellation reply arrives late', async () => {
  const h = handoffHarness()
  await prepare(h)
  const old = h.client.getSnapshot().retained!
  const late = deferred<unknown>()
  h.request.mockReturnValueOnce(late.promise)
  const cancelling = h.client.cancel(old.intent.operationId)
  h.scope.ownerId = 'other-owner'
  h.client.restore()
  h.scope.ownerId = old.intent.expectedOwnerId
  h.client.restore()
  expect(await h.client.cancel(old.intent.operationId)).toBe(true)
  await h.client.loadTargets(entryFixture())
  h.request.mockRejectedValueOnce(new Error('new write uncertain'))
  await h.client.prepare(entryFixture(), targetFixture(), 'SYNTHETIC_NEW_VALUE')
  const newer = h.client.getSnapshot().retained!
  const stored = [...h.values.values()][0]
  late.resolve({ operationId: old.intent.operationId, state: 'cancelled', receipt: old.preparation })
  await cancelling
  expect(h.client.getSnapshot().retained).toEqual(newer)
  expect([...h.values.values()][0]).toBe(stored)
})

it('ignores old socket responses and never sends input on the replacement route automatically', async () => {
  const h = handoffHarness()
  await h.client.loadTargets(entryFixture())
  const late = deferred<unknown>()
  h.request.mockReturnValueOnce(late.promise)
  const preparing = h.client.prepare(entryFixture(), targetFixture(), secret)
  const intent = h.client.getSnapshot().retained!.intent
  const replacementRequest = vi.fn()
  h.scope.key = 'socket-b'
  h.scope.request = replacementRequest
  h.client.restore()
  late.resolve(preparationFixture(intent))
  await preparing
  expect(replacementRequest).not.toHaveBeenCalled()
  expect(h.client.getSnapshot().retained?.preparation).toBeNull()
  expect(h.client.getSnapshot().error).toBe('uncertain')
  h.scope.eligible = false
  expect(h.client.getSnapshot().error).toBe('unavailable')
  expect(h.client.getSnapshot().retained).toBeNull()
  expect(h.values.size).toBe(1)
})

it('fails closed before input transmission when durable storage is invalid or unavailable', async () => {
  const h = handoffHarness()
  const broken = createCredentialHandoffClient(() => h.scope, () => 'operation', { ...h.storage, setItem: () => { throw new Error('storage unavailable') } })
  broken.restore()
  await broken.loadTargets(entryFixture())
  expect(await broken.prepare(entryFixture(), targetFixture(), secret)).toBe(false)
  expect(broken.getSnapshot().error).toBe('storage')
  expect(h.request.mock.calls.filter(([method]) => method.endsWith('.prepareReplacement'))).toHaveLength(0)
  await prepare(h)
  const key = [...h.values.keys()][0]
  h.values.set(key, JSON.stringify({ intent: { ...h.client.getSnapshot().retained!.intent, value: secret }, preparation: null }))
  const restored = h.makeClient()
  restored.restore()
  expect(restored.getSnapshot().error).toBe('storage')
  expect(await restored.prepare(entryFixture(), targetFixture(), secret)).toBe(false)
})

it('checks the captured route again immediately before transmitting input', async () => {
  const h = handoffHarness()
  await h.client.loadTargets(entryFixture())

  const unsubscribe = h.client.subscribe(() => {
    const view = h.client.getSnapshot()

    if (view.busy && view.retained) { h.scope.key = 'new-socket' }
  })

  expect(await h.client.prepare(entryFixture(), targetFixture(), secret)).toBe(false)
  unsubscribe()
  expect(h.request.mock.calls.filter(([method]) => method.endsWith('.prepareReplacement'))).toHaveLength(0)
  expect(h.values.size).toBe(0)
})

it('adopts an already retained operation instead of replacing it before first hydration', async () => {
  const h = handoffHarness()
  const lateClient = h.makeClient()
  await lateClient.loadTargets(entryFixture())
  await prepare(h)
  const original = h.client.getSnapshot().retained!
  const stored = [...h.values.values()][0]
  expect(await lateClient.prepare(entryFixture(), targetFixture(), secret)).toBe(false)
  expect(lateClient.getSnapshot().retained).toEqual(original)
  expect([...h.values.values()][0]).toBe(stored)
  expect(h.request.mock.calls.filter(([method]) => method.endsWith('.prepareReplacement'))).toHaveLength(1)
})
