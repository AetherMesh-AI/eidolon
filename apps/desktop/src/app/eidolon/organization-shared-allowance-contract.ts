import { policyEqual } from './organization-owner-policy-contract'
import type {
  AllowanceAmounts,
  SharedAllowanceAction,
  SharedAllowanceConfiguration,
  SharedAllowanceDescriptor,
  SharedAllowanceIntent,
  SharedAllowanceObservation,
  SharedAllowancePreview,
  SharedAllowanceReceipt,
  SharedAllowanceRequest,
  SharedAllowanceScope
} from './organization-shared-allowance-types'

const record = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value)

const text = (value: unknown, max = 300): value is string =>
  typeof value === 'string' &&
  value.length > 0 &&
  value.length <= max &&
  value.trim() === value &&
  !/\p{C}/u.test(value)

const integer = (value: unknown, min = 0): value is number => Number.isSafeInteger(value) && (value as number) >= min
const hash = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)

const uuid = (value: unknown) =>
  typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value)

const keys = (value: Record<string, unknown>, names: string[]) =>
  Object.keys(value).every(key => names.includes(key)) && names.every(key => Object.hasOwn(value, key))

const time = (value: unknown): value is string =>
  typeof value === 'string' &&
  /^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value) &&
  Number.isFinite(Date.parse(value))

const decimal = (value: unknown): value is string =>
  typeof value === 'string' &&
  /^(?:0|[1-9]\d*)(?:\.\d{1,9})?$/.test(value) &&
  Number.isFinite(Number(value)) &&
  Number(value) <= 1000000

const profile = (value: unknown): value is string => text(value, 64) && /^[a-z0-9][a-z0-9_-]*$/.test(value)
const amountKeys = ['modelCalls', 'tokens', 'configuredCostUsd']

const money = (value: string) => {
  const [whole, fraction = ''] = value.split('.')

  return BigInt(whole) * 1000000000n + BigInt(fraction.padEnd(9, '0'))
}

export function validAllowanceAmounts(value: unknown, positive = false): value is AllowanceAmounts {
  return (
    record(value) &&
    keys(value, amountKeys) &&
    integer(value.modelCalls, positive ? 1 : 0) &&
    value.modelCalls <= 1000000 &&
    integer(value.tokens, positive ? 1 : 0) &&
    value.tokens <= 1000000000000 &&
    (value.configuredCostUsd === null || decimal(value.configuredCostUsd))
  )
}

function fits(part: AllowanceAmounts, total: AllowanceAmounts) {
  return (
    part.modelCalls <= total.modelCalls &&
    part.tokens <= total.tokens &&
    (total.configuredCostUsd === null
      ? part.configuredCostUsd === null
      : part.configuredCostUsd === null || money(part.configuredCostUsd) <= money(total.configuredCostUsd))
  )
}

export function validSharedAllowanceConfiguration(value: unknown): value is SharedAllowanceConfiguration {
  if (
    !record(value) ||
    !keys(value, ['periodId', 'startsAt', 'endsAt', 'limits', 'protected', 'allocations']) ||
    !text(value.periodId, 128) ||
    !time(value.startsAt) ||
    !time(value.endsAt) ||
    Date.parse(value.endsAt) <= Date.parse(value.startsAt) ||
    Date.parse(value.endsAt) - Date.parse(value.startsAt) > 366 * 86400000 ||
    !validAllowanceAmounts(value.limits, true) ||
    !validAllowanceAmounts(value.protected) ||
    !fits(value.protected, value.limits) ||
    !Array.isArray(value.allocations) ||
    value.allocations.length < 1 ||
    value.allocations.length > 64
  ) {
    return false
  }

  const allocations = value.allocations

  if (
    !allocations.every(
      item =>
        record(item) &&
        keys(item, ['profile', 'organizationId', 'ownerId', 'limits', 'protected']) &&
        profile(item.profile) &&
        uuid(item.organizationId) &&
        uuid(item.ownerId) &&
        validAllowanceAmounts(item.limits, true) &&
        validAllowanceAmounts(item.protected) &&
        fits(item.protected, item.limits)
    )
  ) {
    return false
  }

  if (new Set(allocations.map(item => item.profile)).size !== allocations.length) {
    return false
  }

  const limits = value.limits

  return (
    allocations.reduce((sum, item) => sum + item.limits.modelCalls, 0) <= limits.modelCalls &&
    allocations.reduce((sum, item) => sum + item.limits.tokens, 0) <= limits.tokens &&
    (limits.configuredCostUsd === null ||
      (allocations.every(item => item.limits.configuredCostUsd !== null) &&
        allocations.reduce((sum, item) => sum + money(item.limits.configuredCostUsd!), 0n) <=
          money(limits.configuredCostUsd)))
  )
}

export function validSharedAllowanceObservation(value: unknown): value is SharedAllowanceObservation {
  return (
    record(value) &&
    keys(value, ['remainingPercent', 'asOf', 'expiresAt', 'resetAt']) &&
    typeof value.remainingPercent === 'number' &&
    Number.isFinite(value.remainingPercent) &&
    value.remainingPercent >= 0 &&
    value.remainingPercent <= 100 &&
    time(value.asOf) &&
    time(value.expiresAt) &&
    Date.parse(value.expiresAt) > Date.parse(value.asOf) &&
    Date.parse(value.expiresAt) - Date.parse(value.asOf) <= 604800000 &&
    (value.resetAt === null ||
      (time(value.resetAt) &&
        Date.parse(value.resetAt) >= Date.parse(value.asOf) &&
        Date.parse(value.resetAt) - Date.parse(value.asOf) <= 604800000))
  )
}

export function validSharedAllowanceCandidate(
  action: SharedAllowanceAction,
  value: Pick<SharedAllowanceRequest, 'configuration' | 'observation'>,
  data?: SharedAllowanceDescriptor
) {
  if (action === 'observation') {
    return value.configuration === undefined && validSharedAllowanceObservation(value.observation)
  }

  if (value.observation !== undefined || !validSharedAllowanceConfiguration(value.configuration)) {
    return false
  }

  if (
    data?.configuration?.allocations.some(
      saved =>
        !value.configuration!.allocations.some(
          item =>
            item.profile === saved.profile &&
            item.organizationId === saved.organizationId &&
            item.ownerId === saved.ownerId
        )
    )
  ) {
    return false
  }

  return (
    !data ||
    value.configuration.allocations.every(item =>
      data.candidates.some(
        candidate =>
          candidate.profile === item.profile &&
          candidate.organizationId === item.organizationId &&
          candidate.ownerId === item.ownerId
      )
    )
  )
}

const scopeMatches = (value: unknown, scope: SharedAllowanceScope) =>
  record(value) && value.kind === 'served-profile' && value.profile === scope.profile

function validProjection(value: unknown) {
  return (
    value === null ||
    (record(value) &&
      ['limits', 'reserved', 'remaining', 'protectedRemaining', 'ordinaryRemaining'].every(
        key =>
          record(value[key]) &&
          keys(value[key], amountKeys) &&
          integer(value[key].modelCalls) &&
          integer(value[key].tokens) &&
          (value[key].configuredCostUsd === null ||
            (typeof value[key].configuredCostUsd === 'string' &&
              /^(?:0|[1-9]\d*)(?:\.\d{1,9})?$/.test(value[key].configuredCostUsd)))
      ))
  )
}

function validPacing(value: unknown) {
  return (
    value === null ||
    (record(value) &&
      value.policy === 'shared-stage-start-v1' &&
      value.enabled === true &&
      value.scope === 'installation' &&
      ['unknown', 'normal', 'conserve', 'critical'].includes(value.mode as string) &&
      (value.remainingPercent === null ||
        (typeof value.remainingPercent === 'number' &&
          Number.isFinite(value.remainingPercent) &&
          value.remainingPercent >= 0 &&
          value.remainingPercent <= 100)) &&
      ['shared_headroom', 'manual_observation', 'stale_observation'].includes(value.reason as string) &&
      integer(value.minIntervalSeconds) &&
      [0, 60, 300].includes(value.minIntervalSeconds) &&
      (value.deferUntil === null || time(value.deferUntil)) &&
      value.advisory === false &&
      value.globalInflightLimit === null &&
      value.limitation === 'stage_start_spacing_only_no_provider_quota_inference_or_automatic_refill')
  )
}

function validRecordedObservation(value: unknown) {
  if (!record(value)) {
    return false
  }

  const { revision, status, source, scope, advisory, ...observation } = value

  return (
    validSharedAllowanceObservation(observation) &&
    integer(revision, 1) &&
    ['fresh', 'expired', 'reset_unconfirmed'].includes(status as string) &&
    source === 'owner_manual' &&
    scope === 'installation' &&
    advisory === true
  )
}

export function validateSharedAllowance(value: unknown, scope: SharedAllowanceScope): SharedAllowanceDescriptor {
  if (
    !record(value) ||
    value.capabilityVersion !== 1 ||
    !scopeMatches(value.scope, scope) ||
    !['ready', 'unconfigured', 'unsupported'].includes(value.state as string) ||
    value.organizationId !== scope.organizationId ||
    value.ownerId !== scope.ownerId ||
    !['installation-owner', 'member'].includes(value.authority as string) ||
    !integer(value.generation) ||
    !hash(value.sourceRevision) ||
    !record(value.capabilities) ||
    !keys(value.capabilities, ['configuration', 'observation']) ||
    !Object.values(value.capabilities).every(item => typeof item === 'boolean') ||
    typeof value.managed !== 'boolean' ||
    !Array.isArray(value.candidates) ||
    !value.candidates.every(
      item =>
        record(item) &&
        profile(item.profile) &&
        uuid(item.organizationId) &&
        uuid(item.ownerId) &&
        text(item.displayName) &&
        text(item.ownerDisplayName)
    ) ||
    !validProjection(value.pool) ||
    !validProjection(value.member) ||
    !validPacing(value.pacing) ||
    !(value.observation === null || validRecordedObservation(value.observation))
  ) {
    throw new Error('Invalid allowance authority')
  }

  if (
    (value.authority === 'member' &&
      (value.configuration !== null ||
        value.pool !== null ||
        value.candidates.length ||
        Object.values(value.capabilities).some(Boolean))) ||
    (value.managed && Object.values(value.capabilities).some(Boolean)) ||
    (value.state === 'unsupported' && Object.values(value.capabilities).some(Boolean)) ||
    !(value.configuration === null || validSharedAllowanceConfiguration(value.configuration)) ||
    new Set(value.candidates.map(item => item.profile)).size !== value.candidates.length
  ) {
    throw new Error('Invalid allowance capability')
  }

  return value as unknown as SharedAllowanceDescriptor
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) {
    const rows = value.map(canonical)

    return rows.every(item => record(item) && typeof item.profile === 'string')
      ? rows.sort((a, b) => (a as { profile: string }).profile.localeCompare((b as { profile: string }).profile, 'en'))
      : rows
  }

  if (record(value)) {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        ['startsAt', 'endsAt', 'asOf', 'expiresAt', 'resetAt'].includes(key) && time(item)
          ? new Date(item).toISOString()
          : key === 'configuredCostUsd' && decimal(item)
            ? money(item).toString()
            : canonical(item)
      ])
    )
  }

  return value
}

const same = (a: unknown, b: unknown) => policyEqual(canonical(a), canonical(b))

const priorObservation = (value: unknown, action: SharedAllowanceAction) => {
  if (action !== 'observation' || !record(value)) {
    return value
  }

  const { status: _status, ...rest } = value

  return rest
}

const warningCodes = [
  'owner_declared_allowance',
  'configured_prices_not_invoice',
  'no_refunds_or_auto_reset',
  'existing_local_limits_still_apply',
  'enrollment_requires_idle'
]

export function validateSharedAllowancePreview(
  value: unknown,
  scope: SharedAllowanceScope,
  action: SharedAllowanceAction,
  request: SharedAllowanceRequest,
  data: SharedAllowanceDescriptor
): SharedAllowancePreview {
  if (
    !record(value) ||
    value.capabilityVersion !== 1 ||
    !scopeMatches(value.scope, scope) ||
    value.organizationId !== request.expectedOrganizationId ||
    value.ownerId !== request.expectedOwnerId ||
    value.generation !== request.expectedGeneration ||
    value.sourceRevision !== request.sourceRevision ||
    !hash(value.previewVersion) ||
    !validSharedAllowanceCandidate(action, value, data) ||
    !same(value[action], request[action]) ||
    !Array.isArray(value.changes) ||
    value.changes.length !== 1 ||
    !record(value.changes[0]) ||
    value.changes[0].field !== action ||
    !same(priorObservation(value.changes[0].before, action), priorObservation(data[action], action)) ||
    !same(value.changes[0].after, value[action]) ||
    !Array.isArray(value.warnings) ||
    value.warnings.length !== warningCodes.length ||
    !value.warnings.every(
      item =>
        record(item) &&
        warningCodes.includes(item.code as string) &&
        record(item.values) &&
        !Object.keys(item.values).length
    ) ||
    !warningCodes.every(code => (value.warnings as { code: string }[]).some(item => item.code === code)) ||
    !record(value.impact) ||
    value.impact.scope !== 'installation-allowance' ||
    value.impact.reservations !== 'unchanged' ||
    value.impact.automaticReset !== false
  ) {
    throw new Error('Invalid allowance preview')
  }

  return value as unknown as SharedAllowancePreview
}

export function validSharedAllowanceIntent(value: unknown): value is SharedAllowanceIntent {
  return (
    record(value) &&
    ['configuration', 'observation'].includes(value.action as string) &&
    keys(value, [
      'action',
      'expectedOrganizationId',
      'expectedOwnerId',
      'expectedGeneration',
      'sourceRevision',
      value.action as string,
      'previewVersion',
      'idempotencyKey',
      'confirmed'
    ]) &&
    uuid(value.expectedOrganizationId) &&
    uuid(value.expectedOwnerId) &&
    integer(value.expectedGeneration) &&
    hash(value.sourceRevision) &&
    hash(value.previewVersion) &&
    text(value.idempotencyKey, 128) &&
    value.confirmed === true &&
    validSharedAllowanceCandidate(value.action as SharedAllowanceAction, value)
  )
}

export function validateSharedAllowanceReceipt(
  value: unknown,
  scope: SharedAllowanceScope,
  intent: SharedAllowanceIntent
): SharedAllowanceReceipt {
  if (!record(value) || value.capabilityVersion !== 1 || !scopeMatches(value.scope, scope) || !record(value.receipt)) {
    throw new Error('Invalid allowance receipt')
  }

  const r = value.receipt

  if (
    r.action !== intent.action ||
    r.idempotencyKey !== intent.idempotencyKey ||
    r.organizationId !== intent.expectedOrganizationId ||
    r.ownerId !== intent.expectedOwnerId ||
    r.generation !== intent.expectedGeneration + 1 ||
    r.sourceRevision !== intent.sourceRevision ||
    r.previewVersion !== intent.previewVersion ||
    !policyEqual(r.configuration, intent.configuration) ||
    !policyEqual(r.observation, intent.observation)
  ) {
    throw new Error('Unmatched allowance receipt')
  }

  return value as unknown as SharedAllowanceReceipt
}
