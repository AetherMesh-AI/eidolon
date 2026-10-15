export type OutcomeKind = 'deliverable' | 'authorized_check' | 'source_integration' | 'human_judgment'
export interface OutcomeCriterionInput {
  id: string
  text: string
  kind: OutcomeKind
  checkKind?: 'project_tests' | 'managed_validation' | 'project_typecheck' | 'project_build'
  projectId?: string
}
export interface OutcomeContract {
  schemaVersion: 1
  objectiveId: string
  revision: number
  sha256: string
  criteria: (OutcomeCriterionInput & { revision: number; legacy: boolean })[]
}
export interface OutcomeJudgmentInput {
  expectedOrganizationId: string
  objectiveId: string
  criterionId: string
  contractSha256: string
  deliverableSha256: string
  approved: boolean
  reason: string
  idempotencyKey: string
  confirmed: true
}
export interface OutcomeJudgment {
  criterionId: string
  contractSha256: string
  deliverableSha256: string
  approved: boolean
  reason: string
}

export const validOutcomeContract = (value: unknown): value is OutcomeContract => {
  const v = value as OutcomeContract

  return !!v && v.schemaVersion === 1 && typeof v.objectiveId === 'string' &&
    Number.isSafeInteger(v.revision) && v.revision > 0 && /^[a-f0-9]{64}$/.test(v.sha256) &&
    Array.isArray(v.criteria) && v.criteria.length > 0 && v.criteria.length <= 12 &&
    new Set(v.criteria.map(c => c?.id)).size === v.criteria.length && v.criteria.every(c =>
      !!c && typeof c.id === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(c.id) && typeof c.text === 'string' && c.text.trim().length > 0 && c.text.length <= 2000 && typeof c.legacy === 'boolean' &&
      Number.isSafeInteger(c.revision) && c.revision > 0 &&
      ['deliverable', 'authorized_check', 'source_integration', 'human_judgment'].includes(c.kind) &&
      (c.kind !== 'authorized_check' || ['project_tests', 'managed_validation', 'project_typecheck', 'project_build'].includes(c.checkKind ?? '')) &&
      (c.projectId === undefined || typeof c.projectId === 'string'))
}
