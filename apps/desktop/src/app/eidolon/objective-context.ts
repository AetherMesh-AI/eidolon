import type { Objective, OrganizationSnapshot } from './types'

/** Responsibility comes from explicit scoped relationships, never role names. */
export function responsibleAgents(snapshot: OrganizationSnapshot, objective: Objective) {
  const ids = new Set([objective.ownerId, objective.managerId, ...snapshot.tasks.filter(task => task.objectiveId === objective.id).flatMap(task => [task.ownerId, task.managingAgentId])])

  return snapshot.agents.filter(agent => ids.has(agent.id) || agent.objectiveId === objective.id)
}

export function responsibleTeams(snapshot: OrganizationSnapshot, objective: Objective) {
  const agents = responsibleAgents(snapshot, objective)
  const teams = [...new Set(agents.map(agent => agent.team?.trim()).filter((team): team is string => !!team))]

  if (!agents.length || agents.some(agent => !agent.team?.trim())) {teams.push('Unknown · team not recorded')}

  return teams.join(', ')
}
