import type { OwnerChatThread } from './runtime-owner-chat-types'

const text = (value: unknown, max: number, empty = false): value is string =>
  typeof value === 'string' && value.length <= max * 2 && [...value].length <= max && (empty || !!value.trim())

const id = (value: unknown): value is string =>
  text(value, 128) && value === value.trim() && [...value].every(character => character.charCodeAt(0) >= 32 && character.charCodeAt(0) !== 127)

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
    item.messages.length > 100 ||
    !Array.isArray(item.turns) ||
    item.turns.length > 101
  ) {
    return false
  }

  if (
    (item.latestMessageId !== undefined && !nullableId(item.latestMessageId)) ||
    (item.policyGeneration !== undefined && !count(item.policyGeneration)) ||
    (item.budget.version !== undefined && !count(item.budget.version)) ||
    item.budget.callsReserved > item.budget.maxCalls ||
    item.budget.tokensReserved > item.budget.maxTokens ||
    item.budget.remainingCalls !== item.budget.maxCalls - item.budget.callsReserved ||
    item.budget.remainingTokens !== item.budget.maxTokens - item.budget.tokensReserved ||
    item.limits.maxMessageChars > 6000
  ) {
    return false
  }

  const paged = item.history !== undefined

  if (
    paged &&
    (!item.history ||
      typeof item.history.hasMore !== 'boolean' ||
      !nullableId(item.history.oldestMessageId) ||
      !nullableId(item.latestMessageId) ||
      item.history.oldestMessageId !== (item.messages[0]?.id ?? null) ||
      (item.history.hasMore && !item.history.oldestMessageId))
  ) {
    return false
  }

  if (
    item.renewal !== undefined &&
    (!item.renewal ||
      !count(item.policyGeneration) ||
      !count(item.budget.version) ||
      typeof item.renewal.canRenew !== 'boolean' ||
      !['maxAdditionalCalls', 'maxOutstandingCalls', 'maxCumulativeCalls', 'tokensPerCall'].every(key =>
        count(item.renewal![key as 'maxAdditionalCalls'])
      ) ||
      item.renewal.maxAdditionalCalls > 32 ||
      item.renewal.tokensPerCall <= 0 ||
      !Number.isSafeInteger(item.renewal.maxAdditionalCalls * item.renewal.tokensPerCall) ||
      !(item.renewal.unavailableReason === null || text(item.renewal.unavailableReason, 2000)) ||
      (item.renewal.canRenew && (recipient.lifecycle !== 'active' || item.renewal.maxAdditionalCalls < 1)))
  ) {
    return false
  }

  if (
    item.renewalReceipt != null &&
    (!id(item.renewalReceipt.id) ||
      !id(item.renewalReceipt.idempotencyKey) ||
      !count(item.renewalReceipt.additionalCalls) ||
      item.renewalReceipt.additionalCalls < 1 ||
      item.renewalReceipt.additionalCalls > 32 ||
      !count(item.renewalReceipt.budgetVersion) ||
      !count(item.renewalReceipt.policyGeneration) ||
      !date(item.renewalReceipt.createdAt))
  ) {
    return false
  }

  const allMessageIds = new Set(item.messages.map(message => message?.id))
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
      !(
        message.replyToMessageId === null ||
        messages.has(message.replyToMessageId) ||
        (paged && id(message.replyToMessageId) && !allMessageIds.has(message.replyToMessageId))
      )
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
      !id(turn.ownerMessageId) ||
      !nullableId(turn.replyMessageId) ||
      turns.has(turn.id) ||
      receipts.has(turn.idempotencyKey) ||
      !statuses.has(turn.status) ||
      (!owner && (!paged || (!reply && turn.id !== item.activeTurnId))) ||
      (owner && (owner.role !== 'owner' || owner.turnId !== turn.id)) ||
      (turn.replyMessageId !== null &&
        ((!reply && !paged) ||
          (reply &&
            (reply.role !== 'agent' || reply.turnId !== turn.id || reply.replyToMessageId !== turn.ownerMessageId)))) ||
      (turn.status === 'completed' && !reply && !paged) ||
      !(turn.reason === null || text(turn.reason, 2000)) ||
      !date(turn.createdAt) ||
      !(turn.finishedAt === null || date(turn.finishedAt))
    ) {
      return false
    }

    if (
      turn.context !== undefined &&
      (!turn.context ||
        !(turn.context.omittedMessageCount === null || count(turn.context.omittedMessageCount)) ||
        !nullableId(turn.context.oldestIncludedMessageId) ||
        !(
          turn.context.includedMessageIds === null ||
          (Array.isArray(turn.context.includedMessageIds) &&
            turn.context.includedMessageIds.length > 0 &&
            turn.context.includedMessageIds.length <= 100 &&
            turn.context.includedMessageIds.every(id))
        ))
    ) {
      return false
    }

    if (turn.context) {
      const context = turn.context

      if (
        context.includedMessageIds === null
          ? context.omittedMessageCount !== null || context.oldestIncludedMessageId !== null
          : context.includedMessageIds.at(-1) !== turn.ownerMessageId ||
            context.omittedMessageCount === null ||
            new Set(context.includedMessageIds).size !== context.includedMessageIds.length ||
            context.oldestIncludedMessageId !== (context.includedMessageIds[0] ?? null)
      ) {
        return false
      }
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
    item.messages.every(message => referenced.has(message.id)) &&
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

export class OwnerChatRenewalRejected extends Error {}
