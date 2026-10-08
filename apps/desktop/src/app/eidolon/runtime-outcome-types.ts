import type { OrganizationAttentionQuery, OrganizationAttentionSeen } from './runtime-attention-types'

export interface OrganizationOutcome {
  objectiveId: string
  revision: number
  seen: boolean
  status: 'accepted' | 'cancelled' | 'legacy_completed'
  title: string
  summary: string
  round: number
  deliverableId: string | null
  acceptanceRequestId: string | null
  evidenceIds: string[]
  createdAt: string
  updatedAt: string
  archived: boolean
}

export interface OrganizationOutcomePage {
  generation: string
  items: OrganizationOutcome[]
  unread: number
  total: number
  hasMore: boolean
  nextCursor: string | null
}

export type OrganizationOutcomeQuery = OrganizationAttentionQuery
export type OrganizationOutcomeSeen = OrganizationAttentionSeen

export function validOutcomePage(value: OrganizationOutcomePage): boolean {
  const count = (item: unknown) => typeof item === 'number' && Number.isSafeInteger(item) && item >= 0
  const text = (item: unknown) => typeof item === 'string' && item.length > 0
  const optionalId = (item: unknown) => item === null || text(item)

  return (
    !!value && text(value.generation) && Array.isArray(value.items) && value.items.length <= 100 &&
    count(value.total) && count(value.unread) && value.unread <= value.total && value.items.length <= value.total &&
    typeof value.hasMore === 'boolean' &&
    (value.hasMore ? text(value.nextCursor) && value.items.at(-1)?.objectiveId === value.nextCursor : value.nextCursor === null) &&
    new Set(value.items.map(item => item?.objectiveId)).size === value.items.length &&
    value.items.every(item => !!item && text(item.objectiveId) && count(item.revision) && item.revision > 0 &&
      typeof item.seen === 'boolean' && ['accepted', 'cancelled', 'legacy_completed'].includes(item.status) &&
      typeof item.title === 'string' && typeof item.summary === 'string' && count(item.round) &&
      optionalId(item.deliverableId) && optionalId(item.acceptanceRequestId) && Array.isArray(item.evidenceIds) &&
      item.evidenceIds.every(text) && new Set(item.evidenceIds).size === item.evidenceIds.length &&
      text(item.createdAt) && text(item.updatedAt) && typeof item.archived === 'boolean')
  )
}
