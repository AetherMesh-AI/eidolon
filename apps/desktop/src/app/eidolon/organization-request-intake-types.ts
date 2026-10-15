import type { MetadataScope } from './organization-metadata-client'

export type IntakePriority = 'P1' | 'P2' | 'P3' | 'P4' | 'P5'
export type RequestIntakeStatus =
  'pending' | 'proposed' | 'admitted' | 'needs_answer' | 'answered' | 'needs_review' | 'failed' | 'uncertain'
export interface RequestIntakeProposal {
  snapshotId: string
  routeId: string | null
  executiveId: string | null
  managerId: string | null
  projectIds: string[]
  deliveryMode: 'source_project' | 'managed_artifact'
  inferredPriority: IntakePriority
  effectivePriority: IntakePriority
  ownerOverride: boolean
  rationale: string
  evidenceIds: string[]
  acceptanceCriteria: string[]
  assumptions: string[]
  duplicateCandidateIds: string[]
  dependencyCandidateIds: string[]
  question: { text: string; neededFor: string; checkedEvidenceIds: string[] } | null
  status: 'needs_answer' | 'proposed'
}
export interface RequestIntakeRecord {
  id: string
  idempotencyKey: string
  request: string
  status: RequestIntakeStatus
  parentIntakeId: string | null
  snapshotId: string
  dispatched: boolean
  proposal: RequestIntakeProposal | null
  objectiveId: string | null
  reason: string | null
  profile: string
  usage: {
    provider: string | null
    model: string | null
    reservedTokens: number
    reservedCostUsd: string | null
    inputTokens: number | null
    outputTokens: number | null
  }
}
export interface RequestIntakeIntent {
  text: string
  idempotencyKey: string
  intakeId?: string
  ownerPriority?: IntakePriority
}
export interface RequestIntakeHistory {
  items: RequestIntakeRecord[]
  busy: boolean
  error: boolean
}
export interface RequestIntakeView {
  history: RequestIntakeHistory
  text: string
  ownerPriority?: IntakePriority
  answer: string
  record: RequestIntakeRecord | null
  intent: RequestIntakeIntent | null
  busy: boolean
  error: 'uncertainTransport' | 'readFailed' | 'unsupported' | 'invalid' | null
}
export type RequestIntakeScope = MetadataScope
