import type { OrganizationAttentionItem, OrganizationAttentionPage, OrganizationAttentionQuery } from '../src/app/eidolon/runtime-attention-types'

/** Serve the same bounded cursor shape consumed by the current attention inbox. */
export function galleryAttentionPage(records: OrganizationAttentionItem[], query: OrganizationAttentionQuery = {}): OrganizationAttentionPage {
  const matching = records.filter(item => !query.unreadOnly || !item.seen)
  const cursorIndex = query.before ? matching.findIndex(item => item.requestId === query.before) : -1

  if (query.before && cursorIndex < 0) { throw new Error('Unknown synthetic attention cursor') }
  const remaining = matching.slice(cursorIndex + 1)
  const items = remaining.slice(0, query.limit ?? 25)
  const hasMore = remaining.length > items.length

  return {
    items,
    total: records.length,
    unread: records.filter(item => !item.seen).length,
    hasMore,
    nextCursor: hasMore ? items.at(-1)!.requestId : null
  }
}
