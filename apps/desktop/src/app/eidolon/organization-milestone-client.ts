import type { OwnerPolicyScope } from './organization-owner-policy-types'

export interface Milestone {
  id: string; title: string; revision: number; sha256: string
  status: 'planned' | 'blocked' | 'in_progress' | 'needs_attention' | 'complete'
  objectives: { id: string }[]; dependencies: { id: string }[]; reasons: string[]
}
export interface MilestoneView {
  version: 1; organizationId: string; ownerId: string; profile: string
  milestones: Milestone[]; objectives: { id: string; title: string; projectIds: string[] }[]
}
export interface MilestoneDraft { milestoneId: string; title: string; objectiveIds: string[]; dependencyIds: string[]; expectedRevision: number }
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
const ids = (value: unknown): value is string[] => Array.isArray(value) && value.length <= 128 && value.every(id => typeof id === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$/.test(id)) && new Set(value).size === value.length
const hash = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)

/** A captured owner socket and retained exact save intent, including uncertain retries. */
export function createMilestoneClient(source: () => OwnerPolicyScope, newKey: () => string = () => crypto.randomUUID()) {
  let captured: OwnerPolicyScope | null = null
  let view: MilestoneView | null = null
  let intent: Record<string, unknown> | null = null
  let busy = false
  const identity = (value: OwnerPolicyScope) => JSON.stringify([value.key, value.ownerKey, value.profile, value.eligible, value.organizationId, value.ownerId])
  const current = () => !!captured && captured.eligible && identity(captured) === identity(source())

  const check = () => { if (!current()) { throw new Error('Organization owner connection changed. Reopen milestones.') } }
  const bound = (value: unknown) => object(value) && value.version === 1 && value.organizationId === captured?.organizationId && value.ownerId === captured?.ownerId && value.profile === captured?.profile

  return {
    current,
    pending: () => intent !== null,
    async read() {
      if (busy || intent) { throw new Error('Resolve the pending milestone save first.') }
      captured = { ...source() }

      if (!captured.eligible || !captured.organizationId || !captured.ownerId) { throw new Error('Connect the organization owner to edit milestones.') }
      busy = true

      try {
        const result = await captured.request<unknown>('organization.milestones', { expectedOrganizationId: captured.organizationId, expectedOwnerId: captured.ownerId })
        check()

        if (!bound(result) || !object(result) || !Array.isArray(result.milestones) || result.milestones.length > 128 || !Array.isArray(result.objectives) || result.objectives.length > 1000 ||
          !result.objectives.every(o => object(o) && ids([o.id]) && typeof o.title === 'string' && ids(o.projectIds)) ||
          !result.milestones.every(m => object(m) && ids([m.id]) && typeof m.title === 'string' && Number.isSafeInteger(m.revision) && (m.revision as number) > 0 && hash(m.sha256) &&
            ['planned','blocked','in_progress','needs_attention','complete'].includes(m.status as string) && Array.isArray(m.objectives) && m.objectives.every(o => object(o) && ids([o.id])) &&
            Array.isArray(m.dependencies) && m.dependencies.every(d => object(d) && ids([d.id])) && Array.isArray(m.reasons) && m.reasons.every(r => typeof r === 'string'))) {
          throw new Error('Milestone response is invalid.')
        }

        view = structuredClone(result) as unknown as MilestoneView

        return structuredClone(view)
      } finally { busy = false }
    },
    async reconcile() {
      if (busy || !intent || !captured) { throw new Error('No pending milestone save to reconcile.') }
      const next = { ...source() }

      if (!next.eligible || next.ownerKey !== captured.ownerKey || next.profile !== captured.profile || next.organizationId !== captured.organizationId || next.ownerId !== captured.ownerId) { throw new Error('Return to the same authenticated organization owner.') }
      busy = true

      try {
        const result = await next.request<unknown>('organization.milestoneReceipt', { expectedOrganizationId: next.organizationId, expectedOwnerId: next.ownerId, idempotencyKey: intent.idempotencyKey })

        if (identity(next) !== identity(source()) || !bound(result) || !object(result) || !('receipt' in result)) { throw new Error('Receipt scope changed or response is invalid.') }

        if (result.receipt !== null && (!object(result.receipt) || result.receipt.milestoneId !== intent.milestoneId || result.receipt.revision !== (intent.expectedRevision as number) + 1 || !hash(result.receipt.sha256))) { throw new Error('Milestone receipt is invalid.') }
        captured = next

        if (result.receipt !== null) { intent = null }

        return result.receipt !== null
      } finally { busy = false }
    },
    async save(draft: MilestoneDraft, confirmed: boolean) {
      check()

      if (busy || !view || confirmed !== true) { throw new Error('Review and confirm the milestone first.') }

      if (intent && ['milestoneId', 'title', 'objectiveIds', 'dependencyIds', 'expectedRevision'].some(key => JSON.stringify(intent![key]) !== JSON.stringify(key === 'title' ? draft.title.trim() : draft[key as keyof MilestoneDraft]))) { throw new Error('Retry only the exact saved milestone intent.') }

      if (!intent) {
        if (!ids([draft.milestoneId]) || !draft.title.trim() || draft.title.length > 500 || !ids(draft.objectiveIds) || !draft.objectiveIds.length || draft.objectiveIds.length > 64 || !ids(draft.dependencyIds) || draft.dependencyIds.length > 32) { throw new Error('Milestone selection is invalid.') }
        const old = view.milestones.find(m => m.id === draft.milestoneId)

        if (draft.expectedRevision !== (old?.revision ?? 0) || draft.dependencyIds.some(id => id === draft.milestoneId || !view!.milestones.some(m => m.id === id))) { throw new Error('Milestone revision or dependencies changed.') }
        const selected = draft.objectiveIds.map(id => view!.objectives.find(o => o.id === id))

        if (selected.some(o => !o?.projectIds.length)) { throw new Error('Choose objectives bound to workspace projects.') }
        const projectIds = [...new Set(selected.flatMap(o => o!.projectIds))].sort()

        if (projectIds.length > 8) { throw new Error('A milestone supports at most eight projects.') }
        intent = { ...structuredClone(draft), title: draft.title.trim(), projectIds, expectedOrganizationId: captured!.organizationId, expectedOwnerId: captured!.ownerId, idempotencyKey: newKey(), confirmed: true }
      }

      busy = true

      try {
        const result = await captured!.request<unknown>('organization.reviseMilestone', structuredClone(intent))
        check()

        if (!bound(result) || !object(result) || !object(result.receipt) || result.receipt.milestoneId !== intent.milestoneId || result.receipt.revision !== (intent.expectedRevision as number) + 1 || !hash(result.receipt.sha256)) { throw new Error('Milestone receipt is uncertain. Retry the same save.') }
        intent = null

        return result
      } finally { busy = false }
    }
  }
}
