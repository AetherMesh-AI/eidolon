import { fireEvent, render, screen, within } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { adaptivePacingCopy } from '@/i18n/organization-adaptive-pacing'
import { completionReserveCopy } from '@/i18n/organization-completion-reserve'

import { RuntimeAcceptance } from './runtime-acceptance'
import type { Objective, OrganizationAdaptivePacing } from './types'

const pacing: OrganizationAdaptivePacing = {
  version: 1,
  policy: 'local-stage-start-v1',
  enabled: true,
  scope: 'objective',
  mode: 'conserve',
  reason: 'manual_observation',
  evaluatedAt: '2026-10-14T07:00:00Z',
  deferUntil: '2026-10-14T07:01:00Z',
  maxInflight: 1,
  minIntervalSeconds: 60,
  remainingPercent: 12.5,
  remaining: { modelCalls: 3, tokens: 3000, configuredCostUsd: '0.10' },
  protectedRemaining: { modelCalls: 4, tokens: 4000, configuredCostUsd: '0.20' },
  observation: {
    revision: 1,
    remainingPercent: 12.5,
    asOf: '2026-10-14T06:00:00Z',
    expiresAt: '2026-10-14T08:00:00Z',
    resetAt: '2026-10-14T09:00:00Z',
    status: 'fresh',
    source: 'owner_manual',
    scope: 'objective',
    advisory: true
  },
  limitation: 'stage_start_only_no_quota_inference'
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
    adaptivePacing: pacing
  }
}

it('updates read-only pacing evidence without stealing focus, opening disclosure or inferring a reset refill', () => {
  const openEvidence = vi.fn()
  const view = render(<RuntimeAcceptance objective={objective} onOpenEvidence={openEvidence} />)
  const summary = screen.getByText('Execution limits and usage')
  const details = summary.closest('details')!
  expect(details.open).toBe(false)
  fireEvent.click(summary)
  summary.focus()
  const region = screen.getByRole('region', { name: 'Adaptive pacing' })
  const row = (label: string) => within(region).getByText(label).nextElementSibling
  expect(row('Pacing state')?.textContent).toBe('Conserve')
  expect(row('Pacing signal (%)')?.textContent).toBe('12.5')
  expect(row('Maximum ordinary attempts in flight')?.textContent).toBe('1')
  expect(row('Minimum start interval (seconds)')?.textContent).toBe('60')
  expect(row('Next ordinary start no earlier than')?.querySelector('time')?.dateTime).toBe(pacing.deferUntil)
  expect(row('Evaluated at')?.querySelector('time')?.dateTime).toBe(pacing.evaluatedAt)
  expect(row('Remaining for ordinary work')?.textContent).toContain('Configured USD: 0.10')
  expect(row('Unspent completion protection')?.textContent).toContain('Reserved tokens: 4,000')
  expect(row('Source')?.textContent).toContain('advisory only')
  expect(row('Observed at')?.querySelector('time')?.dateTime).toBe(pacing.observation!.asOf)
  expect(row('Expires at')?.querySelector('time')?.dateTime).toBe(pacing.observation!.expiresAt)
  expect(row('Reported reset at')?.querySelector('time')?.dateTime).toBe(pacing.observation!.resetAt)
  expect(within(region).getByText(/not measured shared provider quota/)).toBeTruthy()
  expect(within(region).getByText(/completion stages bypass pacing/)).toBeTruthy()

  const update = (adaptivePacing: OrganizationAdaptivePacing | undefined) =>
    view.rerender(
      <RuntimeAcceptance
        objective={{ ...objective, usage: { ...objective.usage!, adaptivePacing } }}
        onOpenEvidence={openEvidence}
      />
    )

  for (const [status, evaluatedAt] of [
    ['expired', '2026-10-14T08:00:00Z'],
    ['reset_unconfirmed', '2026-10-14T09:00:00Z']
  ] as const) {
    update({
      ...pacing,
      evaluatedAt,
      deferUntil: null,
      reason: 'stale_observation',
      observation: { ...pacing.observation!, status }
    })
    expect(row('Observation state')?.textContent).toBe(adaptivePacingCopy.en[status])
    expect(row('Remaining for ordinary work')?.textContent).toContain('Model calls: 3')
    expect(row('Pacing signal (%)')?.textContent).toBe('12.5')
    expect(row('Next ordinary start no earlier than')?.textContent).toBe(adaptivePacingCopy.en.noDeferral)
    expect(details.open).toBe(true)
    expect(summary.ownerDocument.activeElement).toBe(summary)
  }

  update({
    ...pacing,
    enabled: false,
    mode: 'off',
    reason: 'disabled',
    minIntervalSeconds: 0,
    deferUntil: null,
    observation: null
  })
  expect(row('Pacing state')?.textContent).toBe('Off (default)')
  expect(within(region).getByText(adaptivePacingCopy.en.noObservation)).toBeTruthy()
  expect(within(region).queryByRole('button')).toBeNull()
  expect(within(region).queryByRole('textbox')).toBeNull()
  expect(within(region).queryByRole('progressbar')).toBeNull()
  expect(openEvidence).not.toHaveBeenCalled()
  update(undefined)
  expect(screen.queryByRole('region', { name: 'Adaptive pacing' })).toBeNull()
  expect(details.open).toBe(true)
})

it.each(['en', 'ja', 'zh', 'zh-hant', 'ar', 'ru'] as const)(
  'distinguishes unknown local capacity from a zero advisory signal in every locale (%s)',
  locale => {
    const copy = adaptivePacingCopy[locale]
    const reserveCopy = completionReserveCopy[locale]
    const unknown = { modelCalls: null, tokens: null, configuredCostUsd: null }
    render(
      <I18nProvider configClient={null} initialLocale={locale}>
        <RuntimeAcceptance
          objective={{
            ...objective,
            usage: {
              ...objective.usage!,
              adaptivePacing: {
                ...pacing,
                mode: 'unknown',
                reason: 'budget_unknown',
                remainingPercent: null,
                remaining: unknown,
                protectedRemaining: unknown,
                minIntervalSeconds: 0,
                deferUntil: null,
                observation: { ...pacing.observation!, remainingPercent: 0, resetAt: null }
              }
            }
          }}
          onOpenEvidence={vi.fn()}
        />
      </I18nProvider>
    )
    const region = screen.getByRole('region', { name: copy.heading, hidden: true })
    const row = (label: string) => within(region).getByText(label).nextElementSibling?.textContent
    expect(row(copy.remainingPercent)).toBe(copy.unknown)
    expect(row(reserveCopy.ordinaryRemaining)).toContain(`${reserveCopy.modelCalls}: ${reserveCopy.unknown}`)
    expect(row(copy.reportedPercent)).toBe(new Intl.NumberFormat(locale).format(0))
    expect(row(copy.resetAt)).toBe(copy.noReset)
    expect(row(copy.observationState)).toBe(copy.fresh)
    expect(within(region).getByText(copy.budget_unknown)).toBeTruthy()
    expect(within(region).getByText(copy.note)).toBeTruthy()
    expect(within(region).getByText(copy.boundary)).toBeTruthy()

    if (locale !== 'en') {
      for (const key of Object.keys(copy) as Array<keyof typeof copy>) {
        expect(copy[key]).not.toBe(adaptivePacingCopy.en[key])
      }
    }
  }
)
