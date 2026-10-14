import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter } from './runtime-adapter'
import { validAdaptivePacing } from './runtime-adaptive-pacing-contract'
import { validateSnapshot } from './runtime-snapshot-contract'
import type {
  OrganizationAdaptivePacing,
  OrganizationCompletionReserve,
  OrganizationSnapshot,
  OrganizationUsage
} from './types'

const pacing: OrganizationAdaptivePacing = {
  version: 1,
  policy: 'local-stage-start-v1',
  enabled: true,
  scope: 'objective',
  mode: 'conserve',
  reason: 'manual_observation',
  evaluatedAt: '2026-10-14T07:00:00+00:00',
  deferUntil: '2026-10-14T07:01:00+00:00',
  maxInflight: 1,
  minIntervalSeconds: 60,
  remainingPercent: 12.5,
  remaining: { modelCalls: 3, tokens: 3000, configuredCostUsd: '0.000000001' },
  protectedRemaining: { modelCalls: 4, tokens: 4000, configuredCostUsd: '0.000000002' },
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

const usage: OrganizationUsage = {
  stages: 1,
  stageLimit: 12,
  inputTokens: 0,
  outputTokens: 0,
  usageComplete: false,
  perCallOutputLimit: 500
}

const reserve: OrganizationCompletionReserve = {
  version: 1,
  policy: 'completion-v1',
  requestTypes: ['request.review', 'request.test_review', 'request.integrate', 'request.accept'],
  captured: { modelCalls: 4, tokens: 4000, configuredCostUsd: '0.000000002' },
  completionReserved: { modelCalls: 0, tokens: 0, configuredCostUsd: '0' },
  protectedRemaining: pacing.protectedRemaining,
  ordinaryRemaining: pacing.remaining,
  totalRemaining: { modelCalls: 7, tokens: 7000, configuredCostUsd: '0.000000003' },
  admissionUsageKnown: true,
  unavailableReason: null
}

const snapshot: OrganizationSnapshot = {
  source: 'runtime',
  agents: [],
  tasks: [],
  activity: [],
  knowledge: [],
  requests: [],
  objectives: [
    {
      id: 'objective',
      title: 'Scope',
      description: 'Scope',
      ownerId: 'executive',
      createdAt: '2026-10-14T00:00:00Z',
      status: 'active',
      source: 'runtime',
      usage
    }
  ],
  runtime: { state: 'ready', capabilities: [], maxWorkers: 1, scope: 'Scope' }
}

it('allows old-runtime absence but rejects malformed or contradictory pacing evidence through both read boundaries', async () => {
  const withPacing = (value: unknown, completionReserve?: OrganizationCompletionReserve) =>
    ({
      ...snapshot,
      objectives: [{ ...snapshot.objectives[0], usage: { ...usage, adaptivePacing: value, completionReserve } }]
    }) as OrganizationSnapshot

  const request = vi.fn()

  const adapter = createRuntimeAdapter({
    request,
    getScope: () => ({ key: 'socket:profile', connected: true }),
    subscribeScope: () => () => {}
  })

  const draft = {
    sourceId: 'objective',
    reasonCodes: ['model_calls'],
    sourceUsage: usage,
    allowance: { modelCalls: 12, tokens: 12000, costUsd: null }
  }

  expect(validateSnapshot(snapshot).objectives[0].usage?.adaptivePacing).toBeUndefined()
  request.mockResolvedValue(draft)
  await expect(adapter.previewReplacement!('objective')).resolves.toEqual(draft)
  expect(validateSnapshot(withPacing(pacing)).objectives[0].usage?.adaptivePacing).toEqual(pacing)
  request.mockResolvedValue({ ...draft, sourceUsage: { ...usage, adaptivePacing: pacing } })
  await expect(adapter.previewReplacement!('objective')).resolves.toHaveProperty('sourceUsage.adaptivePacing', pacing)

  const badObservation = (patch: object) => ({ ...pacing, observation: { ...pacing.observation!, ...patch } })

  const invalid = [
    null,
    {},
    { ...pacing, version: 2 },
    { ...pacing, scope: 'provider' },
    { ...pacing, limitation: 'shared_quota' },
    { ...pacing, enabled: false },
    { ...pacing, mode: 'auto' },
    { ...pacing, reason: 'provider_quota' },
    { ...pacing, reason: 'disabled' },
    { ...pacing, maxInflight: 0 },
    { ...pacing, maxInflight: 5 },
    { ...pacing, maxInflight: 1.5 },
    { ...pacing, minIntervalSeconds: 0 },
    { ...pacing, minIntervalSeconds: 3601 },
    { ...pacing, remainingPercent: null },
    { ...pacing, remainingPercent: NaN },
    { ...pacing, remainingPercent: -1 },
    { ...pacing, evaluatedAt: 'yesterday' },
    { ...pacing, evaluatedAt: '2026-10-14T07:00:00' },
    { ...pacing, evaluatedAt: '2026-02-30T07:00:00Z' },
    { ...pacing, evaluatedAt: '2026-10-13T24:00:00Z' },
    { ...pacing, deferUntil: pacing.evaluatedAt },
    { ...pacing, deferUntil: undefined },
    { ...pacing, remaining: { ...pacing.remaining, modelCalls: -1 } },
    { ...pacing, protectedRemaining: { ...pacing.protectedRemaining, tokens: Number.MAX_SAFE_INTEGER + 1 } },
    { ...pacing, remaining: { ...pacing.remaining, configuredCostUsd: '1e-9' } },
    { ...pacing, observation: undefined },
    { ...pacing, observation: null },
    badObservation({ revision: 0 }),
    badObservation({ source: 'provider' }),
    badObservation({ scope: 'subscription' }),
    badObservation({ advisory: false }),
    badObservation({ remainingPercent: 101 }),
    badObservation({ remainingPercent: 10 }),
    badObservation({ asOf: '2026-10-14T07:30:00Z' }),
    badObservation({ expiresAt: pacing.observation!.asOf }),
    badObservation({ resetAt: '2026-10-14T05:00:00Z' }),
    badObservation({ status: 'expired' }),
    { ...pacing, reason: 'stale_observation' },
    { ...pacing, mode: 'unknown', reason: 'budget_unknown', remainingPercent: null, deferUntil: null }
  ]

  for (const value of invalid) {
    expect(() => validateSnapshot(withPacing(value)), JSON.stringify(value)).toThrow('invalid adaptive-pacing')
    request.mockResolvedValue({ ...draft, sourceUsage: { ...usage, adaptivePacing: value } })
    await expect(adapter.previewReplacement!('objective')).rejects.toThrow('invalid adaptive-pacing')
  }

  expect(validateSnapshot(withPacing(pacing, reserve)).objectives[0].usage?.adaptivePacing).toEqual(pacing)

  for (const key of ['remaining', 'protectedRemaining'] as const) {
    const contradictory = { ...pacing, [key]: { ...pacing[key], modelCalls: pacing[key].modelCalls! + 1 } }
    expect(() => validateSnapshot(withPacing(contradictory, reserve))).toThrow('invalid adaptive-pacing')
    request.mockResolvedValue({
      ...draft,
      sourceUsage: { ...usage, adaptivePacing: contradictory, completionReserve: reserve }
    })
    await expect(adapter.previewReplacement!('objective')).rejects.toThrow('invalid adaptive-pacing')
  }
})

it('retains stale advisory provenance, exact local amounts, default-off and unknown states without treating a reset as refill', () => {
  expect(validAdaptivePacing(pacing)).toBe(true)

  const microseconds = {
    ...pacing,
    evaluatedAt: '2026-10-14T07:00:00.123001+00:00',
    deferUntil: '2026-10-14T07:00:00.123999+00:00',
    observation: {
      ...pacing.observation!,
      asOf: '2026-10-14T07:00:00.123000+00:00',
      expiresAt: '2026-10-14T07:00:00.123002+00:00',
      resetAt: '2026-10-14T07:00:00.123003+00:00'
    }
  }

  expect(validAdaptivePacing(microseconds)).toBe(true)
  expect(
    validAdaptivePacing({
      ...microseconds,
      evaluatedAt: microseconds.observation.expiresAt,
      reason: 'stale_observation',
      observation: { ...microseconds.observation, status: 'expired' }
    })
  ).toBe(true)
  expect(
    validAdaptivePacing({
      ...microseconds,
      evaluatedAt: microseconds.observation.resetAt,
      reason: 'stale_observation',
      observation: { ...microseconds.observation, status: 'reset_unconfirmed' }
    })
  ).toBe(true)
  expect(validAdaptivePacing({ ...microseconds, deferUntil: microseconds.evaluatedAt })).toBe(false)
  expect(
    validAdaptivePacing(
      {
        ...pacing,
        remaining: { ...pacing.remaining, configuredCostUsd: '0.1' },
        protectedRemaining: { ...pacing.protectedRemaining, configuredCostUsd: '0.2' }
      },
      {
        ...reserve,
        captured: { ...reserve.captured, configuredCostUsd: '0.20' },
        ordinaryRemaining: { ...reserve.ordinaryRemaining, configuredCostUsd: '0.10' },
        protectedRemaining: { ...reserve.protectedRemaining, configuredCostUsd: '0.20' },
        totalRemaining: { ...reserve.totalRemaining, configuredCostUsd: '0.30' }
      }
    )
  ).toBe(true)

  const expired = {
    ...pacing,
    evaluatedAt: '2026-10-14T08:00:00Z',
    deferUntil: null,
    reason: 'stale_observation',
    observation: { ...pacing.observation!, status: 'expired' }
  }

  expect(validAdaptivePacing(expired)).toBe(true)
  expect(
    validAdaptivePacing({
      ...expired,
      evaluatedAt: '2026-10-14T09:00:00Z',
      observation: { ...pacing.observation!, status: 'reset_unconfirmed' }
    })
  ).toBe(true)
  // Reported resets have precedence even when the observation has not expired.
  expect(
    validAdaptivePacing({
      ...expired,
      observation: {
        ...pacing.observation!,
        resetAt: '2026-10-14T07:30:00Z',
        expiresAt: '2026-10-14T10:00:00Z',
        status: 'reset_unconfirmed'
      }
    })
  ).toBe(true)
  expect(
    validAdaptivePacing({
      ...pacing,
      reason: 'local_headroom',
      observation: null,
      mode: 'normal',
      minIntervalSeconds: 0,
      deferUntil: null
    })
  ).toBe(true)
  expect(
    validAdaptivePacing({
      ...pacing,
      mode: 'critical',
      remainingPercent: 0,
      observation: { ...pacing.observation!, remainingPercent: 0, resetAt: null }
    })
  ).toBe(true)
  const unknown = { modelCalls: null, tokens: null, configuredCostUsd: null }

  const unavailable = {
    ...pacing,
    mode: 'unknown',
    reason: 'budget_unknown',
    remainingPercent: null,
    remaining: unknown,
    protectedRemaining: unknown,
    minIntervalSeconds: 0,
    deferUntil: null
  }

  expect(validAdaptivePacing(unavailable)).toBe(true)
  expect(validAdaptivePacing({ ...unavailable, enabled: false, mode: 'off', reason: 'disabled' })).toBe(true)
  expect(
    validAdaptivePacing({
      ...pacing,
      enabled: false,
      mode: 'off',
      reason: 'disabled',
      minIntervalSeconds: 0,
      deferUntil: null
    })
  ).toBe(true)
  expect(
    validAdaptivePacing({
      ...pacing,
      remaining: { ...pacing.remaining, configuredCostUsd: null },
      protectedRemaining: { ...pacing.protectedRemaining, configuredCostUsd: null }
    })
  ).toBe(true)
})
