import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'

import { AgentAvatar } from './avatar'
import { objectiveWorkData } from './objective-work-data'
import type { Objective, OrganizationSnapshot, OrganizationTask } from './types'

export function TaskCounts({ objective, snapshot }: { objective: Objective; snapshot: OrganizationSnapshot }) {
  const { t, locale } = useI18n()
  const copy = t.organizationHome.detail
  const data = objectiveWorkData(objective, snapshot)
  const number = new Intl.NumberFormat(locale)

  return <ul aria-label={copy.taskStates} className="eid-task-counts">
    {Object.entries(data.counts).map(([state, count]) => count > 0 && <li key={state}><strong>{number.format(count)}</strong> {copy.taskStatus[state as keyof typeof copy.taskStatus]}</li>)}
    {!data.tasks.length && <li>{copy.noTasks}</li>}
  </ul>
}

export function ObjectiveProgress({ objective, snapshot, onReviewRequests, onReviewWork }: {
  objective: Objective
  snapshot: OrganizationSnapshot
  onReviewRequests(): void
  onReviewWork(): void
}) {
  const { t, locale } = useI18n()
  const copy = t.organizationHome.detail
  const data = objectiveWorkData(objective, snapshot)
  const number = new Intl.NumberFormat(locale)
  const agentName = (id?: string | null) => snapshot.agents.find(agent => agent.id === id)?.name || id || t.organizationHome.unassigned
  const progress = objective.progress

  const taskRows = (tasks: OrganizationTask[]) => <ul className="eid-progress-tasks">
    {tasks.slice(0, 3).map(task => <li key={task.id}>
      <span className={`eid-status eid-status-${task.status}`}>{copy.taskStatus[task.status]}</span>
      <strong>{task.title}</strong>
      <small>{agentName(task.assignedAgentId ?? task.ownerId)}</small>
      {task.coordination?.state === 'waiting' && <small>{task.coordination.reason || copy.dependenciesWaiting}</small>}
    </li>)}
    {tasks.length > 3 && <li>{copy.moreTasks(number.format(tasks.length - 3))}</li>}
    {!tasks.length && <li>{copy.noTasks}</li>}
  </ul>

  return <div aria-label={copy.overview} className="eid-objective-overview" role="region">
    <div className="eid-objective-progress-main">
      <section aria-label={copy.progress} className="eid-home-panel">
        <header><h2>{copy.progress}</h2><p>{copy.progressNote}</p></header>
        <div className="eid-recorded-phase"><span>{copy.phase}</span><strong>{objective.phase || copy.phaseUnknown}</strong></div>
        {progress !== undefined && Number.isFinite(progress) ? <div className="eid-home-progress">
          <progress aria-label={copy.progress} max={100} value={progress} />
          <small>{t.organizationHome.completedTasks(new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 1 }).format(progress / 100))}</small>
        </div> : <p>{t.organizationHome.progressUnknown}</p>}
        <TaskCounts objective={objective} snapshot={snapshot} />
        {objective.dispatchControl?.paused && <p className="eid-home-paused">{t.organizationHome.paused} · {t.organizationHome.running(number.format(objective.dispatchControl.runningCount))}</p>}
        <Button onClick={onReviewWork} size="sm" variant="secondary">{copy.inspectWork}</Button>
      </section>
      <section aria-label={copy.teamWork} className="eid-team-work">
        <h2>{copy.teamWork}</h2>
        <p>{copy.teamNote}</p>
        <div className="eid-team-work-grid">
          {data.groups.map(group => <article className="eid-home-panel" key={group.package.id}>
            <header><div className="eid-team-owner"><AgentAvatar name={agentName(group.package.managerId)} /><div><h3>{group.package.title}</h3><small>{t.organizationRoster.objectiveManager} · {agentName(group.package.managerId)}</small></div></div></header>
            <p>{group.package.description}</p>
            <p className="eid-note">{copy.packageStatus}: {t.organizationWork[group.package.status === 'planning' ? 'packagePlanning' : group.package.status === 'planned' ? 'packagePlanned' : group.package.status === 'working' ? 'packageWorking' : group.package.status]}</p>
            {taskRows(group.tasks)}
          </article>)}
          {(data.ungrouped.length > 0 || !data.groups.length) && <article className="eid-home-panel">
            <header><h3>{copy.otherTasks}</h3><p>{copy.otherTasksNote}</p></header>
            {taskRows(data.ungrouped)}
          </article>}
        </div>
      </section>
    </div>
    <div aria-label={copy.attentionAndDelivery} className="eid-objective-progress-aside" role="region">
      <section aria-label={copy.needsInput} className="eid-home-panel">
        <header><h2>{copy.needsInput}</h2></header>
        {data.requests.slice(0, 2).map(request => <article className="eid-home-record" key={request.id}><p>{request.reason || request.requestedOutcome || t.organizationWork.needsYou}</p><small>{request.team}</small></article>)}
        {!data.requests.length && <p>{snapshot.requests ? t.organizationHome.noNeeds : t.organizationHome.requestsUnavailable}</p>}
        {data.requests.length > 2 && <p>{copy.moreRequests(number.format(data.requests.length - 2))}</p>}
        <Button onClick={onReviewRequests} size="sm" variant="secondary">{copy.inspectRequests}</Button>
      </section>
      <section aria-label={copy.delivery} className="eid-home-panel">
        <header><h2>{copy.delivery}</h2></header>
        <p>{data.accepted ? t.organizationHome.accepted : copy.notAccepted}</p>
        {data.accepted && <p className="eid-result-text">{objective.acceptance?.summary || objective.result || copy.acceptedRecorded}</p>}
        <Button onClick={onReviewWork} size="sm" variant="secondary">{copy.inspectAcceptance}</Button>
      </section>
    </div>
  </div>
}
