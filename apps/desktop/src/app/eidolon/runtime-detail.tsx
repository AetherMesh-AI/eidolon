import { useRef, useState } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n/context'

import { Activity } from './activity'
import { Inspector } from './inspector'
import { MetadataSummary } from './objective-metadata'
import { ObjectiveOverview } from './objective-overview'
import { RuntimeArtifact } from './runtime-artifact'
import { RuntimeCapabilities } from './runtime-capabilities'
import { RuntimeRequestToolReceipts, ToolReceiptSummary } from './runtime-tool-receipts'
import type { Objective, OrganizationRequest, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'
import { WorkGraph } from './work-graph'
import { Knowledge, Status } from './workspace'

const priorityLabel = (value: number) => ['Lowest', 'Low', 'Normal', 'High', 'Highest'][value - 1] ?? 'Unknown'

const requestLabels: Record<OrganizationRequest['status'], string> = {
  queued: 'Queued', running: 'Running', completed: 'Completed', pending_intervention: 'Pending intervention', cancelled: 'Cancelled'
}

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
    {snapshot.runtime && <p className="eid-note">{snapshot.runtime.profile && `Profile: ${snapshot.runtime.profile} · `}{snapshot.runtime.scope} · Worker limit: {snapshot.runtime.maxWorkers}</p>}
  </section>
}

export function RuntimeRequests({ adapter, snapshot, objective }: { adapter: RuntimeOrganizationAdapter; snapshot: OrganizationSnapshot; objective: Objective }) {
  const { t } = useI18n()
  const copy = t.organizationRuntime
  const requests = (snapshot.requests ?? []).filter(request => request.objectiveId === objective.id)
  const [selected, setSelected] = useState<string | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState('')
  const busy = useRef(false)
  const request = requests.find(item => item.id === selected)
  const terminal = objective.status === 'completed' || objective.status === 'cancelled'
  const disconnected = snapshot.connection && snapshot.connection.state !== 'ready'

  const run = (id: string, action: () => Promise<void>) => {
    if (busy.current) {return}
    busy.current = true
    setPending(id)
    setError('')
    void action().catch(reason => setError(reason instanceof Error ? reason.message : 'The request could not be updated.')).finally(() => { busy.current = false; setPending(null) })
  }

  return <section aria-label="Runtime requests">
    <div className="eid-toolbar"><h2>Runtime requests</h2>{!terminal && <Button disabled={!!pending || Boolean(disconnected)} onClick={() => run('cancel', () => adapter.cancelObjective(objective.id))} size="sm" variant="secondary">{pending === 'cancel' ? 'Cancelling…' : 'Cancel objective'}</Button>}</div>
    {error && <p role="alert">{error}</p>}
    {!requests.length && <p>No requests reported for this objective yet.</p>}
    <ol className="eid-list">{requests.map(item => <li key={item.id}>
      <button aria-label={`Inspect request: ${item.type}`} className="eid-row" onClick={() => setSelected(item.id)}><span><strong>{item.type}</strong><small>Team: {item.team} · Priority: {priorityLabel(item.priority)} · Attempts: {item.attempts}</small><small>{item.agentId ? `Claimed by ${snapshot.agents.find(agent => agent.id === item.agentId)?.name ?? item.agentId}` : 'Unclaimed'}</small>{item.reason && <small>{copy.routeReason}: <span>{item.reason}</span></small>}<ToolReceiptSummary receipts={item.toolReceipts} />{(item.toolReceiptsTruncated || (item.toolReceiptCount ?? 0) > 20) && <small>{copy.moreReceipts}: {item.toolReceiptCount ?? copy.notReported}</small>}{item.requestedRoutes?.length ? <small>{copy.requestedRoutes}: {item.requestedRoutes.map(route => `${route.type} → ${route.team}`).join('; ')}</small> : null}</span><span className={`eid-status eid-request-${item.status}`}>{requestLabels[item.status]}</span></button>
      {item.status === 'pending_intervention' && item.type !== 'request.merge' && !terminal && <Button disabled={!!pending || Boolean(disconnected)} onClick={() => run(item.id, () => adapter.retryRequest(item.id))} size="sm" variant="secondary">{pending === item.id ? 'Retrying…' : `Retry ${item.type}`}</Button>}
    </li>)}</ol>
    {requests.some(item => item.type === 'request.merge' && item.status === 'pending_intervention') && <p className="eid-note">{copy.edits.mergePending} {copy.edits.mergeNote}</p>}
    {requests.some(item => item.status === 'pending_intervention' && item.type !== 'request.merge') && <p className="eid-note">Review the reason before retrying. Retry uses the same request and does not grant permissions or add unsupported capabilities.</p>}
    {request && <Inspector kind="request" onClose={() => setSelected(null)} title={request.type}><dl><dt>Status</dt><dd>{requestLabels[request.status]}</dd><dt>Team</dt><dd>{request.team}</dd><dt>Priority</dt><dd>{priorityLabel(request.priority)}</dd><dt>Claimed by</dt><dd>{request.agentId ? snapshot.agents.find(agent => agent.id === request.agentId)?.name ?? request.agentId : 'No worker claimed this request'}</dd><dt>Attempts</dt><dd>{request.attempts}</dd><dt>Lease expires</dt><dd>{request.leaseExpiresAt ? new Date(request.leaseExpiresAt).toLocaleString() : 'No active lease reported'}</dd><dt>{copy.routeReason}</dt><dd>{request.reason || copy.routeUnknown}</dd>{typeof request.requestedWorkers === 'number' && <><dt>{copy.requestedWorkers}</dt><dd>{request.requestedWorkers}</dd></>}{request.requestedRoutes?.length ? <><dt>{copy.requestedRoutes}</dt><dd>{request.requestedRoutes.map(route => `${route.type} → ${route.team}`).join('; ')}</dd></> : null}<dt>Request ID</dt><dd>{request.id}</dd><dt>Task ID</dt><dd>{request.taskId || 'No task linked'}</dd></dl>{request.type === 'request.merge' && <p className="eid-note">{copy.edits.mergeNote}</p>}<RuntimeRequestToolReceipts adapter={adapter} key={request.id} request={request} /></Inspector>}
  </section>
}

export function RuntimeObjectiveDetail({ objective, adapter, snapshot }: { objective: Objective; adapter: RuntimeOrganizationAdapter; snapshot: OrganizationSnapshot }) {
  const [tab, setTab] = useState('Overview')
  const [inspecting, setInspecting] = useState(false)
  const [evidenceId, setEvidenceId] = useState<string | null>(null)
  const tasks = snapshot.tasks.filter(task => task.objectiveId === objective.id)
  const owner = snapshot.agents.find(agent => agent.id === objective.ownerId)

  return <>
    <Link className="eid-back" to="/objectives">← Objectives</Link>
    <header className="eid-page-header"><div><p className="eid-eyebrow">Objective · Runtime</p><h1>{objective.title}</h1><p className="eid-result-text">{objective.description}</p></div><Status status={objective.status} /></header>
    <div className="eid-inline"><span>Lead owner · {owner?.name || objective.ownerId || 'Not assigned'}</span><Button onClick={() => setInspecting(true)} size="sm" variant="secondary">Inspect objective</Button></div>
    {inspecting && <Inspector kind="objective" onClose={() => setInspecting(false)} title={objective.title}><MetadataSummary objective={objective} /><dl><dt>Status</dt><dd>{objective.status}</dd><dt>Owner</dt><dd>{owner?.name || objective.ownerId}</dd><dt>Tasks</dt><dd>{tasks.length}</dd><dt>Result</dt><dd className="eid-result-text">{objective.result || 'No reviewed result yet.'}</dd></dl><p>State and completion are reported by the current-profile runtime.</p></Inspector>}
    {evidenceId && <RuntimeArtifact adapter={adapter} evidenceId={evidenceId} key={evidenceId} onClose={() => setEvidenceId(null)} snapshot={snapshot} title="Full task evidence" />}
    <RuntimeRequests adapter={adapter} objective={objective} snapshot={snapshot} />
    <div aria-label="Objective views" className="eid-tabs" role="tablist">{['Overview', 'Plan', 'Tasks', 'Activity', 'Artifacts', 'Decisions'].map(name => <button aria-selected={tab === name} key={name} onClick={() => setTab(name)} role="tab">{name}</button>)}</div>
    <section aria-label={tab} role="tabpanel">
      {tab === 'Overview' && <><MetadataSummary objective={objective} /><h2>Execution scope</h2><p>{snapshot.runtime?.scope}</p><p>A manager plans the request, an eligible worker claims it, and a separate reviewer checks the output. A result is complete only after the runtime records its review evidence.</p><h2>Result</h2><p className="eid-result-text">{objective.result || 'No reviewed result yet. Inspect the requests for current work or intervention reasons.'}</p><ObjectiveOverview objective={objective} snapshot={snapshot} /></>}
      {tab === 'Plan' && <><h2>Plan & dependencies</h2><p>Runtime-reported planning and assignments. Work requiring unavailable capabilities remains pending intervention.</p>{tasks.length ? <ol className="eid-plan">{tasks.map(task => <li key={task.id}><h3>{task.title}</h3><p>{task.requestType} · Team: {task.team || 'Unassigned'} · {task.status}</p><p>{task.dependsOn.length ? `After ${task.dependsOn.map(id => tasks.find(item => item.id === id)?.title ?? id).join(', ')}` : 'No prerequisites'}</p></li>)}</ol> : <p>No plan has been recorded.</p>}</>}
      {tab === 'Tasks' && <WorkGraph objectiveId={objective.id} onOpenEvidence={setEvidenceId} snapshot={snapshot} />}
      {tab === 'Activity' && <Activity objectiveId={objective.id} snapshot={snapshot} />}
      {tab === 'Artifacts' && <Knowledge adapter={adapter} objectiveId={objective.id} snapshot={snapshot} />}
      {tab === 'Decisions' && <><p>Runtime decisions and review gates are recorded here. Permissions cannot be approved from this view.</p>{snapshot.decisions?.filter(item => item.objectiveId === objective.id).map(item => <article key={item.id}><h2>{item.title}</h2><Status status={item.status} /><p>{item.summary}</p></article>)}</>}
    </section>
  </>
}
