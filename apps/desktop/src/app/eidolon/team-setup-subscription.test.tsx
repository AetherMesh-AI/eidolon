import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { setupSubscriptionCopy } from '@/i18n/organization-team-setup-subscription'

import { createTeamOnboardingClient } from './team-onboarding-client'
import { TeamSetupSubscription } from './team-setup-subscription'
import {
  subscriptionCatalog,
  subscriptionPolicy,
  subscriptionPolicyState,
  subscriptionPreview,
  subscriptionSetup,
  subscriptionStatus
} from './team-setup-subscription-fixtures'

const copy = setupSubscriptionCopy.en

function rig() {
  let setup = structuredClone(subscriptionSetup),
    connection = structuredClone(subscriptionStatus),
    policy = structuredClone(subscriptionPolicyState)

  let nextStage: 'active' | 'pending' = 'active'

  const request = vi.fn(async (method: string, params: Record<string, unknown>) => {
    if (method === 'organization.teamSetup') {
      return structuredClone(setup)
    }

    if (method === 'organization.setupSubscriptionStatus') {
      return structuredClone(connection)
    }

    if (method === 'organization.setupSubscriptionPolicy') {
      return structuredClone(policy)
    }

    if (method === 'organization.beginSetupSubscription') {
      connection = {
        ...connection,
        connectionState: nextStage === 'active' ? 'active' : 'absent',
        connectionRevision: nextStage === 'active' ? 1 : 0,
        stage: nextStage === 'active' ? 'connected' : 'pending',
        attemptId: 'synthetic-attempt'
      }

      return { stage: connection.stage }
    }

    if (method === 'organization.cancelSetupSubscription') {
      connection = { ...connection, stage: 'cancelled' }

      return { stage: 'cancelled' }
    }

    if (method === 'organization.setupSubscriptionModels') {
      if (policy.policyVersion === 3) {
        setup = { ...setup, readiness: { ...setup.readiness, status: 'unchecked', blockers: [] } }
      }

      return { ...subscriptionCatalog, sourceRevision: setup.sourceRevision }
    }

    if (method === 'organization.previewSetupSubscriptionPolicy') {
      return { ...subscriptionPreview, configuration: params.configuration }
    }

    if (method === 'organization.applySetupSubscriptionPolicy') {
      setup = {
        ...setup,
        sourceRevision: 'e'.repeat(64),
        generationAllowance: {
          policyVersion: 3,
          mode: 'subscription_calls',
          requestTimeoutSeconds: 120,
          hardTokenGuarantee: false,
          hardUsdGuarantee: false,
          setupUsage: {
            version: 1,
            tokenStatus: 'unknown',
            costStatus: 'unknown',
            costUsd: null,
            inputTokens: null,
            outputTokens: null,
            totalTokens: null
          },
          status: 'blocked',
          remainingCalls: 5,
          remainingByOperation: { improveTeamInput: 4, generateTeam: 3 }
        }
      }
      policy = {
        ...policy,
        sourceRevision: setup.sourceRevision!,
        policyGeneration: 2,
        policyVersion: 3,
        configuration: subscriptionPolicy,
        setupBudget: {
          limits: { improveCalls: 4, generationCalls: 3, totalCalls: 7, totalTokens: null, maxCostUsd: null },
          requestTimeoutSeconds: 120,
          reservedTokens: null,
          reservedCostUsd: null,
          hardTokenGuarantee: false,
          hardUsdGuarantee: false,
          setupUsage: {
            version: 1,
            tokenStatus: 'unknown',
            costStatus: 'unknown',
            costUsd: null,
            inputTokens: null,
            outputTokens: null,
            totalTokens: null
          }
        }
      }

      return {
        capabilityVersion: 1,
        historical: false,
        sourceRevision: setup.sourceRevision,
        receipt: {
          version: 1,
          action: 'retireSetupInheritance',
          idempotencyKey: params.idempotencyKey,
          organizationId: setup.organizationId,
          ownerId: policy.ownerId,
          priorGeneration: 1,
          appliedGeneration: 2,
          sourceRevision: subscriptionSetup.sourceRevision,
          previewDigest: subscriptionPreview.previewVersion,
          candidate: subscriptionPolicy
        }
      }
    }

    throw new Error('Unexpected mutation')
  })

  const client = createTeamOnboardingClient(
    () => ({ key: 'socket', ownerKey: 'owner', profile: 'default', eligible: true, request: request as never }),
    () => () => {}
  )

  const invalidated = vi.fn(),
    refreshed = vi.fn()

  function Harness() {
    const [currentSetup, setSetup] = useState(setup)

    return (
      <I18nProvider>
        <TeamSetupSubscription
          client={client}
          disabled={false}
          onBusyChange={() => {}}
          onInvalidate={invalidated}
          onRefreshed={value => {
            refreshed(value)
            setSetup(value)
          }}
          setup={currentSetup}
        />
      </I18nProvider>
    )
  }

  return {
    request,
    client,
    Harness,
    invalidated,
    refreshed,
    setPending: () => {
      nextStage = 'pending'
    },
    block: () => {
      policy = { ...policy, state: 'blocked', reason: 'attempt_running_or_uncertain' }
    }
  }
}

async function inspect() {
  fireEvent.click(screen.getByRole('button', { name: copy.inspect }))
  await screen.findByLabelText(copy.consentCheck)
}

async function connect() {
  fireEvent.click(screen.getByLabelText(copy.consentCheck))
  fireEvent.click(screen.getByRole('button', { name: copy.continue }))
  await screen.findByRole('button', { name: copy.loadModels })
  fireEvent.click(screen.getByRole('button', { name: copy.loadModels }))
  await screen.findByLabelText(copy.model)
  fireEvent.change(screen.getByLabelText(copy.model), { target: { value: subscriptionPolicy.modelSlug } })
}

it('requires consent, account model choice and an acknowledged review, then preserves explicit Generate intent', async () => {
  const test = rig()
  render(<test.Harness />)
  expect(test.request).not.toHaveBeenCalled()
  await inspect()
  expect((screen.getByRole('button', { name: copy.continue }) as HTMLButtonElement).disabled).toBe(true)
  expect(test.request.mock.calls.some(([method]) => method.includes('begin'))).toBe(false)
  await connect()
  expect((screen.getByRole('button', { name: copy.review }) as HTMLButtonElement).disabled).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: copy.review }))
  await screen.findByLabelText(copy.ack)
  expect((screen.getByRole('button', { name: copy.apply }) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.getByText(copy.nonHard)).toBeTruthy()
  expect(screen.getByText(copy.unknown)).toBeTruthy()
  fireEvent.click(screen.getByLabelText(copy.ack))
  fireEvent.click(screen.getByRole('button', { name: copy.apply }))
  await screen.findByText(copy.changed)
  expect(test.invalidated).toHaveBeenCalledTimes(3)
  expect(
    test.request.mock.calls.filter(([method]) => method === 'organization.applySetupSubscriptionPolicy')
  ).toHaveLength(1)
  expect(
    test.request.mock.calls.filter(([method]) =>
      ['organization.generateTeam', 'organization.improveTeamInput', 'organization.createTeam'].includes(method)
    )
  ).toHaveLength(0)
  expect(test.request.mock.calls.filter(([method]) => method === 'organization.setupSubscriptionModels')).toHaveLength(
    1
  )
  fireEvent.click(screen.getByRole('button', { name: copy.loadModels }))
  await waitFor(() => expect(test.refreshed.mock.calls.at(-1)?.[0].readiness.status).toBe('unchecked'))
  expect(test.request.mock.calls.filter(([method]) => method === 'organization.setupSubscriptionModels')).toHaveLength(
    2
  )
})

it('keeps pending consent cancellable and uncertain policy blocked without retrying a request', async () => {
  const test = rig()
  test.setPending()
  test.block()
  render(<test.Harness />)
  await inspect()
  fireEvent.click(screen.getByLabelText(copy.consentCheck))
  fireEvent.click(screen.getByRole('button', { name: copy.continue }))
  await screen.findByText(copy.pending)
  expect(screen.getByText(copy.blocked)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: copy.cancelConsent }))
  await waitFor(() => expect(screen.queryByText(copy.pending)).toBeNull())
  expect(test.request.mock.calls.filter(([method]) => method === 'organization.beginSetupSubscription')).toHaveLength(1)
  expect(test.request.mock.calls.filter(([method]) => method === 'organization.cancelSetupSubscription')).toHaveLength(
    1
  )
  expect(test.request.mock.calls.some(([method]) => method.includes('generateTeam'))).toBe(false)
})

it.each(['unsupported_platform', 'local_owner_required'] as const)(
  'shows truthful %s refusal without touching auth or changing API setup',
  state => {
    const test = rig()
    render(
      <I18nProvider>
        <TeamSetupSubscription
          client={test.client}
          disabled={false}
          onBusyChange={() => {}}
          onInvalidate={() => {}}
          onRefreshed={() => {}}
          setup={{ ...subscriptionSetup, setupSubscriptionState: state }}
        />
      </I18nProvider>
    )
    expect(screen.getByText(copy.unsupported)).toBeTruthy()
    expect(screen.queryByRole('button', { name: copy.inspect })).toBeNull()
    expect(test.request).not.toHaveBeenCalled()
  }
)

it('does not chain a prepared authorization operation after the dialog closes', async () => {
  const test = rig()
  const original = test.request.getMockImplementation()!
  let finish!: (value: unknown) => void
  test.request.mockImplementation(async (method, params) =>
    method === 'organization.prepareSetupSubscriptionOperation'
      ? (new Promise(resolve => {
          finish = resolve
        }) as never)
      : original(method, params)
  )
  const view = render(<test.Harness />)
  await inspect()
  await connect()
  fireEvent.click(screen.getByRole('button', { name: copy.refresh }))
  await waitFor(() => expect(finish).toBeTypeOf('function'))
  view.unmount()
  finish({
    state: 'prepared',
    ticket: {
      version: 1,
      sequence: '1',
      nonce: 'a'.repeat(64),
      kind: 'refresh',
      bindingDigest: 'b'.repeat(64),
      authorizationEpoch: subscriptionPolicy.authorizationEpoch,
      priorStoreRevision: 1,
      sourceRevision: subscriptionSetup.sourceRevision
    }
  })
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(test.request.mock.calls.some(([method]) => method === 'organization.executeSetupSubscriptionOperation')).toBe(
    false
  )
})

it('recovers a lost policy acknowledgement by reading setup without repeating the write', async () => {
  const test = rig(),
    original = test.request.getMockImplementation()!

  test.request.mockImplementation(async (method, params) => {
    const result = await original(method, params)

    if (method === 'organization.applySetupSubscriptionPolicy') {
      throw new Error('Lost acknowledgement')
    }

    return result
  })
  render(<test.Harness />)
  await inspect()
  await connect()
  fireEvent.click(screen.getByRole('button', { name: copy.review }))
  await screen.findByLabelText(copy.ack)
  fireEvent.click(screen.getByLabelText(copy.ack))
  fireEvent.click(screen.getByRole('button', { name: copy.apply }))
  await screen.findByText(copy.failed)
  await waitFor(() => expect(test.refreshed.mock.calls.at(-1)?.[0].sourceRevision).toBe('e'.repeat(64)))
  fireEvent.click(screen.getByRole('button', { name: copy.check }))
  await waitFor(() =>
    expect((screen.getByRole('button', { name: copy.check }) as HTMLButtonElement).disabled).toBe(false)
  )
  expect(
    test.request.mock.calls.filter(([method]) => method === 'organization.applySetupSubscriptionPolicy')
  ).toHaveLength(1)
  expect(test.request.mock.calls.filter(([method]) => method === 'organization.beginSetupSubscription')).toHaveLength(1)
  expect(test.request.mock.calls.some(([method]) => method === 'organization.generateTeam')).toBe(false)
})

it('forgets a resolved operation ticket so recovery checks current durable state', async () => {
  const test = rig(),
    original = test.request.getMockImplementation()!

  test.request.mockImplementation(async (method, params) => {
    if (method === 'organization.prepareSetupSubscriptionOperation') {
      return {
        state: 'prepared',
        ticket: {
          version: 1,
          sequence: '1',
          nonce: 'a'.repeat(64),
          kind: 'refresh',
          bindingDigest: 'b'.repeat(64),
          authorizationEpoch: subscriptionPolicy.authorizationEpoch,
          priorStoreRevision: 1,
          sourceRevision: subscriptionSetup.sourceRevision
        }
      } as never
    }

    if (
      ['organization.executeSetupSubscriptionOperation', 'organization.recoverSetupSubscriptionOperation'].includes(
        method
      )
    ) {
      return { state: 'completed' } as never
    }

    return original(method, params)
  })
  render(<test.Harness />)
  await inspect()
  await connect()
  fireEvent.click(screen.getByRole('button', { name: copy.refresh }))
  await waitFor(() =>
    expect((screen.getByRole('button', { name: copy.recover }) as HTMLButtonElement).disabled).toBe(false)
  )
  fireEvent.click(screen.getByRole('button', { name: copy.recover }))
  await waitFor(() =>
    expect(
      test.request.mock.calls.filter(([method]) => method === 'organization.recoverSetupSubscriptionOperation')
    ).toHaveLength(1)
  )
  expect(
    test.request.mock.calls.some(([method]) => method === 'organization.reconcileSetupSubscriptionOperation')
  ).toBe(false)
  expect(
    test.request.mock.calls.filter(([method]) => method === 'organization.executeSetupSubscriptionOperation')
  ).toHaveLength(1)
})
