import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'
import { teamOnboardingCopy } from '@/i18n/organization-team-onboarding'
import type { Locale } from '@/i18n/types'

import { I18nProvider } from '@/i18n/context'

import { TeamOnboardingDialog } from './team-onboarding'
import type { TeamOnboardingClient, TeamPreview, TeamSetup } from './team-onboarding-client'

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
  fireEvent.change(screen.getByLabelText('Jobs to handle'), { target: { value: 'Research' } })
  fireEvent.change(screen.getByLabelText('Goals and outcomes'), { target: { value: 'Reliable evidence' } })
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
  fireEvent.change(screen.getByLabelText('Jobs to handle'), { target: { value: 'Research' } })
  fireEvent.change(screen.getByLabelText('Goals and outcomes'), { target: { value: 'Evidence' } })
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
  fireEvent.click(screen.getByText('Set up manually · Not recommended'))
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
it('retries uncertain generation with the same idempotency key', async () => {
  const h = harness()
  vi.mocked(h.client.generate).mockRejectedValueOnce(new Error('timeout'))
  fireEvent.change(screen.getByLabelText('Jobs to handle'), { target: { value: 'Research' } })
  fireEvent.change(screen.getByLabelText('Goals and outcomes'), { target: { value: 'Evidence' } })
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
  fireEvent.click(screen.getByText('Set up manually · Not recommended'))
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
  fireEvent.click(screen.getByText('Set up manually · Not recommended'))
  fireEvent.click(screen.getByRole('button', { name: 'Research and writing' }))
  fireEvent.click(screen.getByRole('button', { name: 'Review changes' }))
  await screen.findByRole('button', { name: 'Create team' })
  fireEvent.click(screen.getByRole('button', { name: 'Create team' }))
  await screen.findByText(teamOnboardingCopy.en.modelBlocked)
  expect(screen.queryByText('model_not_configured')).toBeNull()
  expect(h.client.generate).not.toHaveBeenCalled()
})
