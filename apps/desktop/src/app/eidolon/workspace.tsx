import './eidolon.css'

import { lazy, Suspense, useState, useSyncExternalStore } from 'react'
import { Link, useLocation } from 'react-router'

import { Loader } from '@/components/ui/loader'

import { Activity } from './activity'
import { Command } from './command'
import { Inspector } from './inspector'
import { responsibleTeams } from './objective-context'
import { MetadataEditor, MetadataSummary } from './objective-metadata'
import { ObjectiveOverview } from './objective-overview'
import { Organization } from './organization'
import { RuntimeArtifact } from './runtime-artifact'
import { RuntimeObjectiveDetail, RuntimeStatus } from './runtime-detail'
import { type Objective, type ObjectiveStatus, objectiveStatusLabels, type OrganizationAdapter, type OrganizationSnapshot, type PrototypeOrganizationAdapter, type RuntimeOrganizationAdapter } from './types'
import { WorkGraph } from './work-graph'

const labels = { ...objectiveStatusLabels, pending: 'Pending', approved: 'Approved', rejected: 'Rejected', recorded: 'Recorded' }

export function Status({ status }: { status: keyof typeof labels }) {
  return <span className={`eid-status eid-status-${status}`}><span aria-hidden="true">●</span> {labels[status]}</span>
}

function ObjectiveList({ objectives, snapshot }: { objectives: Objective[]; snapshot: OrganizationSnapshot }) {
  const [page, setPage] = useState(0)
  const lastPage = Math.max(0, Math.ceil(objectives.length / 25) - 1)
  const currentPage = Math.min(page, lastPage)

  return objectives.length ? <><div className="eid-list">{objectives.slice(currentPage * 25, (currentPage + 1) * 25).map(objective => <Link className="eid-row" key={objective.id} to={`/objectives/${objective.id}`}>
    <span><strong>{objective.title}</strong><small>{objective.description}</small><small>{objective.progress === undefined ? 'Progress unknown' : `${objective.progress}%${objective.source === 'runtime' ? '' : ' · local estimate'}`}</small><small>{objective.milestone || 'No milestone recorded'}</small><small>Responsible team: {responsibleTeams(snapshot, objective)}</small><small>Active agents: {snapshot.agents.filter(agent => agent.objectiveId === objective.id && ['working', 'active', 'thinking', 'executing', 'reviewing'].includes(agent.status)).length}{objective.source === 'runtime' ? '' : ' · simulated'}</small><small>{objective.source === 'runtime' ? 'Created: ' : 'Created locally: '}{new Date(objective.createdAt).toLocaleString()} · Runtime duration unknown</small></span><Status status={objective.status} />
  </Link>)}</div>{objectives.length > 25 && <nav aria-label="Objective pages" className="eid-toolbar"><button className="eid-button" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>Previous page</button><span role="status">{currentPage * 25 + 1}–{Math.min((currentPage + 1) * 25, objectives.length)} of {objectives.length}</span><button className="eid-button" disabled={currentPage === lastPage} onClick={() => setPage(currentPage + 1)}>Next page</button></nav>}</> : <div className="eid-empty"><h2>No objectives yet</h2><p>Give the organization a goal. You can inspect its plan and work here.</p><Link className="eid-button" to="/home">Create an objective</Link></div>
}


function OutcomeForm({ objective, adapter }: { objective: Objective; adapter: PrototypeOrganizationAdapter }) {
  const [summary, setSummary] = useState('')
  const [error, setError] = useState('')

  return <form className="eid-composer" onSubmit={event => {
    event.preventDefault()

    try { adapter.recordOutcome(objective.id, summary); setSummary(''); setError('') }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not record outcome.') }
  }}>
    <label htmlFor="eid-outcome">Local outcome summary</label>
    <textarea aria-describedby="eid-outcome-note" id="eid-outcome" maxLength={4000} onChange={event => { setSummary(event.target.value); setError('') }} rows={3} value={summary} />
    <p className="eid-note" id="eid-outcome-note">Record a user-reported result and artifact locally. This does not execute work, verify completion, or complete the planning tasks.</p>
    <button className="eid-primary" disabled={!summary.trim()} type="submit">Record local outcome</button>
    {error && <p role="alert">{error}</p>}
  </form>
}

function ObjectiveDetail({ objective, adapter, snapshot }: { objective: Objective; adapter: PrototypeOrganizationAdapter; snapshot: OrganizationSnapshot }) {
  const [tab, setTab] = useState('Overview')
  const [inspecting, setInspecting] = useState(false)
  const tasks = snapshot.tasks.filter(task => task.objectiveId === objective.id)

  return <>
    <Link className="eid-back" to="/objectives">← Objectives</Link>
    <header className="eid-page-header"><div><p className="eid-eyebrow">Objective · Local prototype</p><h1>{objective.title}</h1><p>{objective.description}</p></div><Status status={objective.status} /></header>
    <div className="eid-inline"><button className="eid-button" onClick={() => adapter.setObjectiveStatus(objective.id, objective.status === 'paused' ? 'planning' : 'paused')}>{objective.status === 'paused' ? 'Resume planning' : 'Pause objective'}</button><span>Lead owner · No live session assigned</span></div>
    <label className="eid-filter">Local objective state<select aria-describedby="eid-state-note" onChange={event => adapter.setObjectiveStatus(objective.id, event.target.value as ObjectiveStatus)} value={objective.status}>{Object.entries(objectiveStatusLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
    <p className="eid-note" id="eid-state-note">Manual local state only. Waiting and Needs Input do not monitor dependencies or request input. Archived retains this record; it does not delete data or stop runtime work.</p>
    <button className="eid-button" onClick={() => setInspecting(true)} type="button">Inspect objective</button>
    {inspecting && <Inspector kind="objective" onClose={() => setInspecting(false)} title={objective.title}><p>{objective.description}</p><dl><dt>Status</dt><dd>{labels[objective.status]}</dd><dt>Owner</dt><dd>No live session assigned</dd><dt>Tasks</dt><dd>{tasks.length}</dd><dt>Result</dt><dd>{objective.result || 'No result has been produced.'}</dd></dl><MetadataSummary objective={objective} /><p className="eid-note">Local planning record, not runtime execution.</p></Inspector>}
    <div aria-label="Objective views" className="eid-tabs" role="tablist">{['Overview', 'Plan', 'Tasks', 'Activity', 'Artifacts', 'Decisions'].map(name => <button aria-selected={tab === name} key={name} onClick={() => setTab(name)} role="tab">{name}</button>)}</div>
    <section aria-label={tab} role="tabpanel">
      {tab === 'Overview' && <><MetadataSummary objective={objective} /><MetadataEditor objective={objective} onSave={value => adapter.updateObjectiveMetadata(objective.id, value)} /><h2>Planning context</h2><p>No live planner is connected. This objective records your intent, not a claim that an agent is working.</p><div className="eid-metrics"><div><small>Tasks</small><strong>{tasks.length}</strong></div><div><small>Assigned agents</small><strong>{new Set(tasks.map(task => task.ownerId)).size}</strong></div><div><small>Approval</small><strong>Not requested</strong></div></div><h2>Result</h2><p>{objective.result || 'No result has been produced.'}</p><ObjectiveOverview objective={objective} snapshot={snapshot} /><OutcomeForm adapter={adapter} objective={objective} /></>}
      {tab === 'Plan' && <><h2>Plan & dependencies</h2><p>Proposed local plan · Template scaffold, not an AI-generated or executing plan.</p><div className="eid-plan">{snapshot.tasks.filter(task => task.objectiveId === objective.id).map((task, index) => <article key={task.id}><span className="eid-eyebrow">Step {index + 1} · {task.status}</span><h3>{task.title}</h3><p>{snapshot.agents.find(agent => agent.id === task.ownerId)?.name ?? 'Unassigned'} · {task.dependsOn.length ? `After ${task.dependsOn.map(id => snapshot.tasks.find(candidate => candidate.id === id)?.title ?? id).join(', ')}` : 'Ready to scope'}</p><p>{task.status === 'blocked' ? 'Blocked: review the dependency and pending decisions before proceeding.' : 'Completion requires a reviewed deliverable; no work is dispatched in this prototype.'}</p></article>)}</div></>}
      {tab === 'Tasks' && <WorkGraph objectiveId={objective.id} snapshot={snapshot} />}
      {tab === 'Activity' && <Activity objectiveId={objective.id} snapshot={snapshot} />}
      {tab === 'Decisions' && <><p>Review gates in this local prototype do not grant runtime permissions.</p>{snapshot.decisions?.filter(item => item.objectiveId === objective.id).map(item => <article className="eid-card" key={item.id}><h2>{item.title}</h2><Status status={item.status} /><p>{item.summary}</p>{item.status === 'pending' && <div className="eid-inline"><button className="eid-button eid-primary" onClick={() => adapter.resolveDecision(item.id, 'approved')}>Approve locally</button><button className="eid-button" onClick={() => adapter.resolveDecision(item.id, 'rejected')}>Reject locally</button></div>}</article>)}{!snapshot.decisions?.some(item => item.objectiveId === objective.id) && <div className="eid-empty"><h2>No decisions yet</h2><p>Review gates and recorded decisions will appear here when requested.</p></div>}</>}
    {tab === 'Artifacts' && <Knowledge objectiveId={objective.id} snapshot={snapshot} />}
    </section>
  </>
}

import { MemoryWeb } from './memory-web'

export function Knowledge({ snapshot, objectiveId, adapter }: { snapshot: OrganizationSnapshot; objectiveId?: string; adapter?: RuntimeOrganizationAdapter }) {
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const items = snapshot.knowledge.filter(item => (!objectiveId || item.objectiveId === objectiveId) && `${item.title} ${item.body}`.toLowerCase().includes(search.toLowerCase()))
  const selectedItem = items.find(item => item.id === selected)

  return <><label className="eid-filter">Search knowledge<input onChange={event => setSearch(event.target.value)} placeholder="Find retained context…" type="search" value={search} /></label>{items.length ? <div className="eid-list">{items.map(item => <button className="eid-row" key={item.id} onClick={() => setSelected(item.id)}><span>{item.title}<small>{item.kind}</small></span></button>)}</div> : <div className="eid-empty"><h2>{search ? 'No matching knowledge' : 'No organization knowledge yet'}</h2><p>Retained memory and artifacts require a durable knowledge adapter. Existing workspace files remain available in Workspace.</p><Link to="/artifacts">Browse existing artifacts →</Link></div>}
    {selectedItem && (adapter ? <RuntimeArtifact adapter={adapter} evidenceId={selectedItem.id} key={selectedItem.id} onClose={() => setSelected(null)} snapshot={snapshot} title={selectedItem.title} /> : <Inspector kind="artifact" onClose={() => setSelected(null)} title={selectedItem.title}><p>{selectedItem.body}</p><dl><dt>Kind</dt><dd>{selectedItem.kind}</dd></dl>{selectedItem.objectiveId && <Link to={`/objectives/${selectedItem.objectiveId}`}>Source objective →</Link>}<p className="eid-note">{snapshot.source === 'runtime' ? 'Output retained by the current-profile organization runtime. Review evidence is recorded with its task.' : 'Local retained context, not runtime-verified output.'}</p></Inspector>)}
  </>
}

const RuntimeOrganizationWorkspace = lazy(() => import('./runtime-workspace'))

export function OrganizationWorkspace({ adapter }: { adapter?: OrganizationAdapter }) {
  // Explicit examples/tests never load the gateway boundary or dispatch work.
  return adapter ? <OrganizationWorkspaceView adapter={adapter} /> : <Suspense fallback={<Loader label="Connecting to organization" />}><RuntimeOrganizationWorkspace /></Suspense>
}

export function OrganizationWorkspaceView({ adapter }: { adapter: OrganizationAdapter }) {
  const snapshot = useSyncExternalStore(adapter.subscribe, adapter.getSnapshot)
  const { pathname } = useLocation()
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const objective = snapshot.objectives.find(item => pathname === `/objectives/${item.id}`)
  const titles: Record<string, string> = { '/objectives': 'Objectives', '/organization': 'Organization', '/activity': 'Activity', '/knowledge': 'Knowledge' }

  return <main aria-label="Organization workspace" className="eidolon eid-workspace">
    <div className="eid-page">
      {adapter.mode === 'prototype' ? <div className="eid-demo-bar"><span>{snapshot.agents.length ? 'Fictional example · Not live' : 'Prototype · No organization runtime connected'}</span><button className="eid-button" onClick={() => adapter.loadDemo()}>Load example</button><button className="eid-button" onClick={() => { if (window.confirm('Clear local prototype objectives and example data? Runtime sessions are unaffected.')) {adapter.reset()} }}>Clear local data</button></div> : <RuntimeStatus adapter={adapter} snapshot={snapshot} />}
      {pathname === '/home' && <><Command adapter={adapter} key={snapshot.connection?.ownerScope ?? snapshot.connection?.scope ?? adapter.mode} snapshot={snapshot} />{snapshot.objectives.length > 0 && <section><h2>In motion</h2><ObjectiveList objectives={snapshot.objectives.slice(0, 4)} snapshot={snapshot} /></section>}</>}
      {titles[pathname] && <header className="eid-page-header"><div><p className="eid-eyebrow">Eidolon · Organization</p><h1>{titles[pathname]}</h1></div><span className="eid-prototype">{adapter.mode === 'runtime' ? 'Runtime' : 'Local prototype'}</span></header>}
      {pathname === '/objectives' && <>{snapshot.runtime?.historyLimited && <p className="eid-note">All open objectives and the latest 25 completed or cancelled objectives are shown. Older history remains in the backend ledger.</p>}<div className="eid-toolbar"><label className="eid-filter">Search objectives<input onChange={event => setQuery(event.target.value)} placeholder="Find an objective…" value={query} /></label><label className="eid-filter">Status<select onChange={event => setFilter(event.target.value)} value={filter}><option value="all">All statuses</option>{Object.entries(objectiveStatusLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><Link className="eid-primary" to="/home">New objective</Link></div><ObjectiveList objectives={snapshot.objectives.filter(item => (filter === 'all' || item.status === filter) && item.title.toLowerCase().includes(query.toLowerCase()))} snapshot={snapshot} /></>}
      {pathname.startsWith('/objectives/') && (objective ? adapter.mode === 'runtime' ? <RuntimeObjectiveDetail adapter={adapter} key={`${snapshot.connection?.scope}:${objective.id}`} objective={objective} snapshot={snapshot} /> : <ObjectiveDetail adapter={adapter} key={objective.id} objective={objective} snapshot={snapshot} /> : adapter.mode === 'runtime' && snapshot.connection?.state !== 'ready' ? <p>Waiting for the current profile’s organization snapshot.</p> : <div className="eid-empty"><h1>Objective not found</h1><p>{adapter.mode === 'runtime' ? 'This objective is not in the current connection and profile snapshot.' : 'This objective is not in the local prototype.'}</p><Link to="/objectives">Back to objectives</Link></div>)}
      {pathname === '/activity' && <Activity key={snapshot.connection?.scope} snapshot={snapshot} />}
      {pathname === '/knowledge' && (adapter.mode === 'runtime' ? <Knowledge adapter={adapter} key={snapshot.connection?.scope} snapshot={snapshot} /> : <MemoryWeb items={snapshot.knowledge} />)}
      {pathname === '/organization' && <Organization key={snapshot.connection?.scope} snapshot={snapshot} />}
    </div>
  </main>
}
