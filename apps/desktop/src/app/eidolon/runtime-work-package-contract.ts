import type { Objective, OrganizationSnapshot, OrganizationWorkPackage } from './types'

const stringIds = (value: unknown): value is string[] =>
  Array.isArray(value) &&
  value.every(item => typeof item === 'string' && item.length > 0) &&
  new Set(value).size === value.length

const count = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0
const statuses = new Set(['planning', 'planned', 'working', 'completed', 'blocked', 'cancelled'])

function validPackage(value: unknown, objective: Objective): value is OrganizationWorkPackage {
  if (!value || typeof value !== 'object') {
    return false
  }

  const item = value as OrganizationWorkPackage

  return (
    ['id', 'managerId', 'title', 'planRequestId'].every(
      key =>
        typeof item[key as keyof OrganizationWorkPackage] === 'string' &&
        Boolean(item[key as keyof OrganizationWorkPackage])
    ) &&
    item.objectiveId === objective.id &&
    typeof item.description === 'string' &&
    count(item.round) &&
    Array.isArray(item.criterionIndexes) &&
    item.criterionIndexes.length > 0 &&
    item.criterionIndexes.every(count) &&
    new Set(item.criterionIndexes).size === item.criterionIndexes.length &&
    (item.criteria === undefined || (Array.isArray(item.criteria) && item.criteria.length === item.criterionIndexes.length && item.criteria.every(criterion => typeof criterion === 'string' && criterion.length > 0))) &&
    stringIds(item.projectIds) &&
    stringIds(item.dependencyIds) &&
    stringIds(item.taskIds) &&
    count(item.maxTasks) &&
    item.maxTasks > 0 &&
    statuses.has(item.status)
  )
}

/** Older runtimes omit this additive contract. Supplied delegation must be valid
 * before it can replace a last-known-good ownership record. */
export function validWorkPackages(snapshot: OrganizationSnapshot): boolean {
  return (
    snapshot.objectives.every(objective => {
      if (objective.planningMode !== undefined && !['legacy', 'executive_packages'].includes(objective.planningMode)) {
        return false
      }

      if (objective.workPackages === undefined) {
        return true
      }

      if (
        !Array.isArray(objective.workPackages) ||
        !objective.workPackages.every(item => validPackage(item, objective))
      ) {
        return false
      }

      return new Set(objective.workPackages.map(item => item.id)).size === objective.workPackages.length
    }) &&
    snapshot.tasks.every(
      task => task.workPackageId == null || (typeof task.workPackageId === 'string' && task.workPackageId.length > 0)
    )
  )
}
