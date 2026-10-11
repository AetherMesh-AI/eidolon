import { useEffect, useRef } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'

import { AgentAvatar } from './avatar'
import { Command } from './command'
import { homeData } from './home-data'
import { LocalizedTime } from './localized-time'
import { RuntimeSetup } from './runtime-setup'
import type { Objective, OrganizationAdapter, OrganizationSnapshot } from './types'

export function HomeObjective({
  objective,
  snapshot,
  compact = false
}: {
  objective: Objective
  snapshot: OrganizationSnapshot
  compact?: boolean
}) {
  const { t, locale } = useI18n()
  const copy = t.organizationHome
  const owner = snapshot.agents.find(agent => agent.id === objective.ownerId)

  const progress = new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 1 }).format(
    (objective.progress ?? 0) / 100
  )

  const progressLabel =
    objective.source === 'prototype'
      ? t.organizationFoundation.estimatedProgress(progress)
      : copy.completedTasks(progress)

  const status = copy.statuses[objective.status as keyof typeof copy.statuses] ?? copy.unknownStatus

  return (
    <article className={`eid-home-record${compact ? ' eid-home-record-compact' : ''}`}>
      <div className="eid-home-record-heading">
        <h3>
          <Link to={`/objectives/${encodeURIComponent(objective.id)}`}>{objective.title}</Link>
        </h3>
        <span className={`eid-status eid-status-${objective.status}`}>{status}</span>
      </div>
      {!compact && <p className="eid-home-description">{objective.description}</p>}
      <p>
        <small>
          {copy.owner}: {owner?.name || objective.ownerId || copy.unassigned}
        </small>
      </p>
      {objective.progress !== undefined && Number.isFinite(objective.progress) ? (
        <div className="eid-home-progress">
          <progress aria-label={progressLabel} max={100} value={objective.progress} />
          <small>{progressLabel}</small>
        </div>
      ) : (
        <small>{copy.progressUnknown}</small>
      )}
      {objective.dispatchControl?.paused && (
        <p className="eid-home-paused">
          {copy.paused} · {copy.running(new Intl.NumberFormat(locale).format(objective.dispatchControl.runningCount))}
        </p>
      )}
      {!compact && (
        <Link className="eid-home-action" to={`/objectives/${encodeURIComponent(objective.id)}`}>
          {copy.viewObjective} →
        </Link>
      )}
    </article>
  )
}

function focusIntake() {
  const intake = document.getElementById('eid-home-intake') as HTMLDetailsElement | null

  if (intake) {intake.open = true}
  intake?.scrollIntoView({ block: 'start' })
  document.getElementById('eid-objective')?.focus({ preventScroll: true })
}

export function Home({ adapter, snapshot }: { adapter: OrganizationAdapter; snapshot: OrganizationSnapshot }) {
  const { t } = useI18n()
  const copy = t.organizationHome
  const data = homeData(snapshot)
  const { hash } = useLocation()
  const navigate = useNavigate()
  const canChooseRecipient = snapshot.connection?.state === 'ready' && Boolean(snapshot.connection.ownerScope ?? snapshot.connection.scope)
  const intakeFocused = useRef(false)
  // eslint-disable-next-line no-restricted-syntax -- one-shot focus guard for explicit navigation, not mirrored reactive data
  useEffect(() => {
    if (hash !== '#eid-home-intake') {
      intakeFocused.current = false

      return
    }

    if (!intakeFocused.current && snapshot.connection?.state === 'ready') {
      focusIntake()
      intakeFocused.current = true
    }
  }, [hash, snapshot.connection?.state])

  const intake = (
    <details className="eid-home-intake" id="eid-home-intake" open={hash === '#eid-home-intake' || undefined}>
      <summary>{copy.newObjective}</summary>
      {adapter.mode === 'runtime' && (
        <Command
          adapter={adapter}
          headingLevel={2}
          key={snapshot.connection?.ownerScope ?? snapshot.connection?.scope}
          snapshot={snapshot}
        />
      )}
      {adapter.mode === 'runtime' && <RuntimeSetup snapshot={snapshot} />}
    </details>
  )

  return (
    <div className="eid-home">
      <header className="eid-home-hero">
        <div>
          <p className="eid-eyebrow">Eidolon</p>
          <h1>{copy.heading}</h1>
          <p>{copy.introduction}</p>
        </div>
        {adapter.mode === 'runtime' && (
          <div className="eid-home-actions">
            <Button disabled={!canChooseRecipient} onClick={() => navigate('/messages?recipient=executive')} variant="default">{copy.messageOrganization}</Button>
            <Button onClick={focusIntake} variant="secondary">{copy.newObjective}</Button>
            {!canChooseRecipient && <p role="status">{copy.messageOrganizationUnavailable}</p>}
          </div>
        )}
      </header>
      {snapshot.connection && snapshot.connection.state !== 'ready' && (
        <p role="status">{snapshot.connection.state === 'connecting' ? copy.connecting : copy.lastKnown}</p>
      )}
      <div className="eid-home-grid">
        <section aria-label={copy.happening} className="eid-home-panel">
          <header>
            <h2>{copy.happening}</h2>
            <p>{copy.happeningNote}</p>
          </header>
          <div aria-label={copy.happening} className="eid-home-panel-body" role="group" tabIndex={0}>
          {data.objectives.length ? (
            data.objectives
              .slice(0, 2)
              .map(objective => <HomeObjective compact key={objective.id} objective={objective} snapshot={snapshot} />)
          ) : (
            <p className="eid-home-empty">{copy.noWork}</p>
          )}
          </div>
          <Link className="eid-home-action" to="/objectives">
            {copy.viewObjectives} →
          </Link>
        </section>
        <section aria-label={copy.needsYou} className="eid-home-panel">
          <header>
            <h2>{copy.needsYou}</h2>
            <p>{copy.needsNote}</p>
          </header>
          <div aria-label={copy.needsYou} className="eid-home-panel-body" role="group" tabIndex={0}>
          {data.requests.slice(0, 3).map(request => (
            <article className="eid-home-record" key={request.id}>
              <h3>{snapshot.objectives.find(item => item.id === request.objectiveId)?.title ?? request.objectiveId}</h3>
              <p>{request.reason || request.requestedOutcome || t.organizationWork.needsYou}</p>
              <Link className="eid-home-action" to={`/objectives/${encodeURIComponent(request.objectiveId)}`}>
                {copy.reviewRequest} →
              </Link>
            </article>
          ))}
          {!data.requests.length && (
            <p className="eid-home-empty">{snapshot.requests ? copy.noNeeds : copy.requestsUnavailable}</p>
          )}
          </div>
          <Link className="eid-home-action" to="/requests">
            {copy.viewRequests} →
          </Link>
        </section>
        <section aria-label={copy.ready} className="eid-home-panel">
          <header>
            <h2>{copy.ready}</h2>
            <p>{copy.readyNote}</p>
          </header>
          <div aria-label={copy.ready} className="eid-home-panel-body" role="group" tabIndex={0}>
          {data.deliverables.slice(0, 3).map(item => (
            <article className="eid-home-record" key={item.objectiveId}>
              <small className="eid-status-completed">{copy.accepted}</small>
              <h3>{item.title}</h3>
              <p>{item.summary}</p>
              <LocalizedTime value={item.updatedAt} />
              <Link className="eid-home-action" to={`/objectives/${encodeURIComponent(item.objectiveId)}`}>
                {copy.viewObjective} →
              </Link>
            </article>
          ))}
          {!data.deliverables.length && (
            <p className="eid-home-empty">{snapshot.outcomes ? copy.noReady : copy.outcomesUnavailable}</p>
          )}
          </div>
          <Link className="eid-home-action" to="/artifacts">
            {copy.viewDeliverables} →
          </Link>
        </section>
      </div>
      <section aria-label={copy.activity} className="eid-home-panel eid-home-activity">
        <header>
          <div>
            <h2>{copy.activity}</h2>
            <p>{copy.activityNote}</p>
          </div>
          <Link to="/activity">{copy.viewActivity} →</Link>
        </header>
        <ol>
          {data.activity.map(event => {
            const agent = snapshot.agents.find(item => item.id === event.agentId)

            return (
              <li key={event.id}>
                <AgentAvatar name={agent?.name ?? copy.organization} />
                <div>
                  <strong>{agent?.name ?? copy.organization}</strong>
                  <p>{event.text}</p>
                  <LocalizedTime value={event.timestamp} />
                </div>
              </li>
            )
          })}
        </ol>
        {!data.activity.length && <p>{copy.noActivity}</p>}
      </section>
      <p className="eid-note">{copy.snapshotNote}</p>
      {adapter.mode === 'runtime' && intake}
    </div>
  )
}
