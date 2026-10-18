import type { TeamSetup } from './team-onboarding-client'

export interface SubscriptionRecovery {
  version: 1
  disposition: 'retry_unsent'
  idempotencyKey: string
  operation: 'improve_jobs' | 'improve_goals' | 'generate'
  sourceRevision: string
}

export interface SubscriptionPendingRequest extends Omit<SubscriptionRecovery, 'disposition'> {
  disposition: 'reconcile_unsent'
}

function readSubscriptionRequest<T extends 'retry_unsent' | 'reconcile_unsent'>(
  value: unknown,
  disposition: T,
  expected: Pick<SubscriptionRecovery, 'operation' | 'sourceRevision'> & { idempotencyKey?: string }
): (Omit<SubscriptionRecovery, 'disposition'> & { disposition: T }) | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null
  }

  const request = value as Record<string, unknown>

  if (
    Object.keys(request).sort().join(',') !== 'disposition,idempotencyKey,operation,sourceRevision,version' ||
    request.version !== 1 ||
    request.disposition !== disposition ||
    !text(request.idempotencyKey, 128) ||
    !hash(request.sourceRevision) ||
    typeof request.operation !== 'string' ||
    !['improve_jobs', 'improve_goals', 'generate'].includes(request.operation) ||
    (expected.idempotencyKey !== undefined && request.idempotencyKey !== expected.idempotencyKey) ||
    request.operation !== expected.operation ||
    request.sourceRevision !== expected.sourceRevision
  ) {
    return null
  }

  return {
    version: 1,
    disposition,
    idempotencyKey: request.idempotencyKey,
    operation: expected.operation,
    sourceRevision: expected.sourceRevision
  }
}

/** An advisory about one old attempt, never authority for the next request. */
export function readSubscriptionRecovery(
  failure: unknown,
  expected: Pick<SubscriptionRecovery, 'idempotencyKey' | 'operation' | 'sourceRevision'>
): SubscriptionRecovery | null {
  const error = failure as { code?: unknown; data?: { recovery?: unknown } } | null

  return error?.code === 5076 ? readSubscriptionRequest(error.data?.recovery, 'retry_unsent', expected) : null
}

export function readSubscriptionPendingRequest(
  value: unknown,
  expected: Pick<SubscriptionRecovery, 'operation' | 'sourceRevision'>
): SubscriptionPendingRequest | null {
  return readSubscriptionRequest(value, 'reconcile_unsent', expected)
}

export function readSubscriptionReconciledRequest(
  value: unknown,
  expected: Pick<SubscriptionRecovery, 'idempotencyKey' | 'operation' | 'sourceRevision'>
): SubscriptionRecovery | null {
  return readSubscriptionRequest(value, 'retry_unsent', expected)
}

export interface SubscriptionPolicy {
  version: 3
  mode: 'subscription_calls'
  improveCallLimit: number
  generationCallLimit: number
  totalCallLimit: number
  requestTimeoutSeconds: number
  connectionId: string
  authorizationEpoch: string
  modelSlug: string
}
export interface SubscriptionStatus {
  attemptId: string | null
  stage: string
  expiresAt: number | null
  connectionRevision: number
  connectionState: 'absent' | 'active' | 'refreshing' | 'disconnected'
  cleanupPending: boolean
  canCancel?: boolean
}
export interface SubscriptionCatalog {
  version: 1
  state: 'ready'
  catalogId: string
  connectionRevision: number
  connectionId: string
  authorizationEpoch: string
  sourceRevision: string
  fetchedAt: number
  expiresAt: number
  models: { slug: string; displayName: string }[]
}
export interface SubscriptionCounts {
  legacy: number
  improveTeamInput: number
  generateTeam: number
  total: number
}
export interface SubscriptionUsage {
  version: 1
  tokenStatus: 'observed' | 'unknown'
  costStatus: 'unknown'
  inputTokens: number | null
  outputTokens: number | null
  totalTokens: number | null
  costUsd: null
}
export interface SubscriptionPolicyState {
  capabilityVersion: 1
  organizationId: string
  ownerId: string
  policyGeneration: number
  sourceRevision: string
  policyVersion: 1 | 2 | 3
  configuration: SubscriptionPolicy | Record<string, unknown> | null
  state: 'available' | 'blocked'
  reason: string | null
  setupBudget: {
    setupUsage?: SubscriptionUsage
    limits?: { improveCalls: number; generationCalls: number; totalCalls: number; totalTokens: null; maxCostUsd: null }
    requestTimeoutSeconds?: number
    [key: string]: unknown
  }
}
export interface SubscriptionAcknowledgement {
  version: 1
  code: 'retire_setup_inheritance_non_hard_usage_v1'
  text: string
}
export interface RetiredGuarantee {
  guarantee: string
  value: unknown
  source: string
  scope: 'setup_only'
  historicalOrigin?: 'unknown'
}
export interface SubscriptionPreview {
  capabilityVersion: 1
  organizationId: string
  ownerId: string
  policyGeneration: number
  sourceRevision: string
  configuration: SubscriptionPolicy
  previewVersion: string
  priorPolicy: Record<string, unknown> | null
  effectiveCallLimits: Pick<SubscriptionPolicy, 'improveCallLimit' | 'generationCallLimit' | 'totalCallLimit'>
  retainedCallCounts: SubscriptionCounts
  remainingCalls: { total: number; improveTeamInput: number; generateTeam: number }
  retainedInheritanceCallCeiling: number
  effectiveTimeoutSeconds: number
  retiredGuarantees: RetiredGuarantee[]
  unchangedExecutionDigest: string
  acknowledgement: SubscriptionAcknowledgement
  hardTokenGuarantee: false
  hardUsdGuarantee: false
  allOrganizationConstraints: unknown[]
}
export interface SubscriptionReceipt {
  version: 1
  action: 'retireSetupInheritance'
  idempotencyKey: string
  organizationId: string
  ownerId: string
  priorGeneration: number
  appliedGeneration: number
  sourceRevision: string
  previewDigest: string
  candidate: SubscriptionPolicy
}
export interface SubscriptionApplied {
  capabilityVersion: 1
  receipt: SubscriptionReceipt
  sourceRevision?: string
  contextState?: 'refresh_required'
  historical: boolean
}
export interface SubscriptionTicket {
  version: 1
  sequence: string
  nonce: string
  kind: 'refresh' | 'revoke'
  bindingDigest: string
  authorizationEpoch: string
  priorStoreRevision: number
  sourceRevision: string
}
export interface SubscriptionOperation {
  state: string
  ticket?: SubscriptionTicket | null
  catalog?: SubscriptionCatalog | null
  catalogState?: string
}

export const subscriptionAcknowledgement: SubscriptionAcknowledgement = {
  version: 1,
  code: 'retire_setup_inheritance_non_hard_usage_v1',
  text: 'I retire only the listed setup token, context, output and cost guarantees. Setup uses subscription allowance with bounded calls and a local timeout, without a hard token or USD guarantee. Usage may continue after local cancellation. Historical origins are unknown. Execution constraints and prior charges remain.'
}

const invalid = () => {
  throw new Error('invalid_setup_subscription')
}

const object = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return invalid()
  }

  return value as Record<string, unknown>
}

const integer = (value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER) =>
  Number.isSafeInteger(value) && Number(value) >= min && Number(value) <= max

const text = (value: unknown, max = 256): value is string =>
  typeof value === 'string' &&
  value.length > 0 &&
  value.length <= max &&
  value === value.trim() &&
  !Array.from(value).some(char => char.charCodeAt(0) < 32)

const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)

const uuid = (value: unknown): value is string =>
  typeof value === 'string' && /^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value)

const equal = (left: unknown, right: unknown): boolean => {
  if (left === right) {
    return true
  }

  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') {
    return false
  }

  const a = Object.keys(left).sort(),
    b = Object.keys(right).sort()

  return (
    a.join() === b.join() &&
    a.every(key => equal((left as Record<string, unknown>)[key], (right as Record<string, unknown>)[key]))
  )
}

const callFields = ['improveCallLimit', 'generationCallLimit', 'totalCallLimit'] as const

export function validateSubscriptionUsage(value: unknown): asserts value is SubscriptionUsage {
  const usage = object(value)
  const fields = ['inputTokens', 'outputTokens', 'totalTokens']

  if (
    usage.version !== 1 ||
    !['observed', 'unknown'].includes(String(usage.tokenStatus)) ||
    usage.costStatus !== 'unknown' ||
    usage.costUsd !== null ||
    (usage.tokenStatus === 'unknown'
      ? !fields.every(key => usage[key] === null)
      : !fields.every(key => integer(usage[key])) ||
        usage.totalTokens !== Number(usage.inputTokens) + Number(usage.outputTokens))
  ) {
    invalid()
  }
}

function validateTicket(
  value: unknown,
  setup: TeamSetup,
  kind?: 'refresh' | 'revoke',
  revision?: number
): asserts value is SubscriptionTicket {
  const ticket = object(value)

  if (
    Object.keys(ticket).sort().join() !==
      [
        'version',
        'sequence',
        'nonce',
        'kind',
        'bindingDigest',
        'authorizationEpoch',
        'priorStoreRevision',
        'sourceRevision'
      ]
        .sort()
        .join() ||
    ticket.version !== 1 ||
    !text(ticket.sequence, 19) ||
    !/^[1-9][0-9]*$/.test(ticket.sequence) ||
    !hash(ticket.nonce) ||
    !hash(ticket.bindingDigest) ||
    !uuid(ticket.authorizationEpoch) ||
    !integer(ticket.priorStoreRevision, 1) ||
    !['refresh', 'revoke'].includes(String(ticket.kind)) ||
    (kind !== undefined && ticket.kind !== kind) ||
    (revision !== undefined && ticket.priorStoreRevision !== revision) ||
    ticket.sourceRevision !== setup.sourceRevision
  ) {
    invalid()
  }
}

export function validateSubscriptionPolicy(value: unknown): asserts value is SubscriptionPolicy {
  const policy = object(value)

  if (
    Object.keys(policy).sort().join() !==
      ['version', 'mode', ...callFields, 'requestTimeoutSeconds', 'connectionId', 'authorizationEpoch', 'modelSlug']
        .sort()
        .join() ||
    policy.version !== 3 ||
    policy.mode !== 'subscription_calls' ||
    !callFields.every(key => integer(policy[key], 0, 1000)) ||
    Number(policy.improveCallLimit) > Number(policy.totalCallLimit) ||
    Number(policy.generationCallLimit) > Number(policy.totalCallLimit) ||
    !integer(policy.requestTimeoutSeconds, 1, 120) ||
    !hash(policy.connectionId) ||
    !uuid(policy.authorizationEpoch) ||
    !text(policy.modelSlug)
  ) {
    invalid()
  }
}

function validateStatus(value: unknown): asserts value is SubscriptionStatus {
  const status = object(value)

  if (
    !(status.attemptId === null || text(status.attemptId, 128)) ||
    !text(status.stage, 64) ||
    !(status.expiresAt === null || (typeof status.expiresAt === 'number' && Number.isFinite(status.expiresAt))) ||
    !integer(status.connectionRevision) ||
    !['absent', 'active', 'refreshing', 'disconnected'].includes(String(status.connectionState)) ||
    typeof status.cleanupPending !== 'boolean'
  ) {
    invalid()
  }
}

function validateCatalog(value: unknown, setup: TeamSetup, revision: number): asserts value is SubscriptionCatalog {
  const catalog = object(value)

  if (
    catalog.version !== 1 ||
    catalog.state !== 'ready' ||
    !uuid(catalog.catalogId) ||
    catalog.connectionRevision !== revision ||
    catalog.sourceRevision !== setup.sourceRevision ||
    !hash(catalog.connectionId) ||
    !uuid(catalog.authorizationEpoch) ||
    typeof catalog.fetchedAt !== 'number' ||
    typeof catalog.expiresAt !== 'number' ||
    !Number.isFinite(catalog.fetchedAt) ||
    !Number.isFinite(catalog.expiresAt) ||
    catalog.expiresAt <= catalog.fetchedAt ||
    !Array.isArray(catalog.models) ||
    catalog.models.length > 512
  ) {
    invalid()
  }

  const models = catalog.models as unknown[]
  const seen = new Set<string>()

  for (const raw of models) {
    const row = object(raw)

    if (
      !text(row.slug, 128) ||
      !/^[A-Za-z0-9][A-Za-z0-9._:/-]*$/.test(row.slug) ||
      !text(row.displayName) ||
      seen.has(row.slug)
    ) {
      invalid()
    }

    seen.add(row.slug as string)
  }
}

export function validateSubscriptionPreview(
  value: unknown,
  state: SubscriptionPolicyState,
  candidate: SubscriptionPolicy
): asserts value is SubscriptionPreview {
  const preview = object(value)
  validateSubscriptionPolicy(preview.configuration)

  if (
    preview.capabilityVersion !== 1 ||
    preview.organizationId !== state.organizationId ||
    preview.ownerId !== state.ownerId ||
    preview.policyGeneration !== state.policyGeneration ||
    preview.sourceRevision !== state.sourceRevision ||
    !hash(preview.previewVersion) ||
    !equal(preview.configuration, candidate) ||
    !equal(preview.acknowledgement, subscriptionAcknowledgement) ||
    preview.hardTokenGuarantee !== false ||
    preview.hardUsdGuarantee !== false ||
    !hash(preview.unchangedExecutionDigest) ||
    !Array.isArray(preview.allOrganizationConstraints) ||
    preview.allOrganizationConstraints.length ||
    !Array.isArray(preview.retiredGuarantees) ||
    !preview.retiredGuarantees.length ||
    !integer(preview.retainedInheritanceCallCeiling, 1) ||
    !integer(preview.effectiveTimeoutSeconds, 1, candidate.requestTimeoutSeconds)
  ) {
    invalid()
  }

  const limits = object(preview.effectiveCallLimits),
    counts = object(preview.retainedCallCounts),
    remaining = object(preview.remainingCalls)

  if (
    !callFields.every(key => integer(limits[key], 0, candidate[key])) ||
    !['legacy', 'improveTeamInput', 'generateTeam', 'total'].every(key => integer(counts[key])) ||
    counts.total !== Number(counts.legacy) + Number(counts.improveTeamInput) + Number(counts.generateTeam) ||
    remaining.total !== Math.max(0, Number(limits.totalCallLimit) - Number(counts.total)) ||
    remaining.improveTeamInput !==
      Math.min(
        Number(remaining.total),
        Math.max(0, Number(limits.improveCallLimit) - Number(counts.improveTeamInput))
      ) ||
    remaining.generateTeam !==
      Math.min(Number(remaining.total), Math.max(0, Number(limits.generationCallLimit) - Number(counts.generateTeam)))
  ) {
    invalid()
  }

  for (const raw of preview.retiredGuarantees as unknown[]) {
    const guarantee = object(raw)

    if (
      !text(guarantee.guarantee) ||
      !text(guarantee.source) ||
      guarantee.scope !== 'setup_only' ||
      (guarantee.source === 'execution_configuration_inheritance' && guarantee.historicalOrigin !== 'unknown') ||
      !('value' in guarantee)
    ) {
      invalid()
    }
  }
}

/** Shares the onboarding client's captured socket, serialization and retirement guard. */
export function createSetupSubscriptionClient(
  request: <T>(method: string, params: Record<string, unknown>) => Promise<T>
) {
  const context = (setup: TeamSetup) => {
    if (setup.setupSubscriptionVersion !== 1 || !setup.organizationId || !setup.sourceRevision) {
      throw new Error('unsupported')
    }

    return { expectedOrganizationId: setup.organizationId, sourceRevision: setup.sourceRevision }
  }

  return {
    subscriptionStatus: async (setup: TeamSetup) => {
      const result = await request<unknown>('organization.setupSubscriptionStatus', context(setup))
      validateStatus(result)

      return result
    },
    beginSubscription: (setup: TeamSetup, revision: number) =>
      request<unknown>('organization.beginSetupSubscription', {
        ...context(setup),
        expectedRevision: revision,
        confirmed: true,
        consentVersion: 1
      }),
    cancelSubscription: (setup: TeamSetup, attemptId: string) =>
      request<unknown>('organization.cancelSetupSubscription', { ...context(setup), attemptId }),
    subscriptionModels: async (setup: TeamSetup, revision: number) => {
      const result = await request<unknown>('organization.setupSubscriptionModels', {
        ...context(setup),
        expectedRevision: revision
      })

      validateCatalog(result, setup, revision)

      return result
    },
    subscriptionPolicy: async (setup: TeamSetup) => {
      const result = await request<unknown>('organization.setupSubscriptionPolicy', {
        expectedOrganizationId: context(setup).expectedOrganizationId
      })

      const state = object(result)

      if (
        state.capabilityVersion !== 1 ||
        state.organizationId !== setup.organizationId ||
        !text(state.ownerId) ||
        !integer(state.policyGeneration, 1) ||
        state.sourceRevision !== setup.sourceRevision ||
        ![1, 2, 3].includes(state.policyVersion as number) ||
        !['available', 'blocked'].includes(String(state.state)) ||
        (state.reason !== null && !text(state.reason, 128))
      ) {
        invalid()
      }

      const budget = object(state.setupBudget)

      if (budget.setupUsage !== undefined) {
        validateSubscriptionUsage(budget.setupUsage)
      }

      if (state.policyVersion === 3) {
        validateSubscriptionPolicy(state.configuration)
        const limits = object(budget.limits)

        if (
          !['improveCalls', 'generationCalls', 'totalCalls'].every(key => integer(limits[key], 0, 1000)) ||
          limits.totalTokens !== null ||
          limits.maxCostUsd !== null ||
          budget.reservedTokens !== null ||
          budget.reservedCostUsd !== null ||
          budget.hardTokenGuarantee !== false ||
          budget.hardUsdGuarantee !== false ||
          !integer(budget.requestTimeoutSeconds, 1, 120)
        ) {
          invalid()
        }

        validateSubscriptionUsage(budget.setupUsage)
      }

      return result as SubscriptionPolicyState
    },
    previewSubscriptionPolicy: async (state: SubscriptionPolicyState, configuration: SubscriptionPolicy) => {
      validateSubscriptionPolicy(configuration)

      const result = await request<unknown>('organization.previewSetupSubscriptionPolicy', {
        expectedOrganizationId: state.organizationId,
        sourceRevision: state.sourceRevision,
        expectedGeneration: state.policyGeneration,
        configuration
      })

      validateSubscriptionPreview(result, state, configuration)

      return result
    },
    applySubscriptionPolicy: async (preview: SubscriptionPreview, idempotencyKey: string) => {
      const result = await request<unknown>('organization.applySetupSubscriptionPolicy', {
        expectedOrganizationId: preview.organizationId,
        sourceRevision: preview.sourceRevision,
        expectedGeneration: preview.policyGeneration,
        configuration: preview.configuration,
        previewVersion: preview.previewVersion,
        idempotencyKey,
        confirmed: true,
        acknowledgement: preview.acknowledgement
      })

      const value = object(result),
        receipt = object(value.receipt)

      if (
        value.capabilityVersion !== 1 ||
        receipt.version !== 1 ||
        receipt.action !== 'retireSetupInheritance' ||
        receipt.idempotencyKey !== idempotencyKey ||
        receipt.organizationId !== preview.organizationId ||
        receipt.ownerId !== preview.ownerId ||
        receipt.priorGeneration !== preview.policyGeneration ||
        receipt.appliedGeneration !== preview.policyGeneration + 1 ||
        receipt.sourceRevision !== preview.sourceRevision ||
        receipt.previewDigest !== preview.previewVersion ||
        !equal(receipt.candidate, preview.configuration) ||
        (value.sourceRevision !== undefined && !hash(value.sourceRevision)) ||
        typeof value.historical !== 'boolean'
      ) {
        invalid()
      }

      return result as SubscriptionApplied
    },
    prepareSubscriptionOperation: async (setup: TeamSetup, kind: 'refresh' | 'revoke', revision: number) => {
      const result = await request<SubscriptionOperation>('organization.prepareSetupSubscriptionOperation', {
        ...context(setup),
        kind,
        expectedRevision: revision,
        confirmed: true
      })

      const response = object(result)

      if (!text(response.state, 64)) {
        invalid()
      }

      if (response.ticket) {
        validateTicket(response.ticket, setup, kind, revision)
      }

      if (response.state === 'prepared' && !response.ticket) {
        invalid()
      }

      return result
    },
    executeSubscriptionOperation: (setup: TeamSetup, ticket: SubscriptionTicket) => {
      validateTicket(ticket, setup)

      return request<SubscriptionOperation>('organization.executeSetupSubscriptionOperation', {
        ...context(setup),
        ticket
      })
    },
    reconcileSubscriptionOperation: (setup: TeamSetup, ticket: SubscriptionTicket) =>
      request<SubscriptionOperation>('organization.reconcileSetupSubscriptionOperation', { ...context(setup), ticket }),
    recoverSubscriptionOperation: (setup: TeamSetup) =>
      request<SubscriptionOperation>('organization.recoverSetupSubscriptionOperation', {
        ...context(setup),
        confirmed: true
      })
  }
}
