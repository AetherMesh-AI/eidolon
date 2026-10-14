import { expect, it, vi } from 'vitest'

import { createOwnerPolicyClient } from './organization-owner-policy-client'
import {
  policyEqual,
  validateOwnerPolicy,
  validatePolicyPreview,
  validPolicyConfiguration
} from './organization-owner-policy-contract'
import type {
  OwnerPolicyAction,
  OwnerPolicyDescriptor,
  OwnerPolicyPreview,
  OwnerPolicyRequest,
  OwnerPolicyScope
} from './organization-owner-policy-types'

const identity = {
  organizationId: '11111111-1111-4111-8111-111111111111',
  ownerId: '22222222-2222-4222-8222-222222222222'
}

const descriptor = (): OwnerPolicyDescriptor => ({
  capabilityVersion: 1,
  state: 'ready',
  ...identity,
  scope: { kind: 'served-profile', profile: 'default' },
  policyGeneration: 4,
  sourceRevision: 'a'.repeat(64),
  managedFields: [],
  capabilities: { modelRouting: true, budgetPolicy: true, budgetObservation: true },
  agents: [{ id: 'executive', name: 'Lead', role: 'Executive' }],
  observations: [{ objectiveId: 'objective-1', revision: 0, observation: null }],
  configuration: {
    model_routes: [
      {
        id: 'general',
        provider: 'custom',
        model: 'configured-model',
        reasoning: 'none',
        request_types: ['work.draft'],
        evidence_level: 2,
        max_context_tokens: 32768,
        max_output_tokens: 8000
      }
    ],
    model_assignments: [
      { agent_id: 'executive', mode: 'fixed', route_ids: ['general'], minimum_evidence: 1, max_fallbacks: 0 }
    ],
    max_model_calls: 120,
    max_total_tokens: 8000000,
    max_context_tokens: 128000,
    max_output_tokens: 8000,
    max_cost_usd: null,
    objective_timeout_seconds: 86400,
    completion_reserve_model_calls: 0,
    completion_reserve_tokens: 0,
    completion_reserve_cost_usd: null,
    model_costs: [],
    adaptive_pacing_enabled: false,
    pacing_conserve_below_percent: 30,
    pacing_critical_below_percent: 10,
    pacing_conserve_interval_seconds: 60,
    pacing_critical_interval_seconds: 300,
    pacing_conserve_max_inflight: 1,
    pacing_critical_max_inflight: 1
  }
})

function preview(
  data: OwnerPolicyDescriptor,
  params: OwnerPolicyRequest,
  action: OwnerPolicyAction
): OwnerPolicyPreview {
  return {
    capabilityVersion: 1,
    ...identity,
    policyGeneration: data.policyGeneration,
    sourceRevision: data.sourceRevision,
    previewVersion: 'b'.repeat(64),
    scope: data.scope,
    ...(params.configuration
      ? { configuration: structuredClone(params.configuration) }
      : { observation: structuredClone(params.observation) }),
    changes: params.configuration
      ? Object.entries(params.configuration)
          .filter(([key, after]) => !policyEqual(data.configuration[key as keyof typeof data.configuration], after))
          .map(([field, after]) => ({
            field,
            before: data.configuration[field as keyof typeof data.configuration],
            after
          }))
      : [{ field: 'observation', before: null, after: params.observation }],
    warnings:
      action === 'budgetObservation' ? [] : [{ code: 'running_attempts_fenced', values: { activeAttempts: 0 } }],
    impact: {
      scope:
        action === 'budgetPolicy'
          ? 'objective-defaults'
          : action === 'modelRouting'
            ? 'persistent-agent-routing'
            : 'objective-advisory',
      evaluatedAt: '2026-10-14T12:00:00Z',
      activeObjectiveCount: 1,
      existingObjectives:
        action === 'budgetPolicy'
          ? 'may-tighten-never-refill'
          : action === 'modelRouting'
            ? 'new-attempts'
            : 'advisory-only',
      futureObjectives: action === 'budgetPolicy' ? 'new-defaults' : 'unchanged',
      reservations: 'unchanged',
      completionReserves: 'captured-unchanged',
      usageProjection: 'not-estimated',
      runningAttempts: action === 'budgetObservation' ? 'unchanged' : 'fenced-to-owner-review',
      runningAttemptCount: 0
    }
  }
}

function setup() {
  let data = descriptor()
  const storage = new Map<string, string>()

  const intentStorage = {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => {
      storage.set(key, value)
    },
    removeItem: (key: string) => {
      storage.delete(key)
    }
  }

  const request = vi.fn(async (method: string, params: Record<string, unknown>): Promise<unknown> => {
    if (method === 'organization.ownerPolicy') {
      return structuredClone(data)
    }

    const action = method.includes('ModelRouting')
      ? 'modelRouting'
      : method.includes('BudgetObservation')
        ? 'budgetObservation'
        : 'budgetPolicy'

    if (method.includes('preview')) {
      return preview(data, params as unknown as OwnerPolicyRequest, action)
    }

    const result = {
      capabilityVersion: 1,
      scope: data.scope,
      receipt: {
        ...identity,
        idempotencyKey: params.idempotencyKey,
        action,
        policyGeneration: Number(params.expectedGeneration) + (action === 'budgetObservation' ? 0 : 1),
        sourceRevision: params.sourceRevision,
        previewVersion: params.previewVersion,
        ...(params.configuration
          ? { configuration: params.configuration }
          : {
              observation: params.observation,
              observationRevision: Number((params.observation as { expectedRevision: number }).expectedRevision) + 1
            })
      }
    }

    if (params.configuration) {
      data = {
        ...data,
        policyGeneration: data.policyGeneration + 1,
        sourceRevision: 'c'.repeat(64),
        configuration: { ...data.configuration, ...(params.configuration as object) }
      }
    }

    return result
  })

  let scope: OwnerPolicyScope = {
    key: 'socket-a',
    ownerKey: 'route-a',
    profile: 'default',
    eligible: true,
    ...identity,
    request: request as OwnerPolicyScope['request']
  }

  const newKey = vi.fn(() => 'same-original-key')
  const client = createOwnerPolicyClient(() => scope, newKey, intentStorage)

  return {
    client,
    request,
    newKey,
    storage,
    intentStorage,
    source: () => scope,
    update: (next: OwnerPolicyDescriptor) => {
      data = next
    },
    switch: (next: Partial<OwnerPolicyScope>) => {
      scope = { ...scope, ...next }
    }
  }
}

const prepare = async (h: ReturnType<typeof setup>) => {
  await h.client.read()
  await h.client.preview('budgetPolicy', { configuration: { max_model_calls: 100 } })
}

it('requires preview then explicit Apply on the captured authority and refreshes generation after the receipt', async () => {
  const h = setup()
  h.client.subscribe(vi.fn())
  await h.client.apply()
  expect(h.request).not.toHaveBeenCalled()
  await prepare(h)
  expect(h.request.mock.calls.map(call => call[0])).toEqual([
    'organization.ownerPolicy',
    'organization.previewBudgetPolicy'
  ])
  await h.client.apply()
  expect(h.client.getSnapshot().descriptor?.policyGeneration).toBe(5)
  expect(h.client.getSnapshot().preview).toBeNull()
  expect(h.storage.size).toBe(0)
  expect(h.request.mock.calls[2][1]).toEqual({
    expectedOrganizationId: identity.organizationId,
    expectedGeneration: 4,
    sourceRevision: 'a'.repeat(64),
    configuration: { max_model_calls: 100 },
    previewVersion: 'b'.repeat(64),
    idempotencyKey: 'same-original-key',
    confirmed: true
  })
})
it('never probes ineligible/missing identity routes or writes absent capabilities and unsupported versions', async () => {
  const h = setup()
  h.switch({ eligible: false })
  await prepare(h)
  h.switch({ eligible: true, organizationId: null })
  await prepare(h)
  expect(h.request).not.toHaveBeenCalled()
  h.switch(identity)
  h.request.mockResolvedValueOnce({ ...descriptor(), capabilityVersion: 2 })
  await h.client.read()
  await h.client.preview('budgetPolicy', { configuration: { max_model_calls: 100 } })
  expect(h.client.getSnapshot().error).toBe('invalid')
  h.update({ ...descriptor(), capabilities: { ...descriptor().capabilities, budgetPolicy: false } })
  await h.client.read()
  await h.client.preview('budgetPolicy', { configuration: { max_model_calls: 100 } })
  expect(h.request).toHaveBeenCalledTimes(2)
})
it('retains exact original intent through lost acknowledgement, reload and a newer generation', async () => {
  const h = setup()
  await prepare(h)
  const real = h.request.getMockImplementation()!
  let receipt: unknown
  h.request.mockImplementationOnce(async (method, params) => {
    receipt = await real(method, params)
    throw new Error('ack lost')
  })
  await h.client.apply()
  const original = h.client.getSnapshot().intent
  const restored = createOwnerPolicyClient(h.source, h.newKey, h.intentStorage)
  await restored.read()
  expect(restored.getSnapshot().descriptor?.policyGeneration).toBe(5)
  expect(restored.getSnapshot().intent).toEqual(original)
  h.request.mockResolvedValueOnce(receipt)
  await restored.apply()
  expect(h.newKey).toHaveBeenCalledTimes(1)
  expect(restored.getSnapshot().intent).toBeNull()
  expect(
    h.request.mock.calls.filter(call => call[0] === 'organization.configureBudgetPolicy').map(call => call[1])
  ).toEqual([h.request.mock.calls[2][1], h.request.mock.calls[2][1]])
})
it('a rejected replay or unmatched receipt cannot erase an uncertain original write', async () => {
  const h = setup()
  await prepare(h)
  h.request.mockRejectedValueOnce(new Error('timeout'))
  await h.client.apply()
  h.request.mockRejectedValueOnce({ code: 5074, data: { reason: 'generation_conflict' } })
  await h.client.apply()
  expect(h.client.getSnapshot().error).toBe('uncertain')
  expect(h.storage.size).toBe(1)
  h.request.mockResolvedValueOnce({
    capabilityVersion: 1,
    scope: descriptor().scope,
    receipt: { organizationId: 'foreign' }
  })
  await h.client.apply()
  expect(h.client.getSnapshot().intent).not.toBeNull()
})
it('isolates A→B→A socket results and retains the old-owner intent for reconciliation', async () => {
  const h = setup()
  await prepare(h)
  const real = h.request.getMockImplementation()!
  let finish!: (value: unknown) => void
  h.request.mockImplementationOnce(
    () =>
      new Promise(resolve => {
        finish = resolve
      })
  )
  const applying = h.client.apply()
  const original = h.request.mock.calls[2][1]
  h.switch({ key: 'socket-b', ownerKey: 'route-b' })
  await h.client.read()
  finish(await real('organization.configureBudgetPolicy', original))
  await applying
  expect(h.client.getSnapshot().intent).toBeNull()
  expect(h.client.getSnapshot().descriptor?.policyGeneration).toBe(4)
  h.switch({ key: 'socket-a', ownerKey: 'route-a' })
  await h.client.read()
  expect(h.client.getSnapshot().intent?.idempotencyKey).toBe('same-original-key')
  expect(h.client.getSnapshot().error).toBe('uncertain')
})
it('refuses an old intent after owner replacement on the same route', async () => {
  const h = setup()
  await prepare(h)
  h.request.mockRejectedValueOnce(new Error('timeout'))
  await h.client.apply()

  const replacement = {
    organizationId: '33333333-3333-4333-8333-333333333333',
    ownerId: '44444444-4444-4444-8444-444444444444'
  }

  h.update({ ...descriptor(), ...replacement })
  h.switch(replacement)
  await h.client.read()
  const calls = h.request.mock.calls.length
  await h.client.apply()
  expect(h.request).toHaveBeenCalledTimes(calls)
  expect(h.client.getSnapshot().intent?.expectedOrganizationId).toBe(identity.organizationId)
})
it('discard invalidates late previews but never cancels an in-flight read', async () => {
  const h = setup()
  await h.client.read()
  let finish!: (value: unknown) => void
  h.request.mockImplementationOnce(
    () =>
      new Promise(resolve => {
        finish = resolve
      })
  )
  const reading = h.client.read()
  h.client.discardPreview()
  finish(descriptor())
  await reading
  expect(h.client.getSnapshot().descriptor).not.toBeNull()
  h.request.mockImplementationOnce(
    () =>
      new Promise(resolve => {
        finish = resolve
      })
  )
  const previewing = h.client.preview('budgetPolicy', { configuration: { max_model_calls: 100 } })
  h.client.discardPreview()
  finish(preview(descriptor(), h.request.mock.calls.at(-1)![1] as unknown as OwnerPolicyRequest, 'budgetPolicy'))
  await previewing
  expect(h.client.getSnapshot().preview).toBeNull()
  expect(h.client.getSnapshot().busy).toBe(false)
})
it('reconnect invalidates Apply even for the same owner until a fresh read and preview', async () => {
  const h = setup()
  await prepare(h)
  h.switch({ key: 'new-socket' })
  await h.client.apply()
  expect(h.request).toHaveBeenCalledTimes(2)
  await h.client.read()
  expect(h.client.getSnapshot().preview).toBeNull()
})
it('coalesces repeated clicks and closing cannot undo an already sent write', async () => {
  const h = setup()
  await prepare(h)
  let finish!: (value: unknown) => void
  const real = h.request.getMockImplementation()!
  h.request.mockImplementationOnce(
    () =>
      new Promise(resolve => {
        finish = resolve
      })
  )
  const applying = h.client.apply()
  h.client.discardPreview()
  await h.client.apply()
  expect(h.request).toHaveBeenCalledTimes(3)
  finish(await real('organization.configureBudgetPolicy', h.request.mock.calls[2][1]))
  await applying
  expect(h.client.getSnapshot().receipt).not.toBeNull()
})
it('fails closed before sending on storage failure and managed-field edits', async () => {
  const h = setup()

  const blocked = createOwnerPolicyClient(h.source, h.newKey, {
    ...h.intentStorage,
    setItem: () => {
      throw new Error('full')
    }
  })

  await blocked.read()
  await blocked.preview('budgetPolicy', { configuration: { max_model_calls: 100 } })
  await blocked.apply()
  expect(blocked.getSnapshot().error).toBe('storage')
  expect(h.request.mock.calls.some(call => call[0].includes('configure'))).toBe(false)
  h.update({ ...descriptor(), managedFields: ['max_model_calls'] })
  await h.client.read()
  const calls = h.request.mock.calls.length
  await h.client.preview('budgetPolicy', { configuration: { max_model_calls: 100 } })
  expect(h.request).toHaveBeenCalledTimes(calls)
  expect(h.client.getSnapshot().error).toBe('invalid')
})
it('rejects altered candidates, missing differences, incorrect impact and incomplete descriptors', () => {
  const h = setup(),
    data = descriptor()

  const request = {
    expectedOrganizationId: identity.organizationId,
    expectedGeneration: 4,
    sourceRevision: data.sourceRevision,
    configuration: { max_model_calls: 100 }
  }

  const value = preview(data, request, 'budgetPolicy')
  expect(() => validatePolicyPreview(value, h.source(), request, 'budgetPolicy', data)).not.toThrow()

  for (const bad of [
    { ...value, configuration: { max_model_calls: 999 } },
    { ...value, changes: [] },
    { ...value, impact: { ...value.impact, reservations: 'refunded' } },
    { ...value, ownerId: 'foreign' }
  ]) {
    expect(() => validatePolicyPreview(bad, h.source(), request, 'budgetPolicy', data)).toThrow()
  }

  const partial = descriptor()
  delete (partial.configuration as Partial<typeof partial.configuration>).max_total_tokens
  expect(() => validateOwnerPolicy(partial, h.source())).toThrow()
})
it('accepts the last committed observation revision without making another valid observation intent', () => {
  const h = setup(),
    data = descriptor()

  data.observations = [
    {
      objectiveId: 'objective-1',
      revision: 1000,
      observation: {
        revision: 1000,
        remainingPercent: 12,
        asOf: '2026-10-14T12:00:00Z',
        expiresAt: '2026-10-14T13:00:00Z',
        resetAt: null,
        status: 'fresh',
        source: 'owner_manual',
        scope: 'objective',
        advisory: true
      }
    }
  ]
  expect(() => validateOwnerPolicy(data, h.source())).not.toThrow()
})
it('rejects malformed authority hashes and zero generations before review', () => {
  const h = setup(),
    data = descriptor()

  for (const bad of [
    { ...data, sourceRevision: 'not-a-hash' },
    { ...data, policyGeneration: 0 }
  ]) {
    expect(() => validateOwnerPolicy(bad, h.source())).toThrow()
  }

  const request = {
    expectedOrganizationId: identity.organizationId,
    expectedGeneration: 4,
    sourceRevision: data.sourceRevision,
    configuration: { max_model_calls: 100 }
  }

  expect(() =>
    validatePolicyPreview(
      { ...preview(data, request, 'budgetPolicy'), previewVersion: 'bad' },
      h.source(),
      request,
      'budgetPolicy',
      data
    )
  ).toThrow()
})

it('accepts signed zero rate ceilings while rejecting negative decimals', () => {
  expect(validPolicyConfiguration('budgetPolicy', { completion_reserve_cost_usd: '-0.000' })).toBe(true)
  expect(validPolicyConfiguration('budgetPolicy', { completion_reserve_cost_usd: '-0.1' })).toBe(false)
  expect(
    validPolicyConfiguration('budgetPolicy', {
      model_costs: [
        { provider: 'custom', model: 'model', input_usd_per_million: '-0.001', output_usd_per_million: '1' }
      ]
    })
  ).toBe(false)
})
