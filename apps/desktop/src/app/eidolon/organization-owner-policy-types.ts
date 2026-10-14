import type { MetadataScope } from './organization-metadata-client'

export const ownerModelRequestTypes = [
  'work.draft',
  'work.analyze',
  'work.inspect',
  'work.edit',
  'request.decompose',
  'request.plan',
  'request.review',
  'request.test_review',
  'request.integrate',
  'request.accept',
  'request.question',
  'request.decision',
  'request.message'
]

export interface OwnerModelRoute {
  id: string
  provider: string
  model: string
  reasoning: string
  request_types: string[]
  evidence_level: number
  max_context_tokens: number
  max_output_tokens: number
}
export interface OwnerModelAssignment {
  agent_id: string
  mode: 'fixed' | 'auto'
  route_ids: string[]
  minimum_evidence: number
  max_fallbacks: number
}
export interface OwnerModelPrice {
  provider: string
  model: string
  input_usd_per_million: string
  output_usd_per_million: string
}
export const budgetNumbers = {
  max_model_calls: [1, 1000],
  max_total_tokens: [4096, 1000000000],
  objective_timeout_seconds: [60, 604800],
  max_context_tokens: [4096, 2000000],
  max_output_tokens: [256, 16000],
  completion_reserve_model_calls: [0, 1000],
  completion_reserve_tokens: [0, 1000000000],
  pacing_conserve_below_percent: [1, 100],
  pacing_critical_below_percent: [1, 100],
  pacing_conserve_interval_seconds: [1, 3600],
  pacing_critical_interval_seconds: [1, 3600],
  pacing_conserve_max_inflight: [1, 4],
  pacing_critical_max_inflight: [1, 4]
} as const
export type BudgetNumber = keyof typeof budgetNumbers
export type OwnerBudgetConfiguration = Record<BudgetNumber, number> & {
  max_cost_usd: string | null
  completion_reserve_cost_usd: string | null
  adaptive_pacing_enabled: boolean
  model_costs: OwnerModelPrice[]
}
export interface OwnerRoutingConfiguration {
  model_routes: OwnerModelRoute[]
  model_assignments: OwnerModelAssignment[]
}
export type OwnerPolicyConfiguration = OwnerBudgetConfiguration & OwnerRoutingConfiguration
export interface OwnerBudgetObservation {
  objectiveId: string
  expectedRevision: number
  remainingPercent: number
  asOf: string
  expiresAt: string
  resetAt: string | null
}
export type OwnerPolicyAction = 'modelRouting' | 'budgetPolicy' | 'budgetObservation'
export interface OwnerPolicyScope extends MetadataScope {
  organizationId: string | null
  ownerId: string | null
}
export interface OwnerPolicyDescriptor {
  capabilityVersion: 1
  state: 'ready' | 'unconfigured' | 'unsupported'
  scope: { kind: 'served-profile'; profile: string }
  organizationId: string
  ownerId: string
  policyGeneration: number
  sourceRevision: string
  configuration: OwnerPolicyConfiguration
  managedFields: string[]
  capabilities: Record<OwnerPolicyAction, boolean>
  agents: { id: string; name: string; role: string }[]
  observations: {
    objectiveId: string
    revision: number
    observation:
      | (Omit<OwnerBudgetObservation, 'objectiveId' | 'expectedRevision'> & {
          revision: number
          status: 'fresh' | 'expired' | 'reset_unconfirmed'
          source: 'owner_manual'
          scope: 'objective'
          advisory: true
        })
      | null
  }[]
  reason?: string
}
export interface OwnerPolicyRequest {
  expectedOrganizationId: string
  expectedGeneration: number
  sourceRevision: string
  configuration?: OwnerRoutingConfiguration | Partial<OwnerBudgetConfiguration>
  observation?: OwnerBudgetObservation
}
export interface OwnerPolicyPreview {
  capabilityVersion: 1
  organizationId: string
  ownerId: string
  policyGeneration: number
  sourceRevision: string
  previewVersion: string
  scope: OwnerPolicyDescriptor['scope']
  configuration?: OwnerPolicyRequest['configuration']
  observation?: OwnerBudgetObservation
  warnings: {
    code:
      | 'no_topup'
      | 'captured_reserves_unchanged'
      | 'configured_prices_not_invoice'
      | 'owner_declared_routes'
      | 'direct_chat_only'
      | 'owner_chat_unchanged'
      | 'objective_advisory_only'
      | 'no_reset_or_refill'
      | 'existing_caps_may_tighten'
      | 'future_defaults_only'
      | 'running_attempts_fenced'
      | 'unchanged_values_pinned'
      | 'captured_caps_still_limit_increase'
    values: { fields?: string[]; activeAttempts?: number }
  }[]
  changes: { field: string; before: unknown; after: unknown }[]
  impact: {
    scope: 'objective-defaults' | 'persistent-agent-routing' | 'objective-advisory'
    evaluatedAt: string
    activeObjectiveCount: number
    existingObjectives: 'may-tighten-never-refill' | 'new-attempts' | 'advisory-only'
    futureObjectives: 'new-defaults' | 'unchanged'
    reservations: 'unchanged'
    completionReserves: 'captured-unchanged'
    usageProjection: 'not-estimated'
    runningAttempts: 'fenced-to-owner-review' | 'unchanged'
    runningAttemptCount: number
  }
}
export interface OwnerPolicyIntent extends OwnerPolicyRequest {
  action: OwnerPolicyAction
  ownerId: string
  previewVersion: string
  idempotencyKey: string
  confirmed: true
}
export interface OwnerPolicyReceipt {
  capabilityVersion: 1
  scope: OwnerPolicyDescriptor['scope']
  receipt: {
    idempotencyKey: string
    organizationId: string
    ownerId: string
    action: OwnerPolicyAction
    policyGeneration: number
    sourceRevision: string
    previewVersion: string
    configuration?: OwnerPolicyRequest['configuration']
    observation?: OwnerBudgetObservation
    observationRevision?: number
  }
}
export type OwnerPolicyError =
  'unavailable' | 'unsupported' | 'unconfigured' | 'invalid' | 'conflict' | 'sourceConflict' | 'uncertain' | 'storage'
export interface OwnerPolicyView {
  descriptor: OwnerPolicyDescriptor | null
  preview: { action: OwnerPolicyAction; value: OwnerPolicyPreview; request: OwnerPolicyRequest } | null
  intent: OwnerPolicyIntent | null
  receipt: OwnerPolicyReceipt | null
  busy: boolean
  error: OwnerPolicyError | null
}
