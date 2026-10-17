import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { teamOnboardingCopy } from '@/i18n/organization-team-onboarding'
import { setupSubscriptionCopy } from '@/i18n/organization-team-setup-subscription'

import { TeamOnboardingDialog } from './team-onboarding'
import type { TeamOnboardingClient, TeamSetup } from './team-onboarding-client'
import {
  subscriptionCatalog,
  subscriptionPolicy,
  subscriptionPolicyState,
  subscriptionPreview,
  subscriptionSetup,
  subscriptionStatus
} from './team-setup-subscription-fixtures'

const copy = setupSubscriptionCopy.en,
  teamCopy = teamOnboardingCopy.en

const team = {
  executive: { name: 'Ash', purpose: 'Coordinate work', responsibilities: ['Review outcomes'] },
  members: []
}

const unknownUsage = {
  version: 1 as const,
  tokenStatus: 'unknown' as const,
  costStatus: 'unknown' as const,
  inputTokens: null,
  outputTokens: null,
  totalTokens: null,
  costUsd: null
}

function harness(
  initial: TeamSetup = {
    ...subscriptionSetup,
    readiness: { ...subscriptionSetup.readiness, blockers: ['subscription_generation_unsupported'] }
  }
) {
  let setup = structuredClone(initial),
    connection = structuredClone(subscriptionStatus),
    policyState = structuredClone(subscriptionPolicyState)

  const allowance = (remaining = 5) => ({
    policyVersion: 3 as const,
    status: 'available' as const,
    remainingCalls: remaining,
    remainingByOperation: { improveTeamInput: Math.min(4, remaining), generateTeam: Math.min(3, remaining) },
    setupUsage: unknownUsage
  })

  const preview = () => ({
    capabilityVersion: 1,
    organizationId: setup.organizationId,
    sourceRevision: setup.sourceRevision,
    previewVersion: 'reviewed-team',
    team,
    executiveModelMode: 'execFixed',
    readiness: setup.readiness,
    warnings: [],
    generationAllowance: setup.generationAllowance
  })

  const client = {
    current: () => true,
    setup: vi.fn(async () => setup),
    subscriptionStatus: vi.fn(async () => connection),
    subscriptionPolicy: vi.fn(async () => policyState),
    beginSubscription: vi.fn(async () => {
      connection = { ...connection, connectionState: 'active', connectionRevision: 1 }

      return { stage: 'connected' }
    }),
    subscriptionModels: vi.fn(async () => {
      if (policyState.policyVersion === 3) {
        setup = {
          ...setup,
          readiness: { ...setup.readiness, status: 'unchecked', blockers: [] },
          generationAllowance: allowance()
        }
      }

      return { ...subscriptionCatalog, sourceRevision: setup.sourceRevision }
    }),
    previewSubscriptionPolicy: vi.fn(async () => subscriptionPreview),
    applySubscriptionPolicy: vi.fn(async () => {
      setup = {
        ...setup,
        sourceRevision: 'e'.repeat(64),
        generationAllowance: { ...allowance(), status: 'blocked' },
        readiness: { ...setup.readiness, blockers: ['subscription_models_required'] }
      }
      policyState = {
        ...policyState,
        sourceRevision: setup.sourceRevision!,
        policyVersion: 3,
        configuration: subscriptionPolicy
      }

      return { receipt: { candidate: subscriptionPolicy } }
    }),
    improve: vi.fn(async () => {
      setup = { ...setup, generationAllowance: allowance(4) }

      return {
        capabilityVersion: 1,
        organizationId: setup.organizationId,
        sourceRevision: setup.sourceRevision,
        field: 'jobs',
        suggestion: 'Research weekly changes and cite sources.',
        assumptions: [],
        generationAllowance: setup.generationAllowance
      }
    }),
    generate: vi.fn(async () => {
      setup = { ...setup, generationAllowance: allowance(3) }

      return preview()
    }),
    preview: vi.fn(async () => preview()),
    create: vi.fn(async () => ({
      capabilityVersion: 1,
      state: 'created',
      organizationId: setup.organizationId,
      team,
      readiness: setup.readiness,
      receipt: { idempotencyKey: 'created', previewVersion: 'reviewed-team' }
    })),
    dispose: vi.fn()
  } as unknown as TeamOnboardingClient

  const onCreated = vi.fn()
  render(
    <MemoryRouter>
      <I18nProvider configClient={null} initialLocale="en">
        <TeamOnboardingDialog
          client={client}
          onClose={() => {}}
          onCreated={onCreated}
          onReturnFocus={() => {}}
          profile="default"
          setup={initial}
        />
      </I18nProvider>
    </MemoryRouter>
  )

  return { client, onCreated }
}

it('uses the existing review/Create path after explicit subscription consent, policy and Improve review', async () => {
  const h = harness()
  fireEvent.change(screen.getByLabelText(teamCopy.jobs), { target: { value: 'Research changes' } })
  fireEvent.change(screen.getByLabelText(teamCopy.goals), { target: { value: 'Reliable weekly summary' } })
  expect((screen.getByRole('button', { name: teamCopy.generate }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: copy.inspect }))
  await screen.findByLabelText(copy.consentCheck)
  fireEvent.click(screen.getByLabelText(copy.consentCheck))
  fireEvent.click(screen.getByRole('button', { name: copy.continue }))
  await screen.findByRole('button', { name: copy.loadModels })
  fireEvent.click(screen.getByRole('button', { name: copy.loadModels }))
  await screen.findByLabelText(copy.model)
  fireEvent.change(screen.getByLabelText(copy.model), { target: { value: subscriptionPolicy.modelSlug } })
  fireEvent.click(screen.getByRole('button', { name: copy.review }))
  await screen.findByLabelText(copy.ack)
  fireEvent.click(screen.getByLabelText(copy.ack))
  fireEvent.click(screen.getByRole('button', { name: copy.apply }))
  await screen.findByText(copy.changed)
  expect(h.client.generate).not.toHaveBeenCalled()
  expect(h.client.improve).not.toHaveBeenCalled()
  expect((screen.getByRole('button', { name: teamCopy.generate }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: copy.loadModels }))
  await waitFor(() =>
    expect((screen.getByRole('button', { name: teamCopy.improveJobs }) as HTMLButtonElement).disabled).toBe(false)
  )
  fireEvent.click(screen.getByRole('button', { name: teamCopy.improveJobs }))
  await screen.findByRole('button', { name: teamCopy.applySuggestion })
  expect((screen.getByLabelText(teamCopy.jobs) as HTMLTextAreaElement).value).toBe('Research changes')
  fireEvent.click(screen.getByRole('button', { name: teamCopy.applySuggestion }))
  expect((screen.getByLabelText(teamCopy.jobs) as HTMLTextAreaElement).value).toBe(
    'Research weekly changes and cite sources.'
  )
  expect(h.client.generate).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: teamCopy.generate }))
  await screen.findByRole('button', { name: teamCopy.create })
  expect(h.client.generate).toHaveBeenCalledTimes(1)
  expect(h.client.create).not.toHaveBeenCalled()
  expect(screen.queryByText(/Token, cost and shared limits still apply/)).toBeNull()
  expect(screen.getByText(copy.unknown)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: teamCopy.create }))
  await waitFor(() => expect(h.onCreated).toHaveBeenCalledTimes(1))
  expect(h.client.create).toHaveBeenCalledTimes(1)
  expect(h.client.generate).toHaveBeenCalledWith(
    expect.objectContaining({ sourceRevision: 'e'.repeat(64) }),
    expect.objectContaining({ jobs: 'Research weekly changes and cite sources.' })
  )
})

it('leaves fresh API setup unchanged without touching subscription authorization', async () => {
  const initial = {
    ...subscriptionSetup,
    readiness: { ...subscriptionSetup.readiness, status: 'unchecked' as const, blockers: [] },
    generationAllowance: { status: 'available' as const, remainingCalls: 3 }
  }

  const h = harness(initial)
  expect(screen.queryByRole('button', { name: copy.inspect })).toBeNull()
  fireEvent.change(screen.getByLabelText(teamCopy.jobs), { target: { value: 'Research' } })
  fireEvent.change(screen.getByLabelText(teamCopy.goals), { target: { value: 'Summary' } })
  fireEvent.click(screen.getByRole('button', { name: teamCopy.generate }))
  await screen.findByRole('button', { name: teamCopy.create })
  expect(h.client.generate).toHaveBeenCalledTimes(1)
  expect(h.client.beginSubscription).not.toHaveBeenCalled()
  expect(h.client.subscriptionModels).not.toHaveBeenCalled()
})

it('keeps manual setup available on an unsupported subscription host', async () => {
  const h = harness({
    ...subscriptionSetup,
    setupSubscriptionState: 'unsupported_platform',
    readiness: { ...subscriptionSetup.readiness, blockers: ['subscription_generation_unsupported'] }
  })

  expect(screen.getByText(copy.unsupported)).toBeTruthy()
  expect(screen.queryByRole('button', { name: copy.inspect })).toBeNull()
  fireEvent.click(screen.getByText(teamCopy.manual))
  const manual = screen.getByRole('button', { name: teamCopy.manualStart })
  expect(manual).toHaveProperty('disabled', false)
  fireEvent.click(manual)
  expect(screen.getByRole('button', { name: teamCopy.validate })).toHaveProperty('disabled', false)
  expect(h.client.beginSubscription).not.toHaveBeenCalled()
  expect(h.client.generate).not.toHaveBeenCalled()
})
