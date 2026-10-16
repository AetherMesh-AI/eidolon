import type { HandoffScope, PreparationReceipt, ReplacementIntent, ReplacementStatus, ReplacementTarget, RetainedReplacement } from './organization-credential-handoff-types'

const identifier = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value)
const positive = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) > 0
const intentFields = ['expectedOrganizationId', 'expectedOwnerId', 'entryId', 'expectedRevision', 'nextVersion', 'actorId', 'requestId', 'generation', 'expectedGrantRevision', 'operationId']
const preparationFields = ['action', 'entryId', 'expectedRevision', 'actorId', 'requestId', 'generation', 'operationId', 'workerOperationId', 'handoffId', 'nextVersion', 'grantRevision', 'createdAt', 'expiresAt']

function object(value: unknown, fields: string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== fields.length || !fields.every(field => Object.hasOwn(value, field))) {
    throw new Error('Invalid replacement metadata')
  }

  return value as Record<string, unknown>
}

export function replacementIntent(value: ReplacementIntent): ReplacementIntent {
  return { expectedOrganizationId: value.expectedOrganizationId, expectedOwnerId: value.expectedOwnerId,
    entryId: value.entryId, expectedRevision: value.expectedRevision, nextVersion: value.nextVersion,
    actorId: value.actorId, requestId: value.requestId, generation: value.generation,
    expectedGrantRevision: value.expectedGrantRevision, operationId: value.operationId }
}

export function validateReplacementTargets(value: unknown, entryId: string, revision: number): ReplacementTarget[] {
  const raw = object(value, ['targets'])

  if (!Array.isArray(raw.targets)) {
    throw new Error('Invalid replacement targets')
  }

  const keys = new Set<string>()

  return raw.targets.map(item => {
    const target = object(item, ['actorId', 'requestId', 'generation', 'entryId', 'expectedRevision', 'grantRevision'])
    const key = JSON.stringify([target.actorId, target.requestId, target.generation])

    if (![target.actorId, target.requestId, target.generation].every(identifier) || target.entryId !== entryId ||
      target.expectedRevision !== revision || !positive(target.grantRevision) || keys.has(key)) {
      throw new Error('Invalid replacement target')
    }

    keys.add(key)

    return { actorId: String(target.actorId), requestId: String(target.requestId), generation: String(target.generation),
      entryId, expectedRevision: revision, grantRevision: target.grantRevision }
  })
}

export function validatePreparation(value: unknown, intent: ReplacementIntent, known: PreparationReceipt | null = null): PreparationReceipt {
  const receipt = object(value, preparationFields)

  if (receipt.action !== 'prepare_replacement' || receipt.operationId !== intent.operationId || receipt.entryId !== intent.entryId ||
    receipt.expectedRevision !== intent.expectedRevision || receipt.nextVersion !== intent.nextVersion || receipt.actorId !== intent.actorId ||
    receipt.requestId !== intent.requestId || receipt.generation !== intent.generation || receipt.grantRevision !== intent.expectedGrantRevision ||
    !identifier(receipt.workerOperationId) || receipt.workerOperationId === intent.operationId || !identifier(receipt.handoffId) ||
    typeof receipt.createdAt !== 'number' || !Number.isFinite(receipt.createdAt) || receipt.createdAt <= 0 || receipt.expiresAt !== receipt.createdAt + 90) {
    throw new Error('Invalid preparation receipt')
  }

  const normalized = Object.fromEntries(preparationFields.map(field => [field, receipt[field]])) as unknown as PreparationReceipt

  if (known && JSON.stringify(normalized) !== JSON.stringify(Object.fromEntries(preparationFields.map(field => [field, known[field as keyof PreparationReceipt]])))) {
    throw new Error('Changed preparation receipt')
  }

  return normalized
}

export function validateReplacementStatus(value: unknown, retained: RetainedReplacement, cancellation = false): ReplacementStatus {
  const result = object(value, ['operationId', 'state', 'receipt'])
  const { intent, preparation } = retained

  if (result.operationId !== intent.operationId || !['absent', 'pending', 'unavailable', 'cancelled', 'committed'].includes(String(result.state)) ||
    (cancellation && !['cancelled', 'committed'].includes(String(result.state)))) {
    throw new Error('Invalid replacement status')
  }

  if (result.state === 'committed') {
    const receipt = object(result.receipt, ['action', 'entryId', 'revision', 'version', 'operationId'])

    if (receipt.action !== 'replace_value' || receipt.entryId !== intent.entryId || receipt.revision !== intent.expectedRevision + 1 ||
      receipt.version !== intent.nextVersion || !identifier(receipt.operationId) || receipt.operationId === intent.operationId ||
      (preparation && receipt.operationId !== preparation.workerOperationId)) {
      throw new Error('Invalid replacement receipt')
    }

    return { operationId: intent.operationId, state: 'committed', receipt: { action: 'replace_value', entryId: intent.entryId,
      revision: intent.expectedRevision + 1, version: intent.nextVersion, operationId: receipt.operationId } }
  }

  if (result.state === 'absent') {
    if (result.receipt !== null || preparation) { throw new Error('Missing recorded preparation') }

    return { operationId: intent.operationId, state: 'absent', receipt: null }
  }

  if (result.state === 'cancelled' && result.receipt === null) {
    if (preparation) { throw new Error('Missing cancelled preparation') }

    return { operationId: intent.operationId, state: 'cancelled', receipt: null }
  }

  return { operationId: intent.operationId, state: result.state as 'pending' | 'unavailable' | 'cancelled',
    receipt: validatePreparation(result.receipt, intent, preparation) }
}

export function validateRetainedReplacement(value: unknown, scope: HandoffScope): RetainedReplacement {
  const record = object(value, ['intent', 'preparation'])
  const intent = object(record.intent, intentFields)

  if (intent.expectedOrganizationId !== scope.organizationId || intent.expectedOwnerId !== scope.ownerId ||
    !['expectedOrganizationId', 'expectedOwnerId', 'entryId', 'actorId', 'requestId', 'generation', 'operationId'].every(field => identifier(intent[field])) ||
    !['expectedRevision', 'nextVersion', 'expectedGrantRevision'].every(field => positive(intent[field]))) {
    throw new Error('Invalid retained replacement')
  }

  const normalized = replacementIntent(intent as unknown as ReplacementIntent)

  return { intent: normalized, preparation: record.preparation === null ? null : validatePreparation(record.preparation, normalized) }
}
