import { policyEqual } from './organization-owner-policy-contract'
import type { OwnerPolicyScope } from './organization-owner-policy-types'

export interface FolderGrant {
  alias: string
  path: string
  sourcePrefix: string[]
  device: number
  inode: number
  grantHash: string
  revoked: boolean
  reviewed: boolean
}

export interface FolderAccess {
  capabilityVersion: 1
  organizationId: string
  ownerId: string
  policyGeneration: number
  grants: FolderGrant[]
  paused: boolean
  reviewVersion: string
  executionStillStopping: boolean
  uncertainToolReceipts: { id: string; request_id: string; status: 'running' | 'unknown' }[]
  uncertainSourceRequests: { id: string; request_id: string }[]
  unsettledProjectRuns: { id: string; request_id: string }[]
  scope: { kind: 'served-profile'; profile: string }
}

interface FolderAccessRequest {
  expectedOrganizationId: string
  expectedGeneration: number
  alias: string
  grantHash: string
  confirmed: true
}
interface RevocationRequest extends FolderAccessRequest { idempotencyKey: string }
interface ReviewRequest extends FolderAccessRequest { reviewVersion: string; acknowledgeUncertain: boolean }
export type FolderAccessIntent =
  | { action: 'revoke'; params: RevocationRequest; grant: FolderGrant }
  | { action: 'review'; params: ReviewRequest; grant: FolderGrant }
export type FolderAccessErrorCode = 'changed' | 'unavailable' | 'invalid' | 'pending' | 'confirm' | 'blocked' | 'uncertain' | 'conflict'

export class FolderAccessError extends Error {
  constructor(readonly code: FolderAccessErrorCode) { super(code) }
}

const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
const text = (value: unknown): value is string => typeof value === 'string' && value.length > 0
const hash = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const integer = (value: unknown, min = 0) => Number.isSafeInteger(value) && (value as number) >= min

const validGrant = (value: unknown): value is FolderGrant => object(value) && typeof value.alias === 'string' && /^root[0-7]$/.test(value.alias) &&
  text(value.path) && Array.isArray(value.sourcePrefix) && value.sourcePrefix.every(text) && integer(value.device) && integer(value.inode) &&
  hash(value.grantHash) && typeof value.revoked === 'boolean' && typeof value.reviewed === 'boolean' && (!value.reviewed || value.revoked)

const validRun = (value: unknown) => object(value) && text(value.id) && text(value.request_id)

// These explicit owner-policy reasons leave the current request uncommitted.
// Transport errors and unreadable/malformed replies cannot establish noncommit.
const rejectedBeforeCommit = (reason: unknown) => object(reason) && reason.code === 5074 && object(reason.data) &&
  ['generation_conflict', 'grant_conflict', 'already_revoked', 'idempotency_conflict', 'review_conflict',
    'execution_still_stopping', 'project_reconciliation_required', 'uncertain_outcome_review_required', 'execution_not_initialized'].includes(String(reason.data.reason))

/** Reads do not inspect source paths. Writes stay on the original owner socket. */
export function createFolderAccessClient(source: () => OwnerPolicyScope, newKey: () => string = () => crypto.randomUUID()) {
  let captured: OwnerPolicyScope | null = null
  let descriptor: FolderAccess | null = null
  let intent: FolderAccessIntent | null = null
  let busy = false
  let invalidated = false
  const identity = (scope: OwnerPolicyScope) => [scope.key, scope.ownerKey, scope.profile, scope.eligible, scope.organizationId, scope.ownerId]

  const current = () => {
    if (captured && !policyEqual(identity(captured), identity(source()))) { invalidated = true }

    return !!captured && captured.eligible && !invalidated
  }

  const requireCurrent = () => { if (!current()) { throw new FolderAccessError('changed') } }

  const validEnvelope = (value: unknown): value is Record<string, unknown> => object(value) && value.capabilityVersion === 1 &&
    object(value.scope) && value.scope.kind === 'served-profile' && value.scope.profile === captured?.profile

  const validateRead = (value: unknown): FolderAccess => {
    if (!validEnvelope(value) || value.organizationId !== captured?.organizationId || value.ownerId !== captured?.ownerId ||
      !integer(value.policyGeneration, 1) || !Array.isArray(value.grants) || value.grants.length > 8 || !value.grants.every(validGrant) ||
      new Set(value.grants.map(grant => grant.alias)).size !== value.grants.length || typeof value.paused !== 'boolean' || !hash(value.reviewVersion) ||
      typeof value.executionStillStopping !== 'boolean' || !Array.isArray(value.uncertainToolReceipts) ||
      !value.uncertainToolReceipts.every(receipt => validRun(receipt) && object(receipt) && ['running', 'unknown'].includes(String(receipt.status))) ||
      !Array.isArray(value.uncertainSourceRequests) || !value.uncertainSourceRequests.every(validRun) ||
      !Array.isArray(value.unsettledProjectRuns) || !value.unsettledProjectRuns.every(validRun)) {
      throw new FolderAccessError('invalid')
    }

    return structuredClone(value) as unknown as FolderAccess
  }

  const requireGrant = (alias: string, confirmed: boolean) => {
    requireCurrent()

    if (busy || intent) { throw new FolderAccessError('pending') }
    const grant = descriptor?.grants.find(value => value.alias === alias)

    if (!confirmed || !descriptor || !grant) { throw new FolderAccessError('confirm') }

    return { grant, params: { expectedOrganizationId: descriptor.organizationId, expectedGeneration: descriptor.policyGeneration,
      alias: grant.alias, grantHash: grant.grantHash, confirmed: true as const } }
  }

  const send = async () => {
    requireCurrent()

    if (!intent || busy) { throw new FolderAccessError('pending') }
    const saved = intent

    if (saved.action === 'review' && (!descriptor || descriptor.executionStillStopping || descriptor.unsettledProjectRuns.length)) {
      throw new FolderAccessError('blocked')
    }

    busy = true

    try {
      const result = await captured!.request(saved.action === 'revoke' ? 'organization.revokeProjectFolder' : 'organization.reviewFolderRevocation', { ...saved.params })
      requireCurrent()

      if (!validEnvelope(result)) { throw new FolderAccessError('uncertain') }

      if (saved.action === 'revoke') {
        const receipt = result.receipt

        if (!object(receipt) || receipt.idempotencyKey !== saved.params.idempotencyKey || receipt.organizationId !== captured!.organizationId ||
          receipt.ownerId !== captured!.ownerId || receipt.alias !== saved.params.alias || receipt.grantHash !== saved.params.grantHash ||
          receipt.policyGeneration !== saved.params.expectedGeneration + 1 || receipt.accessRevoked !== true || receipt.dispatchPaused !== true) {
          throw new FolderAccessError('uncertain')
        }
      } else if (result.alias !== saved.params.alias || result.accessRevoked !== true || result.reviewed !== true) {
        throw new FolderAccessError('uncertain')
      }

      // A receipt confirms this action only. Refresh before offering another action.
      descriptor = null
      intent = null

      return saved.action
    } catch (reason) {
      if (current() && rejectedBeforeCommit(reason)) {
        intent = null
        descriptor = null
        throw new FolderAccessError('conflict')
      }

      throw reason
    } finally { busy = false }
  }

  return {
    current,
    pending: () => intent ? structuredClone(intent) : null,
    async read() {
      if (busy) { throw new FolderAccessError('pending') }

      if (captured) { requireCurrent() } else {
        const scope = source()

        if (!scope.eligible || !scope.organizationId || !scope.ownerId) { throw new FolderAccessError('unavailable') }
        captured = { ...scope }
      }

      descriptor = null
      busy = true

      try {
        const result = await captured.request('organization.folderAccess', { expectedOrganizationId: captured.organizationId })
        requireCurrent()
        descriptor = validateRead(result)

        return structuredClone(descriptor)
      } finally { busy = false }
    },
    async revoke(alias: string, confirmed: boolean) {
      const { grant, params } = requireGrant(alias, confirmed)

      if (grant.revoked) { throw new FolderAccessError('confirm') }
      intent = { action: 'revoke', params: { ...params, idempotencyKey: newKey() }, grant: structuredClone(grant) }

      return send()
    },
    async review(alias: string, confirmed: boolean, acknowledgeUncertain: boolean) {
      const { grant, params } = requireGrant(alias, confirmed)

      if (!grant.revoked || grant.reviewed) { throw new FolderAccessError('confirm') }

      if (descriptor!.executionStillStopping || descriptor!.unsettledProjectRuns.length || ((descriptor!.uncertainToolReceipts.length || descriptor!.uncertainSourceRequests.length) && !acknowledgeUncertain)) {
        throw new FolderAccessError('blocked')
      }

      intent = { action: 'review', params: { ...params, reviewVersion: descriptor!.reviewVersion, acknowledgeUncertain }, grant: structuredClone(grant) }

      return send()
    },
    retry: send
  }
}
