import './eidolon.css'

import { lazy, Suspense, useState, useSyncExternalStore } from 'react'
import { Link, useLocation } from 'react-router'

import { Loader } from '@/components/ui/loader'
import { SearchField } from '@/components/ui/search-field'
import { useI18n } from '@/i18n/context'

import { Activity } from './activity'
import { Command } from './command'
import { Inspector } from './inspector'
import { responsibleTeams } from './objective-context'
import { MetadataSummary } from './objective-metadata'
import { ObjectiveOverview } from './objective-overview'
import { Organization } from './organization'
import { RuntimeArtifact } from './runtime-artifact'
import { RuntimeObjectiveDetail, RuntimeStatus } from './runtime-detail'
import { RuntimeRequests } from './runtime-requests'
import { type Objective, objectiveStatusLabels, type OrganizationAdapter, type OrganizationSnapshot, type RuntimeOrganizationAdapter } from './types'
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


function StaticObjectiveDetail({ objective, snapshot }: { objective: Objective; snapshot: OrganizationSnapshot }) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const [inspecting, setInspecting] = useState(false)

  return <><Link className="eid-back" to="/objectives">← {copy.objectives}</Link><header className="eid-page-header"><div><p className="eid-eyebrow">{copy.legacyDemo}</p><h1>{objective.title}</h1><p>{objective.description}</p></div><Status status={objective.status} /></header><p>{copy.legacyNote}</p><button className="eid-button" onClick={() => setInspecting(true)}>Inspect objective</button>{inspecting && <Inspector kind="objective" onClose={() => setInspecting(false)} title={objective.title}><MetadataSummary objective={objective} /><p>{copy.legacyNote}</p></Inspector>}<MetadataSummary objective={objective} /><ObjectiveOverview objective={objective} snapshot={snapshot} />{objective.result && <><h2>{copy.result}</h2><p>{objective.result}</p></>}<WorkGraph objectiveId={objective.id} snapshot={snapshot} /><Activity objectiveId={objective.id} snapshot={snapshot} /><Knowledge objectiveId={objective.id} snapshot={snapshot} /></>
}

import { MemoryWeb } from './memory-web'

export function Knowledge({ snapshot, objectiveId, adapter }: { snapshot: OrganizationSnapshot; objectiveId?: string; adapter?: RuntimeOrganizationAdapter }) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const items = snapshot.knowledge.filter(item => (!objectiveId || item.objectiveId === objectiveId) && `${item.title} ${item.body}`.toLowerCase().includes(search.toLowerCase()))
  const selectedItem = items.find(item => item.id === selected)

  return <><p>{copy.evidenceSource}</p><p className="eid-note">{copy.evidenceNote}</p>{snapshot.knowledge.length > 0 && <SearchField aria-label={copy.evidence} onChange={setSearch} placeholder={copy.evidence} value={search} />}{items.length ? <div className="eid-list">{items.map(item => <button className="eid-row" key={item.id} onClick={() => setSelected(item.id)}><span>{item.title}<small>{item.kind} · {item.objectiveId || copy.evidenceSource}</small></span></button>)}</div> : <p>{copy.evidenceEmpty}</p>}
    {selectedItem && (adapter ? <RuntimeArtifact adapter={adapter} evidenceId={selectedItem.id} key={selectedItem.id} onClose={() => setSelected(null)} snapshot={snapshot} title={selectedItem.title} /> : <Inspector kind="artifact" onClose={() => setSelected(null)} title={selectedItem.title}><p>{selectedItem.body}</p><p>{copy.legacyNote}</p>{selectedItem.objectiveId && <Link to={`/objectives/${selectedItem.objectiveId}`}>{copy.openObjective}</Link>}</Inspector>)}
  </>
}

const RuntimeOrganizationWorkspace = lazy(() => import('./runtime-workspace'))

export function OrganizationWorkspace({ adapter }: { adapter?: OrganizationAdapter }) {
  // Explicit examples/tests never load the gateway boundary or dispatch work.
  return adapter ? <OrganizationWorkspaceView adapter={adapter} /> : <Suspense fallback={<Loader label="Connecting to organization" />}><RuntimeOrganizationWorkspace /></Suspense>
}

export function OrganizationWorkspaceView({ adapter }: { adapter: OrganizationAdapter }) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const snapshot = useSyncExternalStore(adapter.subscribe, adapter.getSnapshot)
  const { pathname } = useLocation()
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const objective = snapshot.objectives.find(item => pathname === `/objectives/${item.id}`)
  const titles: Record<string, string> = { '/objectives': 'Objectives', '/organization': 'Organization', '/activity': 'Activity', '/knowledge': 'Knowledge' }

  return <main aria-label="Organization workspace" className="eidolon eid-workspace">
    <div className="eid-page">
      {adapter.mode === 'prototype' ? <div className="eid-demo-bar"><span>{copy.legacyDemo} · {copy.legacyNote}</span></div> : <RuntimeStatus adapter={adapter} snapshot={snapshot} />}
      {pathname === '/home' && <>{adapter.mode === 'runtime' && <Command adapter={adapter} key={snapshot.connection?.ownerScope ?? snapshot.connection?.scope} snapshot={snapshot} />}{snapshot.objectives.length > 0 && <section><h2>In motion</h2><ObjectiveList objectives={snapshot.objectives.slice(0, 4)} snapshot={snapshot} /></section>}</>}
      {titles[pathname] && <header className="eid-page-header"><div><p className="eid-eyebrow">Eidolon · Organization</p><h1>{titles[pathname]}</h1></div><span className="eid-prototype">{adapter.mode === 'runtime' ? 'Runtime' : 'Local prototype'}</span></header>}
      {pathname === '/objectives' && <>{snapshot.runtime?.historyLimited && <p className="eid-note">All open objectives and the latest 25 completed or cancelled objectives are shown. Older history remains in the backend ledger.</p>}<div className="eid-toolbar"><label className="eid-filter">Search objectives<input onChange={event => setQuery(event.target.value)} placeholder="Find an objective…" value={query} /></label><label className="eid-filter">Status<select onChange={event => setFilter(event.target.value)} value={filter}><option value="all">All statuses</option>{Object.entries(objectiveStatusLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><Link className="eid-primary" to="/home">New objective</Link></div><ObjectiveList objectives={snapshot.objectives.filter(item => (filter === 'all' || item.status === filter) && item.title.toLowerCase().includes(query.toLowerCase()))} snapshot={snapshot} /></>}
      {pathname.startsWith('/objectives/') && (objective ? adapter.mode === 'runtime' ? <RuntimeObjectiveDetail adapter={adapter} key={`${snapshot.connection?.scope}:${objective.id}`} objective={objective} snapshot={snapshot} /> : <StaticObjectiveDetail key={objective.id} objective={objective} snapshot={snapshot} /> : adapter.mode === 'runtime' && snapshot.connection?.state !== 'ready' ? <p>Waiting for the current profile’s organization snapshot.</p> : <div className="eid-empty"><h1>Objective not found</h1><p>{adapter.mode === 'runtime' ? 'This objective is not in the current connection and profile snapshot.' : 'This objective is not in the local prototype.'}</p><Link to="/objectives">Back to objectives</Link></div>)}
      {pathname === '/requests' && adapter.mode === 'runtime' && <RuntimeRequests adapter={adapter} key={snapshot.connection?.ownerScope ?? snapshot.connection?.scope} snapshot={snapshot} />}
      {pathname === '/activity' && <Activity key={snapshot.connection?.scope} snapshot={snapshot} />}
      {pathname === '/knowledge' && (adapter.mode === 'runtime' ? <Knowledge adapter={adapter} key={snapshot.connection?.scope} snapshot={snapshot} /> : <MemoryWeb items={snapshot.knowledge} />)}
      {pathname === '/organization' && <Organization key={snapshot.connection?.scope} snapshot={snapshot} />}
    </div>
  </main>
}
