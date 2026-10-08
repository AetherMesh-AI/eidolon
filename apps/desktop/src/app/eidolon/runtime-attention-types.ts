export interface OrganizationAttentionItem {
  requestId: string
  objectiveId: string
  revision: number
  seen: boolean
  createdAt: string
  updatedAt: string
}

export interface OrganizationAttentionPage {
  items: OrganizationAttentionItem[]
  unread: number
  total: number
  hasMore: boolean
  nextCursor: string | null
}

export interface OrganizationAttentionQuery {
  limit?: number
  before?: string
  unreadOnly?: boolean
}

/** A read acknowledgement is tied to the exact blocker content, never an answer. */
export interface OrganizationAttentionSeen {
  id: string
  revision: number
}

export function validAttentionPage(value: OrganizationAttentionPage): boolean {
  const count = (item: unknown) => typeof item === 'number' && Number.isSafeInteger(item) && item >= 0
  const text = (item: unknown) => typeof item === 'string' && item.length > 0

  return (
    !!value &&
    Array.isArray(value.items) &&
    value.items.length <= 100 &&
    count(value.total) &&
    count(value.unread) &&
    value.unread <= value.total &&
    value.items.length <= value.total &&
    typeof value.hasMore === 'boolean' &&
    (value.hasMore
      ? text(value.nextCursor) && value.items.at(-1)?.requestId === value.nextCursor
      : value.nextCursor === null) &&
    new Set(value.items.map(item => item?.requestId)).size === value.items.length &&
    value.items.every(
      item =>
        !!item &&
        text(item.requestId) &&
        text(item.objectiveId) &&
        count(item.revision) &&
        item.revision > 0 &&
        typeof item.seen === 'boolean' &&
        text(item.createdAt) &&
        text(item.updatedAt)
    )
  )
}
