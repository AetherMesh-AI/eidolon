import { expect, it, vi } from 'vitest'

import { createTeamOnboardingClient } from './team-onboarding-client'
import {
  createSetupSubscriptionClient,
  readSubscriptionRecovery,
  validateSubscriptionPreview,
  validateSubscriptionUsage
} from './team-setup-subscription-client'
import {
  subscriptionCatalog,
  subscriptionPolicy,
  subscriptionPolicyState,
  subscriptionPreview,
  subscriptionSetup,
  subscriptionStatus
} from './team-setup-subscription-fixtures'

it('keeps connection consent, discovery, review and apply as separate exact owner actions', async () => {
  const request = vi.fn(
    async (method: string) =>
      ({
        'organization.setupSubscriptionModels': subscriptionCatalog,
        'organization.previewSetupSubscriptionPolicy': subscriptionPreview,
        'organization.applySetupSubscriptionPolicy': {
          capabilityVersion: 1,
          historical: false,
          sourceRevision: 'e'.repeat(64),
          receipt: {
            version: 1,
            action: 'retireSetupInheritance',
            idempotencyKey: 'intent',
            organizationId: subscriptionSetup.organizationId,
            ownerId: subscriptionPolicyState.ownerId,
            priorGeneration: 1,
            appliedGeneration: 2,
            sourceRevision: subscriptionSetup.sourceRevision,
            previewDigest: subscriptionPreview.previewVersion,
            candidate: subscriptionPolicy
          }
        }
      })[method]
  )

  const client = createSetupSubscriptionClient(request as never)
  expect(request).not.toHaveBeenCalled()
  await client.beginSubscription(subscriptionSetup, 0)
  expect(request.mock.calls[0]).toEqual([
    'organization.beginSetupSubscription',
    {
      expectedOrganizationId: subscriptionSetup.organizationId,
      sourceRevision: subscriptionSetup.sourceRevision,
      expectedRevision: 0,
      confirmed: true,
      consentVersion: 1
    }
  ])
  await client.subscriptionModels(subscriptionSetup, 1)
  const preview = await client.previewSubscriptionPolicy(subscriptionPolicyState, subscriptionPolicy)
  await client.applySubscriptionPolicy(preview, 'intent')
  expect(request.mock.calls.map(call => call[0])).toEqual([
    'organization.beginSetupSubscription',
    'organization.setupSubscriptionModels',
    'organization.previewSetupSubscriptionPolicy',
    'organization.applySetupSubscriptionPolicy'
  ])
  expect(request.mock.calls[3]).toEqual([
    'organization.applySetupSubscriptionPolicy',
    {
      expectedOrganizationId: subscriptionSetup.organizationId,
      sourceRevision: subscriptionSetup.sourceRevision,
      expectedGeneration: 1,
      configuration: subscriptionPolicy,
      previewVersion: preview.previewVersion,
      idempotencyKey: 'intent',
      confirmed: true,
      acknowledgement: preview.acknowledgement
    }
  ])
})

it('rejects changed owner, source, limits, false zero usage and unsupported hard guarantees', () => {
  for (const change of [
    { ownerId: 'another-owner' },
    { sourceRevision: 'f'.repeat(64) },
    { configuration: { ...subscriptionPolicy, totalCallLimit: 8 } },
    { hardTokenGuarantee: true },
    { hardUsdGuarantee: true },
    { allOrganizationConstraints: [{ scope: 'all_work' }] },
    { acknowledgement: { ...subscriptionPreview.acknowledgement, text: 'Safe and free' } },
    { remainingCalls: { total: 7, improveTeamInput: 4, generateTeam: 3 } },
    { retiredGuarantees: [{ ...subscriptionPreview.retiredGuarantees[1], historicalOrigin: 'default' }] }
  ]) {
    expect(() =>
      validateSubscriptionPreview({ ...subscriptionPreview, ...change }, subscriptionPolicyState, subscriptionPolicy)
    ).toThrow('invalid_setup_subscription')
  }

  expect(() =>
    validateSubscriptionUsage({
      version: 1,
      tokenStatus: 'unknown',
      costStatus: 'unknown',
      inputTokens: 0,
      outputTokens: 0,
      totalTokens: 0,
      costUsd: null
    })
  ).toThrow()
  expect(() =>
    validateSubscriptionUsage({
      version: 1,
      tokenStatus: 'unknown',
      costStatus: 'unknown',
      inputTokens: null,
      outputTokens: null,
      totalTokens: null,
      costUsd: null
    })
  ).not.toThrow()
})

it('shares click serialization and permanently fences a switched owner without retrying consent or inference', async () => {
  let finish!: (value: unknown) => void

  let changed = () => {},
    ownerKey = 'owner-a'

  const request = vi.fn(
    () =>
      new Promise(resolve => {
        finish = resolve
      })
  )

  const client = createTeamOnboardingClient(
    () => ({ key: 'socket', ownerKey, profile: 'default', eligible: true, request: request as never }),
    callback => {
      changed = callback

      return () => {}
    }
  )

  const pending = client.beginSubscription(subscriptionSetup, 0)
  await expect(client.subscriptionModels(subscriptionSetup, 1)).rejects.toThrow('busy')
  ownerKey = 'owner-b'
  changed()
  ownerKey = 'owner-a'
  changed()
  finish({ stage: 'pending' })
  await expect(pending).rejects.toThrow('stale')
  await expect(
    client.generate(subscriptionSetup, { jobs: '', goals: '', namingStyle: '', idempotencyKey: 'x' })
  ).rejects.toThrow('stale')
  expect(request).toHaveBeenCalledTimes(1)
})

it('rejects stale account discovery and cross-source refresh tickets before any execute', async () => {
  const request = vi.fn(async () => ({ ...subscriptionCatalog, sourceRevision: 'f'.repeat(64) }))
  const client = createSetupSubscriptionClient(request as never)
  await expect(client.subscriptionModels(subscriptionSetup, 1)).rejects.toThrow('invalid_setup_subscription')
  expect(() =>
    client.executeSubscriptionOperation(subscriptionSetup, {
      version: 1,
      kind: 'refresh',
      sequence: '1',
      nonce: 'a'.repeat(64),
      bindingDigest: 'b'.repeat(64),
      authorizationEpoch: subscriptionPolicy.authorizationEpoch,
      priorStoreRevision: 1,
      sourceRevision: 'f'.repeat(64)
    })
  ).toThrow('invalid_setup_subscription')
  expect(request).toHaveBeenCalledTimes(1)
})

it('accepts a complete v3 allowance and rejects incomplete or hard-cap claims on all responses', async () => {
  const generationAllowance = {
    policyVersion: 3,
    status: 'available',
    remainingCalls: 5,
    remainingByOperation: { improveTeamInput: 4, generateTeam: 3 },
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
    }
  }

  let result = { ...subscriptionSetup, generationAllowance }
  const request = vi.fn(async () => result)

  const client = createTeamOnboardingClient(
    () => ({ key: 'socket', ownerKey: 'owner', profile: 'default', eligible: true, request: request as never }),
    () => () => {}
  )

  await expect(client.setup(subscriptionSetup.organizationId!)).resolves.toEqual(result)

  for (const change of [
    { setupUsage: undefined },
    { mode: 'tokens' },
    { requestTimeoutSeconds: 121 },
    { hardTokenGuarantee: true },
    { hardUsdGuarantee: true }
  ]) {
    result = { ...subscriptionSetup, generationAllowance: { ...generationAllowance, ...change } } as typeof result
    await expect(client.setup(subscriptionSetup.organizationId!)).rejects.toThrow('invalid_allowance')
  }
})

it('accepts only the closed unsent-recovery contract for the exact original request', () => {
  const expected = {
    idempotencyKey: 'same-intent',
    operation: 'improve_jobs' as const,
    sourceRevision: 'a'.repeat(64)
  }

  const recovery = { version: 1, disposition: 'retry_unsent', ...expected }

  const failure = (value: unknown) => ({
    code: 5076,
    data: { recovery: value }
  })

  expect(readSubscriptionRecovery(failure(recovery), expected)).toEqual(recovery)

  const invalid = [
    null,
    [],
    {},
    { ...recovery, version: 2 },
    { ...recovery, version: true },
    { ...recovery, operation: 'generate' },
    { ...recovery, idempotencyKey: 'another' },
    { ...recovery, sourceRevision: 'b'.repeat(64) },
    { ...recovery, disposition: 'retry' },
    { ...recovery, safe: true }
  ]

  for (const value of invalid) {
    expect(readSubscriptionRecovery(failure(value), expected)).toBeNull()
  }

  expect(readSubscriptionRecovery({ code: '5076', data: { recovery } }, expected)).toBeNull()
  expect(readSubscriptionRecovery(new Error('unavailable'), expected)).toBeNull()
})

it('fences rejected subscription responses when their authenticated scope is no longer current', async () => {
  let reject!: (value: unknown) => void
  let ownerKey = 'owner-a'

  const request = vi.fn(
    () =>
      new Promise<never>((_resolve, failure) => {
        reject = failure
      })
  )

  const client = createTeamOnboardingClient(
    () => ({
      key: 'socket',
      ownerKey,
      profile: 'default',
      eligible: true,
      request
    }),
    () => () => {}
  )

  const pending = client.improve(subscriptionSetup, {
    field: 'jobs',
    jobs: 'Research',
    goals: '',
    namingStyle: '',
    idempotencyKey: 'attempt'
  })

  ownerKey = 'owner-b'
  reject({
    code: 5076,
    data: {
      recovery: {
        version: 1,
        disposition: 'retry_unsent',
        idempotencyKey: 'attempt',
        operation: 'improve_jobs',
        sourceRevision: subscriptionSetup.sourceRevision
      }
    }
  })
  await expect(pending).rejects.toThrow('stale')
  expect(request).toHaveBeenCalledTimes(1)
  client.dispose()
})

it('uses a distinct revision-bound migration action and fails closed on malformed identity eligibility', async () => {
  const request = vi.fn(async () => subscriptionStatus)
  const client = createSetupSubscriptionClient(request as never)
  await client.beginSubscriptionHostMigration(subscriptionSetup, 7)
  expect(request.mock.calls).toEqual([
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

  for (const change of [
    { hostIdentityState: undefined },
    { canMigrateHostIdentity: undefined },
    { hostIdentityState: 'unknown' },
    { canMigrateHostIdentity: 1 },
    { canMigrateHostIdentity: true },
    { hostIdentityState: 'migration_required', canMigrateHostIdentity: true, connectionState: 'active' },
    { hostIdentityState: 'migration_required', canMigrateHostIdentity: true, cleanupPending: true },
    { hostIdentityState: 'migration_required', canMigrateHostIdentity: true, stage: 'publication_uncertain' },
    { hostIdentityState: 'migration_required', canMigrateHostIdentity: true, stage: 'pending' }
  ]) {
    request.mockResolvedValue({ ...subscriptionStatus, ...change } as never)
    await expect(client.subscriptionStatus(subscriptionSetup)).rejects.toThrow('invalid_setup_subscription')
  }

  const migration = {
    ...subscriptionStatus,
    hostIdentityState: 'migration_required',
    canMigrateHostIdentity: true
  }

  for (const change of [
    ...['allocating', 'prepared', 'admitted', 'uncertain', 'recovery_required'].map(state => ({
      operation: { ...subscriptionStatus.operation, state }
    })),
    { operation: undefined },
    { operation: { ...subscriptionStatus.operation, revision: 1 } },
    { operation: { ...subscriptionStatus.operation, verification: 'verified_current' } },
    { canCancel: true },
    { stage: 'expired' },
    { stage: 'reauthorization_required' },
    { stage: 'stale_source', canCancel: true }
  ]) {
    request.mockResolvedValue({ ...migration, ...change } as never)
    await expect(client.subscriptionStatus(subscriptionSetup)).rejects.toThrow('invalid_setup_subscription')
  }

  for (const state of ['none', 'completed', 'local_retired']) {
    const settled = {
      ...migration,
      stage: 'stale_source',
      canCancel: false,
      operation: { ...subscriptionStatus.operation, state }
    }

    request.mockResolvedValue(settled as never)
    await expect(client.subscriptionStatus(subscriptionSetup)).resolves.toEqual(settled)
  }

  // Paired absence supports older-runtime status/disconnect, never authorization.
  const legacy = { ...subscriptionStatus, hostIdentityState: undefined, canMigrateHostIdentity: undefined }
  request.mockResolvedValue(legacy)
  await expect(client.subscriptionStatus(subscriptionSetup)).resolves.toEqual(legacy)
})
