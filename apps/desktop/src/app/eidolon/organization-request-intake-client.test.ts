import { describe, expect, it, vi } from 'vitest'

import { createRequestIntakeClient } from './organization-request-intake-client'
import { intakeProposal, intakeRecord } from './organization-request-intake-test-support'
import type { RequestIntakeScope } from './organization-request-intake-types'

function harness() {
  const request = vi.fn()

  const scope: RequestIntakeScope = {
    key: 'socket-a',
    ownerKey: 'owner-a',
    eligible: true,
    profile: 'default',
    request
  }

  let key = 0

  const client = createRequestIntakeClient(
    () => ({ ...scope }),
    () => `key-${++key}`
  )

  return { request, scope, client }
}

function deferred<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(done => {
    resolve = done
  })

  return { promise, resolve }
}

describe('request intake owner boundaries', () => {
  it('retains one exact action across lost responses, profile changes, and A → B → A callbacks', async () => {
    const h = harness()
    const pending = deferred<unknown>()
    h.request.mockReturnValueOnce(pending.promise)
    h.client.edit({ text: ' Fix launch failure ', ownerPriority: 'P1' })
    const first = h.client.submit()
    await h.client.submit()
    expect(h.request).toHaveBeenCalledTimes(1)
    const original = h.request.mock.calls[0][1]
    expect(original).toEqual({ text: 'Fix launch failure', ownerPriority: 'P1', idempotencyKey: 'key-1' })
    h.scope.key = 'socket-b'
    h.scope.ownerKey = 'owner-b'
    expect(h.client.getSnapshot().text).toBe('')
    h.scope.key = 'socket-a'
    h.scope.ownerKey = 'owner-a'
    expect(h.client.getSnapshot().intent).toEqual(original)
    pending.resolve(
      intakeRecord({ idempotencyKey: 'key-1', status: 'admitted', objectiveId: 'old', proposal: intakeProposal() })
    )
    await first
    expect(h.client.getSnapshot().record).toBeNull()
    h.client.edit({ text: 'A different request' })
    expect(h.client.getSnapshot().text).toBe(' Fix launch failure ')
    h.request.mockRejectedValueOnce(new Error('Lost response'))
    await h.client.retry()
    h.scope.eligible = false
    expect(h.client.getSnapshot().intent).toBeNull()
    await h.client.retry()
    expect(h.request).toHaveBeenCalledTimes(2)
    h.scope.eligible = true
    h.request.mockResolvedValueOnce(intakeRecord({ idempotencyKey: 'key-1' }))
    await h.client.retry()
    expect(h.request.mock.calls.map(call => call[1])).toEqual([original, original, original])
    expect(h.client.getSnapshot().record?.id).toBe('intake-a')
    expect(h.client.getSnapshot().intent).toBeNull()
  })

  it('never accepts mismatched receipt identities or malformed admission, and reads cannot submit', async () => {
    const h = harness()
    h.client.edit({ text: 'Fix launch failure' })
    h.request.mockResolvedValueOnce(intakeRecord({ idempotencyKey: 'key-1', profile: 'other' }))
    await h.client.submit()
    expect(h.client.getSnapshot().error).toBe('uncertainTransport')
    h.request.mockResolvedValueOnce(intakeRecord({ idempotencyKey: 'key-1' }))
    await h.client.retry()
    const previous = h.client.getSnapshot().record
    h.request.mockResolvedValueOnce(
      intakeRecord({
        id: 'unrelated',
        idempotencyKey: 'key-1',
        status: 'admitted',
        objectiveId: 'objective-a',
        proposal: intakeProposal()
      })
    )
    await h.client.read()
    expect(h.client.getSnapshot().error).toBe('readFailed')
    expect(h.client.getSnapshot().record).toEqual(previous)
    h.request.mockResolvedValueOnce(intakeRecord({ idempotencyKey: 'key-1', status: 'admitted' }))
    await h.client.read()
    expect(h.client.getSnapshot().record).toEqual(previous)
    h.request.mockResolvedValueOnce(
      intakeRecord({
        idempotencyKey: 'key-1',
        status: 'admitted',
        objectiveId: 'objective-a',
        proposal: intakeProposal()
      })
    )
    await h.client.read()
    expect(h.client.getSnapshot().record?.objectiveId).toBe('objective-a')
    expect(h.request.mock.calls.slice(2)).toEqual(Array(3).fill(['organization.intake.read', { intakeId: 'intake-a' }]))
    await h.client.submit()
    expect(h.request).toHaveBeenCalledTimes(5)
  })
})

it('restores persisted questions through passive history and fences mismatched or late list responses', async () => {
  const question = intakeRecord({
    status: 'needs_answer',
    proposal: {
      ...intakeProposal(),
      status: 'needs_answer',
      question: { text: 'Which app?', neededFor: 'Choose project', checkedEvidenceIds: [] }
    }
  })

  const h = harness()
  expect(h.request).not.toHaveBeenCalled()
  h.client.edit({ text: 'An unsent request' })
  h.request.mockResolvedValueOnce({ profile: 'default', items: [question] })
  await h.client.list()
  expect(h.request.mock.calls).toEqual([['organization.intake.list', {}]])
  expect(h.client.getSnapshot().record).toBeNull()
  expect(h.client.select(question.id)).toBe(true)
  expect(h.client.getSnapshot().record?.proposal?.question?.text).toBe('Which app?')
  h.client.newRequest()
  expect(h.client.getSnapshot().text).toBe('An unsent request')
  h.client.select(question.id)
  h.client.edit({ answer: 'Desktop' })
  h.request.mockRejectedValueOnce(new Error('Lost answer response'))
  await h.client.answer()
  const intent = h.client.getSnapshot().intent
  h.request.mockResolvedValueOnce({ profile: 'default', items: [] })
  await h.client.list()
  expect(h.client.getSnapshot().intent).toEqual(intent)
  expect(h.client.getSnapshot().answer).toBe('Desktop')
  expect(h.client.select(question.id)).toBe(false)
  const delayed = deferred<unknown>()
  h.request.mockReturnValueOnce(delayed.promise)
  const listing = h.client.list()
  h.scope.key = 'socket-b'
  h.scope.ownerKey = 'owner-b'
  expect(h.client.getSnapshot().history.items).toEqual([])
  h.scope.key = 'socket-a'
  h.scope.ownerKey = 'owner-a'
  expect(h.client.getSnapshot().history.items).toEqual([])
  delayed.resolve({ profile: 'default', items: [question] })
  await listing
  expect(h.client.getSnapshot().history.items).toEqual([])
  h.request.mockResolvedValueOnce({ profile: 'other', items: [question] })
  await h.client.list()
  expect(h.client.getSnapshot().history.error).toBe(true)
  expect(h.client.getSnapshot().history.items).toEqual([])
  expect(h.client.getSnapshot().intent).toEqual(intent)
  h.request.mockResolvedValueOnce({ profile: 'default', items: [{ ...question, profile: 'other' }] })
  await h.client.list()
  expect(h.client.getSnapshot().history.error).toBe(true)
  expect(h.client.getSnapshot().history.items).toEqual([])
  expect(h.request.mock.calls.filter(([method]) => method !== 'organization.intake.list')).toEqual([
    ['organization.intake.answer', { intakeId: question.id, text: 'Desktop', idempotencyKey: 'key-1' }]
  ])
  h.request.mockResolvedValueOnce(
    intakeRecord({
      id: 'answered-child',
      parentIntakeId: question.id,
      idempotencyKey: intent!.idempotencyKey,
      request: 'The original request plus the desktop clarification'
    })
  )
  await h.client.retry()
  expect(h.request.mock.lastCall).toEqual(['organization.intake.answer', intent])
  expect(h.client.getSnapshot().record?.id).toBe('answered-child')
  expect(h.client.getSnapshot().text).toBe('An unsent request')
  h.client.newRequest()
  expect(h.client.getSnapshot().record).toBeNull()
  expect(h.client.getSnapshot().text).toBe('An unsent request')
})

describe('exact-repeat intent', () => {
  it('keeps force-separate immutable across an uncertain send and retries', async () => {
    const h = harness()
    h.client.edit({ text: 'Fix launch failure', forceSeparate: true })
    h.request.mockRejectedValueOnce(new Error('lost response'))
    await h.client.submit()
    const original = h.request.mock.calls[0][1]
    expect(original.forceSeparate).toBe(true)
    h.client.edit({ forceSeparate: false })
    expect(h.client.getSnapshot().forceSeparate).toBe(true)
    h.request.mockResolvedValueOnce(intakeRecord({ idempotencyKey: 'key-1' }))
    await h.client.retry()
    expect(h.request.mock.calls[1][1]).toEqual(original)
  })

  it('reads linked receipts with the exact existing objective and no dispatch', async () => {
    const h = harness()
    const linked = intakeRecord({ status: 'linked', objectiveId: 'existing-objective', proposal: intakeProposal() })
    h.request.mockResolvedValueOnce({ profile: 'default', items: [linked] })
    await h.client.list()
    expect(h.client.select(linked.id)).toBe(true)
    h.request.mockResolvedValueOnce(linked)
    await h.client.read()
    expect(h.client.getSnapshot().record?.objectiveId).toBe('existing-objective')
    expect(h.client.getSnapshot().record?.status).toBe('linked')
    expect(h.request.mock.calls.map(call => call[0])).toEqual(['organization.intake.list', 'organization.intake.read'])
  })
})
