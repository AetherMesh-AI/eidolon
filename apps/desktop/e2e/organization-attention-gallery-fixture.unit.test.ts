import { expect, it } from 'vitest'

import { validAttentionPage } from '../src/app/eidolon/runtime-attention-types'

import { galleryAttentionPage } from './organization-attention-gallery-fixture'

it('returns valid bounded attention pages with stable global counts and filtered cursor continuity', () => {
  const records = Array.from({ length: 27 }, (_, index) => ({
    requestId: `attention-${index}`, objectiveId: 'objective', revision: 1, seen: index === 0,
    createdAt: '2026-10-08T09:00:00Z', updatedAt: '2026-10-08T09:00:00Z'
  }))

  const first = galleryAttentionPage(records, { limit: 25, unreadOnly: true })
  const second = galleryAttentionPage(records, { limit: 25, unreadOnly: true, before: first.nextCursor! })
  expect([first, second].every(validAttentionPage)).toBe(true)
  expect(first.items).toHaveLength(25)
  expect(first.nextCursor).toBe(first.items.at(-1)!.requestId)
  expect(second.items).toEqual([records.at(-1)])
  expect(second.hasMore).toBe(false)
  expect(second.nextCursor).toBeNull()

  for (const page of [first, second]) {
    expect(page.total).toBe(records.length)
    expect(page.unread).toBe(records.filter(item => !item.seen).length)
  }

  expect(() => galleryAttentionPage(records, { before: 'unknown' })).toThrow('Unknown synthetic attention cursor')
})
