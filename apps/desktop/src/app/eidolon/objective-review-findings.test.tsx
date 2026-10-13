import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'
import { I18nProvider } from '@/i18n/context'

import { ObjectiveProgress } from './objective-progress'
import type { Objective, OrganizationSnapshot } from './types'

afterEach(cleanup)

const objective: Objective = {
  id: 'goal', title: 'Recorded goal', description: 'Exact outcome', ownerId: 'executive',
  status: 'waiting', source: 'runtime', createdAt: '2026-10-13T00:00:00Z', progress: 100,
  acceptance: {
    status: 'blocked', round: 1, maxReplans: 2, criteria: ['Verify the launch date'],
    summary: 'Sources disagree', deliverableId: 'draft-evidence',
    criteriaResults: [{ criterion: 'Verify the launch date', satisfied: false, reason: 'Two source dates remain unresolved.', evidenceIds: ['evidence-a', 'evidence-b'] }],
    conflicts: ['Published launch dates disagree.']
  }
}

const snapshot: OrganizationSnapshot = { source: 'runtime', objectives: [objective], agents: [], tasks: [], activity: [], knowledge: [] }

it.each(['en', 'ja', 'zh', 'zh-hant', 'ar', 'ru'] as const)('exposes exact negative findings without treating task completion or cited evidence as acceptance (%s)', locale => {
  const copy = TRANSLATIONS[locale].organizationHome.detail
  const before = JSON.stringify(objective)
  render(<I18nProvider configClient={null} initialLocale={locale}><ObjectiveProgress objective={objective} onReviewRequests={vi.fn()} onReviewWork={vi.fn()} snapshot={snapshot} /></I18nProvider>)
  const findings = within(screen.getByRole('region', { name: copy.reviewFindings.heading }))
  expect(findings.getByRole('region', { name: copy.reviewFindings.conflicts }).textContent).toContain('Published launch dates disagree.')
  expect(findings.getByText('Verify the launch date')).toBeTruthy()
  expect(findings.getByText(copy.reviewFindings.unsatisfied)).toBeTruthy()
  expect(findings.getByText(`${copy.reviewFindings.unmetCriteria} · ${new Intl.NumberFormat(locale).format(1)}`).closest('details')).toBeNull()
  expect(findings.getByText('Two source dates remain unresolved.').getAttribute('dir')).toBe('auto')
  expect(findings.getByText('evidence-a, evidence-b').tagName).toBe('BDI')
  expect(screen.getByRole('region', { name: copy.delivery }).textContent).toContain(copy.notAccepted)
  expect(JSON.stringify(objective)).toBe(before)
})

it('removes old findings when the current round changes or a legacy runtime omits them', () => {
  const draw = (value: Objective) => <ObjectiveProgress objective={value} onReviewRequests={vi.fn()} onReviewWork={vi.fn()} snapshot={snapshot} />
  const view = render(draw({ ...objective, acceptance: { ...objective.acceptance!, conflicts: [] } }))
  expect(screen.getByText('Unmet criteria · 1').closest('details')).toBeNull()
  view.rerender(draw(objective))
  expect(screen.getByText('Published launch dates disagree.')).toBeTruthy()
  view.rerender(draw({ ...objective, acceptance: { ...objective.acceptance!, status: 'reviewing', criteriaResults: [{ ...objective.acceptance!.criteriaResults![0], satisfied: true }], conflicts: [] } }))
  expect(screen.queryByText('Unmet criteria · 1')).toBeNull()
  expect(screen.getByRole('region', { name: 'Delivery' }).textContent).toContain('has not been recorded')
  view.rerender(draw({ ...objective, acceptance: { ...objective.acceptance!, round: 2, criteriaResults: [], conflicts: [] } }))
  expect(screen.queryByRole('region', { name: 'Recorded review findings' })).toBeNull()
  view.rerender(draw({ ...objective, acceptance: { status: 'pending', round: 1, maxReplans: 2, criteria: [], summary: null, deliverableId: null } }))
  expect(screen.queryByRole('region', { name: 'Recorded review findings' })).toBeNull()
  expect(screen.getByRole('region', { name: 'Delivery' }).textContent).toContain('has not been recorded')
})
