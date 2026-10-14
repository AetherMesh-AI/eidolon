import { vi } from 'vitest'

import { createOwnerPolicyClient } from './organization-owner-policy-client'
import type { OwnerPolicyAction, OwnerPolicyDescriptor, OwnerPolicyIntent, OwnerPolicyPreview, OwnerPolicyReceipt, OwnerPolicyRequest, OwnerPolicyScope } from './organization-owner-policy-types'

export function policyDescriptor(): OwnerPolicyDescriptor {
  return {
    capabilityVersion: 1, state: 'ready', scope: { kind: 'served-profile', profile: 'research' },
    organizationId: '11111111-1111-4111-8111-111111111111', ownerId: '22222222-2222-4222-8222-222222222222',
    policyGeneration: 3, sourceRevision: 'a'.repeat(64), managedFields: [],
    capabilities: { modelRouting: true, budgetPolicy: true, budgetObservation: true },
    configuration: {
      model_routes: ['primary', 'fallback'].map(id => ({ id, provider: 'configured-provider', model: `model-${id}`, reasoning: 'high', request_types: ['work.draft', 'request.message'], evidence_level: 2, max_context_tokens: 32768, max_output_tokens: 8000 })),
      model_assignments: [
        { agent_id: 'executive', mode: 'fixed', route_ids: ['primary'], minimum_evidence: 2, max_fallbacks: 0 },
        { agent_id: 'custom-executive', mode: 'auto', route_ids: ['primary'], minimum_evidence: 1, max_fallbacks: 0 }
      ],
      max_model_calls: 20, max_total_tokens: 100000, max_cost_usd: '5.00', objective_timeout_seconds: 3600,
      max_context_tokens: 32768, max_output_tokens: 8000, completion_reserve_model_calls: 2, completion_reserve_tokens: 8000,
      completion_reserve_cost_usd: '0.50', adaptive_pacing_enabled: true, pacing_conserve_below_percent: 25,
      pacing_critical_below_percent: 10, pacing_conserve_interval_seconds: 30, pacing_critical_interval_seconds: 120,
      pacing_conserve_max_inflight: 2, pacing_critical_max_inflight: 1, model_costs: []
    },
    agents: [{ id: 'executive', name: 'Executive', role: 'executive' }, { id: 'custom-executive', name: 'Custom executive', role: 'executive' }],
    observations: [{ objectiveId: 'objective-one', revision: 0, observation: null }]
  }
}

export function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => {resolve = yes; reject = no})

  return { promise, resolve, reject }
}

/** The RPC adds served-profile scope to the Python preview and receipt envelopes. */
export function previewResponse(data: OwnerPolicyDescriptor, action: OwnerPolicyAction, request: OwnerPolicyRequest): OwnerPolicyPreview {
  const warningCodes: Record<OwnerPolicyAction, OwnerPolicyPreview['warnings'][number]['code'][]> = {
    budgetPolicy: ['no_topup', 'captured_reserves_unchanged', 'configured_prices_not_invoice'],
    modelRouting: ['owner_declared_routes', 'direct_chat_only', 'owner_chat_unchanged'],
    budgetObservation: ['objective_advisory_only', 'no_reset_or_refill']
  }

  return {
    capabilityVersion: 1, scope: data.scope, organizationId: data.organizationId, ownerId: data.ownerId,
    policyGeneration: request.expectedGeneration, sourceRevision: request.sourceRevision, previewVersion: 'b'.repeat(64),
    ...(action === 'budgetObservation' ? { observation: request.observation } : { configuration: request.configuration }),
    changes: action === 'budgetObservation'
      ? [{ field: 'observation', before: data.observations.find(item => item.objectiveId === request.observation?.objectiveId)?.observation ?? null, after: request.observation }]
      : Object.entries(request.configuration!).filter(([field, value]) => JSON.stringify(data.configuration[field as keyof typeof data.configuration]) !== JSON.stringify(value)).map(([field, after]) => ({ field, before: data.configuration[field as keyof typeof data.configuration], after })),
    warnings: [...warningCodes[action].map(code => ({ code, values: {} })), ...(action === 'budgetObservation' ? [] : [{ code: 'running_attempts_fenced' as const, values: { activeAttempts: 2 } }])],
    impact: {
      scope: action === 'budgetPolicy' ? 'objective-defaults' : action === 'modelRouting' ? 'persistent-agent-routing' : 'objective-advisory',
      evaluatedAt: '2026-10-14T12:00:00Z', activeObjectiveCount: 7, runningAttemptCount: 2,
      runningAttempts: action === 'budgetObservation' ? 'unchanged' : 'fenced-to-owner-review',
      existingObjectives: action === 'budgetPolicy' ? 'may-tighten-never-refill' : action === 'modelRouting' ? 'new-attempts' : 'advisory-only',
      futureObjectives: action === 'budgetPolicy' ? 'new-defaults' : 'unchanged',
      reservations: 'unchanged', completionReserves: 'captured-unchanged', usageProjection: 'not-estimated'
    }
  }
}

export function receiptResponse(data: OwnerPolicyDescriptor, intent: OwnerPolicyIntent): OwnerPolicyReceipt {
  return {
    capabilityVersion: 1, scope: data.scope,
    receipt: {
      idempotencyKey: intent.idempotencyKey, organizationId: intent.expectedOrganizationId, ownerId: intent.ownerId,
      action: intent.action, policyGeneration: intent.expectedGeneration + (intent.action === 'budgetObservation' ? 0 : 1),
      sourceRevision: intent.sourceRevision, previewVersion: intent.previewVersion,
      ...(intent.action === 'budgetObservation' ? { observation: intent.observation, observationRevision: intent.observation!.expectedRevision + 1 } : { configuration: intent.configuration })
    }
  }
}

let harnessId = 0

export async function policyHarness(data = policyDescriptor()) {
  const request = vi.fn().mockResolvedValue(data)

  const scope: OwnerPolicyScope = {
    key: 'captured-socket', ownerKey: `owner-policy-ui-${++harnessId}`, profile: data.scope.profile,
    eligible: true, organizationId: data.organizationId, ownerId: data.ownerId, request
  }

  const values = new Map<string, string>()
  const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => {values.set(key, value)}, removeItem: (key: string) => {values.delete(key)} }
  const newKey = vi.fn(() => `intent-${harnessId}`)
  const client = createOwnerPolicyClient(() => scope, newKey, storage)
  await client.read()

  return { client, request, scope, data, values, newKey }
}
