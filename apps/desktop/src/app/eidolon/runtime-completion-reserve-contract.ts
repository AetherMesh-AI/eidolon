import type {
  ObjectiveReplacementDraft,
  OrganizationBudgetAmounts,
  OrganizationCompletionReserve,
  OrganizationCompletionReservePolicy
} from './types'

const requestTypes = ['request.review', 'request.test_review', 'request.integrate', 'request.accept']
const dimensions = ['modelCalls', 'tokens', 'configuredCostUsd'] as const
const count = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0

const decimal = (value: unknown): value is string =>
  typeof value === 'string' && /^(?:0|[1-9]\d{0,15})(?:\.\d{1,9})?$/.test(value)

const record = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value)

/** Exact nanodollars keep decimal admission values out of floating-point arithmetic. */
function amount(value: number | string): bigint {
  if (typeof value === 'number') {
    return BigInt(value)
  }

  const [whole, fraction = ''] = value.split('.')

  return BigInt(whole) * 1_000_000_000n + BigInt(fraction.padEnd(9, '0'))
}

function amounts(value: unknown, captured = false): value is OrganizationBudgetAmounts {
  return (
    record(value) &&
    ['modelCalls', 'tokens'].every(key => count(value[key]) || (!captured && value[key] === null)) &&
    (value.configuredCostUsd === null || decimal(value.configuredCostUsd))
  )
}

export function validCompletionReservePolicy(value: unknown): value is OrganizationCompletionReservePolicy {
  return record(value) && value.version === 1 && value.policy === 'completion-v1' && amounts(value.captured, true)
}

export function validReplacementCompletionReserve(allowance: ObjectiveReplacementDraft['allowance']): boolean {
  const reserve = allowance?.completionReserve

  if (reserve === undefined) {
    return true
  }

  if (
    !validCompletionReservePolicy(reserve) ||
    !count(allowance.modelCalls) ||
    !count(allowance.tokens) ||
    reserve.captured.modelCalls > allowance.modelCalls ||
    reserve.captured.tokens > allowance.tokens
  ) {
    return false
  }

  const cost = reserve.captured.configuredCostUsd

  return cost === null || (decimal(allowance.costUsd) && amount(cost) <= amount(allowance.costUsd))
}

export function validCompletionReserve(value: unknown): value is OrganizationCompletionReserve {
  if (!validCompletionReservePolicy(value)) {
    return false
  }

  const view = value as OrganizationCompletionReserve

  if (
    !Array.isArray(view.requestTypes) ||
    view.requestTypes.length !== requestTypes.length ||
    new Set(view.requestTypes).size !== requestTypes.length ||
    !view.requestTypes.every(type => requestTypes.includes(type)) ||
    !['completionReserved', 'protectedRemaining', 'ordinaryRemaining', 'totalRemaining'].every(key =>
      amounts(view[key as keyof OrganizationCompletionReserve])
    ) ||
    typeof view.admissionUsageKnown !== 'boolean' ||
    ![null, 'legacy-usage-unknown', 'configured-cost-unknown', 'classification-unknown'].includes(
      view.unavailableReason
    ) ||
    view.admissionUsageKnown !== (view.unavailableReason === null)
  ) {
    return false
  }

  return dimensions.every(key => {
    const captured = view.captured[key]
    const reserved = view.completionReserved[key]
    const protectedRemaining = view.protectedRemaining[key]
    const ordinary = view.ordinaryRemaining[key]
    const total = view.totalRemaining[key]

    const accountingUnknown = view.unavailableReason === 'legacy-usage-unknown' || view.unavailableReason === 'classification-unknown'

    const unknown =
      accountingUnknown ||
      (key === 'configuredCostUsd' && view.unavailableReason === 'configured-cost-unknown')

    if (accountingUnknown && reserved !== null) {return false}

    if (unknown && [protectedRemaining, ordinary, total].some(value => value !== null)) {
      return false
    }

    if (
      !unknown &&
      key !== 'configuredCostUsd' &&
      [reserved, protectedRemaining, ordinary, total].some(value => value === null)
    ) {
      return false
    }

    if (captured === null && protectedRemaining !== null && amount(protectedRemaining) !== 0n) {
      return false
    }

    if (
      captured !== null &&
      reserved !== null &&
      protectedRemaining !== null &&
      amount(protectedRemaining) !== (amount(captured) > amount(reserved) ? amount(captured) - amount(reserved) : 0n)
    ) {
      return false
    }

    if (
      total !== null &&
      protectedRemaining !== null &&
      ordinary !== null &&
      amount(ordinary) !==
        (amount(total) > amount(protectedRemaining) ? amount(total) - amount(protectedRemaining) : 0n)
    ) {
      return false
    }

    return true
  })
}

export function completionReserveShortfall(view: OrganizationCompletionReserve): boolean {
  return dimensions.some(key => {
    const protectedRemaining = view.protectedRemaining[key]
    const total = view.totalRemaining[key]

    return protectedRemaining !== null && total !== null && amount(protectedRemaining) > amount(total)
  })
}
