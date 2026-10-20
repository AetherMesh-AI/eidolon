import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { teamOnboardingCopy } from '@/i18n/organization-team-onboarding'
import { setupSubscriptionCopy } from '@/i18n/organization-team-setup-subscription'

import { TeamOnboardingDialog } from './team-onboarding'
import type { TeamOnboardingClient, TeamSetup } from './team-onboarding-client'
import type { SubscriptionPendingRequest, SubscriptionRecovery } from './team-setup-subscription-client'
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
  let current = true

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
    current: () => current,
    setup: vi.fn(async () => setup),
    inspectSubscriptionRequest: vi.fn(async () => null),
    reconcileSubscriptionRequest: vi.fn(),
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

  const view = render(
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

  return {
    client,
    onCreated,
    view,
    updateSetup: (next: TeamSetup) => {
      setup = next
    },
    depart: () => {
      current = false
    }
  }
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

const readySubscription = (): TeamSetup => ({
  ...subscriptionSetup,
  readiness: {
    ...subscriptionSetup.readiness,
    status: 'unchecked',
    blockers: []
  },
  generationAllowance: {
    policyVersion: 3,
    status: 'available',
    remainingCalls: 5,
    remainingByOperation: { improveTeamInput: 3, generateTeam: 2 },
    setupUsage: unknownUsage
  }
})

function recoveryFailure(setup: TeamSetup, input: { idempotencyKey: string; field?: 'jobs' | 'goals' }) {
  return {
    code: 5076,
    message: 'Subscription setup is unavailable',
    data: {
      reason: 'rejected',
      recovery: {
        version: 1,
        disposition: 'retry_unsent',
        idempotencyKey: input.idempotencyKey,
        operation: input.field ? `improve_${input.field}` : 'generate',
        sourceRevision: setup.sourceRevision
      }
    }
  }
}

function enterInputs() {
  fireEvent.change(screen.getByLabelText(teamCopy.jobs), {
    target: { value: 'Research changes' }
  })
  fireEvent.change(screen.getByLabelText(teamCopy.goals), {
    target: { value: 'Reliable weekly summary' }
  })
}

it.each(['generate', 'regenerate'] as const)(
  'keeps the %s failed key through checks and creates one new request only on explicit unsent retry',
  async kind => {
    const h = harness(readySubscription())
    enterInputs()
    const method = vi.mocked(h.client.generate)

    const label = kind === 'regenerate' ? teamCopy.regenerate : teamCopy.generate

    if (kind === 'regenerate') {
      fireEvent.click(screen.getByRole('button', { name: teamCopy.generate }))
      await screen.findByRole('button', { name: teamCopy.create })
    }

    const before = method.mock.calls.length
    method.mockImplementationOnce(async (setup, input) => {
      throw recoveryFailure(setup, input)
    })
    fireEvent.click(screen.getByRole('button', { name: label }))
    const retry = await screen.findByRole('button', { name: 'Retry unsent request' })
    await waitFor(() => expect(retry).toHaveProperty('disabled', false))
    const first = method.mock.calls[before]!
    expect(method).toHaveBeenCalledTimes(before + 1)
    expect(h.client.create).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: teamCopy.checkConfiguration }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Retry unsent request' })).toHaveProperty('disabled', false)
    )
    expect(method).toHaveBeenCalledTimes(before + 1)

    // Ordinary operation retry still addresses the failed old attempt.
    method.mockImplementationOnce(async (setup, input) => {
      throw recoveryFailure(setup, input)
    })
    fireEvent.click(
      screen.getByRole('button', {
        name: kind === 'regenerate' ? teamCopy.retryRegeneration : label
      })
    )
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Retry unsent request' })).toHaveProperty('disabled', false)
    )
    expect(method.mock.calls[before + 1]![1]).toEqual(first[1])

    const explicit = screen.getByRole('button', { name: 'Retry unsent request' })
    fireEvent.click(explicit)
    fireEvent.click(explicit)
    await waitFor(() => expect(method).toHaveBeenCalledTimes(before + 3))
    const replacement = method.mock.calls[before + 2]!
    expect(replacement[1].idempotencyKey).not.toBe(first[1].idempotencyKey)
    expect({
      ...replacement[1],
      idempotencyKey: first[1].idempotencyKey
    }).toEqual(first[1])
    expect(replacement[0].organizationId).toBe(first[0].organizationId)
    expect(replacement[0].sourceRevision).toBe(first[0].sourceRevision)
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Retry unsent request' })).toBeNull())
    expect(h.client.create).not.toHaveBeenCalled()
  }
)

it.each([
  'missing',
  'wrong_key',
  'wrong_source',
  'wrong_operation',
  'unknown_disposition',
  'extra_field',
  'wrong_code'
])('never rotates a failed intent or offers recovery for %s evidence', async kind => {
  const h = harness(readySubscription())
  enterInputs()
  vi.mocked(h.client.generate).mockImplementation(async (setup, input) => {
    const failure = recoveryFailure(setup, input)

    if (kind === 'missing') {
      delete (failure.data as { recovery?: unknown }).recovery
    }

    if (kind === 'wrong_key') {
      failure.data.recovery.idempotencyKey = 'different-key'
    }

    if (kind === 'wrong_source') {
      failure.data.recovery.sourceRevision = 'f'.repeat(64)
    }

    if (kind === 'wrong_operation') {
      failure.data.recovery.operation = 'improve_jobs'
    }

    if (kind === 'unknown_disposition') {
      failure.data.recovery.disposition = 'automatic_retry'
    }

    if (kind === 'extra_field') {
      Object.assign(failure.data.recovery, { dispatched: false })
    }

    if (kind === 'wrong_code') {
      failure.code = -32602
    }

    throw failure
  })
  fireEvent.click(screen.getByRole('button', { name: teamCopy.generate }))
  await screen.findByText(teamCopy.error)
  await waitFor(() => expect(screen.getByRole('button', { name: teamCopy.generate })).toHaveProperty('disabled', false))
  expect(screen.queryByRole('button', { name: 'Retry unsent request' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: teamCopy.checkConfiguration }))
  await waitFor(() => expect(screen.getByRole('button', { name: teamCopy.generate })).toHaveProperty('disabled', false))
  fireEvent.click(screen.getByRole('button', { name: teamCopy.generate }))
  await waitFor(() => expect(h.client.generate).toHaveBeenCalledTimes(2))
  expect(vi.mocked(h.client.generate).mock.calls[1]![1]).toEqual(vi.mocked(h.client.generate).mock.calls[0]![1])
  await waitFor(() => expect(screen.getByRole('button', { name: teamCopy.generate })).toHaveProperty('disabled', false))
  expect(screen.queryByRole('button', { name: 'Retry unsent request' })).toBeNull()
})

it.each(['input', 'source', 'organization', 'scope', 'running', 'uncertain', 'exhausted', 'api', 'bucket'])(
  'retires an unsent recovery offer after %s changes without sending',
  async kind => {
    const initial = readySubscription()
    const h = harness(initial)
    enterInputs()
    vi.mocked(h.client.generate).mockImplementationOnce(async (setup, input) => {
      throw recoveryFailure(setup, input)
    })
    fireEvent.click(screen.getByRole('button', { name: teamCopy.generate }))

    const priorButton = await screen.findByRole('button', {
      name: 'Retry unsent request'
    })

    await waitFor(() => expect(priorButton).toHaveProperty('disabled', false))

    if (kind === 'input') {
      fireEvent.change(screen.getByLabelText(teamCopy.goals), {
        target: { value: 'Changed goal' }
      })
    } else if (kind === 'scope') {
      h.depart()
      fireEvent.click(priorButton)
    } else {
      const next = structuredClone(initial)

      if (kind === 'source') {
        next.sourceRevision = 'f'.repeat(64)
      }

      if (kind === 'organization') {
        next.organizationId = 'other-organization'
      }

      if (['running', 'uncertain', 'exhausted'].includes(kind)) {
        next.generationAllowance!.status = kind as 'running' | 'uncertain' | 'exhausted'
      }

      if (kind === 'api') {
        next.generationAllowance = { status: 'available', remainingCalls: 3 }
      }

      if (kind === 'bucket') {
        next.generationAllowance!.remainingByOperation!.generateTeam = 0
      }

      h.updateSetup(next)
      fireEvent.click(screen.getByRole('button', { name: teamCopy.checkConfiguration }))
    }

    await waitFor(() => expect(screen.queryByRole('button', { name: 'Retry unsent request' })).toBeNull())
    fireEvent.click(priorButton)
    expect(h.client.generate).toHaveBeenCalledTimes(1)
    expect(h.client.improve).not.toHaveBeenCalled()
  }
)

it.each(['input', 'scope', 'close'])(
  'fences a late failed response after %s without publishing recovery or sending again',
  async kind => {
    const h = harness(readySubscription())
    enterInputs()
    let reject!: (value: unknown) => void
    vi.mocked(h.client.generate).mockImplementationOnce(
      () =>
        new Promise((_resolve, failure) => {
          reject = failure
        })
    )
    fireEvent.click(screen.getByRole('button', { name: teamCopy.generate }))
    const [setup, input] = vi.mocked(h.client.generate).mock.calls[0]!

    if (kind === 'input') {
      fireEvent.change(screen.getByLabelText(teamCopy.jobs), {
        target: { value: 'New input' }
      })
    }

    if (kind === 'scope') {
      h.depart()
    }

    if (kind === 'close') {
      h.view.unmount()
    }

    await act(async () => {
      reject(recoveryFailure(setup, input))
    })
    expect(screen.queryByRole('button', { name: 'Retry unsent request' })).toBeNull()
    expect(h.client.generate).toHaveBeenCalledTimes(1)
    expect(h.client.improve).not.toHaveBeenCalled()
  }
)

it('does not reuse an old recovery offer after a generic failure or recover it from a passive check', async () => {
  const h = harness(readySubscription())
  enterInputs()
  const improve = vi.mocked(h.client.generate)
  improve.mockImplementationOnce(async (setup, input) => {
    throw recoveryFailure(setup, input)
  })
  fireEvent.click(screen.getByRole('button', { name: teamCopy.generate }))
  await screen.findByRole('button', { name: 'Retry unsent request' })
  await waitFor(() => expect(screen.getByRole('button', { name: teamCopy.generate })).toHaveProperty('disabled', false))
  const first = improve.mock.calls[0]![1]
  improve.mockRejectedValueOnce(new Error('lost response'))
  fireEvent.click(screen.getByRole('button', { name: teamCopy.generate }))
  await waitFor(() => expect(improve).toHaveBeenCalledTimes(2))
  await waitFor(() =>
    expect(screen.getByRole('button', { name: teamCopy.checkConfiguration })).toHaveProperty('disabled', false)
  )
  fireEvent.click(screen.getByRole('button', { name: teamCopy.checkConfiguration }))
  await waitFor(() => expect(screen.getByRole('button', { name: teamCopy.generate })).toHaveProperty('disabled', false))
  expect(screen.queryByRole('button', { name: 'Retry unsent request' })).toBeNull()
  expect(improve.mock.calls[1]![1]).toEqual(first)
  improve.mockImplementationOnce(async (setup, input) => {
    throw recoveryFailure(setup, input)
  })
  fireEvent.click(screen.getByRole('button', { name: teamCopy.generate }))
  const retry = await screen.findByRole('button', { name: 'Retry unsent request' })
  await waitFor(() => expect(retry).toHaveProperty('disabled', false))
  expect(improve.mock.calls[2]![1]).toEqual(first)
  fireEvent.click(retry)
  await waitFor(() => expect(improve).toHaveBeenCalledTimes(4))
  expect(improve.mock.calls[3]![1].idempotencyKey).not.toBe(first.idempotencyKey)
  expect(h.client.create).not.toHaveBeenCalled()
})

it.each([
  ['generate', 'input'],
  ['generate', 'source'],
  ['regenerate', 'draft'],
  ['regenerate', 'source']
])('retires %s recovery when the %s changes and preserves the edited draft', async (kind, change) => {
  const initial = readySubscription()
  const h = harness(initial)
  enterInputs()

  if (kind === 'regenerate') {
    fireEvent.click(screen.getByRole('button', { name: teamCopy.generate }))
    await screen.findByRole('button', { name: teamCopy.create })
  }

  const generate = vi.mocked(h.client.generate)
  const before = generate.mock.calls.length
  generate.mockImplementationOnce(async (setup, input) => {
    throw recoveryFailure(setup, input)
  })
  fireEvent.click(screen.getByRole('button', { name: kind === 'generate' ? teamCopy.generate : teamCopy.regenerate }))
  const retry = await screen.findByRole('button', { name: 'Retry unsent request' })
  await waitFor(() => expect(retry).toHaveProperty('disabled', false))

  if (change === 'input') {
    fireEvent.change(screen.getByLabelText(teamCopy.jobs), { target: { value: 'New jobs' } })
  }

  if (change === 'draft') {
    fireEvent.change(screen.getByLabelText(teamCopy.name), { target: { value: 'Owner-edited executive' } })
  }

  if (change === 'source') {
    h.updateSetup({ ...initial, sourceRevision: 'f'.repeat(64) })
    fireEvent.click(screen.getByRole('button', { name: teamCopy.checkConfiguration }))
  }

  await waitFor(() => expect(screen.queryByRole('button', { name: 'Retry unsent request' })).toBeNull())
  fireEvent.click(retry)
  expect(generate).toHaveBeenCalledTimes(before + 1)

  if (change === 'draft') {
    expect(screen.getByLabelText(teamCopy.name)).toHaveProperty('value', 'Owner-edited executive')
  }

  expect(h.client.create).not.toHaveBeenCalled()
})

it.each(['generate', 'regenerate'] as const)(
  'locally checks a retained %s request without sending, then retries only on a separate click',
  async kind => {
    const initial = readySubscription()
    const h = harness(initial)
    enterInputs()
    const method = vi.mocked(h.client.generate)

    if (kind === 'regenerate') {
      fireEvent.click(screen.getByRole('button', { name: teamCopy.generate }))
      await screen.findByRole('button', { name: teamCopy.create })
    }

    const before = method.mock.calls.length
    method.mockRejectedValueOnce(new Error('lost original reply'))
    h.updateSetup({ ...initial, generationAllowance: { ...initial.generationAllowance!, status: 'running' } })
    fireEvent.click(
      screen.getByRole('button', {
        name: kind === 'regenerate' ? teamCopy.regenerate : teamCopy.generate
      })
    )
    const check = await screen.findByRole('button', { name: 'Check unsent request' })
    await waitFor(() => expect(check).toHaveProperty('disabled', false))
    const [sentSetup, sentInput] = method.mock.calls[before]!
    const operation = 'generate'
    const recovery = recoveryFailure(sentSetup, sentInput).data.recovery
    vi.mocked(h.client.reconcileSubscriptionRequest).mockRejectedValueOnce(new Error('storage unavailable'))
    fireEvent.click(check)
    await screen.findByText(copy.checkUnsentBlocked)
    expect(method).toHaveBeenCalledTimes(before + 1)
    expect(screen.queryByRole('button', { name: 'Retry unsent request' })).toBeNull()
    await waitFor(() => expect(check).toHaveProperty('disabled', false))
    vi.mocked(h.client.reconcileSubscriptionRequest).mockImplementationOnce(async () => {
      h.updateSetup(initial)

      return recovery as never
    })
    fireEvent.click(check)
    fireEvent.click(check)
    const retry = await screen.findByRole('button', { name: 'Retry unsent request' })
    await waitFor(() => expect(retry).toHaveProperty('disabled', false))
    expect(h.client.reconcileSubscriptionRequest).toHaveBeenCalledTimes(2)
    expect(vi.mocked(h.client.reconcileSubscriptionRequest).mock.calls[1]).toEqual([
      expect.objectContaining({ organizationId: sentSetup.organizationId, sourceRevision: sentSetup.sourceRevision }),
      {
        jobs: sentInput.jobs,
        goals: sentInput.goals,
        namingStyle: sentInput.namingStyle,
        idempotencyKey: sentInput.idempotencyKey,
        operation
      }
    ])
    expect(method).toHaveBeenCalledTimes(before + 1)
    fireEvent.click(retry)
    fireEvent.click(retry)
    await waitFor(() => expect(method).toHaveBeenCalledTimes(before + 2))
    expect(method.mock.calls[before + 1]![1].idempotencyKey).not.toBe(sentInput.idempotencyKey)
    expect({ ...method.mock.calls[before + 1]![1], idempotencyKey: sentInput.idempotencyKey }).toEqual(sentInput)
    expect(h.client.create).not.toHaveBeenCalled()
  }
)

it.each(['improve_jobs', 'improve_goals', 'generate'] as const)(
  'recovers a restarted %s request only after exact input discovery, local check, and separate retry',
  async operation => {
    const initial = readySubscription()
    initial.generationAllowance!.status = 'running'
    const h = harness(initial)
    enterInputs()

    const found = {
      version: 1 as const,
      disposition: 'reconcile_unsent' as const,
      idempotencyKey: 'saved-original-key',
      operation,
      sourceRevision: initial.sourceRevision!
    }

    vi.mocked(h.client.inspectSubscriptionRequest).mockResolvedValueOnce(null).mockResolvedValueOnce(found)
    fireEvent.click(screen.getByText(copy.findUnsent, { selector: 'summary' }))
    fireEvent.change(screen.getByLabelText(copy.unsentOperation), { target: { value: operation } })
    fireEvent.change(screen.getByLabelText(teamCopy.jobs), { target: { value: 'Wrong original input' } })
    fireEvent.click(screen.getByRole('button', { name: 'Find unsent request' }))
    await screen.findByText(copy.unsentNotFound)
    fireEvent.change(screen.getByLabelText(teamCopy.jobs), { target: { value: 'Research changes' } })
    expect(screen.queryByRole('button', { name: 'Check unsent request' })).toBeNull()
    expect(h.client.reconcileSubscriptionRequest).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Find unsent request' })).toHaveProperty('disabled', false)
    )
    fireEvent.click(screen.getByRole('button', { name: 'Find unsent request' }))
    const check = await screen.findByRole('button', { name: 'Check unsent request' })
    await waitFor(() => expect(check).toHaveProperty('disabled', false))
    expect(h.client.generate).not.toHaveBeenCalled()
    expect(h.client.improve).not.toHaveBeenCalled()
    expect(h.client.reconcileSubscriptionRequest).not.toHaveBeenCalled()
    expect(h.client.inspectSubscriptionRequest).toHaveBeenLastCalledWith(
      expect.objectContaining({ organizationId: initial.organizationId, sourceRevision: initial.sourceRevision }),
      { jobs: 'Research changes', goals: 'Reliable weekly summary', namingStyle: '', operation }
    )
    // A lost Check reply never enables Retry; the same saved key remains checkable.
    vi.mocked(h.client.reconcileSubscriptionRequest)
      .mockRejectedValueOnce(new Error('lost check reply'))
      .mockImplementationOnce(async () => {
        h.updateSetup(readySubscription())

        return { ...found, disposition: 'retry_unsent' }
      })
    fireEvent.click(check)
    await screen.findByText(copy.checkUnsentBlocked)
    expect(screen.queryByRole('button', { name: 'Retry unsent request' })).toBeNull()
    await waitFor(() => expect(check).toHaveProperty('disabled', false))
    fireEvent.click(check)

    if (operation !== 'generate') {
      await waitFor(() => expect(h.client.reconcileSubscriptionRequest).toHaveBeenCalledTimes(2))
      expect(screen.queryByRole('button', { name: 'Retry unsent request' })).toBeNull()
      expect(h.client.improve).not.toHaveBeenCalled()

      return
    }

    const retry = await screen.findByRole('button', { name: 'Retry unsent request' })
    await waitFor(() => expect(retry).toHaveProperty('disabled', false))
    const checks = vi.mocked(h.client.reconcileSubscriptionRequest).mock.calls
    expect(checks[1]).toEqual(checks[0])
    expect(checks[0]![1].idempotencyKey).toBe(found.idempotencyKey)
    expect(h.client.generate).not.toHaveBeenCalled()
    expect(h.client.improve).not.toHaveBeenCalled()
    fireEvent.click(retry)
    const method = operation === 'generate' ? h.client.generate : h.client.improve
    await waitFor(() => expect(method).toHaveBeenCalledTimes(1))
    expect(vi.mocked(method).mock.calls[0]![1].idempotencyKey).not.toBe(found.idempotencyKey)
  }
)

it.each(['input', 'operation', 'scope', 'close'] as const)(
  'discards late discovery after %s changes without adopting a key or checking it',
  async change => {
    const initial = readySubscription()
    initial.generationAllowance!.status = 'running'
    const h = harness(initial)
    enterInputs()
    let finish!: (value: unknown) => void
    vi.mocked(h.client.inspectSubscriptionRequest).mockImplementationOnce(
      () =>
        new Promise(resolve => {
          finish = value => resolve(value as SubscriptionPendingRequest | null)
        })
    )
    fireEvent.click(screen.getByText(copy.findUnsent, { selector: 'summary' }))
    fireEvent.click(screen.getByRole('button', { name: copy.findUnsent }))

    if (change === 'input') {
      fireEvent.change(screen.getByLabelText(teamCopy.jobs), { target: { value: 'Changed inputs' } })
    }

    if (change === 'operation') {
      fireEvent.change(screen.getByLabelText(copy.unsentOperation), { target: { value: 'improve_jobs' } })
    }

    if (change === 'scope') {
      h.depart()
    }

    if (change === 'close') {
      h.view.unmount()
    }

    await act(async () => {
      finish({
        version: 1,
        disposition: 'reconcile_unsent',
        idempotencyKey: 'saved-key',
        operation: 'generate',
        sourceRevision: initial.sourceRevision
      })
    })
    expect(screen.queryByRole('button', { name: copy.checkUnsent })).toBeNull()
    expect(screen.queryByRole('button', { name: copy.retryUnsent })).toBeNull()
    expect(h.client.reconcileSubscriptionRequest).not.toHaveBeenCalled()
    expect(h.client.improve).not.toHaveBeenCalled()
    expect(h.client.improve).not.toHaveBeenCalled()
  }
)

it.each([
  'input',
  'scope',
  'close',
  'wrong_key',
  'wrong_source',
  'wrong_operation',
  'dispatch_won',
  'running',
  'api',
  'source_changed'
] as const)('never offers a retry for a local check after %s', async change => {
  const initial = readySubscription()
  const h = harness(initial)
  enterInputs()
  vi.mocked(h.client.generate).mockRejectedValueOnce(new Error('lost reply'))
  fireEvent.click(screen.getByRole('button', { name: teamCopy.generate }))
  const check = await screen.findByRole('button', { name: copy.checkUnsent })
  await waitFor(() => expect(check).toHaveProperty('disabled', false))
  const [setup, input] = vi.mocked(h.client.generate).mock.calls[0]!
  let finish!: (value: unknown) => void, reject!: (value: unknown) => void
  vi.mocked(h.client.reconcileSubscriptionRequest).mockImplementationOnce(
    () =>
      new Promise((resolve, failure) => {
        finish = value => resolve(value as SubscriptionRecovery)
        reject = failure
      })
  )
  fireEvent.click(check)
  const result = recoveryFailure(setup, input).data.recovery

  if (change === 'input') {
    fireEvent.change(screen.getByLabelText(teamCopy.goals), { target: { value: 'New goal' } })
  }

  if (change === 'scope') {
    h.depart()
  }

  if (change === 'close') {
    h.view.unmount()
  }

  if (change === 'wrong_key') {
    result.idempotencyKey = 'another-key'
  }

  if (change === 'wrong_source') {
    result.sourceRevision = 'b'.repeat(64)
  }

  if (change === 'wrong_operation') {
    result.operation = 'improve_jobs'
  }

  if (change === 'running') {
    h.updateSetup({ ...initial, generationAllowance: { ...initial.generationAllowance!, status: 'running' } })
  }

  if (change === 'api') {
    h.updateSetup({ ...initial, generationAllowance: { status: 'available', remainingCalls: 3 } })
  }

  if (change === 'source_changed') {
    h.updateSetup({ ...initial, sourceRevision: 'c'.repeat(64) })
  }

  await act(async () => {
    if (change === 'dispatch_won') {
      reject({ code: 5076, data: { reason: 'request_uncertain' } })
    } else {
      finish(result)
    }
  })
  expect(screen.queryByRole('button', { name: copy.retryUnsent })).toBeNull()
  expect(h.client.reconcileSubscriptionRequest).toHaveBeenCalledTimes(1)
  expect(h.client.generate).toHaveBeenCalledTimes(1)
  expect(h.client.improve).not.toHaveBeenCalled()
})

it('retires a discovered key on source change and does not rediscover or reconcile from passive checks', async () => {
  const initial = readySubscription()
  initial.generationAllowance!.status = 'running'
  const h = harness(initial)
  enterInputs()
  vi.mocked(h.client.inspectSubscriptionRequest).mockResolvedValueOnce({
    version: 1,
    disposition: 'reconcile_unsent',
    idempotencyKey: 'saved-key',
    operation: 'generate',
    sourceRevision: initial.sourceRevision!
  })
  expect(h.client.inspectSubscriptionRequest).not.toHaveBeenCalled()
  fireEvent.click(screen.getByText(copy.findUnsent, { selector: 'summary' }))
  fireEvent.click(screen.getByRole('button', { name: copy.findUnsent }))
  const oldCheck = await screen.findByRole('button', { name: copy.checkUnsent })
  await waitFor(() => expect(oldCheck).toHaveProperty('disabled', false))
  h.updateSetup({ ...initial, sourceRevision: 'd'.repeat(64) })
  fireEvent.click(screen.getByRole('button', { name: teamCopy.checkConfiguration }))
  await waitFor(() => expect(screen.queryByRole('button', { name: copy.checkUnsent })).toBeNull())
  fireEvent.click(oldCheck)
  expect(h.client.inspectSubscriptionRequest).toHaveBeenCalledTimes(1)
  expect(h.client.reconcileSubscriptionRequest).not.toHaveBeenCalled()
  expect(h.client.generate).not.toHaveBeenCalled()
})

it('lets a restarted no-catalog request be checked locally before explicit model loading and a new ordinary request', async () => {
  const initial = readySubscription()
  initial.generationAllowance!.status = 'running'
  initial.readiness = { ...initial.readiness, status: 'blocked', blockers: ['subscription_models_required'] }
  const h = harness(initial)
  enterInputs()

  const saved = {
    version: 1 as const,
    disposition: 'reconcile_unsent' as const,
    idempotencyKey: 'old-process-key',
    operation: 'generate' as const,
    sourceRevision: initial.sourceRevision!
  }

  vi.mocked(h.client.inspectSubscriptionRequest).mockResolvedValueOnce(saved)
  vi.mocked(h.client.reconcileSubscriptionRequest).mockImplementationOnce(async () => {
    h.updateSetup({ ...initial, generationAllowance: { ...initial.generationAllowance!, status: 'available' } })

    return { ...saved, disposition: 'retry_unsent' }
  })
  vi.mocked(h.client.subscriptionStatus).mockResolvedValue({
    ...subscriptionStatus,
    connectionState: 'active',
    connectionRevision: 1
  })
  vi.mocked(h.client.subscriptionPolicy).mockResolvedValue({
    ...subscriptionPolicyState,
    policyVersion: 3,
    configuration: subscriptionPolicy
  })
  vi.mocked(h.client.subscriptionModels).mockImplementationOnce(async () => {
    h.updateSetup(readySubscription())

    return subscriptionCatalog
  })
  fireEvent.click(screen.getByText(copy.findUnsent, { selector: 'summary' }))
  fireEvent.click(screen.getByRole('button', { name: copy.findUnsent }))
  const check = await screen.findByRole('button', { name: copy.checkUnsent })
  await waitFor(() => expect(check).toHaveProperty('disabled', false))
  fireEvent.click(check)
  await screen.findByText(copy.unsentVerified.replace('{setup}', copy.inspect).replace('{models}', copy.loadModels))
  expect(screen.queryByRole('button', { name: copy.retryUnsent })).toBeNull()
  expect(h.client.generate).not.toHaveBeenCalled()
  expect(h.client.subscriptionModels).not.toHaveBeenCalled()
  await waitFor(() => expect(screen.getByRole('button', { name: copy.inspect })).toHaveProperty('disabled', false))
  fireEvent.click(screen.getByRole('button', { name: copy.inspect }))
  const models = await screen.findByRole('button', { name: copy.loadModels })
  await waitFor(() => expect(models).toHaveProperty('disabled', false))
  fireEvent.click(models)
  await waitFor(() => expect(screen.getByRole('button', { name: teamCopy.generate })).toHaveProperty('disabled', false))
  expect(h.client.generate).not.toHaveBeenCalled()
  expect(h.client.improve).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: teamCopy.generate }))
  await waitFor(() => expect(h.client.generate).toHaveBeenCalledTimes(1))
  expect(vi.mocked(h.client.generate).mock.calls[0]![1].idempotencyKey).not.toBe(saved.idempotencyKey)
  expect(h.client.reconcileSubscriptionRequest).toHaveBeenCalledTimes(1)
})
