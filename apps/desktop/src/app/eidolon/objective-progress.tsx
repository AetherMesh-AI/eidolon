import type { ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'

import { AgentAvatar } from './avatar'
import { LocalizedTime } from './localized-time'
import { objectivePhaseLabel } from './objective-phase'
import { ObjectiveReviewFindings } from './objective-review-findings'
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

export function ObjectiveProgress({ objective, snapshot, controls, onReviewRequests, onReviewWork }: {
  objective: Objective
  snapshot: OrganizationSnapshot
  controls?: ReactNode
  onReviewRequests(): void
  onReviewWork(): void
}) {
  const { t, locale } = useI18n()
  const copy = t.organizationHome.detail
  const data = objectiveWorkData(objective, snapshot)
  const number = new Intl.NumberFormat(locale)
  const agentName = (id?: string | null) => snapshot.agents.find(agent => agent.id === id)?.name || id || t.organizationHome.unassigned
  const progress = objective.progress
  const lifecycle = ['executive_planning', 'execution_review', 'integration_review', 'accepted'] as const
  const supportedPhase = objective.source === 'runtime' && lifecycle.some(phase => phase === objective.phaseCode)

  const taskRows = (tasks: OrganizationTask[]) => <ul className="eid-progress-tasks">
    {tasks.slice(0, 3).map(task => <li className="eid-progress-task" key={task.id}>
      <strong dir="auto">{task.title}</strong>
      <span className={`eid-status eid-status-${task.status}`}>{copy.taskStatus[task.status]}</span>
      <small dir="auto">{agentName(task.assignedAgentId ?? task.ownerId)}</small>
      {task.coordination?.state === 'waiting' && <small>{task.coordination.reason || copy.dependenciesWaiting}</small>}
    </li>)}
    {tasks.length > 3 && <li>{copy.moreTasks(tasks.length - 3, number.format(tasks.length - 3))}</li>}
    {!tasks.length && <li>{copy.noTasks}</li>}
  </ul>

  return <div aria-label={copy.overview} className="eid-objective-overview" onFocus={event => {
    if (event.target.matches('button:focus-visible, a:focus-visible, select:focus-visible, summary:focus-visible')) {
      event.target.scrollIntoView({ block: 'center', inline: 'nearest' })
    }
  }} role="region">
    <div className="eid-objective-progress-main">
      <section aria-label={copy.phase} className="eid-home-panel eid-objective-phase">
        <div className="eid-objective-phase-heading">
          <div className="eid-recorded-phase"><span>{copy.phase}</span>{!supportedPhase && <strong dir="auto">{objectivePhaseLabel(objective, copy.phases) || copy.phaseUnknown}</strong>}</div>
          <Button onClick={onReviewWork} size="micro" variant="secondary">{copy.inspectWork}</Button>
        </div>
        {supportedPhase && <ol aria-label={copy.phase} className="eid-objective-lifecycle">
          {lifecycle.map(phase => <li aria-current={objective.phaseCode === phase ? 'step' : undefined} key={phase}>
            <span aria-hidden="true" className="eid-lifecycle-marker" />
            <span>{copy.phases[phase]}</span>
          </li>)}
        </ol>}
        {controls && <div className="eid-objective-actions">{controls}</div>}
        {!controls && objective.dispatchControl?.paused && <p className="eid-home-paused">{t.organizationHome.paused} · {t.organizationHome.running(number.format(objective.dispatchControl.runningCount))}</p>}
      </section>
      <section aria-label={copy.progress} className="eid-home-panel eid-progress-summary">
        <header><h2>{copy.progress}</h2><p>{copy.progressNote}</p></header>
        <div className="eid-progress-metrics">
        {progress !== undefined && Number.isFinite(progress) ? <div className="eid-home-progress">
          <progress aria-label={copy.progress} max={100} value={progress} />
          <small>{t.organizationHome.completedTasks(new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 1 }).format(progress / 100))}</small>
        </div> : <p>{t.organizationHome.progressUnknown}</p>}
        <TaskCounts objective={objective} snapshot={snapshot} />
        </div>
      </section>
      <section aria-label={copy.teamWork} className="eid-team-work">
        <h2 className="sr-only">{copy.teamWork}</h2>
        <p>{copy.teamNote}</p>
        <div className="eid-team-work-grid">
          {data.groups.map(group => <article className="eid-home-panel" key={group.package.id}>
            <header><div className="eid-team-owner"><AgentAvatar name={agentName(group.package.managerId)} /><div><h3 dir="auto">{group.package.title}</h3><small>{t.organizationRoster.objectiveManager} · <bdi>{agentName(group.package.managerId)}</bdi></small></div></div></header>
            <p dir="auto">{group.package.description}</p>
            <p className="eid-note">{copy.packageStatus}: {t.organizationWork[group.package.status === 'planning' ? 'packagePlanning' : group.package.status === 'planned' ? 'packagePlanned' : group.package.status === 'working' ? 'packageWorking' : group.package.status]}</p>
            {taskRows(group.tasks)}
          </article>)}
          {(data.ungrouped.length > 0 || !data.groups.length) && <article className="eid-home-panel">
            <header><h3>{copy.otherTasks}</h3><p>{copy.otherTasksNote}</p></header>
            {taskRows(data.ungrouped)}
          </article>}
        </div>
      </section>
      <ObjectiveReviewFindings objective={objective} />
    </div>
    <div aria-label={copy.attentionAndDelivery} className="eid-objective-progress-aside" role="region">
      <section aria-label={copy.needsInput} className="eid-home-panel">
        <header><h2>{copy.needsInput}</h2></header>
        {data.requests.slice(0, 2).map(request => <article className="eid-home-record" key={request.id}><p dir="auto">{request.reason || request.requestedOutcome || t.organizationWork.needsYou}</p><small>{request.team}</small></article>)}
        {!data.requests.length && <p>{snapshot.requests ? t.organizationHome.noNeeds : t.organizationHome.requestsUnavailable}</p>}
        {data.requests.length > 2 && <p>{copy.moreRequests(data.requests.length - 2, number.format(data.requests.length - 2))}</p>}
        <Button onClick={onReviewRequests} size="sm" variant="secondary">{copy.inspectRequests}</Button>
      </section>
      <section aria-label={copy.delivery} className="eid-home-panel">
        <header><h2>{copy.delivery}</h2></header>
        <p>{data.accepted ? t.organizationHome.accepted : copy.notAccepted}</p>
        {data.accepted && <p className="eid-result-text" dir="auto">{objective.acceptance?.summary || objective.result || copy.acceptedRecorded}</p>}
        <Button onClick={onReviewWork} size="sm" variant="secondary">{copy.inspectAcceptance}</Button>
      </section>
      <section aria-label={t.organizationSurfaces.objectiveRecord.heading} className="eid-home-panel eid-objective-record">
        <header><h2>{t.organizationSurfaces.objectiveRecord.heading}</h2></header>
        <dl>
          <dt>{t.organizationSurfaces.objectiveRecord.created}</dt><dd><LocalizedTime value={objective.createdAt} /></dd>
          <dt>{t.organizationSurfaces.objectiveRecord.owner}</dt><dd dir="auto">{agentName(objective.ownerId)}</dd>
          <dt>{t.organizationSurfaces.objectiveRecord.priority}</dt><dd>{objective.priority || t.organizationSurfaces.objectiveRecord.notRecorded}</dd>
          <dt>{t.organizationSurfaces.objectiveRecord.phase}</dt><dd dir="auto">{objectivePhaseLabel(objective, copy.phases) || t.organizationSurfaces.objectiveRecord.notRecorded}</dd>
        </dl>
      </section>
    </div>
  </div>
}
