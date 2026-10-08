import { type ReactNode, useState } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n/context'

import { Activity } from './activity'
import { Inspector } from './inspector'
import { MetadataSummary } from './objective-metadata'
import { RuntimeAcceptance } from './runtime-acceptance'
import { RuntimeArtifact } from './runtime-artifact'
import { RuntimeCapabilities } from './runtime-capabilities'
import { RuntimeRequests } from './runtime-requests'
import type { Objective, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'
import { WorkGraph } from './work-graph'
import { Knowledge, Status } from './workspace'

export function RuntimeStatus({ adapter, snapshot }: { adapter: RuntimeOrganizationAdapter; snapshot: OrganizationSnapshot }) {
  const connection = snapshot.connection

  return <section aria-label="Organization runtime" className="eid-runtime-status">
    <div className="eid-toolbar"><strong>{connection?.state === 'disconnected' ? 'Organization disconnected' : connection?.state === 'error' ? 'Organization updates interrupted' : 'Organization runtime'}</strong>
      {connection?.state === 'connecting' && <Loader label="Connecting to organization" />}
      {connection?.state === 'error' && <Button onClick={() => void adapter.refresh()} size="sm" variant="secondary">Retry connection</Button>}
    </div>
    {connection?.error && <p role="alert">{connection.error}</p>}
    {connection?.state === 'disconnected' && <p>Reconnect the current profile’s gateway to view and submit work. Running work may continue on the backend.</p>}
    {connection?.state === 'error' && <p>Showing the last received state. Automatic recovery is limited; use Retry connection if updates do not resume.</p>}
    {snapshot.runtime && <RuntimeCapabilities runtime={snapshot.runtime} />}
    {snapshot.runtime && <p className="eid-note">{snapshot.runtime.profile && `Profile: ${snapshot.runtime.profile} · `}{snapshot.runtime.scope}</p>}
  </section>
}

export function RuntimeObjectiveDetail({ objective, adapter, snapshot, historyControls }: { objective: Objective; adapter: RuntimeOrganizationAdapter; snapshot: OrganizationSnapshot; historyControls?: ReactNode }) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const [tab, setTab] = useState('work')
  const [inspecting, setInspecting] = useState(false)
  const [evidenceId, setEvidenceId] = useState<string | null>(null)
  const tasks = snapshot.tasks.filter(task => task.objectiveId === objective.id)
  const owner = snapshot.agents.find(agent => agent.id === objective.ownerId)
  const manager = snapshot.agents.find(agent => agent.id === objective.managerId)

  return <>
    <Link className="eid-back" to="/objectives">← Objectives</Link>
    <header className="eid-page-header"><div><p className="eid-eyebrow">Objective · Runtime</p><h1>{objective.title}</h1><p className="eid-result-text">{objective.description}</p></div><Status status={objective.status} /></header>
    <div className="eid-inline"><span>{t.organizationRoster.objectiveExecutive} · {owner?.name || objective.ownerId || 'Not assigned'}</span>{objective.managerId && <span>{t.organizationRoster.objectiveManager} · {manager?.name || objective.managerId}</span>}<Button onClick={() => setInspecting(true)} size="sm" variant="secondary">Inspect objective</Button></div>
    {inspecting && <Inspector kind="objective" onClose={() => setInspecting(false)} title={objective.title}><MetadataSummary objective={objective} /><dl><dt>Status</dt><dd>{objective.status}</dd><dt>Owner</dt><dd>{owner?.name || objective.ownerId}</dd><dt>Tasks</dt><dd>{tasks.length}</dd><dt>Result</dt><dd className="eid-result-text">{objective.result || 'No reviewed result yet.'}</dd></dl><p>State and completion are reported by the current-profile runtime.</p></Inspector>}
    {evidenceId && <RuntimeArtifact adapter={adapter} evidenceId={evidenceId} key={evidenceId} onClose={() => setEvidenceId(null)} snapshot={snapshot} title="Full task evidence" />}
    {objective.projects?.length ? <section aria-label={copy.objectiveProjects}>
      <h2>{copy.objectiveProjects}</h2>
      <ul>{objective.projects.map(project => <li className="eid-result-text" key={project.id}>{project.id} · {project.root} · {project.team}</li>)}</ul>
    </section> : null}
    {historyControls}
    <RuntimeRequests adapter={adapter} objective={objective} snapshot={snapshot} />
    <div aria-label="Objective views" className="eid-tabs" role="tablist">{(['work', 'activity', 'artifacts', 'decisions'] as const).map(name => <button aria-selected={tab === name} key={name} onClick={() => setTab(name)} role="tab">{copy[name]}</button>)}</div>
    <section aria-label={tab} role="tabpanel">
      {tab === 'work' && <><RuntimeAcceptance objective={objective} onOpenEvidence={setEvidenceId} /><WorkGraph objectiveId={objective.id} onOpenEvidence={setEvidenceId} snapshot={snapshot} /></>}
      {tab === 'activity' && <Activity objectiveId={objective.id} snapshot={snapshot} />}
      {tab === 'artifacts' && <Knowledge adapter={adapter} objectiveId={objective.id} snapshot={snapshot} />}
      {tab === 'decisions' && <><p>{copy.acceptanceNote}</p>{snapshot.decisions?.filter(item => item.objectiveId === objective.id).map(item => <article key={item.id}><h2>{item.title}</h2><Status status={item.status} /><p>{item.summary}</p></article>)}</>}
    </section>
  </>
}
