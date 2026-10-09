import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway, type OrganizationScope } from './runtime-adapter'
import { OwnerChatRenewalRejected, validOwnerChatThread } from './runtime-owner-chat-contract'
import type { OwnerChatSend, OwnerChatThread } from './runtime-owner-chat-types'
import {
  admittedChat,
  answeredChat,
  deferred,
  ownerChatSnapshot,
  ownerChatThread
} from './runtime-owner-chat.test-support'

function harness(request: OrganizationGateway['request']) {
  let scope: OrganizationScope = { key: 'socket-a:default', ownerKey: 'connection-a:default', connected: true }
  const listeners = new Set<() => void>()

  const gateway: OrganizationGateway = {
    request: <T>(method: string, params: Record<string, unknown>, timeoutMs: number, signal: AbortSignal) =>
      request(method, params, timeoutMs, signal) as Promise<T>,
    getScope: () => scope,
    subscribeScope: listener => {
      listeners.add(listener)

      return () => listeners.delete(listener)
    }
  }

  return {
    adapter: createRuntimeAdapter(gateway),
    change: (next: OrganizationScope) => {
      scope = next
      listeners.forEach(listener => listener())
    }
  }
}

const input = (): OwnerChatSend => ({
  threadId: 'owner-chat-worker',
  identityId: 'identity-worker',
  text: 'Discuss this',
  replyToMessageId: null,
  idempotencyKey: 'exact-intent'
})

it('uses only identity-scoped chat RPCs, coalesces admission and verifies exact idempotency receipts', async () => {
  const reply = deferred<OwnerChatThread>()
  const thread = admittedChat(ownerChatThread(), input())

  const request = vi
    .fn()
    .mockResolvedValueOnce(ownerChatSnapshot())
    .mockResolvedValueOnce(ownerChatThread())
    .mockReturnValueOnce(reply.promise)

  const { adapter } = harness(request)
  await adapter.refresh()
  await adapter.openOwnerChat!({ agentId: 'worker', identityId: 'identity-worker' })
  const first = adapter.sendOwnerChat!(input())
  const duplicate = adapter.sendOwnerChat!(input())
  await Promise.resolve()
  expect(request.mock.calls.filter(([method]) => method === 'organization.ownerChat.send')).toHaveLength(1)
  reply.resolve(thread)
  await expect(first).resolves.toEqual(thread)
  await expect(duplicate).resolves.toEqual(thread)
  expect(request.mock.calls[1][0]).toBe('organization.ownerChat.open')
  expect(request.mock.calls[1][1]).toEqual({ agentId: 'worker', identityId: 'identity-worker' })
  expect(request.mock.calls[2][1]).toEqual(input())
  request.mockResolvedValueOnce({ ...thread, turns: [{ ...thread.turns[0], idempotencyKey: 'someone-else' }] })
  await expect(adapter.sendOwnerChat!(input())).rejects.toThrow('invalid or mismatched')
  request.mockResolvedValueOnce(thread)
  await expect(adapter.sendOwnerChat!(input())).resolves.toEqual(thread)
  expect(request.mock.calls[4][1]).toEqual(input())
  request.mockResolvedValueOnce(thread)
  await adapter.cancelOwnerChat!({ threadId: thread.id, identityId: thread.identityId, turnId: thread.turns[0].id })
  expect(request.mock.calls[5][0]).toBe('organization.ownerChat.cancel')
  expect(request.mock.calls[5][1]).toEqual({
    threadId: thread.id,
    identityId: thread.identityId,
    turnId: thread.turns[0].id
  })
  const renewal = { threadId: thread.id, identityId: thread.identityId, idempotencyKey: 'renewal-key', expectedBudgetVersion: 0, expectedPolicyGeneration: 3, additionalCalls: 1 }
  request.mockResolvedValueOnce({ ...thread, renewalReceipt: { id: 'receipt-id', idempotencyKey: renewal.idempotencyKey, additionalCalls: 1, budgetVersion: 1, policyGeneration: 3, createdAt: '2026-10-08T12:00:00Z' } })
  await expect(adapter.renewOwnerChat!(renewal)).resolves.toHaveProperty('renewalReceipt.idempotencyKey', 'renewal-key')
  expect(request.mock.calls.at(-1)?.[1]).toEqual(renewal)
  request.mockResolvedValueOnce({ profile: 'default', renewalRejected: true, reason: 'Stale version', threadId: thread.id, identityId: thread.identityId, idempotencyKey: renewal.idempotencyKey })
  await expect(adapter.renewOwnerChat!(renewal)).rejects.toBeInstanceOf(OwnerChatRenewalRejected)
  request.mockRejectedValueOnce(new Error('Transport lost'))
  await expect(adapter.renewOwnerChat!(renewal)).rejects.not.toBeInstanceOf(OwnerChatRenewalRejected)
  expect(
    request.mock.calls.every(
      ([method]) => method === 'organization.snapshot' || method.startsWith('organization.ownerChat.')
    )
  ).toBe(true)
})

it('fences old sockets and rejects wrong thread, immutable recipient and profile responses', async () => {
  const old = deferred<OwnerChatThread>()
  const request = vi.fn().mockResolvedValueOnce(ownerChatSnapshot()).mockReturnValueOnce(old.promise)
  const { adapter, change } = harness(request)
  await adapter.refresh()
  const pending = adapter.sendOwnerChat!(input())
  await Promise.resolve()
  const signal = request.mock.calls[1][3] as AbortSignal
  change({ key: 'socket-b:other', ownerKey: 'connection-b:other', connected: true })
  expect(signal.aborted).toBe(true)
  old.resolve(admittedChat(ownerChatThread(), input()))
  await expect(pending).rejects.toThrow('connection or profile changed')
  request.mockResolvedValueOnce({
    ...ownerChatSnapshot(),
    runtime: { ...ownerChatSnapshot().runtime!, profile: 'other' }
  })
  await adapter.refresh()
  request.mockResolvedValueOnce(ownerChatThread())
  await expect(adapter.openOwnerChat!({ agentId: 'worker', identityId: 'identity-worker' })).rejects.toThrow(
    'invalid or mismatched'
  )
  request.mockResolvedValueOnce({ ...ownerChatThread(), profile: 'other', id: 'wrong-thread' })
  await expect(
    adapter.readOwnerChat!({ threadId: 'owner-chat-worker', identityId: 'identity-worker' })
  ).rejects.toThrow('invalid or mismatched')
  request.mockResolvedValueOnce({
    ...ownerChatThread(),
    profile: 'other',
    identityId: 'wrong-identity',
    recipient: { ...ownerChatThread().recipient, identityId: 'wrong-identity' }
  })
  await expect(adapter.openOwnerChat!({ agentId: 'worker', identityId: 'identity-worker' })).rejects.toThrow(
    'invalid or mismatched'
  )
})

it('validates bounded history, exact reply edges, lifecycle gates and real turn receipts before rendering', () => {
  const pending = admittedChat(ownerChatThread(), input())
  const done = answeredChat(pending)
  expect(validOwnerChatThread(ownerChatThread())).toBe(true)
  expect(validOwnerChatThread(pending)).toBe(true)
  expect(validOwnerChatThread(done)).toBe(true)

  for (const invalid of [
    { ...done, messages: [{ ...done.messages[0], text: 'x'.repeat(6001) }, done.messages[1]] },
    { ...done, messages: [done.messages[0], { ...done.messages[1], replyToMessageId: 'cross-thread-message' }] },
    { ...done, turns: [{ ...done.turns[0], idempotencyKey: '' }] },
    { ...done, turns: [{ ...done.turns[0], ownerMessageId: done.messages[1].id }] },
    { ...done, recipient: { ...done.recipient, identityId: 'another' } },
    { ...done, recipient: { ...done.recipient, lifecycle: 'retired' } },
    { ...done, budget: { ...done.budget, remainingCalls: -1 } },
    { ...done, limits: { ...done.limits, maxMessageChars: 0 } },
    { ...done, activeTurnId: done.turns[0].id }
  ]) {
    expect(validOwnerChatThread(invalid)).toBe(false)
  }

  const paged = { ...done, latestMessageId: done.messages.at(-1)!.id, history: { hasMore: true, oldestMessageId: done.messages[0].id } }
  expect(validOwnerChatThread(paged)).toBe(true)
  expect(validOwnerChatThread({ ...paged, messages: [paged.messages[1]], history: { hasMore: true, oldestMessageId: paged.messages[1].id } })).toBe(true)

  for (const invalid of [
    { ...paged, messages: [{ ...paged.messages[0], replyToMessageId: paged.messages[0].id }, paged.messages[1]] },
    { ...paged, messages: [{ ...paged.messages[0], replyToMessageId: paged.messages[1].id }, paged.messages[1]] },
    { ...paged, turns: [{ ...paged.turns[0], context: { includedMessageIds: [paged.messages[0].id, paged.messages[0].id], omittedMessageCount: 0, oldestIncludedMessageId: paged.messages[0].id } }] },
    { ...paged, turns: [{ ...paged.turns[0], context: { includedMessageIds: null, omittedMessageCount: 2, oldestIncludedMessageId: null } }] },
    { ...paged, budget: { ...paged.budget, maxTokens: Number.MAX_SAFE_INTEGER + 1 } },
  ]) { expect(validOwnerChatThread(invalid)).toBe(false) }

  for (const status of ['cancelled', 'timed_out', 'uncertain', 'blocked'] as const) {
    expect(
      validOwnerChatThread({
        ...pending,
        activeTurnId: null,
        canSend: true,
        turns: [{ ...pending.turns[0], status, finishedAt: '2026-10-08T12:01:00Z' }]
      })
    ).toBe(true)
  }
})
