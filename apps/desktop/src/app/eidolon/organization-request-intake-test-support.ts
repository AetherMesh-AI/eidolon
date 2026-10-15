import type { RequestIntakeRecord } from './organization-request-intake-types'

export function intakeRecord(patch: Partial<RequestIntakeRecord> = {}): RequestIntakeRecord {
  return {
    id: 'intake-a',
    idempotencyKey: 'key-a',
    request: 'Fix launch failure',
    status: 'pending',
    parentIntakeId: null,
    snapshotId: 'snapshot-a',
    dispatched: true,
    proposal: null,
    objectiveId: null,
    reason: null,
    profile: 'default',
    usage: {
      provider: 'configured-provider',
      model: 'configured-model',
      reservedTokens: 5000,
      reservedCostUsd: '0.02',
      inputTokens: null,
      outputTokens: null
    },
    ...patch
  }
}

export function intakeProposal() {
  return {
    snapshotId: 'snapshot-a',
    routeId: 'route-a',
    executiveId: 'executive',
    managerId: 'manager',
    projectIds: ['project-a'],
    deliveryMode: 'source_project' as const,
    inferredPriority: 'P2' as const,
    effectivePriority: 'P1' as const,
    ownerOverride: true,
    rationale: 'An unverified launch blocker needs investigation.',
    evidenceIds: ['owner-request'],
    acceptanceCriteria: ['Launch succeeds'],
    assumptions: ['Use the existing launch path'],
    duplicateCandidateIds: [],
    dependencyCandidateIds: [],
    question: null,
    status: 'proposed' as const
  }
}
