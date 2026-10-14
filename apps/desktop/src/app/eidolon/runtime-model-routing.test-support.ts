import type { OrganizationModelAssignment, OrganizationModelDispatch, OrganizationModelRouting } from './runtime-model-routing-types'
import type { OrganizationExecutionAudit, OrganizationSnapshot } from './types'

export const modelRouting: OrganizationModelRouting = {
  enabled: true, configurationMethod: 'owner-cli', ownerChat: 'separate-legacy-route',
  transport: 'direct-chat-completions-only', fallback: 'invalid-complete-json-tool-free-only', evidenceTiers: 'owner-declared',
  policyGeneration: 7,
  routes: [
    { id: 'quality', provider: 'owner-provider', model: 'current-quality-model', reasoning: 'high', requestTypes: ['request.accept', 'work.draft'], evidenceLevel: 3, maxContextTokens: 32000, maxOutputTokens: 4000, priced: true },
    { id: 'economy', provider: 'owner-provider', model: 'current-economy-model', reasoning: 'none', requestTypes: ['work.draft'], evidenceLevel: 2, maxContextTokens: 16000, maxOutputTokens: 2000, priced: false }
  ]
}

export const autoAssignment: OrganizationModelAssignment = {
  mode: 'auto', routeIds: ['quality', 'economy'], minimumEvidence: 2, maxFallbacks: 1, label: 'Owner worker policy'
}

export const modelDispatch: OrganizationModelDispatch = {
  id: 'dispatch-recorded', requestId: 'draft', routeId: 'economy-v1', mode: 'auto',
  selectedProvider: 'recorded-provider', adapterProvider: 'resolved-adapter', model: 'recorded-model', reasoning: 'low',
  policyGeneration: 6, maxContextTokens: 12000, maxOutputTokens: 2000, attemptToken: 'attempt-one',
  agentId: 'worker', requestType: 'work.draft', evidenceLevel: 2, minimumEvidence: 2, inputBound: 1000,
  createdAt: '2026-10-14T07:00:00Z', selectionReason: 'cheapest-eligible-configured-ceiling',
  pricing: { inputUsdPerMillion: '0.000000001', outputUsdPerMillion: '1.25' }
}

export function modelRoutingSnapshot(): OrganizationSnapshot {
  return {
    source: 'runtime', objectives: [], tasks: [], activity: [], knowledge: [],
    connection: { scope: 'owner-socket', ownerScope: 'owner-profile', state: 'ready' },
    runtime: { state: 'ready', capabilities: ['work.draft'], maxWorkers: 1, scope: 'Submitted work', modelRouting: structuredClone(modelRouting) },
    agents: [
      { id: 'executive', identityId: 'persistent-executive', persistent: true, name: 'Executive', role: 'Executive', status: 'idle', lifecycle: 'active', responsibilities: [], capabilities: ['request.accept'], summary: 'Retained leader', provider: null, model: null,
        modelAssignment: { mode: 'fixed', routeIds: ['quality'], minimumEvidence: 3, maxFallbacks: 0, label: 'Owner executive policy' } },
      { id: 'worker', identityId: 'persistent-worker', persistent: true, name: 'Worker', role: 'Worker', status: 'idle', lifecycle: 'active', managerId: 'manager', responsibilities: [], capabilities: ['work.draft'], summary: 'Retained worker', provider: 'legacy-provider', model: 'legacy-model', modelAssignment: structuredClone(autoAssignment) }
    ],
    requests: [{ id: 'draft', objectiveId: 'objective', agentId: 'worker', type: 'work.draft', team: 'general', status: 'completed', priority: 3, attempts: 1, createdAt: modelDispatch.createdAt, routeDispatch: structuredClone(modelDispatch) }]
  }
}

export function modelRoutingAudit(): OrganizationExecutionAudit {
  return {
    requestId: 'draft', contexts: [], evidencePasses: [], modelDispatches: [structuredClone(modelDispatch)],
    modelCalls: [{ id: 'call-one', request_id: 'draft', provider: modelDispatch.selectedProvider, model: modelDispatch.model,
      dispatchId: modelDispatch.id, token: modelDispatch.attemptToken, pricing: { policyGeneration: modelDispatch.policyGeneration, ...modelDispatch.pricing }, input_limit: 1000, output_limit: 2000, reserved_cost_usd: '0.002500001', createdAt: modelDispatch.createdAt }]
  }
}
