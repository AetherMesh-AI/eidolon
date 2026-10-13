import { expect, it } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'

import { objectivePhaseLabel } from './objective-phase'
import type { Objective } from './types'

const objective: Objective = { id: 'goal', source: 'runtime', status: 'active', title: 'Goal', description: 'Scope', ownerId: 'executive', createdAt: '2026-10-13T00:00:00Z', phase: 'Execution and review' }

it.each([
  ['en', 'Execution and review'], ['ja', '実行とレビュー'], ['zh', '执行与审查'],
  ['zh-hant', '執行與審查'], ['ar', 'التنفيذ والمراجعة'], ['ru', 'Выполнение и проверка']
] as const)('translates only an explicit known runtime phase code in %s', (locale, expected) => {
  const record = { ...objective, phaseCode: 'execution_review' }
  expect(objectivePhaseLabel(record, TRANSLATIONS[locale].organizationHome.detail.phases)).toBe(expected)
  expect(record.phase).toBe('Execution and review')
})

it.each([undefined, 'future_phase', '__proto__', 'constructor'])('preserves legacy or unknown phase text for code %s', phaseCode => {
  const labels = TRANSLATIONS.ar.organizationHome.detail.phases
  expect(objectivePhaseLabel({ ...objective, phaseCode }, labels)).toBe('Execution and review')
  expect(objectivePhaseLabel({ ...objective, phaseCode, phase: 'مرحلة خاصة (v2)' }, labels)).toBe('مرحلة خاصة (v2)')
  expect(objectivePhaseLabel({ ...objective, phaseCode, phase: undefined }, labels)).toBeUndefined()
})

it('does not reinterpret prototype authored text even if a runtime code is present', () => {
  expect(objectivePhaseLabel({ ...objective, source: 'prototype', phaseCode: 'execution_review' }, TRANSLATIONS.ja.organizationHome.detail.phases)).toBe('Execution and review')
})


it('does not translate malformed runtime codes', () => {
  for (const phaseCode of [null, ['execution_review'], { code: 'execution_review' }]) {
    expect(objectivePhaseLabel({ ...objective, phaseCode: phaseCode as unknown as string }, TRANSLATIONS.ar.organizationHome.detail.phases)).toBe('Execution and review')
  }
})
