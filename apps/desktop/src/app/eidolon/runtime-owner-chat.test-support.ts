import type { OwnerChatSend, OwnerChatThread } from './runtime-owner-chat-types'
import type { OrganizationSnapshot } from './types'

export function ownerChatSnapshot(): OrganizationSnapshot {
  return {
    source: 'runtime',
    connection: { scope: 'socket-a:default', ownerScope: 'connection-a:default', state: 'ready' },
    runtime: { capabilities: ['owner_chat'], state: 'ready', profile: 'default', maxWorkers: 2, scope: 'Discussion' },
    agents: [
      {
        id: 'worker',
        identityId: 'identity-worker',
        persistent: true,
        name: 'Worker 1',
        role: 'Worker',
        team: 'Research',
        lifecycle: 'active',
        status: 'idle',
        responsibilities: ['Draft reports'],
        capabilities: [],
        summary: 'A persistent member.'
      }
    ],
    objectives: [],
    tasks: [],
    requests: [],
    activity: [],
    knowledge: []
  }
}

export function ownerChatThread(): OwnerChatThread {
  return {
    id: 'owner-chat-worker',
    identityId: 'identity-worker',
    agentId: 'worker',
    profile: 'default',
    recipient: {
      id: 'worker',
      identityId: 'identity-worker',
      name: 'Worker 1',
      role: 'Worker',
      team: 'Research',
      lifecycle: 'active',
      provider: null,
      model: null
    },
    messages: [],
    turns: [],
    activeTurnId: null,
    canSend: true,
    unavailableReason: null,
    budget: {
      maxCalls: 32,
      callsReserved: 0,
      maxTokens: 1114112,
      tokensReserved: 0,
      remainingCalls: 32,
      remainingTokens: 1114112
    },
    limits: { maxMessageChars: 6000, maxOutputTokens: 2048, timeoutSeconds: 90 }
  }
}

export function admittedChat(thread: OwnerChatThread, input: OwnerChatSend): OwnerChatThread {
  const id = `turn-${thread.turns.length + 1}`
  const ownerMessageId = `owner-${thread.messages.length + 1}`

  return {
    ...thread,
    canSend: false,
    activeTurnId: id,
    messages: [
      ...thread.messages,
      {
        id: ownerMessageId,
        turnId: id,
        role: 'owner',
        text: input.text,
        replyToMessageId: input.replyToMessageId,
        createdAt: '2026-10-08T12:00:00Z'
      }
    ],
    turns: [
      ...thread.turns,
      {
        id,
        idempotencyKey: input.idempotencyKey,
        ownerMessageId,
        replyMessageId: null,
        status: 'pending',
        reason: null,
        createdAt: '2026-10-08T12:00:00Z',
        finishedAt: null
      }
    ],
    budget: {
      ...thread.budget,
      callsReserved: thread.budget.callsReserved + 1,
      remainingCalls: thread.budget.remainingCalls - 1,
      tokensReserved: thread.budget.tokensReserved + 34816,
      remainingTokens: thread.budget.remainingTokens - 34816
    }
  }
}

export function answeredChat(thread: OwnerChatThread, text = 'Let’s discuss the idea.'): OwnerChatThread {
  const turn = thread.turns.at(-1)!
  const replyMessageId = `reply-${thread.messages.length + 1}`

  return {
    ...thread,
    canSend: true,
    activeTurnId: null,
    turns: thread.turns.map(item =>
      item.id === turn.id ? { ...item, status: 'completed', replyMessageId, finishedAt: '2026-10-08T12:00:01Z' } : item
    ),
    messages: [
      ...thread.messages,
      {
        id: replyMessageId,
        role: 'agent',
        text,
        turnId: turn.id,
        replyToMessageId: turn.ownerMessageId,
        createdAt: '2026-10-08T12:00:01Z'
      }
    ]
  }
}

export function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void

  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })

  return { promise, resolve, reject }
}
