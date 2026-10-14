import {
  budgetNumbers,
  type OwnerBudgetObservation,
  ownerModelRequestTypes,
  type OwnerPolicyAction,
  type OwnerPolicyDescriptor,
  type OwnerPolicyIntent,
  type OwnerPolicyPreview,
  type OwnerPolicyReceipt,
  type OwnerPolicyRequest,
  type OwnerPolicyScope
} from './organization-owner-policy-types'

const hash = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)

const uuid = (value: unknown): value is string =>
  typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value)

const record = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value)

const text = (value: unknown, max = 300): value is string =>
  typeof value === 'string' &&
  value.length > 0 &&
  value.length <= max &&
  value.trim() === value &&
  !/\p{C}/u.test(value)

const integer = (value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): value is number =>
  Number.isSafeInteger(value) && (value as number) >= min && (value as number) <= max

const decimal = (value: unknown) =>
  typeof value === 'string' &&
  /^(?:-?0|[1-9]\d{0,6})(?:\.\d{1,9})?$/.test(value) &&
  Number(value) >= 0 &&
  Number(value) <= 1000000

const strings = (value: unknown, min = 0, max = 64): value is string[] =>
  Array.isArray(value) &&
  value.length >= min &&
  value.length <= max &&
  value.every(item => text(item)) &&
  new Set(value).size === value.length

const keys = (value: Record<string, unknown>, allowed: string[]) =>
  Object.keys(value).every(key => allowed.includes(key))

const time = (value: unknown) =>
  typeof value === 'string' &&
  /^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value) &&
  Number.isFinite(Date.parse(value))

export const policyEqual = (a: unknown, b: unknown): boolean => {
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((item, i) => policyEqual(item, b[i]))
  }

  if (record(a) && record(b)) {
    return (
      Object.keys(a).length === Object.keys(b).length &&
      Object.keys(a).every(key => Object.hasOwn(b, key) && policyEqual(a[key], b[key]))
    )
  }

  return a === b
}

export function validPolicyConfiguration(action: OwnerPolicyAction, value: unknown): boolean {
  if (!record(value)) {
    return false
  }

  if (action === 'modelRouting') {
    if (
      !keys(value, ['model_routes', 'model_assignments']) ||
      !Array.isArray(value.model_routes) ||
      !Array.isArray(value.model_assignments) ||
      value.model_routes.length > 64 ||
      value.model_assignments.length > 64
    ) {
      return false
    }

    const routes = value.model_routes

    return (
      routes.every(
        route =>
          record(route) &&
          keys(route, [
            'id',
            'provider',
            'model',
            'reasoning',
            'request_types',
            'evidence_level',
            'max_context_tokens',
            'max_output_tokens'
          ]) &&
          text(route.id, 64) &&
          /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(route.id) &&
          text(route.provider) &&
          route.provider.toLowerCase() !== 'auto' &&
          text(route.model) &&
          ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'].includes(route.reasoning as string) &&
          strings(route.request_types, 1) &&
          route.request_types.every(kind => ownerModelRequestTypes.includes(kind)) &&
          integer(route.evidence_level, 1, 3) &&
          integer(route.max_context_tokens, 4096, 2000000) &&
          integer(route.max_output_tokens, 256, 16000) &&
          route.max_context_tokens > route.max_output_tokens + 2048
      ) &&
      new Set(routes.map(route => route.id)).size === routes.length &&
      value.model_assignments.every(
        assignment =>
          record(assignment) &&
          keys(assignment, ['agent_id', 'mode', 'route_ids', 'minimum_evidence', 'max_fallbacks']) &&
          text(assignment.agent_id, 128) &&
          !['owner', 'control:apply', 'control:project'].includes(assignment.agent_id) &&
          ['fixed', 'auto'].includes(assignment.mode as string) &&
          !(assignment.agent_id === 'executive' && assignment.mode !== 'fixed') &&
          strings(assignment.route_ids, 1, 16) &&
          assignment.route_ids.every(id => routes.some(route => route.id === id)) &&
          integer(assignment.minimum_evidence, 1, 3) &&
          integer(assignment.max_fallbacks, 0, Math.min(2, assignment.route_ids.length - 1))
      ) &&
      new Set(value.model_assignments.map(item => item.agent_id)).size === value.model_assignments.length
    )
  }

  if (action !== 'budgetPolicy' || !Object.keys(value).length) {
    return false
  }

  return Object.entries(value).every(([key, field]) => {
    if (Object.hasOwn(budgetNumbers, key)) {
      const [min, max] = budgetNumbers[key as keyof typeof budgetNumbers]

      return integer(field, min, max)
    }

    if (key === 'adaptive_pacing_enabled') {
      return typeof field === 'boolean'
    }

    if (key === 'max_cost_usd' || key === 'completion_reserve_cost_usd') {
      return field === null || (decimal(field) && (key !== 'max_cost_usd' || Number(field) > 0))
    }

    return (
      key === 'model_costs' &&
      Array.isArray(field) &&
      field.length <= 64 &&
      field.every(
        price =>
          record(price) &&
          keys(price, ['provider', 'model', 'input_usd_per_million', 'output_usd_per_million']) &&
          text(price.provider) &&
          text(price.model) &&
          decimal(price.input_usd_per_million) &&
          decimal(price.output_usd_per_million)
      ) &&
      new Set(field.map(price => JSON.stringify([price.provider, price.model]))).size === field.length
    )
  })
}

export function validPolicyObservation(value: unknown): value is OwnerBudgetObservation {
  return (
    record(value) &&
    keys(value, ['objectiveId', 'expectedRevision', 'remainingPercent', 'asOf', 'expiresAt', 'resetAt']) &&
    text(value.objectiveId, 128) &&
    integer(value.expectedRevision, 0, 999) &&
    typeof value.remainingPercent === 'number' &&
    Number.isFinite(value.remainingPercent) &&
    value.remainingPercent >= 0 &&
    value.remainingPercent <= 100 &&
    time(value.asOf) &&
    time(value.expiresAt) &&
    (value.resetAt === null || time(value.resetAt)) &&
    Date.parse(value.expiresAt as string) > Date.parse(value.asOf as string)
  )
}

function scopeMatches(value: unknown, scope: OwnerPolicyScope) {
  return record(value) && value.kind === 'served-profile' && value.profile === scope.profile
}

export function validateOwnerPolicy(value: unknown, scope: OwnerPolicyScope): OwnerPolicyDescriptor {
  if (
    !record(value) ||
    value.capabilityVersion !== 1 ||
    !scopeMatches(value.scope, scope) ||
    !['ready', 'unconfigured', 'unsupported'].includes(value.state as string)
  ) {
    throw new Error('Invalid owner policy')
  }

  if (value.state !== 'ready') {
    if (record(value.capabilities) && Object.values(value.capabilities).some(Boolean)) {
      throw new Error('Invalid unavailable capability')
    }

    return value as unknown as OwnerPolicyDescriptor
  }

  if (
    value.organizationId !== scope.organizationId ||
    value.ownerId !== scope.ownerId ||
    !integer(value.policyGeneration, 1) ||
    !hash(value.sourceRevision) ||
    !strings(value.managedFields, 0, 100) ||
    !record(value.capabilities) ||
    !['modelRouting', 'budgetPolicy', 'budgetObservation'].every(
      key => typeof (value.capabilities as Record<string, unknown>)[key] === 'boolean'
    ) ||
    !record(value.configuration)
  ) {
    throw new Error('Invalid policy authority')
  }

  const { model_routes, model_assignments, ...budget } = value.configuration

  if (
    ![
      ...Object.keys(budgetNumbers),
      'max_cost_usd',
      'completion_reserve_cost_usd',
      'adaptive_pacing_enabled',
      'model_costs'
    ].every(key => Object.hasOwn(budget, key)) ||
    !validPolicyConfiguration('modelRouting', { model_routes, model_assignments }) ||
    !validPolicyConfiguration('budgetPolicy', budget) ||
    !Array.isArray(value.agents) ||
    !value.agents.every(agent => record(agent) && text(agent.id, 64) && text(agent.name) && text(agent.role)) ||
    !Array.isArray(value.observations) ||
    !value.observations.every(
      item =>
        record(item) &&
        text(item.objectiveId, 128) &&
        integer(item.revision, 0, 1000) &&
        validRecordedObservation(item.observation, item.revision)
    )
  ) {
    throw new Error('Invalid policy configuration')
  }

  return value as unknown as OwnerPolicyDescriptor
}

export function validPolicyIntent(value: unknown): value is OwnerPolicyIntent {
  if (
    !record(value) ||
    !keys(value, [
      'action',
      'ownerId',
      'expectedOrganizationId',
      'expectedGeneration',
      'sourceRevision',
      'configuration',
      'observation',
      'previewVersion',
      'idempotencyKey',
      'confirmed'
    ]) ||
    !['modelRouting', 'budgetPolicy', 'budgetObservation'].includes(value.action as string) ||
    !uuid(value.ownerId) ||
    !uuid(value.expectedOrganizationId) ||
    !integer(value.expectedGeneration, 1) ||
    !hash(value.sourceRevision) ||
    !hash(value.previewVersion) ||
    !text(value.idempotencyKey, 128) ||
    value.confirmed !== true
  ) {
    return false
  }

  return value.action === 'budgetObservation'
    ? value.configuration === undefined && validPolicyObservation(value.observation)
    : value.observation === undefined &&
        validPolicyConfiguration(value.action as OwnerPolicyAction, value.configuration)
}

export function validatePolicyPreview(
  value: unknown,
  scope: OwnerPolicyScope,
  request: OwnerPolicyRequest,
  action: OwnerPolicyAction,
  descriptor: OwnerPolicyDescriptor
): OwnerPolicyPreview {
  if (
    !record(value) ||
    value.capabilityVersion !== 1 ||
    !scopeMatches(value.scope, scope) ||
    value.organizationId !== request.expectedOrganizationId ||
    value.ownerId !== scope.ownerId ||
    value.policyGeneration !== request.expectedGeneration ||
    value.sourceRevision !== request.sourceRevision ||
    !hash(value.previewVersion) ||
    !Array.isArray(value.warnings) ||
    !value.warnings.every(
      item =>
        record(item) &&
        [
          'no_topup',
          'captured_reserves_unchanged',
          'configured_prices_not_invoice',
          'owner_declared_routes',
          'direct_chat_only',
          'owner_chat_unchanged',
          'objective_advisory_only',
          'no_reset_or_refill',
          'existing_caps_may_tighten',
          'future_defaults_only',
          'running_attempts_fenced',
          'unchanged_values_pinned',
          'captured_caps_still_limit_increase'
        ].includes(item.code as string) &&
        record(item.values) &&
        ([
          'existing_caps_may_tighten',
          'future_defaults_only',
          'unchanged_values_pinned',
          'captured_caps_still_limit_increase'
        ].includes(item.code as string)
          ? keys(item.values, ['fields']) && strings(item.values.fields, 1)
          : item.code === 'running_attempts_fenced'
            ? keys(item.values, ['activeAttempts']) && integer(item.values.activeAttempts)
            : !Object.keys(item.values).length)
    )
  ) {
    throw new Error('Invalid preview authority')
  }

  if (
    action === 'budgetObservation'
      ? !validPolicyObservation(value.observation) || !sameObservation(value.observation, request.observation!)
      : !validPolicyConfiguration(action, value.configuration) ||
        !record(value.configuration) ||
        !record(request.configuration) ||
        !policyEqual(value.configuration, request.configuration)
  ) {
    throw new Error('Invalid preview candidate')
  }

  const impact = value.impact

  if (
    !Array.isArray(value.changes) ||
    !value.changes.every(
      change =>
        record(change) && text(change.field, 128) && Object.hasOwn(change, 'before') && Object.hasOwn(change, 'after')
    ) ||
    !record(impact) ||
    !time(impact.evaluatedAt) ||
    !integer(impact.activeObjectiveCount) ||
    impact.scope !==
      {
        modelRouting: 'persistent-agent-routing',
        budgetPolicy: 'objective-defaults',
        budgetObservation: 'objective-advisory'
      }[action] ||
    impact.existingObjectives !==
      { modelRouting: 'new-attempts', budgetPolicy: 'may-tighten-never-refill', budgetObservation: 'advisory-only' }[
        action
      ] ||
    impact.futureObjectives !== (action === 'budgetPolicy' ? 'new-defaults' : 'unchanged') ||
    impact.reservations !== 'unchanged' ||
    impact.completionReserves !== 'captured-unchanged' ||
    impact.usageProjection !== 'not-estimated' ||
    impact.runningAttempts !== (action === 'budgetObservation' ? 'unchanged' : 'fenced-to-owner-review') ||
    !integer(impact.runningAttemptCount)
  ) {
    throw new Error('Invalid preview impact')
  }

  const preview = value as unknown as OwnerPolicyPreview

  if (
    action !== 'budgetObservation' &&
    !preview.warnings.some(
      warning =>
        warning.code === 'running_attempts_fenced' &&
        warning.values.activeAttempts === preview.impact.runningAttemptCount
    )
  ) {
    throw new Error('Missing running-attempt consequence')
  }

  if (action === 'budgetObservation') {
    const prior =
      descriptor.observations.find(item => item.objectiveId === request.observation!.objectiveId)?.observation ?? null

    const change = preview.changes[0]

    // Observation freshness is time-derived. Immutable prior data must still match.
    const withoutStatus = (item: unknown) => {
      if (!record(item)) {
        return item
      }

      const { status: _status, ...rest } = item

      return rest
    }

    if (
      preview.changes.length !== 1 ||
      change.field !== 'observation' ||
      !policyEqual(change.after, preview.observation) ||
      !policyEqual(withoutStatus(change.before), withoutStatus(prior))
    ) {
      throw new Error('Invalid observation changes')
    }
  } else {
    const expectedChanges = Object.entries(preview.configuration!).filter(
      ([key, after]) => !policyEqual(descriptor.configuration[key as keyof typeof descriptor.configuration], after)
    )

    if (
      preview.changes.length !== expectedChanges.length ||
      new Set(preview.changes.map(change => change.field)).size !== preview.changes.length ||
      !expectedChanges.every(([key, after]) =>
        preview.changes.some(
          change =>
            change.field === key &&
            policyEqual(change.before, descriptor.configuration[key as keyof typeof descriptor.configuration]) &&
            policyEqual(change.after, after)
        )
      )
    ) {
      throw new Error('Invalid configuration changes')
    }
  }

  return preview
}

export function validatePolicyReceipt(
  value: unknown,
  scope: OwnerPolicyScope,
  intent: OwnerPolicyIntent
): OwnerPolicyReceipt {
  if (!record(value) || value.capabilityVersion !== 1 || !scopeMatches(value.scope, scope) || !record(value.receipt)) {
    throw new Error('Invalid receipt')
  }

  const r = value.receipt

  if (
    r.idempotencyKey !== intent.idempotencyKey ||
    r.organizationId !== intent.expectedOrganizationId ||
    r.ownerId !== intent.ownerId ||
    r.action !== intent.action ||
    r.previewVersion !== intent.previewVersion ||
    r.sourceRevision !== intent.sourceRevision ||
    r.policyGeneration !== intent.expectedGeneration + (intent.action === 'budgetObservation' ? 0 : 1) ||
    !policyEqual(r.configuration, intent.configuration) ||
    !policyEqual(r.observation, intent.observation) ||
    (intent.action === 'budgetObservation' && r.observationRevision !== intent.observation!.expectedRevision + 1)
  ) {
    throw new Error('Unmatched receipt')
  }

  return value as unknown as OwnerPolicyReceipt
}

function sameObservation(a: OwnerBudgetObservation, b: OwnerBudgetObservation) {
  return (
    a.objectiveId === b.objectiveId &&
    a.expectedRevision === b.expectedRevision &&
    a.remainingPercent === b.remainingPercent &&
    Date.parse(a.asOf) === Date.parse(b.asOf) &&
    Date.parse(a.expiresAt) === Date.parse(b.expiresAt) &&
    ((a.resetAt === null && b.resetAt === null) ||
      (a.resetAt !== null && b.resetAt !== null && Date.parse(a.resetAt) === Date.parse(b.resetAt)))
  )
}

function validRecordedObservation(value: unknown, revision: number): boolean {
  if (value === null) {
    return revision === 0
  }

  return (
    record(value) &&
    value.revision === revision &&
    revision > 0 &&
    typeof value.remainingPercent === 'number' &&
    Number.isFinite(value.remainingPercent) &&
    value.remainingPercent >= 0 &&
    value.remainingPercent <= 100 &&
    time(value.asOf) &&
    time(value.expiresAt) &&
    (value.resetAt === null || time(value.resetAt)) &&
    ['fresh', 'expired', 'reset_unconfirmed'].includes(value.status as string) &&
    value.source === 'owner_manual' &&
    value.scope === 'objective' &&
    value.advisory === true
  )
}
