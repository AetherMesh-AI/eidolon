/** Recorded scalar display metadata; none of these claims authorize work or acceptance. */
export type PreparedValidationBlocker = 'reserved_undispatched' | 'runtime_receipt_unverified' | 'execution_outcome_unknown' | 'validation_failed' | 'resource_cleanup_unconfirmed'
export type PreparedValidationEventKind = 'dispatch_planned' | 'staging_allocated' | 'container_create_planned' | 'container_observed' | 'container_start_planned' | 'cleanup_observed' | 'receipt'

export interface PreparedValidationStepStatus {
  runId: string
  stepId: string
  recipe: 'workspace_typescript' | 'workspace_vitest_ui' | 'workspace_vite_build'
  reservedUnits: 1
  dispatched: boolean
  resourceClaimed: boolean
  resultClaimStatus: null | 'passed' | 'failed' | 'interrupted' | 'cleanup_failed' | 'unknown'
  cleanupClaimSettled: boolean
  latestCleanupSequence: number | null
  latestRuntimeEventSequence: number | null
  latestRuntimeEventKind: PreparedValidationEventKind | null
  blockerReasons: PreparedValidationBlocker[]
}

export interface PreparedValidationSetStatus {
  setId: string
  requestId: string
  projectId: string
  round: number
  generation: number
  profileVersion?: 2
  reservedUnits: 2 | 3
  snapshotSha256: string
  steps: [PreparedValidationStepStatus, PreparedValidationStepStatus] | [PreparedValidationStepStatus, PreparedValidationStepStatus, PreparedValidationStepStatus]
  aggregateClaimPresent: boolean
  reviewClaimPresent: boolean
  pairReviewRecorded: boolean
  state: 'reserved_undispatched' | 'runtime_unverified'
}

export interface PreparedValidationStatus {
  contractVersion?: 2
  setCounts?: { v1: number; v2: number }
  objectiveId: string
  storageState: 'absent' | 'present'
  legacyUnits: number
  preparedUnits: number
  totalUnits: number
  totalSets: number
  totalHistory: number
  current: Record<string, PreparedValidationSetStatus>
  history: PreparedValidationSetStatus[]
  historyTruncated: boolean
  blockers: {
    blocked: boolean
    totalBlockers: number
    entries: { setId: string; projectId: string; generation: number; runId: string; reasons: PreparedValidationBlocker[] }[]
    truncated: boolean
  }
  scalarMetadataOnly: true
  accountingVerified: false
  sourceBytesVerified: false
  runtimeReceiptsVerified: false
  executionAuthorized: false
  acceptanceAuthorized: false
  sourceDeliveryAuthorized: false
}
