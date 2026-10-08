import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway, type OrganizationScope } from './runtime-adapter'
import { normalizeConversations } from './runtime-conversation-contract'
import type { OrganizationConversation, OrganizationSnapshot } from './types'

function conversation(): OrganizationConversation {
  return {
    id: 'thread-1',
    subject: 'Clarify sources',
    objectiveId: 'objective-1',
    taskId: null,
    projectId: null,
    participants: [
      { id: 'agent-a', name: 'Researcher', team: 'Research' },
      { id: 'agent-b', name: 'Reviewer', team: 'Review' }
    ],
    status: 'waiting_reply',
    waitingAgentId: 'agent-b',
    messages: [
      {
        id: 'message-1',
        senderId: 'agent-a',
        recipientId: 'agent-b',
        body: 'Which source?',
        createdAt: '2026-10-08T01:00:00Z',
        readAt: null,
        replyToId: null
      }
    ]
  }
}

const snapshot = (conversations: OrganizationConversation[] | undefined): OrganizationSnapshot => ({
  source: 'runtime',
  objectives: [],
  agents: [],
  tasks: [],
  activity: [],
  knowledge: [],
  requests: [],
  conversations,
  runtime: { state: 'ready', capabilities: [], maxWorkers: 1, scope: 'Internal text' }
})

it('normalizes only bounded conversation fields and rejects identity ambiguity or false reply relationships', () => {
  const input = conversation()

  const extra = {
    ...input,
    internalSecret: 'do not project',
    participants: input.participants.map(item => ({ ...item, privateField: true }))
  }

  const normalized = normalizeConversations([extra])!
  expect(normalized).toEqual([input])
  expect(normalized[0]).not.toBe(input)
  expect(normalized[0].messages[0]).not.toBe(input.messages[0])
  expect(normalizeConversations(undefined)).toBeUndefined()
  expect(normalizeConversations([])).toEqual([])
  expect(
    normalizeConversations([{ ...input, messages: [{ ...input.messages[0], body: '🧭'.repeat(6000) }] }])![0]
      .messages[0].body
  ).toBe('🧭'.repeat(6000))

  const invalid = [
    null,
    {},
    [input, input],
    Array.from({ length: 3001 }, (_, i) => ({ ...input, id: `thread-${i}` })),
    [{ ...input, participants: [input.participants[0], input.participants[0]] }],
    [{ ...input, waitingAgentId: 'missing-agent' }],
    [{ ...input, participants: [{ ...input.participants[0], name: 'x'.repeat(101) }, input.participants[1]] }],
    [{ ...input, messages: [{ ...input.messages[0], senderId: 'missing-agent' }] }],
    [{ ...input, messages: [{ ...input.messages[0], replyToId: input.messages[0].id }] }],
    [{ ...input, messages: [{ ...input.messages[0], body: 'x'.repeat(6001) }] }],
    [{ ...input, messages: [{ ...input.messages[0], readAt: 'not-a-time' }] }],
    [{ ...input, messages: Array.from({ length: 17 }, (_, i) => ({ ...input.messages[0], id: `message-${i}` })) }]
  ]

  for (const value of invalid) {
    expect(() => normalizeConversations(value)).toThrow()
  }
})

it('retains last-good conversations on malformed refresh and fences old-profile replies', async () => {
  let scope: OrganizationScope = { key: 'connection-a:profile:socket', connected: true }
  const listeners = new Set<() => void>()
  const request = vi.fn().mockResolvedValue(snapshot([conversation()]))

  const gateway: OrganizationGateway = {
    request,
    getScope: () => scope,
    subscribeScope: callback => {
      listeners.add(callback)

      return () => {
        listeners.delete(callback)
      }
    }
  }

  const adapter = createRuntimeAdapter(gateway)
  await adapter.refresh()
  const previous = adapter.getSnapshot().conversations
  request.mockResolvedValueOnce(snapshot([{ ...conversation(), waitingAgentId: 'unknown' }]))
  await adapter.refresh()
  expect(adapter.getSnapshot().conversations).toBe(previous)
  expect(adapter.getSnapshot().connection).toMatchObject({
    state: 'error',
    error: 'The runtime returned an invalid internal conversation record.'
  })
  let resolve!: (value: OrganizationSnapshot) => void
  request.mockReturnValueOnce(
    new Promise<OrganizationSnapshot>(yes => {
      resolve = yes
    })
  )
  const oldRead = adapter.refresh()
  await Promise.resolve()
  scope = { key: 'connection-b:profile:socket', connected: true }
  listeners.forEach(listener => listener())
  expect(adapter.getSnapshot().conversations).toBeUndefined()
  resolve(snapshot([conversation()]))
  await oldRead
  expect(adapter.getSnapshot().conversations).toBeUndefined()
  request.mockResolvedValue(snapshot(undefined))
  await adapter.refresh()
  expect(adapter.getSnapshot().connection?.state).toBe('ready')
  expect(adapter.getSnapshot().conversations).toBeUndefined()
})
