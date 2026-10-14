import type { MetadataScope } from './organization-metadata-client'

export interface AllowanceAmounts {
  modelCalls: number
  tokens: number
  configuredCostUsd: string | null
}
export interface AllowanceAllocation {
  profile: string
  organizationId: string
  ownerId: string
  limits: AllowanceAmounts
  protected: AllowanceAmounts
}
export interface SharedAllowanceConfiguration {
  periodId: string
  startsAt: string
  endsAt: string
  limits: AllowanceAmounts
  protected: AllowanceAmounts
  allocations: AllowanceAllocation[]
}
export interface SharedAllowanceObservation {
  remainingPercent: number
  asOf: string
  expiresAt: string
  resetAt: string | null
}
export interface AllowanceCandidate {
  profile: string
  organizationId: string
  ownerId: string
  displayName: string
  ownerDisplayName: string
}
export interface SharedAllowanceScope extends MetadataScope {
  organizationId: string | null
  ownerId: string | null
}
export type SharedAllowanceAction = 'configuration' | 'observation'
export interface SharedAllowancePacing {
  policy: 'shared-stage-start-v1'
  enabled: true
  scope: 'installation'
  mode: 'unknown' | 'normal' | 'conserve' | 'critical'
  remainingPercent: number | null
  reason: 'shared_headroom' | 'manual_observation' | 'stale_observation'
  minIntervalSeconds: 0 | 60 | 300
  deferUntil: string | null
  advisory: false
  globalInflightLimit: null
  limitation: 'stage_start_spacing_only_no_provider_quota_inference_or_automatic_refill'
}
export interface SharedAllowanceDescriptor {
  capabilityVersion: 1
  state: 'ready' | 'unconfigured' | 'unsupported'
  scope: { kind: 'served-profile'; profile: string }
  organizationId: string
  ownerId: string
  authority: 'installation-owner' | 'member'
  generation: number
  sourceRevision: string
  configuration: SharedAllowanceConfiguration | null
  candidates: AllowanceCandidate[]
  pool: AllowanceProjection | null
  member: AllowanceProjection | null
  observation:
    | (SharedAllowanceObservation & {
        revision: number
        status: 'fresh' | 'expired' | 'reset_unconfirmed'
        source: 'owner_manual'
        scope: 'installation'
        advisory: true
      })
    | null
  pacing: SharedAllowancePacing | null
  managed: boolean
  capabilities: Record<SharedAllowanceAction, boolean>
}
export interface AllowanceProjection {
  limits: AllowanceAmounts
  reserved: AllowanceAmounts
  protectedRemaining: AllowanceAmounts
  ordinaryRemaining: AllowanceAmounts
  remaining: AllowanceAmounts
}
export interface SharedAllowanceRequest {
  expectedOrganizationId: string
  expectedOwnerId: string
  expectedGeneration: number
  sourceRevision: string
  configuration?: SharedAllowanceConfiguration
  observation?: SharedAllowanceObservation
}
export interface SharedAllowancePreview {
  capabilityVersion: 1
  scope: SharedAllowanceDescriptor['scope']
  organizationId: string
  ownerId: string
  generation: number
  sourceRevision: string
  previewVersion: string
  configuration?: SharedAllowanceConfiguration
  observation?: SharedAllowanceObservation
  changes: { field: string; before: unknown; after: unknown }[]
  warnings: {
    code:
      | 'owner_declared_allowance'
      | 'configured_prices_not_invoice'
      | 'no_refunds_or_auto_reset'
      | 'existing_local_limits_still_apply'
      | 'enrollment_requires_idle'
    values: Record<string, never>
  }[]
  impact: { scope: 'installation-allowance'; reservations: 'unchanged'; automaticReset: false }
}
export interface SharedAllowanceIntent extends SharedAllowanceRequest {
  action: SharedAllowanceAction
  previewVersion: string
  idempotencyKey: string
  confirmed: true
}
export interface SharedAllowanceReceipt {
  capabilityVersion: 1
  scope: SharedAllowanceDescriptor['scope']
  receipt: {
    action: SharedAllowanceAction
    idempotencyKey: string
    organizationId: string
    ownerId: string
    generation: number
    sourceRevision: string
    previewVersion: string
    configuration?: SharedAllowanceConfiguration
    observation?: SharedAllowanceObservation
  }
}
export type SharedAllowanceError =
  'unavailable' | 'unsupported' | 'unconfigured' | 'invalid' | 'conflict' | 'uncertain' | 'storage'
export interface SharedAllowanceView {
  descriptor: SharedAllowanceDescriptor | null
  preview: { action: SharedAllowanceAction; value: SharedAllowancePreview; request: SharedAllowanceRequest } | null
  intent: SharedAllowanceIntent | null
  receipt: SharedAllowanceReceipt | null
  busy: boolean
  error: SharedAllowanceError | null
}
