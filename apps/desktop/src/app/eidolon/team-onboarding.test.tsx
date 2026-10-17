import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { I18nProvider, useI18n } from '@/i18n/context'
import { teamOnboardingCopy } from '@/i18n/organization-team-onboarding'
import type { Locale } from '@/i18n/types'
import { $desktopOnboarding, closeManualOnboarding } from '@/store/onboarding'

import { TeamOnboardingDialog, TeamOnboardingEntry } from './team-onboarding'
import type { TeamOnboardingClient, TeamPreview, TeamSetup } from './team-onboarding-client'
import type { RuntimeOrganizationAdapter } from './types'

const team = {
  executive: { name: 'Lead', purpose: 'Coordinate', responsibilities: ['Review'] },
  members: [
    {
      id: 'manager',
      name: 'Research lead',
      role: 'Manager' as const,
      manager_id: 'executive',
      team: 'Research',
      purpose: 'Find evidence',
      responsibilities: ['Research'],
      capabilities: ['request.plan']
    }
  ]
}

const readiness = {
  status: 'unchecked' as const,
  blockers: [],
  warnings: ['new_member_routes_unassigned'],
  modelRouting: 'unchanged' as const,
  backgroundEnabled: false
}

const setup: TeamSetup = {
  capabilityVersion: 1,
  inputImprovementVersion: 1,
  state: 'available',
  organizationId: 'organization',
  sourceRevision: 'revision',
  presets: [{ id: 'research-writing', name: 'Research', team }],
  readiness
}

const preview: TeamPreview = {
  capabilityVersion: 1,
  organizationId: 'organization',
  sourceRevision: 'revision',
  previewVersion: 'preview',
  team,
  executiveModelMode: 'execFixed',
  supportAgents: [{ id: 'reviewer', name: 'Saved reviewer name', role: 'Reviewer' }],
  readiness,
  warnings: []
}

function harness(initialSetup = setup, locale: Locale = 'en') {
  const client = {
    current: () => true,
    setup: vi.fn().mockResolvedValue(setup),
    generate: vi.fn().mockResolvedValue(preview),
    improve: vi.fn().mockResolvedValue({
      capabilityVersion: 1,
      organizationId: 'organization',
      sourceRevision: 'revision',
      field: 'jobs',
      suggestion: 'Research product changes weekly and summarize sources.',
      assumptions: ['A weekly cadence is suggested.']
    }),
    preview: vi.fn().mockResolvedValue(preview),
    create: vi.fn().mockResolvedValue({ state: 'created', readiness }),
    dispose: vi.fn()
  } as unknown as TeamOnboardingClient

  const onClose = vi.fn(),
    onCreated = vi.fn(),
    onReturnFocus = vi.fn()

  const view = render(
    <MemoryRouter>
      <I18nProvider configClient={null} initialLocale={locale}>
        <TeamOnboardingDialog
          client={client}
          onClose={onClose}
          onCreated={onCreated}
          onReturnFocus={onReturnFocus}
          profile="default"
          setup={initialSetup}
        />
      </I18nProvider>
    </MemoryRouter>
  )

  return { client, onClose, onCreated, view }
}

it('requires explicit generation and explicit creation; edits revoke the validated preview', async () => {
  const h = harness()
  expect(h.client.generate).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('Work to delegate'), { target: { value: 'Research' } })
  fireEvent.change(screen.getByLabelText('What success looks like'), { target: { value: 'Reliable evidence' } })
  fireEvent.click(screen.getByRole('button', { name: /Generate team/ }))
  await screen.findByRole('button', { name: 'Create team' })
  expect(h.client.create).not.toHaveBeenCalled()
  expect(screen.getByText('Saved reviewer name')).toBeTruthy()
  expect(screen.getByText(teamOnboardingCopy.en.inheritedRoutes)).toBeTruthy()
  expect(screen.queryByText('new_member_routes_unassigned')).toBeNull()
  fireEvent.change(screen.getAllByLabelText('Name')[0], { target: { value: 'Updated lead' } })
  expect(screen.queryByRole('button', { name: 'Create team' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Review changes' }))
  await screen.findByRole('button', { name: 'Create team' })
  fireEvent.click(screen.getByRole('button', { name: 'Create team' }))
  await waitFor(() => expect(h.onCreated).toHaveBeenCalledTimes(1))
  expect(h.client.create).toHaveBeenCalledTimes(1)
  expect(screen.getByText(/No work has started/)).toBeTruthy()
})
it('cancel during generation ignores its late response and never creates a team', async () => {
  const h = harness()
  let finish!: (value: TeamPreview) => void
  vi.mocked(h.client.generate).mockImplementation(
    () =>
      new Promise(resolve => {
        finish = resolve
      })
  )
  fireEvent.change(screen.getByLabelText('Work to delegate'), { target: { value: 'Research' } })
  fireEvent.change(screen.getByLabelText('What success looks like'), { target: { value: 'Evidence' } })
  fireEvent.click(screen.getByRole('button', { name: /Generate team/ }))
  fireEvent.click(screen.getByRole('button', { name: /Generate team/ }))
  expect(h.client.generate).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(h.onClose).toHaveBeenCalledTimes(1)
  h.view.unmount()
  await act(async () => finish(preview))
  expect(h.client.create).not.toHaveBeenCalled()
  expect(h.onCreated).not.toHaveBeenCalled()
})

it('keeps manual setup usable with blocked model readiness', async () => {
  const h = harness({ ...setup, readiness: { ...readiness, status: 'blocked', blockers: ['credential_missing'] } })
  expect((screen.getByRole('button', { name: /Generate team/ }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByText('Set up manually'))
  const manual = screen.getByRole('button', { name: 'Start with an empty team' })
  expect(manual.closest('fieldset[disabled]')).toBeNull()
  expect((manual as HTMLButtonElement).disabled).toBe(false)
  fireEvent.click(manual)
  expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('')
  fireEvent.click(screen.getByRole('button', { name: 'Add manager' }))
  expect(screen.getAllByLabelText('Name')).toHaveLength(2)
  expect(h.client.generate).not.toHaveBeenCalled()
  expect(h.client.create).not.toHaveBeenCalled()
})
it.each([
  ['subscription_generation_unsupported', /selected Codex subscription/],
  ['codex_app_server', /selected Codex app-server/],
  ['generation_route_unsupported', /selected model route/]
])('explains %s while keeping the owner’s work editable without inference', (reason, explanation) => {
  const h = harness({ ...setup, readiness: { ...readiness, status: 'blocked', blockers: [reason as string] } })
  expect(screen.getByRole('status').textContent).toMatch(explanation as RegExp)
  expect(screen.queryByText(teamOnboardingCopy.en.modelRequired)).toBeNull()
  const jobs = screen.getByLabelText(teamOnboardingCopy.en.jobs)
  const goals = screen.getByLabelText(teamOnboardingCopy.en.goals)
  expect(jobs).toHaveProperty('disabled', false)
  expect(goals).toHaveProperty('disabled', false)
  expect(
    screen.getByRole('textbox', { name: teamOnboardingCopy.en.jobs, description: /tasks or responsibilities/ })
  ).toBe(jobs)
  expect(screen.getByRole('textbox', { name: teamOnboardingCopy.en.goals, description: /successful result/ })).toBe(
    goals
  )
  expect(
    screen.getByRole('textbox', { name: teamOnboardingCopy.en.namingStyle, description: /optional/i })
  ).toBeTruthy()
  fireEvent.change(jobs, { target: { value: 'Research weekly product changes' } })
  expect(jobs).toHaveProperty('value', 'Research weekly product changes')
  expect(screen.getByRole('button', { name: /Generate team/ })).toHaveProperty('disabled', true)
  expect(screen.getByRole('button', { name: teamOnboardingCopy.en.improveJobs })).toHaveProperty('disabled', true)
  expect(screen.getByRole('button', { name: teamOnboardingCopy.en.improveJobs }).getAttribute('aria-describedby')).toBe(
    screen.getByRole('status').id
  )
  expect(h.client.improve).not.toHaveBeenCalled()
  expect(h.client.generate).not.toHaveBeenCalled()
  expect(h.client.create).not.toHaveBeenCalled()
})

it('reviews full-context improvements without replacing originals until Apply, and preserves text on Discard', async () => {
  const h = harness()
  const copy = teamOnboardingCopy.en
  const jobs = screen.getByRole('textbox', { name: copy.jobs })
  const goals = screen.getByRole('textbox', { name: copy.goals })
  expect(screen.getByRole('button', { name: copy.improveJobs })).toHaveProperty('disabled', true)
  fireEvent.change(jobs, { target: { value: 'Research products' } })
  fireEvent.change(goals, { target: { value: 'Reliable evidence' } })
  fireEvent.change(screen.getByRole('textbox', { name: copy.namingStyle }), { target: { value: 'Astronomy names' } })
  fireEvent.click(screen.getByRole('button', { name: copy.improveJobs }))
  await screen.findByRole('region', { name: copy.reviewSuggestion })
  expect(h.client.improve).toHaveBeenCalledWith(
    setup,
    expect.objectContaining({
      field: 'jobs',
      jobs: 'Research products',
      goals: 'Reliable evidence',
      namingStyle: 'Astronomy names'
    })
  )
  expect(jobs).toHaveProperty('value', 'Research products')
  expect(screen.getByText('A weekly cadence is suggested.')).toBeTruthy()
  expect(screen.getByRole('button', { name: /Generate team/ })).toHaveProperty('disabled', true)
  fireEvent.change(screen.getByRole('textbox', { name: copy.suggestion }), {
    target: { value: 'Research product changes monthly.' }
  })
  screen.getByRole('button', { name: copy.applySuggestion }).focus()
  fireEvent.click(screen.getByRole('button', { name: copy.applySuggestion }))
  expect(jobs.ownerDocument.activeElement).toBe(jobs)
  expect(jobs).toHaveProperty('value', 'Research product changes monthly.')
  expect(goals).toHaveProperty('value', 'Reliable evidence')
  expect(screen.queryByRole('region', { name: copy.reviewSuggestion })).toBeNull()
  vi.mocked(h.client.improve).mockResolvedValueOnce({
    capabilityVersion: 1,
    organizationId: 'organization',
    sourceRevision: 'revision',
    field: 'goals',
    suggestion: 'Source-linked summaries ready for review.',
    assumptions: []
  })
  fireEvent.click(screen.getByRole('button', { name: copy.improveGoals }))
  await screen.findByRole('region', { name: copy.reviewSuggestion })
  expect(screen.getByText(copy.noAssumptions)).toBeTruthy()
  screen.getByRole('button', { name: copy.discardSuggestion }).focus()
  fireEvent.click(screen.getByRole('button', { name: copy.discardSuggestion }))
  expect(goals.ownerDocument.activeElement).toBe(goals)
  expect(goals).toHaveProperty('value', 'Reliable evidence')
  expect(jobs).toHaveProperty('value', 'Research product changes monthly.')
  expect(h.client.generate).not.toHaveBeenCalled()
  expect(h.client.create).not.toHaveBeenCalled()
})

it('reconciles Improve retries and suppresses responses after changed text, owner or dismissal', async () => {
  const h = harness()
  const copy = teamOnboardingCopy.en
  fireEvent.change(screen.getByLabelText(copy.jobs), { target: { value: 'Research' } })
  vi.mocked(h.client.improve).mockRejectedValueOnce(new Error('timeout'))
  fireEvent.click(screen.getByRole('button', { name: copy.improveJobs }))
  fireEvent.click(screen.getByRole('button', { name: copy.improveJobs }))
  await screen.findByRole('alert')
  expect(h.client.improve).toHaveBeenCalledTimes(1)
  let finish!: (value: Awaited<ReturnType<TeamOnboardingClient['improve']>>) => void
  vi.mocked(h.client.improve).mockImplementation(
    () =>
      new Promise(resolve => {
        finish = resolve
      })
  )
  fireEvent.click(screen.getByRole('button', { name: copy.improveJobs }))
  expect(vi.mocked(h.client.improve).mock.calls[0][1].idempotencyKey).toBe(
    vi.mocked(h.client.improve).mock.calls[1][1].idempotencyKey
  )

  const result = {
    capabilityVersion: 1 as const,
    organizationId: 'organization',
    sourceRevision: 'revision',
    field: 'jobs' as const,
    suggestion: 'Old wording',
    assumptions: []
  }

  // An input event queued before the pending fieldset became disabled must also retire its result.
  fireEvent.change(screen.getByLabelText(copy.goals), { target: { value: 'Newer context' } })
  await act(async () => finish(result))
  expect(screen.queryByRole('region', { name: copy.reviewSuggestion })).toBeNull()
  expect(screen.getByLabelText(copy.jobs)).toHaveProperty('value', 'Research')
  expect(screen.getByLabelText(copy.goals)).toHaveProperty('value', 'Newer context')
  fireEvent.click(screen.getByRole('button', { name: copy.improveJobs }))
  h.client.current = () => false
  await act(async () => finish(result))
  expect(screen.queryByRole('region', { name: copy.reviewSuggestion })).toBeNull()
  h.client.current = () => true
  fireEvent.click(screen.getByRole('button', { name: copy.improveJobs }))
  fireEvent.click(screen.getByRole('button', { name: copy.cancel }))
  h.view.unmount()
  await act(async () => finish(result))
  expect(h.onClose).toHaveBeenCalledTimes(1)
  expect(h.client.generate).not.toHaveBeenCalled()
  expect(h.onCreated).not.toHaveBeenCalled()
})

it('leaves legacy-runtime descriptions editable without advertising unsupported Improve calls', () => {
  const h = harness({ ...setup, inputImprovementVersion: undefined })
  fireEvent.change(screen.getByLabelText(teamOnboardingCopy.en.jobs), { target: { value: 'Research' } })
  expect(screen.getByRole('button', { name: teamOnboardingCopy.en.improveJobs })).toHaveProperty('disabled', true)
  expect(screen.getByText(teamOnboardingCopy.en.improveUnavailable)).toBeTruthy()
  expect(h.client.improve).not.toHaveBeenCalled()
})

it('uses a new intent only for deliberate Regenerate and retains the proposal through recovery', async () => {
  const available = { status: 'available' as const, remainingCalls: 2 }
  const h = harness({ ...setup, generationAllowance: available })
  const copy = teamOnboardingCopy.en
  vi.mocked(h.client.generate).mockResolvedValueOnce({ ...preview, generationAllowance: available })
  fireEvent.change(screen.getByLabelText(copy.jobs), { target: { value: 'Research' } })
  fireEvent.change(screen.getByLabelText(copy.goals), { target: { value: 'Evidence' } })
  fireEvent.click(screen.getByRole('button', { name: copy.generate }))
  await screen.findByRole('button', { name: copy.create })
  expect(screen.getByText(copy.reviewCoverage)).toBeTruthy()
  expect(screen.getByText(copy.regenerateHelp)).toBeTruthy()
  let fail!: (error: Error) => void
  vi.mocked(h.client.generate).mockImplementationOnce(
    () =>
      new Promise((_resolve, reject) => {
        fail = reject
      })
  )
  vi.mocked(h.client.setup).mockResolvedValueOnce({
    ...setup,
    generationAllowance: { status: 'uncertain', remainingCalls: 1 }
  })
  fireEvent.click(screen.getByRole('button', { name: copy.regenerate }))
  fireEvent.click(screen.getByRole('button', { name: copy.retryRegeneration }))
  expect(h.client.generate).toHaveBeenCalledTimes(2)
  expect(screen.getAllByLabelText(copy.name)[0]).toHaveProperty('value', 'Lead')
  expect(screen.getByRole('button', { name: copy.create })).toHaveProperty('disabled', true)
  await act(async () => fail(new Error('timeout')))
  await screen.findByText(copy.allowanceUncertain)
  expect(screen.getAllByLabelText(copy.name)[0]).toHaveProperty('value', 'Lead')

  const replacement = {
    ...preview,
    team: { ...team, executive: { ...team.executive, name: 'New lead' } },
    generationAllowance: { status: 'exhausted' as const, remainingCalls: 0 }
  }

  vi.mocked(h.client.generate).mockResolvedValueOnce(replacement)
  fireEvent.click(screen.getByRole('button', { name: copy.retryRegeneration }))
  await waitFor(() => expect(screen.getAllByLabelText(copy.name)[0]).toHaveProperty('value', 'New lead'))
  const calls = vi.mocked(h.client.generate).mock.calls
  expect(calls[1][1].idempotencyKey).not.toBe(calls[0][1].idempotencyKey)
  expect(calls[2]).toEqual(calls[1])
  expect(screen.getByRole('button', { name: copy.regenerate })).toHaveProperty('disabled', true)
  expect(screen.getByText(copy.allowanceExhausted)).toBeTruthy()
  expect(h.client.create).not.toHaveBeenCalled()
})

it.each(['exhausted', 'running', 'uncertain', 'blocked'] as const)(
  'blocks new Improve and Generate calls when authoritative allowance is %s while preserving manual setup',
  status => {
    const h = harness({ ...setup, generationAllowance: { status, remainingCalls: status === 'exhausted' ? 0 : 1 } })
    const copy = teamOnboardingCopy.en
    fireEvent.change(screen.getByLabelText(copy.jobs), { target: { value: 'Research' } })
    fireEvent.change(screen.getByLabelText(copy.goals), { target: { value: 'Evidence' } })
    expect(screen.getByRole('button', { name: copy.improveJobs })).toHaveProperty('disabled', true)
    expect(screen.getByRole('button', { name: copy.generate })).toHaveProperty('disabled', true)
    fireEvent.click(screen.getByText(copy.manual))
    expect(screen.getByRole('button', { name: copy.manualStart })).toHaveProperty('disabled', false)
    expect(h.client.improve).not.toHaveBeenCalled()
    expect(h.client.generate).not.toHaveBeenCalled()
  }
)

it('preserves newer team edits and refreshes verified allowance when an earlier regeneration is recovered', async () => {
  const available = { status: 'available' as const, remainingCalls: 2 }
  const h = harness({ ...setup, generationAllowance: available })
  const copy = teamOnboardingCopy.en
  vi.mocked(h.client.generate).mockResolvedValueOnce({ ...preview, generationAllowance: available })
  fireEvent.change(screen.getByLabelText(copy.jobs), { target: { value: 'Research' } })
  fireEvent.change(screen.getByLabelText(copy.goals), { target: { value: 'Evidence' } })
  fireEvent.click(screen.getByRole('button', { name: copy.generate }))
  await screen.findByRole('button', { name: copy.create })
  vi.mocked(h.client.generate).mockRejectedValueOnce(new Error('acknowledgement lost'))
  vi.mocked(h.client.setup).mockRejectedValueOnce(new Error('read unavailable'))
  fireEvent.click(screen.getByRole('button', { name: copy.regenerate }))
  await screen.findByText(copy.allowanceBlocked)
  fireEvent.change(screen.getAllByLabelText(copy.name)[0], { target: { value: 'My newer edit' } })
  vi.mocked(h.client.generate).mockResolvedValueOnce({
    ...preview,
    team: { ...team, executive: { ...team.executive, name: 'Recovered older proposal' } },
    generationAllowance: { status: 'available', remainingCalls: 1 }
  })
  fireEvent.click(screen.getByRole('button', { name: copy.retryRegeneration }))
  await screen.findByText(copy.previousDraftPreserved)
  expect(screen.getAllByLabelText(copy.name)[0]).toHaveProperty('value', 'My newer edit')
  expect(screen.queryByRole('button', { name: copy.create })).toBeNull()
  expect(screen.queryByText(copy.allowanceBlocked)).toBeNull()
  expect(screen.getByRole('button', { name: copy.regenerate })).toHaveProperty('disabled', false)
  const calls = vi.mocked(h.client.generate).mock.calls
  expect(calls[2]).toEqual(calls[1])
  expect(h.client.create).not.toHaveBeenCalled()
})
it('retries uncertain generation with the same idempotency key', async () => {
  const h = harness()
  vi.mocked(h.client.generate).mockRejectedValueOnce(new Error('timeout'))
  fireEvent.change(screen.getByLabelText('Work to delegate'), { target: { value: 'Research' } })
  fireEvent.change(screen.getByLabelText('What success looks like'), { target: { value: 'Evidence' } })
  fireEvent.click(screen.getByRole('button', { name: /Generate team/ }))
  await screen.findByRole('alert')
  fireEvent.click(screen.getByRole('button', { name: /Generate team/ }))
  await screen.findByRole('button', { name: 'Create team' })
  expect(vi.mocked(h.client.generate).mock.calls[0][1].idempotencyKey).toBe(
    vi.mocked(h.client.generate).mock.calls[1][1].idempotencyKey
  )
})

it('retains the exact creation intent and locks edits after an uncertain acknowledgement', async () => {
  const h = harness()
  vi.mocked(h.client.create).mockRejectedValueOnce(new Error('connection lost'))
  fireEvent.click(screen.getByText('Set up manually'))
  fireEvent.click(screen.getByRole('button', { name: 'Research and writing' }))
  fireEvent.click(screen.getByRole('button', { name: 'Review changes' }))
  await screen.findByRole('button', { name: 'Create team' })
  fireEvent.click(screen.getByRole('button', { name: 'Create team' }))
  await screen.findByRole('alert')
  expect((screen.getByRole('button', { name: 'Back' }) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.getAllByLabelText('Name')[0].closest('fieldset[disabled]')).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Create team' }))
  await waitFor(() => expect(h.onCreated).toHaveBeenCalledTimes(1))
  expect(vi.mocked(h.client.create).mock.calls[1]).toEqual(vi.mocked(h.client.create).mock.calls[0])
})

it.each(Object.keys(teamOnboardingCopy) as Locale[])('supports localized manual review in %s', locale => {
  const h = harness(setup, locale)
  const copy = teamOnboardingCopy[locale]
  fireEvent.click(screen.getByText(copy.manual))
  fireEvent.click(screen.getByRole('button', { name: copy.researchPreset }))
  expect(screen.getByText(copy.review)).toBeTruthy()
  expect(screen.getByText(copy.capabilityPlan)).toBeTruthy()
  expect(screen.getByText(copy.capabilityIntegrate)).toBeTruthy()
  expect(screen.queryByText('request.plan')).toBeNull()
  expect(h.client.create).not.toHaveBeenCalled()
})

it('keeps blocked readiness explicit after manual creation', async () => {
  const h = harness({ ...setup, readiness: { ...readiness, status: 'blocked', blockers: ['model_not_configured'] } })
  vi.mocked(h.client.create).mockResolvedValue({
    capabilityVersion: 1,
    state: 'created',
    organizationId: 'organization',
    team,
    readiness: { ...readiness, status: 'blocked', blockers: ['model_not_configured'] },
    receipt: { idempotencyKey: 'receipt', previewVersion: 'preview' }
  })
  fireEvent.click(screen.getByText('Set up manually'))
  fireEvent.click(screen.getByRole('button', { name: 'Research and writing' }))
  fireEvent.click(screen.getByRole('button', { name: 'Review changes' }))
  await screen.findByRole('button', { name: 'Create team' })
  fireEvent.click(screen.getByRole('button', { name: 'Create team' }))
  await screen.findByText(teamOnboardingCopy.en.modelBlocked)
  expect(screen.queryByText('model_not_configured')).toBeNull()
  expect(h.client.generate).not.toHaveBeenCalled()
})

vi.mock('./use-organization-metadata', () => ({
  useOrganizationMetadata: () => ({
    client: {},
    scope: { key: 'entry', ownerKey: 'owner', eligible: true, profile: 'default' },
    view: { descriptor: { state: 'unconfigured', metadata: null } }
  }),
  readOrganizationMetadataScope: vi.fn()
}))

function EntryBesideExistingNamesControl() {
  const { t } = useI18n()

  return (
    <>
      <button>{t.organizationMetadata.edit}</button>
      <TeamOnboardingEntry adapter={{} as RuntimeOrganizationAdapter} />
    </>
  )
}

it.each(Object.keys(teamOnboardingCopy) as Locale[])(
  'gives the starter entry a distinct accessible action in %s',
  locale => {
    render(
      <MemoryRouter>
        <I18nProvider configClient={null} initialLocale={locale}>
          <EntryBesideExistingNamesControl />
        </I18nProvider>
      </MemoryRouter>
    )
    expect(screen.getByRole('button', { name: teamOnboardingCopy[locale].setupOrganization })).toBeTruthy()
    const buttons = screen.getAllByRole('button')
    expect(buttons).toHaveLength(2)
    expect(buttons[0].textContent).not.toBe(buttons[1].textContent)
  }
)

it('returns from model setup to the same draft and rechecks readiness exactly once', async () => {
  const original = $desktopOnboarding.get()
  const h = harness()

  try {
    fireEvent.change(screen.getByLabelText('Work to delegate'), { target: { value: 'Keep my work description' } })
    fireEvent.change(screen.getByLabelText('What success looks like'), { target: { value: 'Keep my outcome' } })
    fireEvent.click(screen.getByRole('button', { name: 'Model settings' }))
    expect(screen.queryByRole('dialog', { name: 'Build your starter team' })).toBeNull()
    act(() => closeManualOnboarding())
    await screen.findByRole('dialog', { name: 'Build your starter team' })
    await waitFor(() => expect(h.client.setup).toHaveBeenCalledTimes(1))
    expect(screen.getByLabelText('Work to delegate')).toHaveProperty('value', 'Keep my work description')
    expect(screen.getByLabelText('What success looks like')).toHaveProperty('value', 'Keep my outcome')
    expect(h.client.generate).not.toHaveBeenCalled()
    expect(h.client.create).not.toHaveBeenCalled()
    expect(h.onClose).not.toHaveBeenCalled()
  } finally {
    h.view.unmount()
    act(() => $desktopOnboarding.set(original))
  }
})

it.each(Object.keys(teamOnboardingCopy) as Locale[])(
  'keeps model configuration, execution verification and check failure distinct in %s',
  async locale => {
    const original = $desktopOnboarding.get()
    const copy = teamOnboardingCopy[locale]

    const h = harness(
      { ...setup, readiness: { ...readiness, status: 'blocked', blockers: ['credential_missing'] } },
      locale
    )

    try {
      expect(screen.getByText(copy.modelRequired)).toBeTruthy()
      expect(screen.queryByText(copy.readinessUnchecked)).toBeNull()
      fireEvent.click(screen.getByRole('button', { name: copy.modelSettings }))
      act(() => closeManualOnboarding())
      await screen.findByText(copy.readinessUnchecked)
      expect(screen.queryByText(copy.modelRequired)).toBeNull()
      expect(screen.queryByRole('button', { name: copy.connectModel })).toBeNull()
      fireEvent.change(screen.getByLabelText(copy.jobs), { target: { value: 'Retained job' } })
      fireEvent.change(screen.getByLabelText(copy.goals), { target: { value: 'Retained outcome' } })
      vi.mocked(h.client.setup).mockRejectedValueOnce(new Error('timeout'))
      fireEvent.click(screen.getByRole('button', { name: copy.checkConfiguration }))
      await screen.findByText(copy.checkFailed)
      expect(screen.getByLabelText(copy.jobs)).toHaveProperty('value', 'Retained job')
      expect(screen.getByLabelText(copy.goals)).toHaveProperty('value', 'Retained outcome')
      fireEvent.click(screen.getByRole('button', { name: copy.checkConfiguration }))
      await waitFor(() => expect(screen.queryByText(copy.checkFailed)).toBeNull())
      await waitFor(() => expect(h.client.setup).toHaveBeenCalledTimes(3))
      expect(screen.getByText(copy.readinessUnchecked)).toBeTruthy()
      expect(h.client.generate).not.toHaveBeenCalled()
      expect(h.client.create).not.toHaveBeenCalled()
    } finally {
      h.view.unmount()
      act(() => $desktopOnboarding.set(original))
    }
  }
)

it('ignores a model-return check after the dialog is dismissed', async () => {
  const original = $desktopOnboarding.get()
  const h = harness()
  let finish!: (value: TeamSetup) => void
  vi.mocked(h.client.setup).mockImplementation(
    () =>
      new Promise(resolve => {
        finish = resolve
      })
  )

  try {
    fireEvent.click(screen.getByRole('button', { name: 'Model settings' }))
    act(() => closeManualOnboarding())
    await waitFor(() => expect(h.client.setup).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    h.view.unmount()
    await act(async () => finish({ ...setup, readiness: { ...readiness, status: 'blocked' } }))
    expect(h.onClose).toHaveBeenCalledTimes(1)
    expect(h.client.generate).not.toHaveBeenCalled()
    expect(h.onCreated).not.toHaveBeenCalled()
  } finally {
    h.view.unmount()
    act(() => $desktopOnboarding.set(original))
  }
})
