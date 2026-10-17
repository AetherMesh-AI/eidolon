import { expect, it, vi } from 'vitest'

import { createTeamOnboardingClient } from './team-onboarding-client'
import { validateSetupBudgetApplied, validateSetupBudgetPolicy, validateSetupBudgetPreview, validateSetupBudgetState } from './team-setup-budget-client'
import type { SetupBudgetPreview, SetupBudgetState } from './team-setup-budget-client'
const configuration = { version: 2 as const, mode: 'bounded_api' as const, improveCallLimit: 4, generationCallLimit: 3, totalCallLimit: 7 }

const state: SetupBudgetState = {
  capabilityVersion: 1, state: 'ready', organizationId: 'organization', ownerId: 'owner', policyGeneration: 1,
  sourceRevision: 'a'.repeat(64), configuration: null, suggestedConfiguration: configuration, managed: false, available: true,
  latestReceipt: null, setupBudget: {
    policyVersion: 1, policy: null, usage: { legacy: 0, improveTeamInput: 0, generateTeam: 0, total: 0 },
    limits: { improveCalls: 3, generationCalls: 3, totalCalls: 3, totalTokens: 120000, maxCostUsd: null },
    remaining: { improveCalls: 3, generationCalls: 3, totalCalls: 3 }, status: 'available', reservedTokens: 0, reservedCostUsd: '0'
  }
}

const preview: SetupBudgetPreview = {
  capabilityVersion: 1, organizationId: 'organization', ownerId: 'owner', policyGeneration: 1, sourceRevision: state.sourceRevision,
  configuration, usage: state.setupBudget, previewVersion: 'b'.repeat(64), changes: [{ field: 'setup_budget', before: null, after: configuration }], warnings: ['existing_charges_retained']
}

const result = { capabilityVersion: 1, receipt: { idempotencyKey: 'apply', organizationId: 'organization', ownerId: 'owner',
  action: 'setupBudget', policyGeneration: 2, sourceRevision: state.sourceRevision, previewVersion: preview.previewVersion, configuration } }

it('validates the read-only suggestion, exact preview and confirmed receipt independently', () => {
  expect(() => validateSetupBudgetState(state, 'organization')).not.toThrow()
  expect(() => validateSetupBudgetPreview(preview, state, configuration)).not.toThrow()
  expect(() => validateSetupBudgetApplied(result, preview, 'apply')).not.toThrow()
})
it.each([
  { ...configuration, totalCallLimit: true }, { ...configuration, improveCallLimit: 8 },
  { ...configuration, generationCallLimit: -1 }, { ...configuration, totalCallLimit: 1001 },
  { ...configuration, mode: 'subscription_calls' }, { ...configuration, extra: 1 }
])('rejects unsupported or unbounded setup policy %#', value => {
  expect(() => validateSetupBudgetPolicy(value)).toThrow('invalid_setup_budget')
})
it('rejects owner, source, generation and exact-candidate mismatches', () => {
  for (const change of [{ ownerId: 'other' }, { sourceRevision: 'c'.repeat(64) }, { policyGeneration: 2 },
    { configuration: { ...configuration, improveCallLimit: 3 } }]) {
    expect(() => validateSetupBudgetPreview({ ...preview, ...change }, state, configuration)).toThrow('invalid_setup_budget')
  }

  expect(() => validateSetupBudgetApplied(result, preview, 'other')).toThrow('invalid_setup_budget')
  expect(() => validateSetupBudgetState(state, 'other')).toThrow('invalid_setup_budget')
})
it('rejects fabricated usage totals and operation counters exceeding aggregate capacity', () => {
  expect(() => validateSetupBudgetState({ ...state, setupBudget: { ...state.setupBudget,
    usage: { legacy: 2, improveTeamInput: 1, generateTeam: 1, total: 2 } } }, 'organization')).toThrow('invalid_setup_budget')
  expect(() => validateSetupBudgetState({ ...state, setupBudget: { ...state.setupBudget,
    remaining: { improveCalls: 4, generationCalls: 3, totalCalls: 3 } } }, 'organization')).toThrow('invalid_setup_budget')
})
it('does not apply suggested limits while reading or previewing and sends exact explicit apply', async () => {
  const request = vi.fn().mockResolvedValueOnce(state).mockResolvedValueOnce(preview).mockResolvedValueOnce(result)
  const client = createTeamOnboardingClient(() => ({ key: 'A', ownerKey: 'owner', profile: 'default', eligible: true, request }), () => () => {})
  await client.budget({ setupBudgetVersion: 2, organizationId: 'organization' } as never)
  await client.previewBudget(state, configuration)
  expect(request.mock.calls.map(call => call[0])).toEqual(['organization.setupBudgetPolicy', 'organization.previewSetupBudget'])
  await client.applyBudget(preview, 'apply')
  expect(request).toHaveBeenLastCalledWith('organization.applySetupBudget', {
    expectedOrganizationId: 'organization', expectedGeneration: 1, sourceRevision: state.sourceRevision,
    configuration, previewVersion: preview.previewVersion, idempotencyKey: 'apply', confirmed: true
  })
})
it('rejects older runtimes without issuing an RPC', async () => {
  const request = vi.fn()
  const client = createTeamOnboardingClient(() => ({ key: 'A', ownerKey: 'owner', profile: 'default', eligible: true, request }), () => () => {})
  await expect(client.budget({ organizationId: 'organization' } as never)).rejects.toThrow('unsupported')
  expect(request).not.toHaveBeenCalled()
})

it.each([
  { changes: [] }, { changes: [null] }, { changes: [{ field: 'other', before: null, after: configuration }] },
  { changes: [{ field: 'setup_budget', before: {}, after: configuration }] },
  { changes: [{ field: 'setup_budget', before: null, after: { ...configuration, improveCallLimit: 3 } }] },
  { changes: [{ field: 'setup_budget', before: null, after: configuration, extra: true }] }
])('rejects malformed or contradictory preview changes %#', ({ changes }) => {
  expect(() => validateSetupBudgetPreview({ ...preview, changes }, state, configuration)).toThrow('invalid_setup_budget')
})
it('requires active configuration, budget view and durable receipt to agree', () => {
  const active = { ...state, policyGeneration: 2, configuration, latestReceipt: result.receipt,
    setupBudget: { ...state.setupBudget, policyVersion: 2, policy: configuration,
      limits: { ...state.setupBudget.limits, improveCalls: 4, generationCalls: 3, totalCalls: 7 },
      remaining: { improveCalls: 4, generationCalls: 3, totalCalls: 7 } } }

  expect(() => validateSetupBudgetState(active, 'organization')).not.toThrow()

  for (const change of [
    { configuration: null }, { latestReceipt: null },
    { latestReceipt: { ...result.receipt, ownerId: 'other' } },
    { latestReceipt: { ...result.receipt, policyGeneration: 3 } },
    { latestReceipt: { ...result.receipt, configuration: { ...configuration, improveCallLimit: 3 } } },
    { setupBudget: { ...active.setupBudget, policy: { ...configuration, improveCallLimit: 3 } } },
    { setupBudget: { ...active.setupBudget, remaining: { improveCalls: 3, generationCalls: 3, totalCalls: 6 } } }
  ]) {
    expect(() => validateSetupBudgetState({ ...active, ...change }, 'organization')).toThrow('invalid_setup_budget')
  }
})
