import type { TeamSetup } from './team-onboarding-client'
import {
  subscriptionAcknowledgement,
  type SubscriptionCatalog,
  type SubscriptionPolicy,
  type SubscriptionPolicyState,
  type SubscriptionPreview,
  type SubscriptionStatus
} from './team-setup-subscription-client'

export const subscriptionSetup: TeamSetup = {
  capabilityVersion: 1,
  setupSubscriptionVersion: 1,
  setupSubscriptionState: 'available',
  inputImprovementVersion: 1,
  state: 'available',
  organizationId: '00000000-0000-4000-8000-000000000001',
  sourceRevision: 'a'.repeat(64),
  presets: [],
  generationAllowance: { status: 'blocked', remainingCalls: 3 },
  readiness: {
    status: 'blocked',
    blockers: ['subscription_policy_required'],
    modelRouting: 'unchanged',
    backgroundEnabled: false
  }
}
export const subscriptionStatus: SubscriptionStatus = {
  attemptId: null,
  stage: 'idle',
  expiresAt: null,
  connectionRevision: 0,
  connectionState: 'absent',
  cleanupPending: false
}
export const subscriptionCatalog: SubscriptionCatalog = {
  version: 1,
  state: 'ready',
  catalogId: '00000000-0000-4000-8000-000000000003',
  connectionRevision: 1,
  connectionId: 'd'.repeat(64),
  authorizationEpoch: '00000000-0000-4000-8000-000000000004',
  sourceRevision: subscriptionSetup.sourceRevision!,
  fetchedAt: 1,
  expiresAt: 61,
  models: [{ slug: 'synthetic-plan-model', displayName: 'Synthetic plan model' }]
}
export const subscriptionPolicy: SubscriptionPolicy = {
  version: 3,
  mode: 'subscription_calls',
  improveCallLimit: 4,
  generationCallLimit: 3,
  totalCallLimit: 7,
  requestTimeoutSeconds: 120,
  connectionId: subscriptionCatalog.connectionId,
  authorizationEpoch: subscriptionCatalog.authorizationEpoch,
  modelSlug: subscriptionCatalog.models[0].slug
}
export const subscriptionPolicyState: SubscriptionPolicyState = {
  capabilityVersion: 1,
  organizationId: subscriptionSetup.organizationId!,
  ownerId: '00000000-0000-4000-8000-000000000002',
  policyGeneration: 1,
  sourceRevision: subscriptionSetup.sourceRevision!,
  policyVersion: 1,
  configuration: null,
  setupBudget: {},
  state: 'available',
  reason: null
}
export const subscriptionPreview: SubscriptionPreview = {
  capabilityVersion: 1,
  organizationId: subscriptionPolicyState.organizationId,
  ownerId: subscriptionPolicyState.ownerId,
  policyGeneration: 1,
  sourceRevision: subscriptionSetup.sourceRevision!,
  configuration: subscriptionPolicy,
  previewVersion: 'b'.repeat(64),
  priorPolicy: null,
  effectiveCallLimits: { improveCallLimit: 4, generationCallLimit: 3, totalCallLimit: 7 },
  retainedCallCounts: { legacy: 2, improveTeamInput: 0, generateTeam: 0, total: 2 },
  remainingCalls: { total: 5, improveTeamInput: 4, generateTeam: 3 },
  retainedInheritanceCallCeiling: 120,
  effectiveTimeoutSeconds: 120,
  retiredGuarantees: [
    {
      guarantee: 'setup_product_total_tokens',
      value: 120000,
      source: 'setup_product_ceiling',
      scope: 'setup_only',
      historicalOrigin: 'unknown'
    },
    {
      guarantee: 'max_total_tokens',
      value: 1000000,
      source: 'execution_configuration_inheritance',
      scope: 'setup_only',
      historicalOrigin: 'unknown'
    }
  ],
  unchangedExecutionDigest: 'c'.repeat(64),
  acknowledgement: subscriptionAcknowledgement,
  hardTokenGuarantee: false,
  hardUsdGuarantee: false,
  allOrganizationConstraints: []
}
