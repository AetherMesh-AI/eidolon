import type { MetadataScope } from './organization-metadata-client'
import { validateSetupBudgetApplied, validateSetupBudgetPolicy, validateSetupBudgetPreview, validateSetupBudgetState } from './team-setup-budget-client'
import type { SetupBudgetPolicy, SetupBudgetPreview, SetupBudgetState } from './team-setup-budget-client'

export interface TeamMember {
  id: string
  name: string
  role: 'Manager' | 'Worker'
  manager_id: string
  team: string
  capabilities: string[]
  purpose: string
  responsibilities: string[]
}
export interface TeamDraft {
  executive: { name: string; purpose: string; responsibilities: string[] }
  members: TeamMember[]
}
export interface TeamReadiness {
  status: 'unchecked' | 'blocked'
  blockers: string[]
  warnings?: string[]
  modelRouting: 'unchanged'
  backgroundEnabled: boolean
}
export interface TeamGenerationAllowance {
  policyVersion?: 2
  remainingByOperation?: { improveTeamInput: number; generateTeam: number }
  status: 'available' | 'exhausted' | 'running' | 'uncertain' | 'blocked'
  remainingCalls: number
}
export interface TeamSetup {
  generationAllowance?: TeamGenerationAllowance
  inputImprovementVersion?: 1
  setupBudgetVersion?: 2
  capabilityVersion: 1
  state: 'available' | 'configured' | 'unconfigured' | 'unsupported'
  organizationId?: string
  sourceRevision?: string
  supportAgents?: { id: string; name: string; role: string }[]
  presets: { id: string; name: string; team: TeamDraft }[]
  readiness: TeamReadiness
}
export interface TeamInputSuggestion {
  generationAllowance?: TeamGenerationAllowance
  capabilityVersion: 1
  organizationId: string
  sourceRevision: string
  field: 'jobs' | 'goals'
  suggestion: string
  assumptions: string[]
}
export interface TeamPreview {
  generationAllowance?: TeamGenerationAllowance
  capabilityVersion: 1
  organizationId: string
  sourceRevision: string
  previewVersion: string
  team: TeamDraft
  supportAgents?: { id: string; name: string; role: string }[]
  executiveModelMode: 'execFixed'
  readiness: TeamReadiness
  warnings: string[]
}
export interface TeamReceipt {
  capabilityVersion: 1
  state: 'created'
  organizationId: string
  team: TeamDraft
  readiness: TeamReadiness
  receipt: { idempotencyKey: string; previewVersion: string }
}

function validateGenerationAllowance(value: unknown) {
  if (value === undefined) {return}

  if (!value || typeof value !== 'object' || Array.isArray(value)) {throw new Error('invalid_allowance')}
  const allowance = value as Record<string, unknown>

  if (
    typeof allowance.status !== 'string' ||
    !['available', 'exhausted', 'running', 'uncertain', 'blocked'].includes(allowance.status) ||
    !Number.isInteger(allowance.remainingCalls) ||
    Number(allowance.remainingCalls) < 0 || Number(allowance.remainingCalls) > (allowance.policyVersion === 2 ? 1000 : 3) ||
    (allowance.status === 'available' && allowance.remainingCalls === 0)
  ) {throw new Error('invalid_allowance')}

  if ('policyVersion' in allowance || 'remainingByOperation' in allowance) {
    const operations = allowance.remainingByOperation as Record<string, unknown> | undefined

    if (allowance.policyVersion !== 2 || !operations || typeof operations !== 'object' || Array.isArray(operations) ||
      Object.keys(operations).sort().join(',') !== 'generateTeam,improveTeamInput' ||
      !['generateTeam', 'improveTeamInput'].every(key => Number.isInteger(operations[key]) &&
        Number(operations[key]) >= 0 && Number(operations[key]) <= Number(allowance.remainingCalls))) {
      throw new Error('invalid_allowance')
    }
  }
}

/** One captured authenticated socket; stale results never authorize a later write. */
export function createTeamOnboardingClient(
  readScope: () => MetadataScope,
  subscribe: (changed: () => void) => () => void
) {
  const origin = readScope()
  let invalid = !origin.eligible
  let disposed = false
  let busy = false

  const current = () => {
    const next = readScope()

    return (
      !disposed &&
      !invalid &&
      next.eligible &&
      next.key === origin.key &&
      next.ownerKey === origin.ownerKey &&
      next.profile === origin.profile
    )
  }

  const stop = subscribe(() => {
    if (!current()) {invalid = true}
  })

  async function request<T>(method: string, params: Record<string, unknown>): Promise<T> {
    if (!current()) {throw new Error('stale')}

    if (busy) {throw new Error('busy')}
    busy = true

    try {
      const response = await origin.request<T>(method, params)

      if (!current()) {throw new Error('stale')}

      if (response && typeof response === 'object' && 'generationAllowance' in response) {
        validateGenerationAllowance(response.generationAllowance)
      }

      return response
    } finally {
      busy = false
    }
  }

  return {
    current,
    setup: (organizationId: string | null) =>
      request<TeamSetup>('organization.teamSetup', { expectedOrganizationId: organizationId }),
    budget: async (setup: TeamSetup): Promise<SetupBudgetState> => {
      if (setup.setupBudgetVersion !== 2 || !setup.organizationId) {throw new Error('unsupported')}
      const value = await request<unknown>('organization.setupBudgetPolicy', { expectedOrganizationId: setup.organizationId })
      validateSetupBudgetState(value, setup.organizationId)

      return value
    },
    previewBudget: async (state: SetupBudgetState, configuration: SetupBudgetPolicy): Promise<SetupBudgetPreview> => {
      validateSetupBudgetPolicy(configuration)

      const value = await request<unknown>('organization.previewSetupBudget', {
        expectedOrganizationId: state.organizationId, expectedGeneration: state.policyGeneration,
        sourceRevision: state.sourceRevision, configuration
      })

      validateSetupBudgetPreview(value, state, configuration)

      return value
    },
    applyBudget: async (preview: SetupBudgetPreview, idempotencyKey: string) => {
      const value = await request<unknown>('organization.applySetupBudget', {
        expectedOrganizationId: preview.organizationId, expectedGeneration: preview.policyGeneration,
        sourceRevision: preview.sourceRevision, configuration: preview.configuration,
        previewVersion: preview.previewVersion, idempotencyKey, confirmed: true
      })

      validateSetupBudgetApplied(value, preview, idempotencyKey)

      return value
    },
    generate: (setup: TeamSetup, input: { jobs: string; goals: string; namingStyle: string; idempotencyKey: string }) =>
      request<TeamPreview>('organization.generateTeam', {
        expectedOrganizationId: setup.organizationId,
        sourceRevision: setup.sourceRevision,
        ...input
      }),
    improve: async (
      setup: TeamSetup,
      input: { field: 'jobs' | 'goals'; jobs: string; goals: string; namingStyle: string; idempotencyKey: string }
    ): Promise<TeamInputSuggestion> => {
      if (setup.inputImprovementVersion !== 1) {throw new Error('unsupported')}
      const organizationId = setup.organizationId
      const sourceRevision = setup.sourceRevision
      const field = input.field

      const value = await request<unknown>('organization.improveTeamInput', {
        expectedOrganizationId: organizationId,
        sourceRevision,
        ...input
      })

      if (!value || typeof value !== 'object' || Array.isArray(value)) {throw new Error('invalid_suggestion')}
      const result = value as Record<string, unknown>

      const validText = (text: unknown, maximum: number): text is string =>
        typeof text === 'string' &&
        !!text.trim() &&
        text.length <= maximum &&
        !Array.from(text).some(char => char.charCodeAt(0) < 32 && !'\n\r\t'.includes(char))

      if (
        result.capabilityVersion !== 1 ||
        result.organizationId !== organizationId ||
        result.sourceRevision !== sourceRevision ||
        result.field !== field ||
        !validText(result.suggestion, 4000) ||
        !Array.isArray(result.assumptions) ||
        result.assumptions.length > 5 ||
        !result.assumptions.every(item => validText(item, 300))
      ) {
        throw new Error('invalid_suggestion')
      }

      const allowance = result.generationAllowance

      return {
        ...(allowance === undefined ? {} : { generationAllowance: allowance as TeamGenerationAllowance }),
        capabilityVersion: 1,
        organizationId: result.organizationId as string,
        sourceRevision: result.sourceRevision as string,
        field,
        suggestion: result.suggestion,
        assumptions: result.assumptions
      }
    },
    preview: (setup: TeamSetup, team: TeamDraft) =>
      request<TeamPreview>('organization.previewTeam', {
        expectedOrganizationId: setup.organizationId,
        sourceRevision: setup.sourceRevision,
        team
      }),
    create: (preview: TeamPreview, idempotencyKey: string) =>
      request<TeamReceipt>('organization.createTeam', {
        expectedOrganizationId: preview.organizationId,
        sourceRevision: preview.sourceRevision,
        team: preview.team,
        previewVersion: preview.previewVersion,
        idempotencyKey,
        confirmed: true
      }),
    dispose() {
      disposed = true
      stop()
    }
  }
}

export type TeamOnboardingClient = ReturnType<typeof createTeamOnboardingClient>
