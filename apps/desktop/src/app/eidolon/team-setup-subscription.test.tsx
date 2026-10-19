import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { setupSubscriptionCopy } from '@/i18n/organization-team-setup-subscription'

import { createTeamOnboardingClient } from './team-onboarding-client'
import { TeamSetupSubscription } from './team-setup-subscription'
import type { SubscriptionStatus } from './team-setup-subscription-client'
import {
  subscriptionCatalog,
  subscriptionPolicy,
  subscriptionPolicyState,
  subscriptionPreview,
  subscriptionSetup,
  subscriptionStatus
} from './team-setup-subscription-fixtures'

const copy = setupSubscriptionCopy.en

function rig(initialStatus: Partial<SubscriptionStatus> = {}) {
  let setup = structuredClone(subscriptionSetup),
    connection = { ...structuredClone(subscriptionStatus), ...initialStatus },
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

    if (['organization.beginSetupSubscription', 'organization.beginSetupSubscriptionHostMigration'].includes(method)) {
      connection = {
        ...connection,
        connectionState: nextStage === 'active' ? 'active' : 'absent',
        connectionRevision: nextStage === 'active' ? connection.connectionRevision + 1 : connection.connectionRevision,
        stage: nextStage === 'active' ? 'connected' : 'pending',
        hostIdentityState: nextStage === 'active' ? 'current' : connection.hostIdentityState,
        canMigrateHostIdentity: false,
        attemptId: 'synthetic-attempt'
      }

      return { stage: connection.stage }
    }

    if (method === 'organization.cancelSetupSubscription') {
      connection = {
        ...connection,
        stage: 'cancelled',
        canMigrateHostIdentity: connection.hostIdentityState === 'migration_required'
      }

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

  function Harness({ ownerClient = client }: { ownerClient?: typeof client }) {
    const [currentSetup, setSetup] = useState(setup)

    return (
      <I18nProvider>
        <TeamSetupSubscription
          client={ownerClient}
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
    setSourceRevision: (sourceRevision: string) => {
      setup = { ...setup, sourceRevision }
    },
    setConnection: (change: Partial<SubscriptionStatus>) => {
      connection = { ...connection, ...change }
    },
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

it('keeps the model label independent of catalog options and the selected model', async () => {
  const test = rig()
  render(<test.Harness />)
  await inspect()
  await connect()
  const model = screen.getByRole('combobox', { name: copy.model }) as HTMLSelectElement
  const label = model.labels?.[0]

  expect(label?.textContent).toBe(copy.model)
  expect(label?.htmlFor).toBe(model.id)
  expect(label?.control).toBe(model)
  expect(screen.getByLabelText(copy.model, { exact: true })).toBe(model)
  fireEvent.change(model, { target: { value: '' } })
  expect(label?.textContent).toBe(copy.model)
  expect(screen.getByRole('combobox', { name: copy.model })).toBe(model)
  fireEvent.change(model, { target: { value: subscriptionPolicy.modelSlug } })
  expect(label?.textContent).toBe(copy.model)
  expect(screen.getByLabelText(copy.model, { exact: true })).toBe(model)
})

it.each(['admitted', 'uncertain'])(
  'allows explicit local disconnect after reopening a refresh in %s state without retrying it',
  async stage => {
    const test = rig(),
      original = test.request.getMockImplementation()!

    const ticket = {
      version: 1,
      sequence: '1',
      nonce: 'b'.repeat(64),
      kind: 'refresh',
      bindingDigest: 'c'.repeat(64),
      authorizationEpoch: subscriptionPolicy.authorizationEpoch,
      priorStoreRevision: 7,
      sourceRevision: subscriptionSetup.sourceRevision
    }

    let connection = {
      ...subscriptionStatus,
      connectionRevision: 7,
      connectionState: 'refreshing',
      cleanupPending: false,
      operation: { state: stage, ticket: ticket as typeof ticket | null, revision: 7, verification: 'metadata_only' }
    }

    const retired = {
      state: 'local_retired',
      ticket: null,
      revision: 8,
      verification: 'verified_current',
      receipt: { kind: 'local', revision: 8, localRetired: true, remoteRevocation: 'unconfirmed' }
    }

    test.request.mockImplementation(async (method, params) => {
      if (method === 'organization.setupSubscriptionStatus') {
        return structuredClone(connection) as never
      }

      if (method === 'organization.recoverSetupSubscriptionOperation') {
        return { ...connection.operation, verification: 'verified_current' } as never
      }

      if (method === 'organization.prepareSetupSubscriptionOperation') {
        expect(params).toEqual({
          expectedOrganizationId: subscriptionSetup.organizationId,
          sourceRevision: subscriptionSetup.sourceRevision,
          kind: 'revoke',
          expectedRevision: 7,
          confirmed: true
        })
        connection = {
          ...connection,
          connectionState: 'disconnected',
          connectionRevision: 8,
          operation: { state: 'none', ticket: null, revision: 8, verification: 'metadata_only' }
        }

        return retired as never
      }

      return original(method, params)
    })
    render(<test.Harness />)
    fireEvent.click(screen.getByRole('button', { name: copy.inspect }))
    await waitFor(() =>
      expect(
        test.request.mock.calls.filter(([method]) => method === 'organization.setupSubscriptionStatus')
      ).toHaveLength(1)
    )
    expect(screen.queryByRole('button', { name: copy.refresh })).toBeNull()
    expect(screen.queryByRole('button', { name: copy.loadModels })).toBeNull()
    expect(screen.queryByLabelText(copy.consentCheck)).toBeNull()
    await waitFor(() =>
      expect((screen.getByRole('button', { name: copy.recover }) as HTMLButtonElement).disabled).toBe(false)
    )
    fireEvent.click(screen.getByRole('button', { name: copy.recover }))
    await waitFor(() =>
      expect(
        test.request.mock.calls.filter(([method]) => method === 'organization.setupSubscriptionStatus')
      ).toHaveLength(2)
    )
    expect(connection.connectionState).toBe('refreshing')
    expect(
      test.request.mock.calls.some(([method]) => method === 'organization.prepareSetupSubscriptionOperation')
    ).toBe(false)
    expect(
      test.request.mock.calls.some(([method]) => method === 'organization.reconcileSetupSubscriptionOperation')
    ).toBe(false)
    await waitFor(() =>
      expect((screen.getByRole('button', { name: copy.disconnect }) as HTMLButtonElement).disabled).toBe(false)
    )
    fireEvent.click(screen.getByRole('button', { name: copy.disconnect }))
    await screen.findByLabelText(copy.consentCheck)
    expect((screen.getByLabelText(copy.consentCheck) as HTMLInputElement).checked).toBe(false)
    expect((screen.getByRole('button', { name: copy.continue }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.queryByRole('button', { name: copy.disconnect })).toBeNull()
    expect(screen.queryByRole('button', { name: copy.refresh })).toBeNull()
    expect(screen.getByText(copy.disconnectHelp)).toBeTruthy()
    expect(
      test.request.mock.calls.filter(([method]) => method === 'organization.prepareSetupSubscriptionOperation')
    ).toHaveLength(1)
    expect(
      test.request.mock.calls.some(([method]) => method === 'organization.executeSetupSubscriptionOperation')
    ).toBe(false)
    expect(
      test.request.mock.calls.some(([method]) =>
        [
          'organization.beginSetupSubscription',
          'organization.setupSubscriptionModels',
          'organization.generateTeam'
        ].includes(method)
      )
    ).toBe(false)
  }
)

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

it.each(['publication_uncertain', 'reconciliation_required'])(
  'reconciles %s locally before offering explicit consent without claiming remote revocation',
  async stage => {
    const test = rig()
    const original = test.request.getMockImplementation()!
    let recovered = false
    test.request.mockImplementation(async (method, params) => {
      if (method === 'organization.setupSubscriptionStatus') {
        return {
          ...subscriptionStatus,
          attemptId: 'saved-attempt',
          stage: recovered ? 'locally_retired' : stage,
          connectionRevision: 7,
          connectionState: recovered ? 'disconnected' : 'absent',
          cleanupPending: !recovered
        } as never
      }

      if (method === 'organization.recoverSetupSubscriptionOperation') {
        recovered = true

        return {
          state: 'local_retired',
          ticket: null,
          revision: 7,
          verification: 'verified_current',
          receipt: {
            kind: 'local',
            revision: 7,
            localRetired: true,
            remoteRevocation: 'unconfirmed'
          }
        } as never
      }

      return original(method, params)
    })
    render(<test.Harness />)
    fireEvent.click(screen.getByRole('button', { name: copy.inspect }))
    await waitFor(() => expect(screen.getByRole('button', { name: copy.recover })).toHaveProperty('disabled', false))
    const recover = screen.getByRole('button', { name: copy.recover })
    expect(screen.getByText(copy.recoverHelp)).toBeTruthy()
    expect(screen.getByText(copy.authorizationRecoveryRequired)).toBeTruthy()
    expect(screen.queryByLabelText(copy.consentCheck)).toBeNull()
    expect(screen.queryByRole('button', { name: copy.continue })).toBeNull()
    fireEvent.click(recover)
    await screen.findByText(copy.locallyRetired)
    expect(screen.getByLabelText(copy.consentCheck)).toHaveProperty('checked', false)
    expect(screen.getByRole('button', { name: copy.continue })).toHaveProperty('disabled', true)
    expect(
      test.request.mock.calls.filter(([method]) => method === 'organization.recoverSetupSubscriptionOperation')
    ).toHaveLength(1)
    expect(
      test.request.mock.calls.some(([method]) =>
        [
          'organization.beginSetupSubscription',
          'organization.improveTeamInput',
          'organization.generateTeam',
          'organization.executeSetupSubscriptionOperation',
          'organization.prepareSetupSubscriptionOperation'
        ].includes(method)
      )
    ).toBe(false)
  }
)

const legacyIdentity: Partial<SubscriptionStatus> = {
  hostIdentityState: 'migration_required',
  canMigrateHostIdentity: true,
  connectionRevision: 7,
  connectionState: 'disconnected',
  operation: { state: 'none', ticket: null, revision: 7, verification: 'metadata_only' }
}

async function inspectMigration() {
  fireEvent.click(screen.getByRole('button', { name: copy.inspect }))
  await screen.findByLabelText(copy.hostMigrationCheck)
}

it('requires scoped explicit host-change consent and never reuses normal consent or activates a policy', async () => {
  const test = rig(legacyIdentity)
  render(<test.Harness />)
  expect(test.request).not.toHaveBeenCalled()
  await inspectMigration()
  expect(screen.getByText(copy.hostMigrationConsent)).toBeTruthy()
  expect(screen.getByText(copy.consent)).toBeTruthy()
  expect(screen.queryByLabelText(copy.consentCheck)).toBeNull()
  expect(screen.queryByRole('button', { name: copy.continue })).toBeNull()
  const migrate = screen.getByRole('button', { name: copy.hostMigrationContinue })
  expect(migrate).toHaveProperty('disabled', true)
  fireEvent.click(migrate)
  expect(test.request.mock.calls.map(([method]) => method)).toEqual([
    'organization.teamSetup',
    'organization.setupSubscriptionStatus',
    'organization.setupSubscriptionPolicy'
  ])
  fireEvent.click(screen.getByLabelText(copy.hostMigrationCheck))
  fireEvent.click(migrate)
  fireEvent.click(migrate)
  await screen.findByRole('button', { name: copy.loadModels })
  const auth = test.request.mock.calls.filter(([method]) => method.startsWith('organization.begin'))
  expect(auth).toEqual([
    [
      'organization.beginSetupSubscriptionHostMigration',
      {
        expectedOrganizationId: subscriptionSetup.organizationId,
        sourceRevision: subscriptionSetup.sourceRevision,
        expectedRevision: 7,
        confirmed: true,
        consentVersion: 2
      }
    ]
  ])
  expect(
    test.request.mock.calls.every(
      ([method]) =>
        ![
          'organization.setupSubscriptionModels',
          'organization.applySetupSubscriptionPolicy',
          'organization.generateTeam',
          'organization.improveTeamInput'
        ].includes(method)
    )
  ).toBe(true)
  expect(screen.queryByLabelText(copy.hostMigrationCheck)).toBeNull()
  expect(screen.getByText(copy.unknown)).toBeTruthy()
})

it.each([
  { connectionState: 'active' },
  { connectionState: 'refreshing' },
  { stage: 'pending', attemptId: 'unfinished' },
  { stage: 'exchanging', attemptId: 'unfinished' },
  { stage: 'publication_uncertain', cleanupPending: true },
  { stage: 'reconciliation_required', cleanupPending: true },
  { cleanupPending: true },
  { stage: 'expired', attemptId: 'unfinished', canCancel: true },
  { stage: 'idle' }
] satisfies Partial<SubscriptionStatus>[])(
  'does not offer migration or ordinary consent when server eligibility is false: %j',
  async change => {
    const test = rig({ ...legacyIdentity, ...change, canMigrateHostIdentity: false })
    render(<test.Harness />)
    fireEvent.click(screen.getByRole('button', { name: copy.inspect }))
    await waitFor(() => expect(test.refreshed).toHaveBeenCalled())
    expect(screen.queryByLabelText(copy.hostMigrationCheck)).toBeNull()
    expect(screen.queryByRole('button', { name: copy.hostMigrationContinue })).toBeNull()
    expect(screen.queryByLabelText(copy.consentCheck)).toBeNull()
    expect(test.request.mock.calls.some(([method]) => method.startsWith('organization.begin'))).toBe(false)

    if ('canCancel' in change && change.canCancel) {
      expect(screen.getByRole('button', { name: copy.cancelConsent })).toHaveProperty('disabled', false)
    }
  }
)

it('requires renewed migration consent after cancellation, inspection and editor restart', async () => {
  const test = rig(legacyIdentity)
  test.setPending()
  const view = render(<test.Harness />)
  await inspectMigration()
  fireEvent.click(screen.getByLabelText(copy.hostMigrationCheck))
  fireEvent.click(screen.getByRole('button', { name: copy.check }))
  await waitFor(() => expect(screen.getByRole('button', { name: copy.check })).toHaveProperty('disabled', false))
  expect(screen.getByLabelText(copy.hostMigrationCheck)).toHaveProperty('checked', false)
  fireEvent.click(screen.getByLabelText(copy.hostMigrationCheck))
  fireEvent.click(screen.getByRole('button', { name: copy.hostMigrationContinue }))
  await screen.findByText(copy.pending)
  expect(screen.queryByLabelText(copy.hostMigrationCheck)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: copy.cancelConsent }))
  await screen.findByLabelText(copy.hostMigrationCheck)
  expect(screen.getByLabelText(copy.hostMigrationCheck)).toHaveProperty('checked', false)
  fireEvent.click(screen.getByLabelText(copy.hostMigrationCheck))
  view.unmount()
  render(<test.Harness />)
  await inspectMigration()
  expect(screen.getByLabelText(copy.hostMigrationCheck)).toHaveProperty('checked', false)
  expect(test.request.mock.calls.filter(([method]) => method.startsWith('organization.begin'))).toHaveLength(1)
})

it.each(['source', 'revision', 'eligibility', 'owner', 'failure'] as const)(
  'does not replay migration after %s changes and clears the prior confirmation',
  async change => {
    const test = rig(legacyIdentity)
    render(<test.Harness />)
    await inspectMigration()
    fireEvent.click(screen.getByLabelText(copy.hostMigrationCheck))

    if (change === 'source') {
      test.setSourceRevision('f'.repeat(64))
    } else if (change === 'revision') {
      test.setConnection({ connectionRevision: 8 })
    } else if (change === 'eligibility') {
      test.setConnection({ canMigrateHostIdentity: false })
    } else if (change === 'owner') {
      test.client.dispose()
    } else {
      const original = test.request.getMockImplementation()!
      test.request.mockImplementation(async (method, params) => {
        if (method === 'organization.beginSetupSubscriptionHostMigration') {
          throw new Error('reply lost')
        }

        return original(method, params)
      })
    }

    fireEvent.click(screen.getByRole('button', { name: copy.hostMigrationContinue }))

    if (change !== 'owner') {
      await waitFor(() => expect(screen.getByRole('button', { name: copy.check })).toHaveProperty('disabled', false))
      const checkbox = screen.queryByLabelText(copy.hostMigrationCheck)

      if (checkbox) {
        expect(checkbox).toHaveProperty('checked', false)
      }
    }

    expect(test.request.mock.calls.filter(([method]) => method.startsWith('organization.begin'))).toHaveLength(
      change === 'failure' ? 1 : 0
    )
  }
)

it('preserves older-runtime status and disconnect while withholding unverifiable authorization', async () => {
  const test = rig({ hostIdentityState: undefined, canMigrateHostIdentity: undefined })
  render(<test.Harness />)
  fireEvent.click(screen.getByRole('button', { name: copy.inspect }))
  await screen.findByText(copy.identityUnavailable)
  expect(screen.queryByLabelText(copy.consentCheck)).toBeNull()
  expect(screen.queryByLabelText(copy.hostMigrationCheck)).toBeNull()
  test.setConnection({ connectionState: 'active' })
  fireEvent.click(screen.getByRole('button', { name: copy.check }))
  await screen.findByRole('button', { name: copy.disconnect })
  expect(test.request.mock.calls.some(([method]) => method.startsWith('organization.begin'))).toBe(false)
})

it.each(['absent', 'active'] as const)(
  'keeps %s lifecycle visible when only host identity metadata needs recovery',
  async connectionState => {
    const test = rig({ hostIdentityState: 'recovery_required', canMigrateHostIdentity: false, connectionState })
    render(<test.Harness />)
    fireEvent.click(screen.getByRole('button', { name: copy.inspect }))
    await screen.findByText(copy.identityRecoveryRequired)
    expect(screen.queryByLabelText(copy.consentCheck)).toBeNull()
    expect(screen.queryByLabelText(copy.hostMigrationCheck)).toBeNull()

    if (connectionState === 'active') {
      expect(screen.getByRole('button', { name: copy.disconnect })).toHaveProperty('disabled', false)
    }

    expect(test.request.mock.calls.some(([method]) => method.startsWith('organization.begin'))).toBe(false)
  }
)

it('does not carry host migration confirmation into a replacement owner client', async () => {
  const test = rig(legacyIdentity)
  const replacement = rig(legacyIdentity)
  const view = render(<test.Harness />)
  await inspectMigration()
  fireEvent.click(screen.getByLabelText(copy.hostMigrationCheck))
  expect(screen.getByLabelText(copy.hostMigrationCheck)).toHaveProperty('checked', true)
  view.rerender(<test.Harness ownerClient={replacement.client} />)
  expect(screen.getByLabelText(copy.hostMigrationCheck)).toHaveProperty('checked', false)
  expect(screen.getByRole('button', { name: copy.hostMigrationContinue })).toHaveProperty('disabled', true)
  expect(replacement.request).not.toHaveBeenCalled()
})
