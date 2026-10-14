import type {
  OrganizationModelAssignment,
  OrganizationModelDispatch,
  OrganizationModelRoute,
  OrganizationModelRouting
} from './runtime-model-routing-types'
import type { OrganizationExecutionAudit, OrganizationSnapshot } from './types'

const record = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value)

const text = (value: unknown, max = 300): value is string =>
  typeof value === 'string' && value.length > 0 && value.length <= max && value === value.trim() &&
  !Array.from(value).some(character => character.charCodeAt(0) < 32)

const integer = (value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): value is number =>
  Number.isSafeInteger(value) && (value as number) >= min && (value as number) <= max

const strings = (value: unknown, min = 0, max = 64): value is string[] =>
  Array.isArray(value) && value.length >= min && value.length <= max && value.every(item => text(item)) &&
  new Set(value).size === value.length

const reasoning = (value: unknown) =>
  typeof value === 'string' && ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'].includes(value)

const decimal = (value: unknown, max = 1_000_000): value is string =>
  typeof value === 'string' && /^(?:-?0|[1-9]\d{0,6})(?:\.\d{1,9})?$/.test(value) && Number(value) >= 0 && Number(value) <= max

function nanodollars(value: string): bigint {
  const [whole, fraction = ''] = value.split('.')

  return BigInt(whole) * 1_000_000_000n + BigInt(fraction.padEnd(9, '0'))
}

function validRoute(value: unknown): value is OrganizationModelRoute {
  return record(value) && text(value.id, 64) && /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(value.id) &&
    text(value.provider) && value.provider.toLowerCase() !== 'auto' && text(value.model) && reasoning(value.reasoning) &&
    strings(value.requestTypes, 1) && integer(value.evidenceLevel, 1, 3) &&
    integer(value.maxContextTokens, 4096, 2_000_000) && integer(value.maxOutputTokens, 256, 16000) &&
    value.maxContextTokens > value.maxOutputTokens + 2048 && typeof value.priced === 'boolean'
}

export function validModelRouting(value: unknown): value is OrganizationModelRouting {
  return record(value) && typeof value.enabled === 'boolean' && value.configurationMethod === 'owner-cli' &&
    value.ownerChat === 'separate-legacy-route' && value.transport === 'direct-chat-completions-only' &&
    value.fallback === 'invalid-complete-json-tool-free-only' && value.evidenceTiers === 'owner-declared' &&
    integer(value.policyGeneration) && Array.isArray(value.routes) && value.routes.length <= 64 &&
    value.routes.every(validRoute) && new Set(value.routes.map(route => route.id)).size === value.routes.length
}

export function validModelAssignment(value: unknown): value is OrganizationModelAssignment {
  if (!record(value) || !text(value.label, 500) || !strings(value.routeIds, 0, 16)) {return false}

  if (value.mode === 'legacy') {
    return value.routeIds.length === 0 && value.minimumEvidence === null && value.maxFallbacks === 0
  }

  return ['fixed', 'auto'].includes(value.mode as string) && value.routeIds.length > 0 &&
    integer(value.minimumEvidence, 1, 3) && integer(value.maxFallbacks, 0, Math.min(2, value.routeIds.length - 1))
}

export function validModelDispatch(value: unknown, requestId: string): value is OrganizationModelDispatch {
  if (!record(value)) {return false}
  const price = value.pricing

  const prices = record(price) && ((price.inputUsdPerMillion === null && price.outputUsdPerMillion === null) ||
    (decimal(price.inputUsdPerMillion) && decimal(price.outputUsdPerMillion)))

  return value.requestId === requestId && text(value.id, 128) && text(value.routeId, 64) &&
    ['fixed', 'auto'].includes(value.mode as string) && text(value.selectedProvider) && text(value.adapterProvider) &&
    text(value.model) && reasoning(value.reasoning) && integer(value.policyGeneration) &&
    integer(value.maxContextTokens, 4096, 2_000_000) && integer(value.maxOutputTokens, 256, 16000) &&
    value.maxContextTokens > value.maxOutputTokens && text(value.attemptToken, 128) && text(value.agentId, 128) &&
    text(value.requestType) && integer(value.evidenceLevel, 1, 3) && integer(value.minimumEvidence, 1, 3) &&
    value.evidenceLevel >= value.minimumEvidence && integer(value.inputBound, 1, 100_000_000) &&
    text(value.createdAt) && Number.isFinite(Date.parse(value.createdAt)) && text(value.selectionReason, 500) &&
    prices && (value.mode !== 'auto' || price.inputUsdPerMillion !== null)
}

/** Omission is the old-runtime contract. Present malformed additions cannot replace good cached truth. */
export function validSnapshotModelRouting(snapshot: OrganizationSnapshot): boolean {
  const routing = snapshot.runtime?.modelRouting

  if (routing !== undefined && !validModelRouting(routing)) {return false}

  return snapshot.agents.every(agent => {
    const assignment = agent.modelAssignment

    if (assignment === undefined) {return true}

    if (!validModelAssignment(assignment)) {return false}

    if (agent.id === 'executive' && assignment.mode === 'auto') {return false}

    return !routing || assignment.mode === 'legacy' ||
      (routing.enabled && assignment.routeIds.every(id => routing.routes.some(route => route.id === id)))
  }) && (snapshot.requests ?? []).every(request => request.routeDispatch === undefined || request.routeDispatch === null ||
    validModelDispatch(request.routeDispatch, request.id))
}

export function validAuditModelRouting(audit: OrganizationExecutionAudit): boolean {
  const dispatches = audit.modelDispatches

  if (dispatches !== undefined && (!Array.isArray(dispatches) ||
    !dispatches.every(dispatch => validModelDispatch(dispatch, audit.requestId)) ||
    new Set(dispatches.map(dispatch => dispatch.id)).size !== dispatches.length ||
    new Set(dispatches.map(dispatch => dispatch.attemptToken)).size !== dispatches.length ||
    dispatches.some(dispatch => !audit.modelCalls.some(call => call.dispatchId === dispatch.id)))) {return false}

  return audit.modelCalls.every(call => {
    if (call.dispatchId === undefined || call.dispatchId === null) {return true}

    if (!text(call.dispatchId, 128)) {return false}
    const dispatch = dispatches?.find(item => item.id === call.dispatchId)

    if (!dispatch || call.provider !== dispatch.selectedProvider || call.model !== dispatch.model ||
      call.token !== dispatch.attemptToken || !integer(call.input_limit, 1, 2_000_000) ||
      call.output_limit !== dispatch.maxOutputTokens || call.input_limit + call.output_limit > dispatch.maxContextTokens ||
      !record(call.pricing) || call.pricing.policyGeneration !== dispatch.policyGeneration ||
      call.pricing.inputUsdPerMillion !== dispatch.pricing.inputUsdPerMillion ||
      call.pricing.outputUsdPerMillion !== dispatch.pricing.outputUsdPerMillion) {return false}

    const { inputUsdPerMillion: input, outputUsdPerMillion: output } = dispatch.pricing

    if (input === null || output === null) {return call.reserved_cost_usd === null}

    if (!decimal(call.reserved_cost_usd, 2_000_000)) {return false}

    // The backend rounds each immutable physical-call reservation up to a nanodollar.
    const numerator = BigInt(call.input_limit) * nanodollars(input) + BigInt(call.output_limit) * nanodollars(output)

    return nanodollars(call.reserved_cost_usd) === (numerator + 999_999n) / 1_000_000n
  })
}
