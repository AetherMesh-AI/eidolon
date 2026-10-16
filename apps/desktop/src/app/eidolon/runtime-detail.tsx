import { type ReactNode, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { Button } from '@/components/ui/button'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n/context'

import { Activity } from './activity'
import { Inspector } from './inspector'
import { MetadataSummary } from './objective-metadata'
import { ObjectiveProgress } from './objective-progress'
import { OwnerPolicyEntry, OwnerPolicySettingsLink } from './organization-owner-policy'
import { RuntimeAcceptance } from './runtime-acceptance'
import { RuntimeArtifact } from './runtime-artifact'
import { RuntimeCapabilities } from './runtime-capabilities'
import { RuntimeDispatch } from './runtime-dispatch'
import { RuntimeOutcomeJudgment } from './runtime-outcome-judgment'
import { RuntimePriority } from './runtime-priority'
import { RuntimeReplacement } from './runtime-replacement'
import { RuntimeRequests } from './runtime-requests'
import { RuntimeWorkPackages } from './runtime-work-packages'
import { OrganizationScenery } from './scenery'
import type { Objective, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'
import { WorkGraph } from './work-graph'
import { Knowledge, Status } from './workspace'
import { objectiveReturnTarget } from './workspace-navigation'

export function RuntimeStatus({
  adapter,
  snapshot,
  warningsOnly = false
}: {
  adapter: RuntimeOrganizationAdapter
  snapshot: OrganizationSnapshot
  warningsOnly?: boolean
}) {
  const { t } = useI18n()
  const stateCopy = t.organizationSurfaces.runtimeState
  const connection = snapshot.connection

  if (warningsOnly && connection?.state !== 'error' && connection?.state !== 'disconnected') { return null }

  return (
    <section aria-label={stateCopy.heading} className="eid-runtime-status">
      <div className="eid-toolbar">
        <strong>
          {connection?.state === 'disconnected'
            ? stateCopy.disconnected
            : connection?.state === 'error'
              ? stateCopy.interrupted
              : stateCopy.heading}
        </strong>
        {connection?.state === 'connecting' && <Loader label={t.organizationHome.connecting} />}
        {connection?.state === 'error' && (
          <Button onClick={() => void adapter.refresh()} size="sm" variant="secondary">
            {stateCopy.retry}
          </Button>
        )}
      </div>
      {connection?.error && <p role="alert">{connection.error}</p>}
      {connection?.state === 'disconnected' && (
        <p>
          {stateCopy.disconnectedNote}
        </p>
      )}
      {connection?.state === 'error' && (
        <p>
          {stateCopy.interruptedNote}
        </p>
      )}
      {!warningsOnly && snapshot.runtime && (
        <details className="eid-runtime-disclosure">
          <summary>
            {t.organizationRuntime.configuration}
            {snapshot.runtime.profile && <span> · {snapshot.runtime.profile}</span>}
          </summary>
          <RuntimeCapabilities runtime={snapshot.runtime} />
          <p className="eid-note">{snapshot.runtime.scope}</p>
        </details>
      )}
    </section>
  )
}

export function RuntimeObjectiveDetail({ objective, adapter, snapshot, historyControls }: { objective: Objective; adapter: RuntimeOrganizationAdapter; snapshot: OrganizationSnapshot; historyControls?: ReactNode }) {
  const { t, locale } = useI18n()
  const copy = t.organizationWork
  const [params] = useSearchParams()
  const returnTo = objectiveReturnTarget(params, snapshot.connection?.ownerScope ?? snapshot.connection?.scope)
  const returnLabels: Record<string, string> = { '/home': t.organizationHome.home, '/work': t.organizationHome.work, '/messages': t.organizationHome.messages, '/requests': copy.needsYou, '/objectives': copy.objectives, '/artifacts': t.organizationHome.deliverables }
  const [tab, setTab] = useState<'work' | 'activity' | 'artifacts' | 'decisions'>('work')
  const [inspecting, setInspecting] = useState(false)
  const [evidenceId, setEvidenceId] = useState<string | null>(null)
  const requestsRef = useRef<HTMLDivElement>(null)
  const workRef = useRef<HTMLDivElement>(null)

  const reveal = (element: HTMLDivElement | null) => {
    element?.scrollIntoView({ block: 'start' })
    element?.focus({ preventScroll: true })
  }

  const tasks = snapshot.tasks.filter(task => task.objectiveId === objective.id)
  const owner = snapshot.agents.find(agent => agent.id === objective.ownerId)
  const manager = snapshot.agents.find(agent => agent.id === objective.managerId)

  return <>
    <Link className="eid-back" state={{ workspaceReturnOwner: snapshot.connection?.ownerScope ?? snapshot.connection?.scope }} to={returnTo}>← {returnLabels[returnTo.split('?')[0]]}</Link>
    <header className="eid-page-header eid-objective-hero"><OrganizationScenery className="eid-objective-scenery" /><div><p className="sr-only">{t.organizationHome.detail.objectiveRuntime}</p><div className="eid-objective-heading"><h1 dir="auto">{objective.title}</h1><Status status={objective.status} /></div><p className="eid-result-text" dir="auto">{objective.description}</p></div></header>
    <div className="eid-inline"><span>{t.organizationRoster.objectiveExecutive} · <bdi>{owner?.name || objective.ownerId || t.organizationHome.unassigned}</bdi></span>{objective.managerId && <span>{t.organizationRoster.objectiveManager} · <bdi>{manager?.name || objective.managerId}</bdi></span>}<Button onClick={() => setInspecting(true)} size="sm" variant="secondary">{t.organizationHome.detail.inspectObjective}</Button></div>
    <ObjectiveProgress controls={<>
      <RuntimeDispatch adapter={adapter} key={`dispatch:${snapshot.connection?.scope}:${snapshot.connection?.ownerScope}:${objective.id}`} objective={objective} snapshot={snapshot} />
      <RuntimePriority adapter={adapter} key={`${snapshot.connection?.scope}:${snapshot.connection?.ownerScope}:${objective.id}`} objective={objective} snapshot={snapshot} />
    </>} objective={objective} onReviewRequests={() => reveal(requestsRef.current)} onReviewWork={() => { setTab('work'); reveal(workRef.current) }} snapshot={snapshot} />
    {inspecting && <Inspector kind="objective" onClose={() => setInspecting(false)} title={objective.title}><MetadataSummary objective={objective} /><RuntimeWorkPackages objective={objective} snapshot={snapshot} /><dl>
      <dt>{t.organizationSurfaces.objectives.status}</dt><dd><Status status={objective.status} /></dd>
      <dt>{t.organizationSurfaces.objectiveRecord.owner}</dt><dd dir="auto">{owner?.name || objective.ownerId || t.organizationHome.unassigned}</dd>
      <dt>{t.organizationHome.detail.tasksLabel}</dt><dd>{new Intl.NumberFormat(locale).format(tasks.length)}</dd>
      <dt>{t.organizationHome.detail.resultLabel}</dt><dd className="eid-result-text" dir="auto">{objective.result || t.organizationHome.detail.noReviewedResult}</dd>
    </dl><p>{t.organizationHome.detail.runtimeNote}</p></Inspector>}
    {evidenceId && <RuntimeArtifact adapter={adapter} evidenceId={evidenceId} key={evidenceId} onClose={() => setEvidenceId(null)} snapshot={snapshot} title={t.organizationHome.detail.fullEvidence} />}
    <div aria-label={t.organizationHome.detail.objectiveControls} className="eid-objective-controls" role="region">
    {objective.projects?.length ? <section aria-label={copy.objectiveProjects}>
      <h2>{copy.objectiveProjects}</h2>
      <ul>{objective.projects.map(project => <li className="eid-result-text" key={project.id}>{project.id} · {project.root} · {project.team}</li>)}</ul>
    </section> : null}
    <RuntimeReplacement adapter={adapter} key={`${snapshot.connection?.ownerScope ?? snapshot.connection?.scope}:${objective.id}`} objective={objective} snapshot={snapshot} />
    {historyControls}
    </div>
    <div aria-label={t.organizationHome.detail.requestControls} ref={requestsRef} role="region" tabIndex={-1}>
    <RuntimeRequests adapter={adapter} objective={objective} snapshot={snapshot} />
    </div>
    <div aria-label={t.organizationHome.detail.workDetails} ref={workRef} role="region" tabIndex={-1}>
    <div aria-label={t.organizationHome.detail.views} className="eid-tabs" role="tablist">{(['work', 'activity', 'artifacts', 'decisions'] as const).map(name => <button aria-selected={tab === name} key={name} onClick={() => setTab(name)} role="tab">{copy[name]}</button>)}</div>
    <section aria-label={copy[tab]} role="tabpanel">
      {tab === 'work' && <><RuntimeAcceptance objective={objective} onOpenEvidence={setEvidenceId} /><RuntimeOutcomeJudgment adapter={adapter} objective={objective} /><div className="eid-actions"><OwnerPolicyEntry target={{ kind: 'observation', objectiveId: objective.id }} /><OwnerPolicySettingsLink /></div><RuntimeWorkPackages objective={objective} snapshot={snapshot} /><WorkGraph objectiveId={objective.id} onOpenEvidence={setEvidenceId} snapshot={snapshot} /></>}
      {tab === 'activity' && <Activity objectiveId={objective.id} snapshot={snapshot} />}
      {tab === 'artifacts' && <Knowledge adapter={adapter} objectiveId={objective.id} snapshot={snapshot} />}
      {tab === 'decisions' && <><p>{copy.acceptanceNote}</p>{snapshot.decisions?.filter(item => item.objectiveId === objective.id).map(item => <article key={item.id}><h2>{item.title}</h2><Status status={item.status} /><p>{item.summary}</p></article>)}</>}
    </section>
    </div>
  </>
}
