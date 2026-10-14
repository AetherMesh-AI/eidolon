import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter } from './runtime-adapter'
import {
  completionReserveShortfall,
  validCompletionReserve,
  validCompletionReservePolicy
} from './runtime-completion-reserve-contract'
import { validateSnapshot } from './runtime-snapshot-contract'
import type { OrganizationCompletionReserve, OrganizationSnapshot, OrganizationUsage } from './types'

const reserve: OrganizationCompletionReserve = {
  version: 1,
  policy: 'completion-v1',
  requestTypes: ['request.review', 'request.test_review', 'request.integrate', 'request.accept'],
  captured: { modelCalls: 6, tokens: 6000, configuredCostUsd: '0.000000009' },
  completionReserved: { modelCalls: 2, tokens: 2000, configuredCostUsd: '0.000000002' },
  protectedRemaining: { modelCalls: 4, tokens: 4000, configuredCostUsd: '0.000000007' },
  totalRemaining: { modelCalls: 5, tokens: 5000, configuredCostUsd: '0.000000008' },
  ordinaryRemaining: { modelCalls: 1, tokens: 1000, configuredCostUsd: '0.000000001' },
  admissionUsageKnown: true,
  unavailableReason: null
}

const usage: OrganizationUsage = {
  stages: 1,
  stageLimit: 12,
  inputTokens: 0,
  outputTokens: 0,
  usageComplete: false,
  perCallOutputLimit: 500
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

it('admits old-runtime absence and valid reserve evidence, rejecting malformed new snapshots and replacement disclosures', async () => {
  const withReserve = (value: unknown) =>
    ({
      ...snapshot,
      objectives: [{ ...snapshot.objectives[0], usage: { ...usage, completionReserve: value } }]
    }) as OrganizationSnapshot

  expect(validateSnapshot(snapshot).objectives[0].usage?.completionReserve).toBeUndefined()
  expect(validateSnapshot(withReserve(reserve)).objectives[0].usage?.completionReserve).toEqual(reserve)

  const invalid = [
    null,
    { ...reserve, version: 2 },
    { ...reserve, requestTypes: [...reserve.requestTypes.slice(1), 'request.accept'] },
    { ...reserve, captured: { ...reserve.captured, modelCalls: -1 } },
    { ...reserve, ordinaryRemaining: { ...reserve.ordinaryRemaining, configuredCostUsd: '0.000000002' } },
    { ...reserve, totalRemaining: { ...reserve.totalRemaining, configuredCostUsd: 'NaN' } },
    { ...reserve, admissionUsageKnown: false }
  ]

  for (const value of invalid) {
    expect(() => validateSnapshot(withReserve(value))).toThrow('invalid completion-reserve')
  }

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
    allowance: { modelCalls: 12, tokens: 12000, costUsd: '1' }
  }

  request.mockResolvedValue(draft)
  await expect(adapter.previewReplacement!('objective')).resolves.toEqual(draft)
  request.mockResolvedValue({
    ...draft,
    sourceUsage: { ...usage, completionReserve: reserve },
    allowance: {
      ...draft.allowance,
      completionReserve: { version: 1, policy: 'completion-v1', captured: reserve.captured }
    }
  })
  await expect(adapter.previewReplacement!('objective')).resolves.toHaveProperty(
    'sourceUsage.completionReserve',
    reserve
  )

  // A missing price does not erase known call/token admission reservations.
  const mixed = {
    ...reserve,
    admissionUsageKnown: false,
    unavailableReason: 'configured-cost-unknown',
    totalRemaining: { ...reserve.totalRemaining, configuredCostUsd: null },
    protectedRemaining: { ...reserve.protectedRemaining, configuredCostUsd: null },
    ordinaryRemaining: { ...reserve.ordinaryRemaining, configuredCostUsd: null }
  }

  expect(validateSnapshot(withReserve(mixed)).objectives[0].usage?.completionReserve).toEqual(mixed)
  request.mockResolvedValue({ ...draft, sourceUsage: { ...usage, completionReserve: mixed } })
  await expect(adapter.previewReplacement!('objective')).resolves.toHaveProperty('sourceUsage.completionReserve', mixed)
  const unknown = { modelCalls: null, tokens: null, configuredCostUsd: null }

  const unclassified = {
    ...reserve,
    admissionUsageKnown: false,
    unavailableReason: 'classification-unknown',
    completionReserved: unknown,
    totalRemaining: unknown,
    ordinaryRemaining: unknown,
    protectedRemaining: unknown
  }

  expect(validateSnapshot(withReserve(unclassified)).objectives[0].usage?.completionReserve).toEqual(unclassified)
  request.mockResolvedValue({ ...draft, sourceUsage: { ...usage, completionReserve: unclassified } })
  await expect(adapter.previewReplacement!('objective')).resolves.toHaveProperty(
    'sourceUsage.completionReserve',
    unclassified
  )
  request.mockResolvedValue({ ...draft, sourceUsage: { ...usage, completionReserve: invalid[2] } })
  await expect(adapter.previewReplacement!('objective')).rejects.toThrow('invalid completion-reserve')
  request.mockResolvedValue({
    ...draft,
    allowance: {
      ...draft.allowance,
      costUsd: '0.000000008',
      completionReserve: { version: 1, policy: 'completion-v1', captured: reserve.captured }
    }
  })
  await expect(adapter.previewReplacement!('objective')).rejects.toThrow('invalid completion-reserve')
  request.mockResolvedValue({
    ...draft,
    allowance: {
      ...draft.allowance,
      completionReserve: { version: 1, policy: 'completion-v1', captured: { ...reserve.captured, tokens: null } }
    }
  })
  await expect(adapter.previewReplacement!('objective')).rejects.toThrow('invalid completion-reserve')
})

it('preserves exact decimals, cap-reduction shortfalls, completion use beyond protection and unknown legacy headroom', () => {
  expect(validCompletionReserve({ ...reserve, requestTypes: [...reserve.requestTypes].reverse() })).toBe(true)
  expect(validCompletionReservePolicy({ version: 1, policy: 'completion-v1', captured: reserve.captured })).toBe(true)
  expect(completionReserveShortfall(reserve)).toBe(false)

  const reduced = {
    ...reserve,
    totalRemaining: { modelCalls: 1, tokens: 1000, configuredCostUsd: '0.000000001' },
    ordinaryRemaining: { modelCalls: 0, tokens: 0, configuredCostUsd: '0' }
  }

  expect(validCompletionReserve(reduced)).toBe(true)
  expect(completionReserveShortfall(reduced)).toBe(true)

  const consumed = {
    ...reserve,
    completionReserved: { modelCalls: 7, tokens: 7000, configuredCostUsd: '0.000000010' },
    protectedRemaining: { modelCalls: 0, tokens: 0, configuredCostUsd: '0' },
    ordinaryRemaining: reserve.totalRemaining
  }

  expect(validCompletionReserve(consumed)).toBe(true)

  const unknown = {
    ...reserve,
    admissionUsageKnown: false,
    unavailableReason: 'legacy-usage-unknown',
    completionReserved: { modelCalls: null, tokens: null, configuredCostUsd: null },
    protectedRemaining: { modelCalls: null, tokens: null, configuredCostUsd: null },
    totalRemaining: { modelCalls: null, tokens: null, configuredCostUsd: null },
    ordinaryRemaining: { modelCalls: null, tokens: null, configuredCostUsd: null }
  }

  expect(validCompletionReserve(unknown)).toBe(true)
  expect(completionReserveShortfall(unknown as OrganizationCompletionReserve)).toBe(false)

  const noCost = Object.fromEntries(
    ['captured', 'completionReserved', 'protectedRemaining', 'totalRemaining', 'ordinaryRemaining'].map(key => [
      key,
      { ...(reserve[key as keyof OrganizationCompletionReserve] as object), configuredCostUsd: null }
    ])
  )

  expect(validCompletionReserve({ ...reserve, ...noCost })).toBe(true)
  expect(
    validCompletionReserve({
      ...reserve,
      captured: { ...reserve.captured, configuredCostUsd: null },
      protectedRemaining: { ...reserve.protectedRemaining, configuredCostUsd: '0' },
      ordinaryRemaining: { ...reserve.ordinaryRemaining, configuredCostUsd: reserve.totalRemaining.configuredCostUsd }
    })
  ).toBe(true)
})
