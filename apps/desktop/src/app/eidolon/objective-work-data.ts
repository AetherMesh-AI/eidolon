import type { Objective, OrganizationSnapshot } from './types'

/** Summarize recorded scope only. Task completion never establishes acceptance. */
export function objectiveWorkData(objective: Objective, snapshot: OrganizationSnapshot) {
  const tasks = snapshot.tasks.filter(task => task.objectiveId === objective.id && !task.historical && task.currentRound !== false)
  const packages = (objective.workPackages ?? []).filter(item => item.objectiveId === objective.id && item.round === objective.acceptance?.round)

  const groups = packages.map(item => ({
    package: item,
    tasks: tasks.filter(task => task.workPackageId === item.id)
  }))

  const packageIds = new Set(packages.map(item => item.id))

  return {
    tasks,
    groups,
    ungrouped: tasks.filter(task => !task.workPackageId || !packageIds.has(task.workPackageId)),
    counts: {
      completed: tasks.filter(task => task.status === 'completed').length,
      working: tasks.filter(task => task.status === 'working').length,
      review: tasks.filter(task => task.status === 'review').length,
      blocked: tasks.filter(task => task.status === 'blocked').length,
      queued: tasks.filter(task => task.status === 'queued').length,
      cancelled: tasks.filter(task => task.status === 'cancelled').length
    },
    requests: snapshot.requests?.filter(request => request.objectiveId === objective.id && request.status === 'pending_intervention') ?? [],
    accepted: objective.acceptance?.status === 'accepted' && !!objective.acceptance.deliverableId
  }
}
