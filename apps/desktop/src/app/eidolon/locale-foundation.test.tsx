import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'
import { I18nProvider, useI18n } from '@/i18n/context'
import { defineLocale } from '@/i18n/define-locale'
import { translateFrom } from '@/i18n/runtime'

import { LocalizedTime } from './localized-time'
import { MetadataSummary } from './objective-metadata'
import type { Objective, OrganizationEditProposal } from './types'

const objective = { id: 'goal', title: 'User title', source: 'runtime', status: 'active', description: 'User content', createdAt: '2026-01-15T10:24:00Z', progress: 42.5, phase: 'Do not translate my phase', milestone: 'User milestone' } as Objective

function Probe() {
  const { setLocale } = useI18n()

  return <><button onClick={() => void setLocale('ru')}>Change language</button><MetadataSummary objective={objective} /></>
}

it('reformats mounted objective dates and numbers after language changes without translating content', () => {
  const { container } = render(<I18nProvider configClient={null}><Probe /></I18nProvider>)
  expect(container.querySelector('time')?.textContent).toBe(new Date(objective.createdAt).toLocaleString('en'))
  fireEvent.click(screen.getByRole('button', { name: 'Change language' }))
  expect(container.querySelector('time')?.textContent).toBe(new Date(objective.createdAt).toLocaleString('ru'))
  expect(container.textContent).toContain(new Intl.NumberFormat('ru', { style: 'percent', maximumFractionDigits: 2 }).format(0.425))
  expect(screen.getByText(objective.phase!)).toBeTruthy()
  expect(screen.getByText(objective.milestone!)).toBeTruthy()
})


it('keeps missing locale keys usable through English fallback and interpolates content unchanged', () => {
  const partial = defineLocale({ organizationFoundation: { priority: 'Priority override' } })
  expect(partial.organizationFoundation.priority).toBe('Priority override')
  expect(partial.organizationFoundation.currentPhase).toBe(TRANSLATIONS.en.organizationFoundation.currentPhase)
  const supplied = '<Owner content> {count} 日本語'
  expect(partial.organizationFoundation.inspectEvent(supplied)).toContain(supplied)
  expect(translateFrom(locale => locale === 'en' ? TRANSLATIONS.en : {}, 'ja', 'organizationFoundation.currentPhase', [])).toBe(TRANSLATIONS.en.organizationFoundation.currentPhase)

  for (const [locale, catalog] of Object.entries(TRANSLATIONS)) {
    for (const count of [0, 1, 2, 1000]) {
      const formatted = new Intl.NumberFormat(locale).format(count)
      expect(catalog.organizationFoundation.eventCount(count, formatted)).toBe(`${formatted} ${count === 1 ? 'event' : 'events'}`)
    }
  }
})

it('uses the same locale for numeric timestamps and handles unavailable dates without invalid markup', () => {
  const { container } = render(<I18nProvider configClient={null} initialLocale="ja"><LocalizedTime value={Date.parse(objective.createdAt)} /><LocalizedTime value="invalid" /></I18nProvider>)
  expect(container.querySelector('time')?.textContent).toBe(new Date(objective.createdAt).toLocaleString('ja'))
  expect(container.querySelectorAll('time')).toHaveLength(1)
  expect(screen.getByText(TRANSLATIONS.en.organizationFoundation.notRecorded)).toBeTruthy()
})

it('renders receipt timestamps as one semantic time element each', async () => {
  const { RuntimeProjectReceipts } = await import('./runtime-project-receipts')
  const receipt = { status: 'passed', checks: [], notExecuted: [], files: [], created: 1791072000, limitations: 'Recorded scope', requestId: 'receipt', resultSha256: 'a'.repeat(64) }
  const proposal = { validationReceipt: receipt, sourceVerificationReceipt: { ...receipt, manifestValidation: receipt, validations: [] } } as unknown as OrganizationEditProposal
  const { container } = render(<I18nProvider configClient={null}><RuntimeProjectReceipts proposal={proposal} /></I18nProvider>)
  expect(container.querySelectorAll('time')).toHaveLength(2)
  expect(container.querySelector('time time')).toBeNull()

  for (const time of container.querySelectorAll('time')) {expect(time.dateTime).toBe(new Date(receipt.created * 1000).toISOString())}
})
