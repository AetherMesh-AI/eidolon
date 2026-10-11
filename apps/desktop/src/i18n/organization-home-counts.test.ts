import { describe, expect, it } from 'vitest'

import { TRANSLATIONS } from './catalog'

import { organizationHomeEn } from './organization-home'
import { organizationHomeCountsAr, organizationHomeCountsJa, organizationHomeCountsRu, organizationHomeCountsZh, organizationHomeCountsZhHant } from './organization-home-counts'

describe('organization count summaries', () => {
  it('loads count translations through each catalog while preserving unrelated fallback and workspace copy', () => {
    for (const [locale, copy] of [
      ['ja', organizationHomeCountsJa], ['zh', organizationHomeCountsZh], ['zh-hant', organizationHomeCountsZhHant],
      ['ar', organizationHomeCountsAr], ['ru', organizationHomeCountsRu]
    ] as const) {
      const home = TRANSLATIONS[locale].organizationHome
      expect(home.detail.moreTasks(2, '٢')).toBe(copy.moreTasks(2, '٢'))
      expect(home.detail.overview).toBe(organizationHomeEn.detail.overview)
      expect(home.workspace.search).not.toBe(organizationHomeEn.workspace.search)
    }
  })

  it('selects English singular from raw numbers while retaining localized display digits', () => {
    expect(organizationHomeEn.detail.moreTasks(1, '١')).toBe('١ more task in the full work view')
    expect(organizationHomeEn.detail.moreRequests(1, '١')).toBe('١ more owner request below')
    expect(organizationHomeEn.detail.delegationCount(1, '١')).toBe('١ current-round manager assignment')
    expect(organizationHomeEn.detail.ownerRequestCount(1, '١')).toBe('١ pending owner request')
    expect(organizationHomeEn.detail.moreTasks(2, '٢')).toBe('٢ more tasks in the full work view')
  })

  it.each([
    [0, 'задач'], [1, 'задача'], [2, 'задачи'], [5, 'задач'],
    [11, 'задач'], [21, 'задача'], [22, 'задачи'], [101, 'задача'], [1000, 'задач']
  ])('uses Russian grammatical number for %i independently of display formatting', (count, noun) => {
    const formatted = new Intl.NumberFormat('ar').format(count)
    expect(organizationHomeCountsRu.moreTasks(count, formatted)).toBe(`Ещё ${formatted} ${noun} в полном списке работ`)
  })

  it('uses the same grammatical categories in requests and assignments', () => {
    expect(organizationHomeCountsRu.moreRequests(21, '٢١')).toContain('٢١ запрос владельцу')
    expect(organizationHomeCountsRu.delegationCount(22, '٢٢')).toContain('٢٢ назначения менеджерам')
    expect(organizationHomeCountsRu.ownerRequestCount(11, '١١')).toContain('١١ ожидающих запросов владельцу')
  })

  it.each([0, 1, 2, 3, 11, 21, 1000])('preserves localized display values in every summary for %i', count => {
    for (const [locale, copy] of [
      ['ja', organizationHomeCountsJa], ['zh', organizationHomeCountsZh], ['zh-Hant', organizationHomeCountsZhHant],
      ['ar', organizationHomeCountsAr], ['ru', organizationHomeCountsRu]
    ] as const) {
      const formatted = new Intl.NumberFormat(locale).format(count)
      for (const summarize of Object.values(copy)) expect(summarize(count, formatted)).toContain(formatted)
    }
  })
})
