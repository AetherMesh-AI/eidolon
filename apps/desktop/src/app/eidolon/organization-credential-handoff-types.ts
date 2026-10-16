import type { CredentialScope } from './organization-credentials-types'

export interface ReplacementTarget {
  actorId: string
  requestId: string
  generation: string
  entryId: string
  expectedRevision: number
  grantRevision: number
}

export interface ReplacementIntent {
  expectedOrganizationId: string
  expectedOwnerId: string
  entryId: string
  expectedRevision: number
  nextVersion: number
  actorId: string
  requestId: string
  generation: string
  expectedGrantRevision: number
  operationId: string
}

export interface PreparationReceipt {
  action: 'prepare_replacement'
  entryId: string
  expectedRevision: number
  actorId: string
  requestId: string
  generation: string
  operationId: string
  workerOperationId: string
  handoffId: string
  nextVersion: number
  grantRevision: number
  createdAt: number
  expiresAt: number
}

export interface ReplacementReceipt {
  action: 'replace_value'
  entryId: string
  revision: number
  version: number
  operationId: string
}

export interface RetainedReplacement {
  intent: ReplacementIntent
  preparation: PreparationReceipt | null
}

export type ReplacementState = 'absent' | 'pending' | 'unavailable' | 'cancelled' | 'committed'
export interface ReplacementStatus {
  operationId: string
  state: ReplacementState
  receipt: PreparationReceipt | ReplacementReceipt | null
}
export type HandoffError = 'unavailable' | 'unsupported' | 'conflict' | 'uncertain' | 'storage'
export interface HandoffView {
  entry: { id: string; revision: number; version: number } | null
  targets: ReplacementTarget[] | null
  retained: RetainedReplacement | null
  status: ReplacementStatus | null
  busy: boolean
  error: HandoffError | null
}
export type HandoffScope = CredentialScope
