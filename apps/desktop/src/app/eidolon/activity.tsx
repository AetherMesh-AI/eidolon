import { useState } from 'react'
import { Link } from 'react-router'

import { useI18n } from '@/i18n/context'

import { AgentAvatar } from './avatar'
import { Inspector } from './inspector'
import type { ActivityEvent, OrganizationSnapshot } from './types'

const categories = { assignment: 'Assignments', decision: 'Decisions', tool: 'Tools', file: 'Files', review: 'Reviews', system: 'System' } as const

const eventCategory: Record<ActivityEvent['kind'], keyof typeof categories> = {
 question: 'decision', delegation: 'assignment', completion: 'assignment', decision: 'decision', tool: 'tool', file: 'file',
 approval: 'review', blocker: 'review', review: 'review', planning: 'system', knowledge: 'system', message: 'system', system: 'system',
}

export function Activity({ snapshot, objectiveId }: { snapshot: OrganizationSnapshot; objectiveId?: string }) {
 const { t } = useI18n()
 const [query, setQuery] = useState('')
 const [kind, setKind] = useState('all')
 const [objective, setObjective] = useState('all')
 const [agent, setAgent] = useState('all')
 const [selected, setSelected] = useState<string | null>(null)
 const runtime = snapshot.source === 'runtime'

 const items = snapshot.activity.filter(item =>
   (!objectiveId || item.objectiveId === objectiveId) && (kind === 'all' || eventCategory[item.kind] === kind) && (objective === 'all' || item.objectiveId === objective) &&
   (agent === 'all' || item.agentId === agent) && item.text.toLowerCase().includes(query.toLowerCase()))

 const event = items.find(item => item.id === selected)

 return <><h2>Coordination timeline</h2>
  <p>{runtime ? 'Runtime coordination, decisions and outcomes recorded by the connected gateway.' : 'Human-readable coordination, decisions and outcomes. Prototype records are not live execution logs.'}</p>
  <div className="eid-toolbar">
   <input aria-label="Search activity" onChange={e => setQuery(e.target.value)} placeholder="Search events" type="search" value={query} />
   <select aria-label="Event type" onChange={e => setKind(e.target.value)} value={kind}><option value="all">All</option>{Object.entries(categories).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
   {/* Scoped details lock this selector; mounting unrelated options defeats list pagination at large scale. */}
   <select aria-label="Activity objective" disabled={!!objectiveId} onChange={e => setObjective(e.target.value)} value={objectiveId || objective}>{objectiveId ? <option value={objectiveId}>{snapshot.objectives.find(item => item.id === objectiveId)?.title ?? objectiveId}</option> : <><option value="all">All objectives</option>{snapshot.objectives.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</>}</select>
   <select aria-label="Activity agent" onChange={e => setAgent(e.target.value)} value={agent}><option value="all">All agents</option>{snapshot.agents.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
  </div>
  {items.length ? <ol className="eid-list">{items.map(item => <li key={item.id}>
   <button aria-label={`Inspect event: ${item.text}`} className="eid-row" onClick={() => setSelected(item.id)} style={{ width: '100%', textAlign: 'left' }}>
    <AgentAvatar name={snapshot.agents.find(a => a.id === item.agentId)?.name || 'Organization'} /><span><small>{categories[eventCategory[item.kind]]} · {runtime ? 'Runtime event' : item.provenance === 'fictional' ? 'Fictional example' : 'Local prototype'}</small><strong>{item.text}</strong><small>{item.agentId ? snapshot.agents.find(a => a.id === item.agentId)?.name || item.agentId : 'Organization'}{item.objectiveId ? ` · ${snapshot.objectives.find(o => o.id === item.objectiveId)?.title || item.objectiveId}` : ''}</small></span>
    <time dateTime={item.timestamp}>{new Date(item.timestamp).toLocaleString()}</time>
   </button>
  </li>)}</ol> : <div className="eid-empty"><h2>{snapshot.activity.length ? 'No matching events' : 'No organization events yet'}</h2><p>{runtime ? 'Events appear when the gateway records organization activity.' : t.organizationWork.legacyEventsEmpty}</p></div>}
  <p><Link to="/processes">Inspect live process logs →</Link></p>
  {event && <Inspector kind="event" onClose={() => setSelected(null)} title={event.text}>
   <p className="eid-eyebrow">{runtime ? 'Runtime event · Gateway record' : event.provenance === 'fictional' ? 'Fictional example · Not live' : 'Local prototype event · Not live'}</p><dl><dt>Category</dt><dd>{categories[eventCategory[event.kind]]}</dd><dt>Event type</dt><dd>{event.kind}</dd><dt>Recorded</dt><dd><time dateTime={event.timestamp}>{new Date(event.timestamp).toLocaleString()}</time></dd><dt>Agent</dt><dd>{snapshot.agents.find(item => item.id === event.agentId)?.name || event.agentId || 'No agent linked'}</dd>{runtime && <><dt>Event ID</dt><dd>{event.id}</dd><dt>Source</dt><dd>Connected gateway organization runtime</dd></>}</dl>
   {event.description && <><h3>Observable details</h3><p>{event.description}</p></>}
   {event.objectiveId && <Link to={`/objectives/${event.objectiveId}`}>Open objective</Link>}
   <h3>Diagnostic context</h3>{runtime ? <><p>{snapshot.runtime?.scope || 'Execution scope not reported by runtime.'}</p><p>Coordination events do not by themselves verify external tool execution. Inspect task evidence for recorded results and session identifiers.</p></> : <p>No runtime log or process identifier is attached. This record comes from the local prototype adapter.</p>}
  </Inspector>}
 </>
}
