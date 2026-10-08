import { useI18n } from '@/i18n/context'

import type { Objective, OrganizationSnapshot, OrganizationWorkPackage } from './types'

interface RuntimeWorkPackagesProps {
  objective: Objective
  snapshot: OrganizationSnapshot
}

/** Paint retained delegation records, including cancelled rounds, without
 * deriving objective acceptance or rewriting historical criterion text. */
export function RuntimeWorkPackages({ objective, snapshot }: RuntimeWorkPackagesProps) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const packages = objective.workPackages ?? []

  if (!objective.planningMode && !packages.length) {
    return null
  }

  const current = packages.filter(item => item.round === objective.acceptance?.round)
  const history = packages.filter(item => item.round !== objective.acceptance?.round)

  const states: Record<OrganizationWorkPackage['status'], string> = {
    planning: copy.packagePlanning,
    planned: copy.packagePlanned,
    working: copy.packageWorking,
    completed: copy.completed,
    blocked: copy.blocked,
    cancelled: copy.cancelled
  }

  const renderPackage = (item: OrganizationWorkPackage) => {
    const manager = snapshot.agents.find(agent => agent.id === item.managerId)

    const dependencies = item.dependencyIds.map(id => {
      const dependency = packages.find(candidate => candidate.id === id && candidate.round === item.round)

      return dependency ? `${dependency.title} (${states[dependency.status]}) · ${id}` : id
    })

    return (
      <li aria-label={item.title} className="space-y-2" key={item.id}>
        <h4 className="eid-result-text">{item.title}</h4>
        <p>
          <span className={`eid-status eid-status-${item.status}`}>{states[item.status]}</span> · {copy.round}:{' '}
          {item.round}
        </p>
        <p className="eid-result-text">{item.description}</p>
        <dl>
          <dt>{copy.workPackage}</dt>
          <dd className="eid-result-text">{item.id}</dd>
          <dt>{copy.packageManager}</dt>
          <dd>{manager ? `${manager.name} (${item.managerId})` : item.managerId}</dd>
          <dt>{copy.packageCriteria}</dt>
          <dd>{item.criteria ? <ul>{item.criteria.map((criterion, index) => <li className="eid-result-text" key={item.criterionIndexes[index]}>{item.criterionIndexes[index] + 1}. {criterion}</li>)}</ul> : item.criterionIndexes.map(index => index + 1).join(', ')}</dd>
          <dt>{copy.packageProjects}</dt>
          <dd className="eid-result-text">{item.projectIds.join(', ') || copy.packageNoProjects}</dd>
          <dt>{copy.packageDependencies}</dt>
          <dd className="eid-result-text">{dependencies.join('; ') || copy.packageNoDependencies}</dd>
          <dt>{copy.packageTaskLimit}</dt>
          <dd>{item.maxTasks}</dd>
          <dt>{copy.packagePlanRequest}</dt>
          <dd className="eid-result-text">{item.planRequestId}</dd>
          <dt>{copy.packageTasks}</dt>
          <dd>
            {item.taskIds.length ? (
              <ul>
                {item.taskIds.map(id => {
                  const task = snapshot.tasks.find(
                    candidate => candidate.id === id && candidate.objectiveId === objective.id
                  )

                  return (
                    <li className="eid-result-text" key={id}>
                      {task ? `${task.title} · ${id}` : id}
                    </li>
                  )
                })}
              </ul>
            ) : (
              copy.packageNoTasks
            )}
          </dd>
        </dl>
      </li>
    )
  }

  return (
    <section aria-label={copy.workPackages} className="space-y-3">
      <h2>{copy.workPackages}</h2>
      <p>
        {copy.packagePlanningMode}:{' '}
        {objective.planningMode === 'legacy' ? copy.packageLegacyMode : copy.packageExecutiveMode}
      </p>
      <p className="eid-note">{copy.packageNote}</p>
      {objective.planningMode === 'legacy' ? (
        <p>{copy.packageLegacyNote}</p>
      ) : (
        <>
          <h3>
            {copy.currentRound}
            {objective.acceptance && ` · ${objective.acceptance.round}`}
          </h3>
          {current.length ? (
            <ol aria-label={copy.currentRound} className="space-y-6">
              {current.map(renderPackage)}
            </ol>
          ) : (
            <p>{copy.packageEmpty}</p>
          )}
        </>
      )}
      {history.length > 0 && (
        <details>
          <summary>
            {copy.packageHistory} ({history.length})
          </summary>
          <p className="eid-note">{copy.packageHistoryNote}</p>
          <ol aria-label={copy.packageHistory} className="space-y-6">
            {history.map(renderPackage)}
          </ol>
        </details>
      )}
    </section>
  )
}
