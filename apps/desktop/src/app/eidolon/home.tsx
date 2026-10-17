import { type ReactNode, useEffect, useLayoutEffect, useRef } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'
import { requestIntakeCopy } from '@/i18n/organization-request-intake'
import { CheckCircle2, FileText } from '@/lib/icons'

import { AgentAvatar } from './avatar'
import { Command } from './command'
import { homeData } from './home-data'
import { queueHomeKeyboardReveal } from './home-keyboard-focus'
import { LocalizedTime } from './localized-time'
import { objectivePhaseLabel } from './objective-phase'
import { TaskCounts } from './objective-progress'
import { RuntimeSetup } from './runtime-setup'
import { OrganizationScenery } from './scenery'
import type { Objective, OrganizationAdapter, OrganizationRequest, OrganizationSnapshot } from './types'
import { objectiveHref } from './workspace-navigation'

export function HomeObjective({
  objective,
  snapshot,
  compact = false
}: {
  objective: Objective
  snapshot: OrganizationSnapshot
  compact?: boolean
}) {
  const location = useLocation()
  const href = objectiveHref(objective.id, location.pathname + location.search, snapshot.connection?.ownerScope ?? snapshot.connection?.scope)
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
    <article className={`eid-home-record${compact ? ' eid-home-record-compact' : ' eid-home-objective-primary'}`}>
      {!compact && <header className="eid-home-objective-label"><FileText aria-hidden="true" /><small>{copy.workspace.kinds.objective}</small><span className={`eid-status eid-status-${objective.status}`}>{status}</span></header>}
      <div className="eid-home-record-heading">
        <h3 dir="auto">
          <Link to={href}>{objective.title}</Link>
        </h3>
        {compact && <span className={`eid-status eid-status-${objective.status}`}>{status}</span>}
      </div>
      {!compact && <p className="eid-home-description" dir="auto">{objective.description}</p>}
      {!compact && <>
      <p>
        <small>
          {copy.owner}: <bdi>{owner?.name || objective.ownerId || copy.unassigned}</bdi>
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
      </>}
      {!compact && <div className="eid-home-objective-facts">
        <p><small>{copy.detail.phase}</small><strong dir="auto">{objectivePhaseLabel(objective, copy.detail.phases) || copy.detail.phaseUnknown}</strong></p>
        <TaskCounts objective={objective} snapshot={snapshot} />
      </div>}
      {objective.dispatchControl?.paused && (
        <p className="eid-home-paused">
          {copy.paused} · {copy.running(new Intl.NumberFormat(locale).format(objective.dispatchControl.runningCount))}
        </p>
      )}
      {!compact && (
        <footer className="eid-home-record-actions"><Link className="eid-home-action" to={href}>
          {copy.viewObjective} →
        </Link></footer>
      )}
    </article>
  )
}

export function HomeRequest({ request, snapshot }: { request: OrganizationRequest; snapshot: OrganizationSnapshot }) {
  const location = useLocation()
  const href = objectiveHref(request.objectiveId, location.pathname + location.search, snapshot.connection?.ownerScope ?? snapshot.connection?.scope, request.id)
  const { t } = useI18n()
  const requester = snapshot.agents.find(agent => agent.id === request.requesterId)
  const objective = snapshot.objectives.find(item => item.id === request.objectiveId)

  return (
    <article className="eid-home-record eid-home-request">
      <div className="eid-home-request-context">
        {requester && <AgentAvatar name={requester.name} />}
        <div>
          <small>{t.organizationWork.requester}: <bdi>{requester?.name || request.requesterId || t.organizationWork.notRecorded}</bdi></small>
          <div className="eid-home-record-time">{t.organizationSurfaces.objectiveRecord.created}: <LocalizedTime value={request.createdAt} /></div>
        </div>
      </div>
      <h3 dir="auto">{request.requestedOutcome || request.reason || t.organizationWork.needsYou}</h3>
      {request.requestedOutcome && request.reason && request.reason !== request.requestedOutcome && <p dir="auto">{request.reason}</p>}
      <p className="eid-note" dir="auto">{objective?.title || request.objectiveId}</p>
      <footer className="eid-home-record-actions"><Link className="eid-home-action" to={href}>
        {t.organizationHome.reviewRequest} →
      </Link></footer>
    </article>
  )
}

function focusIntake(mode: 'manual' | 'assisted' = 'manual') {
  const intake = document.getElementById('eid-home-intake') as HTMLDetailsElement | null

  if (intake) {
    intake.open = true
  }

  const manual = document.getElementById('eid-manual-intake') as HTMLDetailsElement | null
  const assisted = document.getElementById('eid-assisted-intake') as HTMLDetailsElement | null

  if (manual) {manual.open = mode === 'manual'}

  if (assisted) {assisted.open = mode === 'assisted'}

  const field = mode === 'manual' ? document.getElementById('eid-objective') : assisted?.querySelector('textarea')

  intake?.scrollIntoView({ block: 'start' })
  const target = field?.matches(':disabled') ? (mode === 'assisted' ? assisted : manual)?.querySelector('summary') : field
  target?.focus({ preventScroll: true })
}

export function Home({ adapter, snapshot, starterEntry }: { adapter: OrganizationAdapter; snapshot: OrganizationSnapshot; starterEntry?: ReactNode }) {
  const { t, locale } = useI18n()
  const copy = t.organizationHome
  const data = homeData(snapshot)
  const { hash, search, pathname } = useLocation()
  const cancelKeyboardReveal = useRef<(() => void) | undefined>(undefined)
  const ownerScope = snapshot.connection?.ownerScope ?? snapshot.connection?.scope
  useLayoutEffect(() => () => cancelKeyboardReveal.current?.(), [ownerScope, pathname, search, hash])
  const navigate = useNavigate()
  const canChooseRecipient = snapshot.connection?.state === 'ready' && Boolean(snapshot.connection.ownerScope ?? snapshot.connection.scope)
  const intakeFocused = useRef(false)
  const intakeTrigger = useRef('eid-home-describe-request')
  // eslint-disable-next-line no-restricted-syntax -- one-shot focus guard for explicit navigation, not mirrored reactive data
  useEffect(() => {
    if (hash !== '#eid-home-intake') {
      intakeFocused.current = false

      return
    }

    if (!intakeFocused.current && snapshot.connection?.state === 'ready') {
      focusIntake(new URLSearchParams(search).get('intake') === 'assisted' ? 'assisted' : 'manual')
      intakeFocused.current = true
    }
  }, [hash, search, snapshot.connection?.state])

  const intake = (
    <details className="eid-home-intake" id="eid-home-intake" onKeyDownCapture={event => {
      cancelKeyboardReveal.current?.()

      if (event.key === 'Tab') {cancelKeyboardReveal.current = queueHomeKeyboardReveal(event.currentTarget)}
    }} onToggle={event => {
      const nextHash = event.currentTarget.open ? '#eid-home-intake' : ''

      if (hash !== nextHash) {
        navigate({ search, hash: nextHash }, { replace: true })
      }

      if (!event.currentTarget.open) {
        const trigger = intakeTrigger.current === 'eid-home-describe-request' && document.querySelector('.eid-home-actions > .eid-team-entry')
          ? 'eid-home-new-objective' : intakeTrigger.current

        document.getElementById(trigger)?.focus({ preventScroll: true })
      }
    }} open={hash === '#eid-home-intake' || undefined}>
      <summary>{requestIntakeCopy[locale].title}</summary>
      {adapter.mode === 'runtime' && <details className="eid-home-setup"><summary>{t.organizationRoster.setupTitle} · {snapshot.runtime?.setup?.provider.status === 'warning' ? t.organizationRoster.setupProviderWarning : t.organizationRoster.setupProviderUnchecked}</summary><RuntimeSetup snapshot={snapshot} /></details>}
      {adapter.mode === 'runtime' && (
        <Command
          adapter={adapter}
          headingLevel={2}
          key={snapshot.connection?.ownerScope ?? snapshot.connection?.scope}
          snapshot={snapshot}
        />
      )}
    </details>
  )

  return (
    <div className="eid-home">
      <header className="eid-home-hero">
        <OrganizationScenery className="eid-home-scenery" />
        <div>
          <p className="eid-eyebrow">Eidolon</p>
          <h1>{copy.heading}</h1>
          <p>{copy.introduction}</p>
        </div>
        {adapter.mode === 'runtime' && (
          <div className="eid-home-actions">
            {starterEntry}
            <Button className="eid-home-request-action" id="eid-home-describe-request" onClick={() => {
              intakeTrigger.current = 'eid-home-describe-request'
              focusIntake('assisted')
              intakeFocused.current = true
              navigate({ search: '?intake=assisted', hash: '#eid-home-intake' }, { replace: true })
            }} variant="default">{requestIntakeCopy[locale].title}</Button>
            <div className="eid-home-secondary-actions">
            <Button disabled={!canChooseRecipient} onClick={() => navigate('/messages?recipient=executive')} variant="text">{copy.messageOrganization}</Button>
            <Button id="eid-home-new-objective" onClick={() => {
              intakeTrigger.current = 'eid-home-new-objective'
              focusIntake()
              intakeFocused.current = true
              navigate({ search: '?intake=manual', hash: '#eid-home-intake' }, { replace: true })
            }} variant="text">{copy.newObjective}</Button>
            </div>
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
              .map((objective, index) => <HomeObjective compact={index > 0} key={objective.id} objective={objective} snapshot={snapshot} />)
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
          {data.requests.slice(0, 3).map(request => <HomeRequest key={request.id} request={request} snapshot={snapshot} />)}
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
            <article className="eid-home-record eid-home-delivery" key={item.objectiveId}>
              <div className="eid-home-delivery-label"><CheckCircle2 aria-hidden="true" /><small className="eid-status-completed">{copy.accepted}</small></div>
              <h3 dir="auto">{item.title}</h3>
              <p dir="auto">{item.summary}</p>
              <div className="eid-home-record-time"><LocalizedTime value={item.updatedAt} /></div>
              <footer className="eid-home-record-actions"><Link className="eid-home-action" to={objectiveHref(item.objectiveId, pathname + search, ownerScope)}>
                {copy.viewObjective} →
              </Link></footer>
            </article>
          ))}
          {!data.deliverables.length && (
            <p className="eid-home-empty">{snapshot.outcomes ? copy.noReady : copy.outcomesUnavailable}</p>
          )}
          </div>
          <Link className="eid-home-action" to="/artifacts?source=organization">
            {copy.viewDeliverables} →
          </Link>
        </section>
      </div>
      <section aria-label={copy.activity} className="eid-home-panel eid-home-activity">
        <header>
          <div>
            <h2>{copy.activity}</h2>
            <p>{copy.activityNote} <span className="eid-home-snapshot-note">{copy.snapshotNote}</span></p>
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
                  <strong dir="auto">{agent?.name ?? copy.organization}</strong>
                  {agent && <small>{({ Owner: t.organizationRoster.owner, Executive: t.organizationRoster.executive, Manager: t.organizationRoster.manager, Worker: t.organizationRoster.worker, Director: t.organizationRoster.manager, Employee: t.organizationRoster.worker } as Record<string, string>)[agent.role] ?? agent.role}</small>}
                  <p dir="auto">{event.text}</p>
                  <LocalizedTime value={event.timestamp} />
                </div>
              </li>
            )
          })}
        </ol>
        {!data.activity.length && <p>{copy.noActivity}</p>}
      </section>
      {adapter.mode === 'runtime' && intake}
    </div>
  )
}
