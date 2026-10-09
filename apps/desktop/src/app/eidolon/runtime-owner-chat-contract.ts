import type { OwnerChatThread } from './runtime-owner-chat-types'

const text = (value: unknown, max: number, empty = false): value is string =>
  typeof value === 'string' && value.length <= max * 2 && [...value].length <= max && (empty || !!value.trim())

const id = (value: unknown): value is string => text(value, 128)
const nullableId = (value: unknown): value is string | null => value === null || id(value)

const count = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0

const date = (value: unknown): value is string =>
  text(value, 64) && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value))

const statuses = new Set(['pending', 'running', 'completed', 'cancelled', 'timed_out', 'uncertain', 'blocked'])

/** Reject cross-thread replies and malformed limits before exposing a composer. */
export function validOwnerChatThread(value: unknown): value is OwnerChatThread {
  if (!value || typeof value !== 'object') {
    return false
  }

  const item = value as OwnerChatThread
  const recipient = item.recipient

  if (
    !id(item.id) ||
    !id(item.identityId) ||
    !id(item.agentId) ||
    !text(item.profile, 128) ||
    !recipient ||
    recipient.id !== item.agentId ||
    recipient.identityId !== item.identityId ||
    !text(recipient.name, 100) ||
    !text(recipient.role, 64) ||
    !text(recipient.team, 64, true) ||
    !['active', 'available', 'disabled', 'retired'].includes(recipient.lifecycle) ||
    !(recipient.provider === null || text(recipient.provider, 256)) ||
    !(recipient.model === null || text(recipient.model, 512)) ||
    typeof item.canSend !== 'boolean' ||
    !nullableId(item.activeTurnId) ||
    !(item.unavailableReason === null || text(item.unavailableReason, 2000)) ||
    !item.budget ||
    !['maxCalls', 'callsReserved', 'maxTokens', 'tokensReserved', 'remainingCalls', 'remainingTokens'].every(key =>
      count(item.budget[key as keyof typeof item.budget])
    ) ||
    !item.limits ||
    !['maxMessageChars', 'maxOutputTokens', 'timeoutSeconds'].every(
      key => count(item.limits[key as keyof typeof item.limits]) && item.limits[key as keyof typeof item.limits] > 0
    ) ||
    !Array.isArray(item.messages) ||
    item.messages.length > 64 ||
    !Array.isArray(item.turns) ||
    item.turns.length > 32
  ) {
    return false
  }

  if (
    item.budget.callsReserved > item.budget.maxCalls ||
    item.budget.tokensReserved > item.budget.maxTokens ||
    item.budget.remainingCalls !== item.budget.maxCalls - item.budget.callsReserved ||
    item.budget.remainingTokens !== item.budget.maxTokens - item.budget.tokensReserved ||
    item.limits.maxMessageChars > 6000
  ) {
    return false
  }

  const messages = new Map<string, OwnerChatThread['messages'][number]>()

  for (const message of item.messages) {
    if (
      !message ||
      !id(message.id) ||
      messages.has(message.id) ||
      !['owner', 'agent'].includes(message.role) ||
      !text(message.text, 6000) ||
      !id(message.turnId) ||
      !date(message.createdAt) ||
      !(message.replyToMessageId === null || messages.has(message.replyToMessageId))
    ) {
      return false
    }

    messages.set(message.id, message)
  }

  const turns = new Set<string>()
  const receipts = new Set<string>()
  const referenced = new Set<string>()

  for (const turn of item.turns) {
    const owner = messages.get(turn?.ownerMessageId)
    const reply = turn?.replyMessageId === null ? null : messages.get(turn?.replyMessageId)

    if (
      !turn ||
      !id(turn.id) ||
      !id(turn.idempotencyKey) ||
      turns.has(turn.id) ||
      receipts.has(turn.idempotencyKey) ||
      !statuses.has(turn.status) ||
      !owner ||
      owner.role !== 'owner' ||
      owner.turnId !== turn.id ||
      (turn.replyMessageId !== null &&
        (!reply || reply.role !== 'agent' || reply.turnId !== turn.id || reply.replyToMessageId !== owner.id)) ||
      (turn.status === 'completed' && !reply) ||
      !(turn.reason === null || text(turn.reason, 2000)) ||
      !date(turn.createdAt) ||
      !(turn.finishedAt === null || date(turn.finishedAt))
    ) {
      return false
    }

    turns.add(turn.id)
    receipts.add(turn.idempotencyKey)
    referenced.add(turn.ownerMessageId)

    if (turn.replyMessageId) {
      referenced.add(turn.replyMessageId)
    }
  }

  const active = item.turns.filter(turn => ['pending', 'running'].includes(turn.status))

  return (
    active.length <= 1 &&
    item.activeTurnId === (active[0]?.id ?? null) &&
    referenced.size === item.messages.length &&
    item.messages.every(message => turns.has(message.turnId)) &&
    (item.activeTurnId === null ||
      item.turns.some(turn => turn.id === item.activeTurnId && ['pending', 'running'].includes(turn.status))) &&
    (!item.canSend ||
      (item.activeTurnId === null &&
        recipient.lifecycle === 'active' &&
        item.budget.remainingCalls > 0 &&
        item.budget.remainingTokens > 0))
  )
}
