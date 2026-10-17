/** Setup-only owner policy. Suggested limits are never applied by reading them. */
export interface SetupBudgetPolicy {
  version: 2
  mode: 'bounded_api'
  improveCallLimit: number
  generationCallLimit: number
  totalCallLimit: number
}
export interface SetupBudgetView {
  policyVersion: 1 | 2
  policy: SetupBudgetPolicy | null
  usage: { legacy: number; improveTeamInput: number; generateTeam: number; total: number }
  limits: { improveCalls: number; generationCalls: number; totalCalls: number; totalTokens: number; maxCostUsd: string | null }
  remaining: { improveCalls: number; generationCalls: number; totalCalls: number }
  status: 'available' | 'exhausted' | 'running' | 'uncertain' | 'blocked'
  reservedTokens: number
  reservedCostUsd: string | null
}
export interface SetupBudgetReceipt {
  idempotencyKey: string
  organizationId: string
  ownerId: string
  action: 'setupBudget'
  policyGeneration: number
  sourceRevision: string
  previewVersion: string
  configuration: SetupBudgetPolicy
}
export interface SetupBudgetState {
  capabilityVersion: 1
  state: 'ready'
  organizationId: string
  ownerId: string
  policyGeneration: number
  sourceRevision: string
  configuration: SetupBudgetPolicy | null
  suggestedConfiguration: SetupBudgetPolicy
  managed: boolean
  available: boolean
  setupBudget: SetupBudgetView
  latestReceipt: SetupBudgetReceipt | null
}
export interface SetupBudgetPreview {
  capabilityVersion: 1
  organizationId: string
  ownerId: string
  policyGeneration: number
  sourceRevision: string
  configuration: SetupBudgetPolicy
  usage: SetupBudgetView
  previewVersion: string
  changes: { field: string; before: SetupBudgetPolicy | null; after: SetupBudgetPolicy }[]
  warnings: string[]
}

const object = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {throw new Error('invalid_setup_budget')}

  return value as Record<string, unknown>
}

const integer = (value: unknown, maximum = Number.MAX_SAFE_INTEGER): value is number =>
  Number.isSafeInteger(value) && Number(value) >= 0 && Number(value) <= maximum

const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const money = (value: unknown): boolean => value === null || (typeof value === 'string' && /^\d+(\.\d+)?$/.test(value) && Number.isFinite(Number(value)))

export function validateSetupBudgetPolicy(value: unknown): asserts value is SetupBudgetPolicy {
  const policy = object(value)

  if (Object.keys(policy).sort().join(',') !== 'generationCallLimit,improveCallLimit,mode,totalCallLimit,version' ||
    policy.version !== 2 || policy.mode !== 'bounded_api' ||
    !['improveCallLimit', 'generationCallLimit', 'totalCallLimit'].every(key => integer(policy[key], 1000)) ||
    Number(policy.improveCallLimit) > Number(policy.totalCallLimit) || Number(policy.generationCallLimit) > Number(policy.totalCallLimit)) {
    throw new Error('invalid_setup_budget')
  }
}

function validateView(value: unknown) {
  const view = object(value), usage = object(view.usage), limits = object(view.limits), remaining = object(view.remaining)

  if (view.policyVersion === 2) {validateSetupBudgetPolicy(view.policy)}
  else if (view.policyVersion !== 1 || view.policy !== null) {throw new Error('invalid_setup_budget')}

  if (!['legacy', 'improveTeamInput', 'generateTeam', 'total'].every(key => integer(usage[key])) ||
    Number(usage.legacy) + Number(usage.improveTeamInput) + Number(usage.generateTeam) !== usage.total ||
    !['improveCalls', 'generationCalls', 'totalCalls'].every(key => integer(limits[key], 1000) && integer(remaining[key], Number(limits[key]))) ||
    Number(remaining.improveCalls) > Number(remaining.totalCalls) || Number(remaining.generationCalls) > Number(remaining.totalCalls) ||
    !integer(limits.totalTokens, 120000) || !money(limits.maxCostUsd) || !integer(view.reservedTokens) || !money(view.reservedCostUsd) ||
    !['available', 'exhausted', 'running', 'uncertain', 'blocked'].includes(String(view.status))) {throw new Error('invalid_setup_budget')}

  const aggregate = Math.max(0, Number(limits.totalCalls) - Number(usage.total))

  if (remaining.totalCalls !== aggregate ||
    remaining.improveCalls !== Math.min(aggregate, Math.max(0, Number(limits.improveCalls) - Number(usage.improveTeamInput))) ||
    remaining.generationCalls !== Math.min(aggregate, Math.max(0, Number(limits.generationCalls) - Number(usage.generateTeam)))) {throw new Error('invalid_setup_budget')}

  if (view.policyVersion === 1) {
    if (Number(limits.totalCalls) > 3 || limits.improveCalls !== limits.totalCalls || limits.generationCalls !== limits.totalCalls ||
      usage.legacy !== usage.total || usage.improveTeamInput !== 0 || usage.generateTeam !== 0) {throw new Error('invalid_setup_budget')}
  } else {
    const policy = view.policy as SetupBudgetPolicy

    if (Number(limits.totalCalls) > policy.totalCallLimit || limits.improveCalls !== Math.min(policy.improveCallLimit, Number(limits.totalCalls)) ||
      limits.generationCalls !== Math.min(policy.generationCallLimit, Number(limits.totalCalls))) {throw new Error('invalid_setup_budget')}
  }

  if (view.status === 'available' && (!aggregate || !(Number(remaining.improveCalls) || Number(remaining.generationCalls)))) {throw new Error('invalid_setup_budget')}
}


function validateIdentity(value: Record<string, unknown>, organizationId: string) {
  if (value.capabilityVersion !== 1 || value.organizationId !== organizationId || typeof value.ownerId !== 'string' || !value.ownerId ||
    !integer(value.policyGeneration) || value.policyGeneration === 0 || !hash(value.sourceRevision)) {throw new Error('invalid_setup_budget')}
}

function validateReceipt(value: unknown, organizationId: string) {
  const receipt = object(value)
  validateIdentity({ ...receipt, capabilityVersion: 1 }, organizationId)
  validateSetupBudgetPolicy(receipt.configuration)

  if (receipt.action !== 'setupBudget' || !hash(receipt.previewVersion) || typeof receipt.idempotencyKey !== 'string' ||
    !receipt.idempotencyKey || receipt.idempotencyKey.length > 128) {throw new Error('invalid_setup_budget')}
}

export function validateSetupBudgetState(value: unknown, organizationId: string): asserts value is SetupBudgetState {
  const state = object(value)
  validateIdentity(state, organizationId)

  if (state.configuration !== null) {validateSetupBudgetPolicy(state.configuration)}
  validateSetupBudgetPolicy(state.suggestedConfiguration)
  validateView(state.setupBudget)

  const view = state.setupBudget as SetupBudgetView

  if (!sameNullablePolicy(state.configuration, view.policy)) {throw new Error('invalid_setup_budget')}

  if (state.configuration === null) {
    if (view.policyVersion !== 1 || state.latestReceipt !== null) {throw new Error('invalid_setup_budget')}
  } else {
    validateReceipt(state.latestReceipt, organizationId)
    const receipt = state.latestReceipt as SetupBudgetReceipt

    if (view.policyVersion !== 2 || receipt.ownerId !== state.ownerId || receipt.policyGeneration > Number(state.policyGeneration) ||
      !samePolicy(receipt.configuration, state.configuration as SetupBudgetPolicy)) {throw new Error('invalid_setup_budget')}
  }

  if (state.state !== 'ready' || typeof state.managed !== 'boolean' || typeof state.available !== 'boolean') {throw new Error('invalid_setup_budget')}
}

export function validateSetupBudgetPreview(value: unknown, state: SetupBudgetState, configuration: SetupBudgetPolicy): asserts value is SetupBudgetPreview {
  const preview = object(value)
  validateIdentity(preview, state.organizationId)
  validateSetupBudgetPolicy(preview.configuration)
  validateView(preview.usage)

  if (preview.ownerId !== state.ownerId || preview.policyGeneration !== state.policyGeneration || preview.sourceRevision !== state.sourceRevision ||
    !hash(preview.previewVersion) || !samePolicy(preview.configuration, configuration) || !Array.isArray(preview.changes) || preview.changes.length !== 1 ||
    !Array.isArray(preview.warnings) || !preview.warnings.every(item => typeof item === 'string')) {throw new Error('invalid_setup_budget')}

  const changes = preview.changes as unknown[]
  const change = object(changes[0])
  validateSetupBudgetPolicy(change.after)

  if (change.before !== null) {validateSetupBudgetPolicy(change.before)}

  if (Object.keys(change).sort().join(',') !== 'after,before,field' || change.field !== 'setup_budget' ||
    !sameNullablePolicy(change.before, state.configuration) || !samePolicy(change.after, configuration) ||
    !sameNullablePolicy((preview.usage as SetupBudgetView).policy, state.configuration) ||
    !sameView(preview.usage as SetupBudgetView, state.setupBudget)) {throw new Error('invalid_setup_budget')}
}

function sameNullablePolicy(left: unknown, right: unknown) {
  if (left === null || right === null) {return left === right}
  validateSetupBudgetPolicy(left)
  validateSetupBudgetPolicy(right)

  return samePolicy(left, right)
}

function sameView(left: SetupBudgetView, right: SetupBudgetView) {
  return left.policyVersion === right.policyVersion && left.status === right.status &&
    left.reservedTokens === right.reservedTokens && left.reservedCostUsd === right.reservedCostUsd &&
    sameNullablePolicy(left.policy, right.policy) &&
    (Object.keys(left.usage) as (keyof SetupBudgetView['usage'])[]).every(key => left.usage[key] === right.usage[key]) &&
    (Object.keys(left.limits) as (keyof SetupBudgetView['limits'])[]).every(key => left.limits[key] === right.limits[key]) &&
    (Object.keys(left.remaining) as (keyof SetupBudgetView['remaining'])[]).every(key => left.remaining[key] === right.remaining[key])
}

export const samePolicy = (left: SetupBudgetPolicy, right: SetupBudgetPolicy) =>
  (Object.keys(left) as (keyof SetupBudgetPolicy)[]).every(key => left[key] === right[key])

export function validateSetupBudgetApplied(value: unknown, preview: SetupBudgetPreview, key: string): asserts value is { capabilityVersion: 1; receipt: SetupBudgetReceipt } {
  const result = object(value), receipt = object(result.receipt)
  validateReceipt(receipt, preview.organizationId)

  if (result.capabilityVersion !== 1 || receipt.ownerId !== preview.ownerId || receipt.idempotencyKey !== key ||
    receipt.policyGeneration !== preview.policyGeneration + 1 || receipt.sourceRevision !== preview.sourceRevision ||
    receipt.previewVersion !== preview.previewVersion || !samePolicy(receipt.configuration as SetupBudgetPolicy, preview.configuration)) {throw new Error('invalid_setup_budget')}
}
