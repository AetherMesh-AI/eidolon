import { expect, it, vi } from 'vitest'

import { createSharedAllowanceClient } from './organization-shared-allowance-client'
import {
  validateSharedAllowance,
  validateSharedAllowancePreview,
  validSharedAllowanceConfiguration
} from './organization-shared-allowance-contract'
import type { SharedAllowancePreview, SharedAllowanceReceipt } from './organization-shared-allowance-types'
import { deferred } from './owner-policy-test-support'
import {
  allowanceConfiguration,
  allowanceDescriptor,
  allowanceHarness,
  allowancePreview,
  memberId,
  memberOwner
} from './shared-allowance-test-support'

it('requires explicit preview and apply, persists before send, and reconciles the canonical configuration', async () => {
  const h = await allowanceHarness(allowanceDescriptor(false))
  await h.client.apply()
  expect(h.request).toHaveBeenCalledTimes(1)
  const configuration = allowanceConfiguration()
  configuration.allocations.reverse()
  await h.client.preview('configuration', { configuration })
  expect(h.client.getSnapshot().preview?.request.configuration?.allocations[0].profile).toBe('default')
  expect(h.request.mock.calls.map(row => row[0])).toEqual([
    'organization.sharedAllowance',
    'organization.previewSharedAllowance'
  ])
  h.request.mockImplementationOnce(async (method, params) => {
    expect(h.values.size).toBe(1)
    const saved = JSON.parse([...h.values.values()][0])
    expect(params).toEqual(Object.fromEntries(Object.entries(saved).filter(([key]) => key !== 'action')))
    expect(saved.confirmed).toBe(true)

    return h.perform(method, params)
  })
  const saved = await h.client.apply()
  expect(saved?.receipt.generation).toBe(1)
  expect(h.client.getSnapshot().descriptor?.state).toBe('ready')
  expect(h.client.getSnapshot().preview).toBeNull()
  expect(h.values.size).toBe(0)
  await h.client.apply()
  expect(h.request.mock.calls.filter(row => row[0] === 'organization.configureSharedAllowance')).toHaveLength(1)
})

it.each(['member', 'managed', 'unsupported', 'ineligible'] as const)(
  'fails closed for %s authority at the request boundary',
  async mode => {
    const data = allowanceDescriptor()

    if (mode === 'member') {
      Object.assign(data, {
        authority: 'member',
        configuration: null,
        pool: null,
        candidates: [],
        capabilities: { configuration: false, observation: false }
      })
    }

    if (mode === 'managed') {
      data.managed = true
      data.capabilities = { configuration: false, observation: false }
    }

    if (mode === 'unsupported') {
      data.state = 'unsupported'
      data.capabilities = { configuration: false, observation: false }
    }

    const h = await allowanceHarness(data)

    if (mode === 'ineligible') {
      h.scope.eligible = false
    }

    await h.client.preview('configuration', { configuration: allowanceConfiguration() })
    await h.client.apply()
    expect(h.request.mock.calls.map(row => row[0])).toEqual(['organization.sharedAllowance'])
  }
)

it('rejects overallocations, unsafe profiles, zero envelopes, invalid periods and saved-member removal before preview RPC', async () => {
  const mutations = [
    (value: ReturnType<typeof allowanceConfiguration>) => {
      value.allocations[1].limits.modelCalls = 20
    },
    (value: ReturnType<typeof allowanceConfiguration>) => {
      value.allocations[0].profile = '../outside'
    },
    (value: ReturnType<typeof allowanceConfiguration>) => {
      value.allocations[0].limits.tokens = 0
    },
    (value: ReturnType<typeof allowanceConfiguration>) => {
      value.endsAt = value.startsAt
    },
    (value: ReturnType<typeof allowanceConfiguration>) => {
      value.protected.configuredCostUsd = '1'
    },
    (value: ReturnType<typeof allowanceConfiguration>) => {
      value.allocations.pop()
    }
  ]

  for (const mutate of mutations) {
    const h = await allowanceHarness()
    const configuration = allowanceConfiguration()
    mutate(configuration)
    await h.client.preview('configuration', { configuration })
    expect(h.client.getSnapshot().error).toBe('invalid')
    expect(h.request).toHaveBeenCalledTimes(1)
  }

  const exact = allowanceConfiguration()
  exact.limits.configuredCostUsd = '0.3'
  exact.allocations[0].limits.configuredCostUsd = '0.1'
  exact.allocations[1].limits.configuredCostUsd = '0.2'
  expect(validSharedAllowanceConfiguration(exact)).toBe(true)
  exact.allocations[1].limits.configuredCostUsd = '0.200000001'
  expect(validSharedAllowanceConfiguration(exact)).toBe(false)
})

it.each([
  new Error('lost acknowledgement'),
  { code: 5075, data: { reason: 'unreadable' } },
  { code: 5075, data: { reason: 'unreadable_or_corrupt' } },
  { code: 5075, data: { reason: 'new_runtime_failure' } }
])('retains and replays the immutable original intent after an ambiguous write failure %j', async failure => {
  const h = await allowanceHarness()
  await h.client.preview('configuration', { configuration: allowanceConfiguration() })
  h.request.mockImplementationOnce(async (method, params) => {
    await h.perform(method, params)
    throw failure
  })
  await h.client.apply()
  const original = h.request.mock.calls[2][1]
  expect(h.client.getSnapshot().error).toBe('uncertain')
  expect(h.values.size).toBe(1)
  expect(Object.isFrozen(h.client.getSnapshot().intent?.configuration)).toBe(true)
  h.setData({ ...allowanceDescriptor(), generation: 20, sourceRevision: 'd'.repeat(64) })
  const restored = createSharedAllowanceClient(() => h.scope, h.newKey, h.storage)
  await restored.read()
  expect(restored.getSnapshot().intent?.expectedGeneration).toBe(3)
  await restored.apply()
  expect(
    h.request.mock.calls.filter(row => row[0] === 'organization.configureSharedAllowance').map(row => row[1])
  ).toEqual([original, original])
  expect(h.newKey).toHaveBeenCalledTimes(1)
  expect(h.values.size).toBe(0)
  expect(restored.getSnapshot().descriptor?.generation).toBe(20)
})

it('keeps late responses and old pending payloads out of another profile or replacement organization', async () => {
  const h = await allowanceHarness()
  await h.client.preview('configuration', { configuration: allowanceConfiguration() })
  const pending = deferred<SharedAllowanceReceipt>()

  const receipt = (await h.perform('organization.configureSharedAllowance', {
    ...h.client.getSnapshot().preview!.request,
    previewVersion: 'b'.repeat(64),
    idempotencyKey: 'pending',
    confirmed: true
  })) as SharedAllowanceReceipt

  h.request.mockReturnValueOnce(pending.promise)
  const applying = h.client.apply()
  const initialOwnerKey = h.scope.ownerKey
  Object.assign(h.scope, {
    key: 'socket-b',
    ownerKey: 'profile-b',
    profile: 'research',
    organizationId: memberId,
    ownerId: memberOwner
  })
  expect(h.client.getSnapshot().intent).toBeNull()
  pending.resolve(receipt)
  await applying
  expect(h.client.getSnapshot().receipt).toBeNull()
  expect(h.values.size).toBe(1)
  Object.assign(h.scope, {
    key: 'socket-a-new',
    ownerKey: initialOwnerKey,
    profile: 'default',
    organizationId: memberId,
    ownerId: memberOwner
  })
  h.setData({ ...allowanceDescriptor(), organizationId: memberId, ownerId: memberOwner })
  await h.client.read()
  expect(h.client.getSnapshot().intent).toBeNull()
  Object.assign(h.scope, { organizationId: h.data.organizationId, ownerId: h.data.ownerId })
  h.setData(allowanceDescriptor())
  await h.client.read()
  expect(h.client.getSnapshot().intent).not.toBeNull()
})

it('invalidates dismissed preview replies and blocks writes when durable intent storage fails', async () => {
  const h = await allowanceHarness()
  const pending = deferred<SharedAllowancePreview>()
  h.request.mockReturnValueOnce(pending.promise)
  const previewing = h.client.preview('configuration', { configuration: allowanceConfiguration() })
  const requested = h.request.mock.calls[1][1]
  h.client.discardPreview()
  pending.resolve(allowancePreview(h.data, 'configuration', requested as never))
  await previewing
  expect(h.client.getSnapshot().preview).toBeNull()
  await h.client.preview('configuration', { configuration: allowanceConfiguration() })
  vi.spyOn(h.storage, 'setItem').mockImplementation(() => {
    throw new Error('Storage blocked')
  })
  await h.client.apply()
  expect(h.client.getSnapshot().error).toBe('storage')
  expect(h.request.mock.calls.some(row => row[0] === 'organization.configureSharedAllowance')).toBe(false)
})

it('rejects malformed authority, projection and previews, but accepts timestamp and exact decimal canonicalization', async () => {
  const h = await allowanceHarness()

  for (const change of [
    { ownerId: memberOwner },
    { capabilityVersion: 2 },
    { pool: { ...h.data.pool, protectedRemaining: null } },
    { authority: 'member' }
  ]) {
    expect(() => validateSharedAllowance({ ...h.data, ...change }, h.scope)).toThrow()
  }

  const data = allowanceDescriptor()
  data.configuration!.limits.configuredCostUsd = '0.30'
  data.configuration!.allocations[0].limits.configuredCostUsd = '0.10'
  data.configuration!.allocations[1].limits.configuredCostUsd = '0.20'

  const request = {
    expectedOrganizationId: data.organizationId,
    expectedOwnerId: data.ownerId,
    expectedGeneration: data.generation,
    sourceRevision: data.sourceRevision,
    configuration: data.configuration!
  }

  const preview = allowancePreview(data, 'configuration', request)
  preview.configuration!.startsAt = '2026-10-14T00:00:00Z'
  preview.configuration!.limits.configuredCostUsd = '0.3'
  expect(validateSharedAllowancePreview(preview, h.scope, 'configuration', request, data)).toBe(preview)
  preview.configuration!.limits.modelCalls++
  expect(() => validateSharedAllowancePreview(preview, h.scope, 'configuration', request, data)).toThrow()
})

it('records shared observations with one generation advance and never sends them as objective observations', async () => {
  const h = await allowanceHarness()

  const observation = {
    remainingPercent: 25,
    asOf: '2026-10-14T10:00:00Z',
    expiresAt: '2026-10-14T20:00:00Z',
    resetAt: null
  }

  await h.client.preview('observation', { observation })
  await h.client.apply()
  expect(h.request.mock.calls.map(row => row[0])).toEqual([
    'organization.sharedAllowance',
    'organization.previewSharedAllowanceObservation',
    'organization.recordSharedAllowanceObservation',
    'organization.sharedAllowance'
  ])
  expect(h.client.getSnapshot().descriptor?.generation).toBe(4)
  expect(h.client.getSnapshot().descriptor?.observation).toMatchObject({
    ...observation,
    advisory: true,
    scope: 'installation'
  })
})

it('fences an ABA socket transition and coalesces repeated apply clicks before acknowledgement', async () => {
  const h = await allowanceHarness()
  const pending = deferred<SharedAllowancePreview>()
  h.request.mockReturnValueOnce(pending.promise)
  const first = h.client.preview('configuration', { configuration: allowanceConfiguration() })
  const params = h.request.mock.calls.at(-1)![1]
  h.scope.key = 'temporary-socket'
  h.client.getSnapshot()
  h.scope.key = 'socket-a'
  h.client.getSnapshot()
  pending.resolve(allowancePreview(h.data, 'configuration', params as never))
  await first
  expect(h.client.getSnapshot().preview).toBeNull()
  await h.client.read()
  await h.client.preview('configuration', { configuration: allowanceConfiguration() })
  const acknowledgement = deferred<unknown>()
  h.request.mockReturnValueOnce(acknowledgement.promise)
  const applying = h.client.apply()
  await h.client.apply()
  const calls = h.request.mock.calls.filter(row => row[0] === 'organization.configureSharedAllowance')
  expect(calls).toHaveLength(1)
  acknowledgement.resolve(await h.perform(calls[0][0], calls[0][1]))
  await applying
  expect(h.values.size).toBe(0)
})
