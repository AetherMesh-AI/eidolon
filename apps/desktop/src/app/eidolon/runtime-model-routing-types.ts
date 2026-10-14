/** Owner configuration is independent of persistent member identity and retained memory. */
export interface OrganizationModelAssignment {
  mode: 'legacy' | 'fixed' | 'auto'
  routeIds: string[]
  minimumEvidence: number | null
  maxFallbacks: number
  label: string
}

export type OrganizationReasoning = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max' | 'ultra'

export interface OrganizationModelRoute {
  id: string
  provider: string
  model: string
  reasoning: OrganizationReasoning
  requestTypes: string[]
  evidenceLevel: number
  maxContextTokens: number
  maxOutputTokens: number
  priced: boolean
}

export interface OrganizationModelRouting {
  enabled: boolean
  configurationMethod: 'owner-cli'
  ownerChat: 'separate-legacy-route'
  transport: 'direct-chat-completions-only'
  fallback: 'invalid-complete-json-tool-free-only'
  evidenceTiers: 'owner-declared'
  routes: OrganizationModelRoute[]
  policyGeneration: number
}

/** Immutable selection evidence, never reconstructed from today's assignment or catalogue. */
export interface OrganizationModelDispatch {
  id: string
  requestId: string
  routeId: string
  mode: 'fixed' | 'auto'
  selectedProvider: string
  adapterProvider: string
  model: string
  reasoning: OrganizationReasoning
  policyGeneration: number
  maxContextTokens: number
  maxOutputTokens: number
  agentId: string
  requestType: string
  evidenceLevel: number
  minimumEvidence: number
  inputBound: number
  attemptToken: string
  createdAt: string
  selectionReason: string
  pricing: { inputUsdPerMillion: string | null; outputUsdPerMillion: string | null }
}
