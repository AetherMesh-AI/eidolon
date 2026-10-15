import type { OwnerPolicyScope } from './organization-owner-policy-types'

export type GoalLimits = { modelCalls: number; tokens: number; costUsd: string | null; stages: number; projectRuns: number; deadlineTimestamp: number; completionReserve: { modelCalls: number; tokens: number; costUsd: string | null } }
export type GoalTemplate = { id: string; title: string; description: string; priority: string; acceptanceCriteria: unknown[]; deliveryMode: string; requiredChecks: string[]; projectIds: string[]; executiveId: string; managerId: string; dependencyIds: string[]; templateDependencyIds: string[] }
export interface GoalView {
  version: 1; organizationId: string; ownerId: string; profile: string
  goals: { id: string; revision: number; paused: boolean; objectiveIds: string[]; limits: GoalLimits; reserved: Record<string, number | string | null> }[]
  plans: { goalId: string; enabled: boolean; controlRevision: number; canEnable: boolean; waitingReason: string | null; templates: GoalTemplate[]; templateStates: { id: string; launched: boolean; blockers: string[] }[]; launches: { template_id: string; objective_id: string }[] }[]
  objectives: { id: string; title: string }[]; projectIds: string[]; leaders: { id: string; name: string; role: string; manager_id: string | null }[]
}
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const hash = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v)
const nonnegative = (v: unknown) => Number.isSafeInteger(v) && (v as number) >= 0
const money = (v: unknown) => v === null || (typeof v === 'string' && /^\d+(\.\d+)?$/.test(v))
const deadline = (v: unknown) => nonnegative(v) && (v as number) <= 253402300799
const identity = (s: OwnerPolicyScope) => JSON.stringify([s.key, s.ownerKey, s.profile, s.eligible, s.organizationId, s.ownerId])
const actions = { 'organization.configureGoal': 'allowance', 'organization.approveGoalContinuation': 'continuation', 'organization.setGoalContinuationEnabled': 'continuation' } as const
export type GoalAction = keyof typeof actions

export function createGoalClient(source: () => OwnerPolicyScope, newKey: () => string = () => crypto.randomUUID()) {
  let captured: OwnerPolicyScope | null = null
  let pending: { method: GoalAction; args: Record<string, unknown>; key: string } | null = null
  let busy = false
  let preview: { args: Record<string, unknown>; sha256: string } | null = null
  const current = () => !!captured?.eligible && identity(captured) === identity(source())

  const check = () => { if (!current()) { throw new Error('The authenticated organization owner connection changed.') } }
  const scope = () => ({ expectedOrganizationId: captured!.organizationId, expectedOwnerId: captured!.ownerId })
  const bound = (r: unknown) => object(r) && r.version === 1 && r.organizationId === captured?.organizationId && r.ownerId === captured?.ownerId && r.profile === captured?.profile

  const receipt = (r: unknown) => {
    if (!pending || !object(r) || r.goalId !== pending.args.goalId || !Number.isSafeInteger(r.revision)) { return false }

    if (pending.method === 'organization.approveGoalContinuation') { return r.revision === 1 && r.enabled === false && r.sha256 === pending.args.previewSha256 }

    if (r.revision !== (pending.args.expectedRevision as number) + 1) { return false }

    return pending.method === 'organization.configureGoal' ? hash(r.sha256) : r.enabled === pending.args.enabled
  }

  return {
    current, pending: () => !!pending,
    async read(): Promise<GoalView> {
      if (busy || pending) { throw new Error('Reconcile the pending goal action first.') }
      captured = { ...source() }; preview = null; check()

      if (!captured.organizationId || !captured.ownerId) { throw new Error('Organization owner identity is required.') }
      busy = true

      try {
        const r = await captured.request<unknown>('organization.goals', scope()); check()

        if (!bound(r) || !object(r) || !Array.isArray(r.goals) || r.goals.length > 128 || !Array.isArray(r.plans) || r.plans.length > 128 || !Array.isArray(r.objectives) || r.objectives.length > 1000 || !Array.isArray(r.leaders) || !Array.isArray(r.projectIds)) { throw new Error('Goal view is invalid.') }

        if (!r.goals.every(g => object(g) && typeof g.id === 'string' && Number.isSafeInteger(g.revision) && typeof g.paused === 'boolean' && Array.isArray(g.objectiveIds) && g.objectiveIds.every(id => typeof id === 'string') && object(g.limits) && object(g.limits.completionReserve) && nonnegative(g.limits.completionReserve.modelCalls) && nonnegative(g.limits.completionReserve.tokens) && money(g.limits.completionReserve.costUsd) && money(g.limits.costUsd) && deadline(g.limits.deadlineTimestamp) && object(g.reserved) && ['modelCalls','tokens','stages','projectRuns'].every(k => (g.reserved as Record<string, unknown>)[k] === null || nonnegative((g.reserved as Record<string, unknown>)[k])) && money(g.reserved.costUsd) && ['modelCalls','tokens','stages','projectRuns','deadlineTimestamp'].every(k => Number.isSafeInteger((g.limits as Record<string, unknown>)[k]))) ||
          !r.plans.every(p => object(p) && typeof p.goalId === 'string' && typeof p.enabled === 'boolean' && typeof p.canEnable === 'boolean' && Number.isSafeInteger(p.controlRevision) && (p.waitingReason === null || typeof p.waitingReason === 'string') && Array.isArray(p.templates) && p.templates.length <= 32 && Array.isArray(p.launches) && p.launches.length <= 32 && p.launches.every(l => object(l) && typeof l.template_id === 'string' && typeof l.objective_id === 'string') && Array.isArray(p.templateStates) && p.templateStates.length <= 32 && p.templateStates.every(t => object(t) && typeof t.id === 'string' && typeof t.launched === 'boolean' && Array.isArray(t.blockers) && t.blockers.every(b => typeof b === 'string'))) ||
          !r.objectives.every(o => object(o) && typeof o.id === 'string' && typeof o.title === 'string') || !r.projectIds.every(id => typeof id === 'string') ||
          !r.leaders.every(l => object(l) && typeof l.id === 'string' && typeof l.name === 'string' && (l.manager_id === null || typeof l.manager_id === 'string') && ['Executive','Manager'].includes(l.role as string))) { throw new Error('Goal view fields are invalid.') }

        return structuredClone(r) as unknown as GoalView
      } finally { busy = false }
    },
    async preview(args: { goalId: string; templates: GoalTemplate[]; deadlineTimestamp: number }) {
      check();

 if (busy || pending) { throw new Error('A goal action is still pending.') }
      busy = true; preview = null
      const capturedArgs = structuredClone(args)

      try {
        const r = await captured!.request<unknown>('organization.previewGoalContinuation', { ...scope(), ...capturedArgs }); check()

        if (!bound(r) || !object(r) || !object(r.preview) || !hash(r.preview.sha256) || !object(r.preview.plan) || r.preview.plan.goalId !== args.goalId) { throw new Error('Goal preview is invalid.') }
        preview = { args: capturedArgs, sha256: r.preview.sha256 as string }

        return structuredClone(r.preview)
      } finally { busy = false }
    },
    async write(method: GoalAction, args: Record<string, unknown>, confirmed: boolean) {
      check();

 if (busy || confirmed !== true) { throw new Error('Review and confirm this exact goal action.') }

      if (pending && (pending.method !== method || JSON.stringify(pending.args) !== JSON.stringify(args))) { throw new Error('Retry only the exact pending action.') }

      if (!pending) {
        if (method === 'organization.approveGoalContinuation') {
          const { previewSha256, ...draft } = args

          if (!preview || preview.sha256 !== previewSha256 || JSON.stringify(preview.args) !== JSON.stringify(draft)) { throw new Error('Review a fresh matching continuation preview.') }
        }

        pending = { method, args: structuredClone(args), key: newKey() }
      }

      busy = true

      try {
        const r = await captured!.request<unknown>(method, { ...scope(), ...structuredClone(pending.args), idempotencyKey: pending.key, confirmed: true }); check()

        if (!bound(r) || !object(r) || !receipt(r.receipt)) { throw new Error('Goal receipt is uncertain; reconcile before changing the action.') }
        pending = null; preview = null

        return r.receipt
      } finally { busy = false }
    },
    async reconcile() {
      if (busy || !pending || !captured) { throw new Error('No pending goal action to reconcile.') }
      const next = { ...source() }

      if (!next.eligible || next.ownerKey !== captured.ownerKey || next.profile !== captured.profile || next.organizationId !== captured.organizationId || next.ownerId !== captured.ownerId) { throw new Error('Return to the same authenticated organization owner.') }
      busy = true

      try {
        const r = await next.request<unknown>('organization.goalReceipt', { ...scope(), receiptKind: actions[pending.method], idempotencyKey: pending.key })

        if (identity(next) !== identity(source()) || !bound(r) || !object(r) || !('receipt' in r) || (r.receipt !== null && !receipt(r.receipt))) { throw new Error('Goal receipt scope or identity is invalid.') }
        captured = next

        if (r.receipt !== null) { pending = null; preview = null }

        return r.receipt !== null
      } finally { busy = false }
    }
  }
}
