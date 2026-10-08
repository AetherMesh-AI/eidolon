import type { OrganizationConversation } from './types'

const boundedText = (value: unknown, limit: number, empty = false): value is string =>
  typeof value === 'string' &&
  value.length <= limit * 2 &&
  [...value].length <= limit &&
  (empty || value.trim().length > 0)

const id = (value: unknown): value is string => boundedText(value, 128)
const nullableId = (value: unknown): value is string | null => value === null || id(value)

const timestamp = (value: unknown): value is string =>
  boundedText(value, 64) && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value))

const statuses = new Set(['waiting_reply', 'answered', 'needs_input', 'cancelled'])

function validConversation(value: unknown): value is OrganizationConversation {
  if (!value || typeof value !== 'object') {
    return false
  }

  const item = value as OrganizationConversation

  if (
    !id(item.id) ||
    !id(item.objectiveId) ||
    !nullableId(item.taskId) ||
    !nullableId(item.projectId) ||
    !boundedText(item.subject, 200) ||
    !statuses.has(item.status) ||
    !nullableId(item.waitingAgentId) ||
    !Array.isArray(item.participants) ||
    item.participants.length !== 2 ||
    !Array.isArray(item.messages) ||
    item.messages.length < 1 ||
    item.messages.length > 16
  ) {
    return false
  }

  const participants = new Set<string>()

  for (const participant of item.participants) {
    if (
      !participant ||
      !id(participant.id) ||
      participants.has(participant.id) ||
      !boundedText(participant.name, 100) ||
      !boundedText(participant.team, 64, true)
    ) {
      return false
    }

    participants.add(participant.id)
  }

  if (item.waitingAgentId !== null && !participants.has(item.waitingAgentId)) {
    return false
  }

  const messages = new Set<string>()

  for (const message of item.messages) {
    if (
      !message ||
      !id(message.id) ||
      messages.has(message.id) ||
      !participants.has(message.senderId) ||
      !participants.has(message.recipientId) ||
      message.senderId === message.recipientId ||
      !boundedText(message.body, 6000) ||
      !timestamp(message.createdAt) ||
      (message.readAt !== null && !timestamp(message.readAt)) ||
      (message.replyToId !== null && !messages.has(message.replyToId))
    ) {
      return false
    }

    messages.add(message.id)
  }

  return true
}

/** Preserve older runtimes' omission. Select only bounded, typed fields, without
 * trimming messages or substituting current-roster identities for retained ones. */
export function normalizeConversations(value: unknown): OrganizationConversation[] | undefined {
  if (value === undefined) {
    return undefined
  }

  if (
    !Array.isArray(value) ||
    value.length > 3000 ||
    !value.every(validConversation) ||
    new Set(value.map(item => item.id)).size !== value.length
  ) {
    throw new Error('invalid_conversations')
  }

  return value.map(
    ({ id, subject, objectiveId, taskId, projectId, participants, status, messages, waitingAgentId }) => ({
      id,
      subject,
      objectiveId,
      taskId,
      projectId,
      status,
      waitingAgentId,
      participants: participants.map(({ id, name, team }) => ({ id, name, team })),
      messages: messages.map(({ id, senderId, recipientId, body, createdAt, readAt, replyToId }) => ({
        id,
        senderId,
        recipientId,
        body,
        createdAt,
        readAt,
        replyToId
      }))
    })
  )
}
