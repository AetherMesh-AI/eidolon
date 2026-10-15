import type { MetadataScope } from './organization-metadata-client'

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
export interface TeamSetup {
  capabilityVersion: 1
  state: 'available' | 'configured' | 'unconfigured' | 'unsupported'
  organizationId?: string
  sourceRevision?: string
  supportAgents?: { id: string; name: string; role: string }[]
  presets: { id: string; name: string; team: TeamDraft }[]
  readiness: TeamReadiness
}
export interface TeamPreview {
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
    if (!current()) invalid = true
  })
  async function request<T>(method: string, params: Record<string, unknown>): Promise<T> {
    if (!current()) throw new Error('stale')
    if (busy) throw new Error('busy')
    busy = true
    try {
      const response = await origin.request<T>(method, params)
      if (!current()) throw new Error('stale')
      return response
    } finally {
      busy = false
    }
  }
  return {
    current,
    setup: (organizationId: string | null) =>
      request<TeamSetup>('organization.teamSetup', { expectedOrganizationId: organizationId }),
    generate: (setup: TeamSetup, input: { jobs: string; goals: string; namingStyle: string; idempotencyKey: string }) =>
      request<TeamPreview>('organization.generateTeam', {
        expectedOrganizationId: setup.organizationId,
        sourceRevision: setup.sourceRevision,
        ...input
      }),
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
