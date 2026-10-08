import type {
  OrganizationConfiguration,
  OrganizationManagement,
  OrganizationMemberConfiguration,
  OrganizationSnapshot,
  OrganizationTransfer
} from './types'

export const leaderRole = { Executive: 'Owner', Manager: 'Executive', Worker: 'Manager' } as const
export const memberRoles = ['Executive', 'Manager', 'Worker'] as const
export const responseAuthority = ['answer.question', 'answer.decision']
export const responseCapabilities = ['request.question', 'request.decision']

export function memberCapabilities(role: OrganizationMemberConfiguration['role'], management: OrganizationManagement) {
  const roles = {
    Executive: ['request.accept', 'request.hire'],
    Manager: ['request.plan', 'request.integrate', 'request.hire'],
    Worker: management.allowedCapabilities
  }

  return [...roles[role], ...responseCapabilities]
}

export function newMember(): OrganizationMemberConfiguration {
  return {
    id: '',
    name: '',
    role: 'Worker',
    manager_id: 'manager',
    team: 'general',
    capabilities: [],
    enabled: true,
    provider: null,
    model: null,
    tool_grants: [],
    responsibilities: [],
    purpose: '',
    authority: [],
    managed_teams: []
  }
}

export function transferTasks(snapshot: OrganizationSnapshot, source: string) {
  return snapshot.tasks.filter(
    task =>
      task.status !== 'completed' &&
      task.status !== 'cancelled' &&
      (task.assignedAgentId === source ||
        task.ownerId === source ||
        task.managingAgentId === source ||
        snapshot.objectives.find(objective => objective.id === task.objectiveId)?.executiveId === source)
  )
}

export function transferObjectives(snapshot: OrganizationSnapshot, source: string, role?: string) {
  return snapshot.objectives.filter(
    objective =>
      objective.status !== 'completed' &&
      objective.status !== 'cancelled' &&
      ((role === 'Manager' && objective.managerId === source) ||
        (role === 'Executive' && objective.executiveId === source))
  )
}

export function validTransfers(
  transfers: OrganizationTransfer[],
  configuration: OrganizationConfiguration,
  snapshot: OrganizationSnapshot
) {
  const roles = new Map([...snapshot.agents, ...configuration.roster].map(agent => [agent.id, agent.role]))
  const selectedTasks = transfers.flatMap(transfer => transfer.taskIds)

  const selectedLeadership = transfers.flatMap(transfer =>
    (transfer.objectiveIds ?? []).map(id => `${roles.get(transfer.fromAgentId)}:${id}`)
  )

  return (
    transfers.length <= 64 &&
    new Set(selectedTasks).size === selectedTasks.length &&
    new Set(selectedLeadership).size === selectedLeadership.length &&
    new Set(transfers.map(transfer => `${transfer.fromAgentId}:${transfer.toAgentId}`)).size === transfers.length &&
    transfers.every(
      transfer =>
        transfer.fromAgentId !== transfer.toAgentId &&
        roles.has(transfer.fromAgentId) &&
        roles.get(transfer.fromAgentId) === roles.get(transfer.toAgentId) &&
        (transfer.includeMemory || transfer.taskIds.length > 0 || (transfer.objectiveIds?.length ?? 0) > 0) &&
        transfer.taskIds.every(id => transferTasks(snapshot, transfer.fromAgentId).some(task => task.id === id)) &&
        (transfer.objectiveIds ?? []).every(id =>
          transferObjectives(snapshot, transfer.fromAgentId, roles.get(transfer.fromAgentId)).some(
            objective => objective.id === id
          )
        )
    )
  )
}

export function validOrganizationConfiguration(
  configuration: OrganizationConfiguration,
  management: OrganizationManagement,
  snapshot: OrganizationSnapshot
) {
  const { roster, max_inflight: inflight, max_members: members } = configuration
  const ids = roster.map(member => member.id)

  const roles = new Map([
    ...snapshot.agents.map(agent => [agent.id, agent.role] as const),
    ...roster.map(member => [member.id, member.role] as const)
  ])

  const validText = (value: string, max: number) =>
    Boolean(value.trim()) && value.length <= max && !Array.from(value).some(character => character.charCodeAt(0) < 32)

  return (
    Number.isInteger(inflight) &&
    inflight >= 1 &&
    inflight <= 4 &&
    Number.isInteger(members) &&
    members >= 1 &&
    members <= 64 &&
    validTransfers(configuration.transfers ?? [], configuration, snapshot) &&
    roster.length <= members &&
    new Set(ids).size === ids.length &&
    roster.every(
      member =>
        /^[a-z][a-z0-9_-]{0,63}$/.test(member.id) &&
        !['owner', 'executive', 'director', 'manager', 'reviewer'].includes(member.id) &&
        !member.id.startsWith('reviewer-') &&
        validText(member.name, 100) &&
        validText(member.team, 64) &&
        roles.get(member.manager_id) === leaderRole[member.role] &&
        member.manager_id !== member.id &&
        (!member.scope || validText(member.scope, 3000)) &&
        (!member.purpose || validText(member.purpose, 3000)) &&
        member.responsibilities.length <= 12 &&
        member.responsibilities.every(value => validText(value, 500)) &&
        Boolean(member.provider?.trim()) === Boolean(member.model?.trim()) &&
        (!member.provider || validText(member.provider, 100)) &&
        (!member.model || validText(member.model, 300)) &&
        member.capabilities.every(capability => memberCapabilities(member.role, management).includes(capability)) &&
        member.tool_grants.every(tool => member.role === 'Worker' && management.allowedTools.includes(tool)) &&
        member.authority.every(
          value => responseAuthority.includes(value) || (value === 'staff.manage' && member.role !== 'Worker')
        ) &&
        (!member.managed_teams.length || member.authority.includes('staff.manage')) &&
        member.managed_teams.length <= 64 &&
        member.managed_teams.every(team => validText(team, 64))
    )
  )
}
