import type {
  OrganizationAdaptivePacing,
  OrganizationBudgetAmounts,
  OrganizationCompletionReserve,
  OrganizationPacingObservation
} from './types'

const record = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value)

const count = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0

const percent = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100

function timestamp(value: unknown): value is string {
  if (
    typeof value !== 'string' ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) ||
    !Number.isFinite(Date.parse(value)) ||
    Number(value.slice(11, 13)) > 23
  ) {
    return false
  }

  // Date.parse normalizes impossible dates such as February 30 instead of rejecting them.
  return new Date(`${value.slice(0, 10)}T00:00:00Z`).toISOString().slice(0, 10) === value.slice(0, 10)
}

/** The runtime keeps microseconds; Date.parse alone loses the last three digits. */
function timestampUnits(value: string): bigint {
  const fraction = /\.(\d{1,6})/.exec(value)?.[1] ?? ''

  return BigInt(Date.parse(value)) * 1000n + BigInt(fraction.padEnd(6, '0').slice(3))
}

const amounts = (value: unknown): boolean =>
  record(value) &&
  ['modelCalls', 'tokens'].every(key => value[key] === null || count(value[key])) &&
  (value.configuredCostUsd === null ||
    (typeof value.configuredCostUsd === 'string' &&
      /^(?:0|[1-9]\d{0,15})(?:\.\d{1,9})?$/.test(value.configuredCostUsd)))

function validObservation(value: unknown, evaluatedAt: string): value is OrganizationPacingObservation {
  if (
    !record(value) ||
    !count(value.revision) ||
    value.revision < 1 ||
    !percent(value.remainingPercent) ||
    !timestamp(value.asOf) ||
    !timestamp(value.expiresAt) ||
    (value.resetAt !== null && !timestamp(value.resetAt)) ||
    value.source !== 'owner_manual' ||
    value.scope !== 'objective' ||
    value.advisory !== true
  ) {
    return false
  }

  const at = timestampUnits(evaluatedAt)
  const observed = timestampUnits(value.asOf)
  const expires = timestampUnits(value.expiresAt)
  const reset = value.resetAt === null ? null : timestampUnits(value.resetAt as string)
  const status = reset !== null && at >= reset ? 'reset_unconfirmed' : at >= expires ? 'expired' : 'fresh'

  return observed <= at && expires > observed && (reset === null || reset >= observed) && value.status === status
}

function costUnits(value: string | null): string | null {
  if (value === null) {
    return null
  }

  const [whole, fraction = ''] = value.split('.')

  return `${whole}.${fraction.padEnd(9, '0')}`
}

const sameAmounts = (left: OrganizationBudgetAmounts, right: OrganizationBudgetAmounts): boolean =>
  left.modelCalls === right.modelCalls &&
  left.tokens === right.tokens &&
  costUnits(left.configuredCostUsd) === costUnits(right.configuredCostUsd)

/** Optional on old backends, but malformed new evidence must never become plausible headroom. */
export function validAdaptivePacing(
  value: unknown,
  reserve?: OrganizationCompletionReserve
): value is OrganizationAdaptivePacing {
  if (
    !record(value) ||
    value.version !== 1 ||
    value.policy !== 'local-stage-start-v1' ||
    typeof value.enabled !== 'boolean' ||
    value.scope !== 'objective' ||
    !['off', 'normal', 'conserve', 'critical', 'unknown'].includes(value.mode as string) ||
    !['disabled', 'local_headroom', 'manual_observation', 'stale_observation', 'budget_unknown'].includes(
      value.reason as string
    ) ||
    !timestamp(value.evaluatedAt) ||
    (value.deferUntil !== null && !timestamp(value.deferUntil)) ||
    !count(value.maxInflight) ||
    value.maxInflight < 1 ||
    value.maxInflight > 4 ||
    !count(value.minIntervalSeconds) ||
    value.minIntervalSeconds > 3600 ||
    (value.remainingPercent !== null && !percent(value.remainingPercent)) ||
    !amounts(value.remaining) ||
    !amounts(value.protectedRemaining) ||
    (value.observation !== null && !validObservation(value.observation, value.evaluatedAt)) ||
    value.limitation !== 'stage_start_only_no_quota_inference'
  ) {
    return false
  }

  const view = value as unknown as OrganizationAdaptivePacing

  if (view.enabled !== (view.mode !== 'off') || (view.mode === 'off') !== (view.reason === 'disabled')) {
    return false
  }

  if ((view.mode === 'unknown') !== (view.reason === 'budget_unknown')) {
    return false
  }

  if (view.mode === 'unknown' && view.remainingPercent !== null) {
    return false
  }

  if (['off', 'normal', 'unknown'].includes(view.mode) && (view.minIntervalSeconds !== 0 || view.deferUntil !== null)) {
    return false
  }

  if (['conserve', 'critical'].includes(view.mode) && (view.minIntervalSeconds < 1 || view.remainingPercent === null)) {
    return false
  }

  if (view.mode === 'normal' && view.remainingPercent === null) {
    return false
  }

  if (view.deferUntil !== null && timestampUnits(view.deferUntil) <= timestampUnits(view.evaluatedAt)) {
    return false
  }

  if (view.reason === 'manual_observation' && view.observation?.status !== 'fresh') {
    return false
  }

  if (view.reason === 'stale_observation' && (!view.observation || view.observation.status === 'fresh')) {
    return false
  }

  if (
    ['manual_observation', 'stale_observation'].includes(view.reason) &&
    view.remainingPercent !== view.observation?.remainingPercent
  ) {
    return false
  }

  if (
    reserve !== undefined &&
    (!sameAmounts(view.remaining, reserve.ordinaryRemaining) ||
      !sameAmounts(view.protectedRemaining, reserve.protectedRemaining))
  ) {
    return false
  }

  return true
}
