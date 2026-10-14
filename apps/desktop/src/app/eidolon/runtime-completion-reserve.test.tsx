import { fireEvent, render, screen, within } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { completionReserveCopy } from '@/i18n/organization-completion-reserve'

import { RuntimeAcceptance } from './runtime-acceptance'
import type { Objective, OrganizationCompletionReserve } from './types'

const reserve: OrganizationCompletionReserve = {
  version: 1,
  policy: 'completion-v1',
  requestTypes: ['request.review', 'request.test_review', 'request.integrate', 'request.accept'],
  captured: { modelCalls: 6, tokens: 6000, configuredCostUsd: '0.30' },
  completionReserved: { modelCalls: 2, tokens: 2000, configuredCostUsd: '0.10' },
  protectedRemaining: { modelCalls: 4, tokens: 4000, configuredCostUsd: '0.20' },
  totalRemaining: { modelCalls: 5, tokens: 5000, configuredCostUsd: '0.25' },
  ordinaryRemaining: { modelCalls: 1, tokens: 1000, configuredCostUsd: '0.05' },
  admissionUsageKnown: true,
  unavailableReason: null
}

const objective: Objective = {
  id: 'objective',
  title: 'Budgeted work',
  description: 'Scope',
  ownerId: 'executive',
  status: 'active',
  source: 'runtime',
  createdAt: '2026-10-14T00:00:00Z',
  usage: {
    stages: 1,
    stageLimit: 12,
    inputTokens: 100,
    outputTokens: 200,
    usageComplete: false,
    perCallOutputLimit: 500,
    configuredCostLimitUsd: '1.00',
    completionReserve: reserve
  }
}

it('reads backend headroom without changing focus or disclosure and retains incomplete observations honestly', () => {
  const view = render(<RuntimeAcceptance objective={objective} onOpenEvidence={vi.fn()} />)
  const summary = screen.getByText('Execution limits and usage')
  const details = summary.closest('details')!
  expect(details.open).toBe(false)
  fireEvent.click(summary)
  summary.focus()
  const region = screen.getByRole('region', { name: 'Completion reserve' })
  const row = (label: string) => within(region).getByText(label).nextElementSibling?.textContent
  expect(row('Total remaining allowance')).toContain('Model calls: 5')
  expect(row('Remaining for ordinary work')).toContain('Configured USD: 0.05')
  expect(row('Unspent completion protection')).toContain('Reserved tokens: 4,000')
  expect(row('Completion calls already reserved')).toContain('Model calls: 2')
  expect(row('Owner-configured completion reserve')).toContain('Model calls: 6')
  expect(screen.getByText(/Observed token lower bounds/).nextElementSibling?.textContent).toBe('≥ 100 / ≥ 200')
  expect(within(region).getByText(/does not guarantee completion/)).toBeTruthy()
  expect(within(region).queryByRole('button')).toBeNull()

  view.rerender(
    <RuntimeAcceptance
      objective={{
        ...objective,
        usage: {
          ...objective.usage!,
          inputTokens: 0,
          outputTokens: 0,
          completionReserve: {
            ...reserve,
            totalRemaining: { modelCalls: 1, tokens: 1000, configuredCostUsd: '0.01' },
            ordinaryRemaining: { modelCalls: 0, tokens: 0, configuredCostUsd: '0' }
          }
        }
      }}
      onOpenEvidence={vi.fn()}
    />
  )
  expect(summary.ownerDocument.activeElement).toBe(summary)
  expect(details.open).toBe(true)
  expect(screen.getByText(/Current hard caps leave less allowance/)).toBeTruthy()
  expect(screen.getByText(/Observed token lower bounds/).nextElementSibling?.textContent).toBe('≥ 0 / ≥ 0')
  expect(row('Remaining for ordinary work')).toContain('Model calls: 0')

  view.rerender(
    <RuntimeAcceptance
      objective={{ ...objective, usage: { ...objective.usage!, completionReserve: undefined, usageComplete: true } }}
      onOpenEvidence={vi.fn()}
    />
  )
  expect(screen.queryByRole('region', { name: 'Completion reserve' })).toBeNull()
  expect(screen.getByText('Observed input / output tokens').nextElementSibling?.textContent).toBe('100 / 200')
})

it.each(['en', 'ja', 'zh', 'zh-hant', 'ar', 'ru'] as const)(
  'keeps unknown allowance distinct from zero in localized read-only copy (%s)',
  locale => {
    const copy = completionReserveCopy[locale]
    const unknown = { modelCalls: null, tokens: null, configuredCostUsd: null }
    render(
      <I18nProvider configClient={null} initialLocale={locale}>
        <RuntimeAcceptance
          objective={{
            ...objective,
            usage: {
              ...objective.usage!,
              completionReserve: {
                ...reserve,
                admissionUsageKnown: false,
                unavailableReason: 'legacy-usage-unknown',
                completionReserved: unknown,
                totalRemaining: unknown,
                ordinaryRemaining: unknown,
                protectedRemaining: unknown
              }
            }
          }}
          onOpenEvidence={vi.fn()}
        />
      </I18nProvider>
    )
    const region = screen.getByRole('region', { name: copy.heading, hidden: true })
    expect(within(region).getByText(copy.totalRemaining).nextElementSibling?.textContent).toContain(
      `${copy.modelCalls}: ${copy.unknown}`
    )
    expect(within(region).getByText(copy.completionReserved).nextElementSibling?.textContent).toContain(`${copy.modelCalls}: ${copy.unknown}`)
    expect(within(region).getByText(copy.unavailable)).toBeTruthy()
    expect(within(region).getByText(copy.note)).toBeTruthy()

    if (locale !== 'en') {
      for (const key of Object.keys(copy) as Array<keyof typeof copy>) {
        expect(copy[key]).not.toBe(completionReserveCopy.en[key])
      }
    }
  }
)
