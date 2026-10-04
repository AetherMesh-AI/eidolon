import './eidolon.css'

import { useState, useSyncExternalStore } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'

import { Activity } from './activity'
import { organizationAdapter } from './adapter'
import { Inspector } from './inspector'
import { responsibleTeams } from './objective-context'
import { MetadataEditor, MetadataSummary } from './objective-metadata'
import { ObjectiveOverview } from './objective-overview'
import { Organization } from './organization'
import type { ObjectiveMetadata } from './types'
import { type Objective, type ObjectiveStatus, objectiveStatusLabels, type OrganizationAdapter, type OrganizationSnapshot } from './types'
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
    <span><strong>{objective.title}</strong><small>{objective.description}</small><small>{objective.progress === undefined ? 'Progress unknown' : `${objective.progress}% · local estimate`}</small><small>{objective.milestone || 'No milestone recorded'}</small><small>Responsible team: {responsibleTeams(snapshot, objective)}</small><small>Active agents: {snapshot.agents.filter(agent => agent.objectiveId === objective.id && agent.status === 'working').length} · simulated</small><small>Created locally: {new Date(objective.createdAt).toLocaleString()} · Runtime duration unknown</small></span><Status status={objective.status} />
  </Link>)}</div>{objectives.length > 25 && <nav aria-label="Objective pages" className="eid-toolbar"><button className="eid-button" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>Previous page</button><span role="status">{currentPage * 25 + 1}–{Math.min((currentPage + 1) * 25, objectives.length)} of {objectives.length}</span><button className="eid-button" disabled={currentPage === lastPage} onClick={() => setPage(currentPage + 1)}>Next page</button></nav>}</> : <div className="eid-empty"><h2>No objectives yet</h2><p>Give the organization a goal. You can inspect its plan and work here.</p><Link className="eid-button" to="/home">Create an objective</Link></div>
}

function Command({ adapter, snapshot }: { adapter: OrganizationAdapter; snapshot: OrganizationSnapshot }) {
  const [goal, setGoal] = useState('')
  const [metadata, setMetadata] = useState<ObjectiveMetadata>({})
  const [error, setError] = useState('')
  const navigate = useNavigate()

  return <>
    <div className="eid-command">
      <div aria-hidden="true" className="eid-mark">◈</div><p className="eid-eyebrow">Eidolon</p>
      <h1>What should the organization do?</h1>
      <p className="eid-subtitle">Set the direction. Keep the work in view.</p>
      <form className="eid-composer" onSubmit={event => {
        event.preventDefault()

        try { const objective = adapter.createObjective(goal, metadata); navigate(`/objectives/${objective.id}`) }
        catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not create objective.') }
      }}>
        <label className="sr-only" htmlFor="eid-objective">Objective</label>
        <textarea id="eid-objective" maxLength={4000} onChange={event => { setGoal(event.target.value); setError('') }} placeholder="Describe an objective…" rows={3} value={goal} />
        <div className="eid-composer-tools"><label><input checked={metadata.priority !== undefined} onChange={event => setMetadata({ ...metadata, priority: event.target.checked ? 'P3' : undefined })} type="checkbox" /> Priority</label><label>Agent <select aria-label="Agent" onChange={event => setMetadata({ ...metadata, agentId: event.target.value || undefined })} value={metadata.agentId ?? ''}><option value="">Optional</option>{snapshot.agents.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label></div>
        {metadata.priority !== undefined && <label className="eid-priority">Priority level <input aria-label="Priority level" aria-valuetext={['Lowest', 'Low', 'Normal', 'High', 'Highest'][['P5', 'P4', 'P3', 'P2', 'P1'].indexOf(metadata.priority)]} max={4} min={0} onChange={event => setMetadata({ ...metadata, priority: (['P5', 'P4', 'P3', 'P2', 'P1'] as const)[Number(event.target.value)] })} step={1} type="range" value={['P5', 'P4', 'P3', 'P2', 'P1'].indexOf(metadata.priority)} /><span aria-hidden="true" className="eid-priority-endpoints"><span>Lowest</span><span>Highest</span></span></label>}
        <div className="eid-composer-tools"><span>Objective · Local prototype</span><button className="eid-primary" disabled={!goal.trim()} type="submit">Create objective <span aria-hidden="true">↑</span></button></div>
      </form>
      {error && <p role="alert">{error}</p>}
      <div className="eid-inline"><Link to="/">Ask a question</Link><span>Uses the connected Eidolon session</span></div>
      <p className="eid-note">Objectives are retained locally in this desktop. No autonomous work is dispatched.</p>
    </div>
    {snapshot.objectives.length > 0 && <section><h2>In motion</h2><ObjectiveList objectives={snapshot.objectives.slice(0, 4)} snapshot={snapshot} /></section>}
  </>
}

function OutcomeForm({ objective, adapter }: { objective: Objective; adapter: OrganizationAdapter }) {
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

function ObjectiveDetail({ objective, adapter, snapshot }: { objective: Objective; adapter: OrganizationAdapter; snapshot: OrganizationSnapshot }) {
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

function Knowledge({ snapshot, objectiveId }: { snapshot: OrganizationSnapshot; objectiveId?: string }) {
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const items = snapshot.knowledge.filter(item => (!objectiveId || item.objectiveId === objectiveId) && `${item.title} ${item.body}`.toLowerCase().includes(search.toLowerCase()))
  const selectedItem = items.find(item => item.id === selected)

  return <><label className="eid-filter">Search knowledge<input onChange={event => setSearch(event.target.value)} placeholder="Find retained context…" type="search" value={search} /></label>{items.length ? <div className="eid-list">{items.map(item => <button className="eid-row" key={item.id} onClick={() => setSelected(item.id)}><span>{item.title}<small>{item.kind}</small></span></button>)}</div> : <div className="eid-empty"><h2>{search ? 'No matching knowledge' : 'No organization knowledge yet'}</h2><p>Retained memory and artifacts require a durable knowledge adapter. Existing workspace files remain available in Workspace.</p><Link to="/artifacts">Browse existing artifacts →</Link></div>}
    {selectedItem && <Inspector kind="artifact" onClose={() => setSelected(null)} title={selectedItem.title}><p>{selectedItem.body}</p><dl><dt>Kind</dt><dd>{selectedItem.kind}</dd></dl>{selectedItem.objectiveId && <Link to={`/objectives/${selectedItem.objectiveId}`}>Source objective →</Link>}<p className="eid-note">Local retained context, not runtime-verified output.</p></Inspector>}
  </>
}

export function OrganizationWorkspace({ adapter = organizationAdapter }: { adapter?: OrganizationAdapter }) {
  const snapshot = useSyncExternalStore(adapter.subscribe, adapter.getSnapshot)
  const { pathname } = useLocation()
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const objective = snapshot.objectives.find(item => pathname === `/objectives/${item.id}`)
  const titles: Record<string, string> = { '/objectives': 'Objectives', '/organization': 'Organization', '/activity': 'Activity', '/knowledge': 'Knowledge' }

  return <main aria-label="Organization workspace" className="eidolon eid-workspace">
    <div className="eid-page">
      <div className="eid-demo-bar"><span>{snapshot.agents.length ? 'Fictional example · Not live' : 'Prototype · No organization runtime connected'}</span><button className="eid-button" onClick={() => adapter.loadDemo()}>Load example</button><button className="eid-button" onClick={() => { if (window.confirm('Clear local prototype objectives and example data? Runtime sessions are unaffected.')) {adapter.reset()} }}>Clear local data</button></div>
      {pathname === '/home' && <Command adapter={adapter} snapshot={snapshot} />}
      {titles[pathname] && <header className="eid-page-header"><div><p className="eid-eyebrow">Eidolon · Organization</p><h1>{titles[pathname]}</h1></div><span className="eid-prototype">Local prototype</span></header>}
      {pathname === '/objectives' && <><div className="eid-toolbar"><label className="eid-filter">Search objectives<input onChange={event => setQuery(event.target.value)} placeholder="Find an objective…" value={query} /></label><label className="eid-filter">Status<select onChange={event => setFilter(event.target.value)} value={filter}><option value="all">All statuses</option>{Object.entries(objectiveStatusLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><Link className="eid-primary" to="/home">New objective</Link></div><ObjectiveList objectives={snapshot.objectives.filter(item => (filter === 'all' || item.status === filter) && item.title.toLowerCase().includes(query.toLowerCase()))} snapshot={snapshot} /></>}
      {pathname.startsWith('/objectives/') && (objective ? <ObjectiveDetail adapter={adapter} key={objective.id} objective={objective} snapshot={snapshot} /> : <div className="eid-empty"><h1>Objective not found</h1><p>Local objectives reset when this window reloads.</p><Link to="/objectives">Back to objectives</Link></div>)}
      {pathname === '/activity' && <Activity snapshot={snapshot} />}
      {pathname === '/knowledge' && <MemoryWeb items={snapshot.knowledge} />}
      {pathname === '/organization' && <Organization snapshot={snapshot} />}
    </div>
  </main>
}
