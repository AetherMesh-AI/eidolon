import { useState } from 'react'
import { Link } from 'react-router'

import { useI18n } from '@/i18n/context'
import type { OrganizationFoundationCopy } from '@/i18n/organization-foundation'

import { AgentAvatar } from './avatar'
import { Inspector } from './inspector'
import { LocalizedTime } from './localized-time'
import type { ActivityEvent, OrganizationSnapshot } from './types'


const eventCategory: Record<ActivityEvent['kind'], keyof OrganizationFoundationCopy['categories']> = {
 question: 'decision', delegation: 'assignment', completion: 'assignment', decision: 'decision', tool: 'tool', file: 'file',
 approval: 'review', blocker: 'review', review: 'review', planning: 'system', knowledge: 'system', message: 'system', system: 'system',
}

export function Activity({ snapshot, objectiveId }: { snapshot: OrganizationSnapshot; objectiveId?: string }) {
 const { t, locale } = useI18n()
 const copy = t.organizationFoundation
 const categories = copy.categories
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

 return <><h2>{copy.timeline}</h2>
  <p>{runtime ? copy.runtimeTimeline : copy.localTimeline}</p>
  <div className="eid-toolbar">
   <input aria-label={copy.searchActivity} onChange={e => setQuery(e.target.value)} placeholder={copy.searchEvents} type="search" value={query} />
   <select aria-label={copy.eventType} onChange={e => setKind(e.target.value)} value={kind}><option value="all">{copy.all}</option>{Object.entries(categories).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
   {/* Scoped details lock this selector; mounting unrelated options defeats list pagination at large scale. */}
   <select aria-label={copy.activityObjective} disabled={!!objectiveId} onChange={e => setObjective(e.target.value)} value={objectiveId || objective}>{objectiveId ? <option value={objectiveId}>{snapshot.objectives.find(item => item.id === objectiveId)?.title ?? objectiveId}</option> : <><option value="all">{copy.allObjectives}</option>{snapshot.objectives.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</>}</select>
   <select aria-label={copy.activityAgent} onChange={e => setAgent(e.target.value)} value={agent}><option value="all">{copy.allAgents}</option>{snapshot.agents.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
  </div>
  <p>{copy.eventCount(items.length, new Intl.NumberFormat(locale).format(items.length))}</p>
  {items.length ? <ol className="eid-list">{items.map(item => <li key={item.id}>
   <button aria-label={copy.inspectEvent(item.text)} className="eid-row" onClick={() => setSelected(item.id)} style={{ width: '100%', textAlign: 'left' }}>
    <AgentAvatar name={snapshot.agents.find(a => a.id === item.agentId)?.name || copy.organization} /><span><small>{categories[eventCategory[item.kind]]} · {runtime ? copy.runtimeKind : item.provenance === 'fictional' ? copy.fictionalKind : copy.localKind}</small><strong>{item.text}</strong><small>{item.agentId ? snapshot.agents.find(a => a.id === item.agentId)?.name || item.agentId : copy.organization}{item.objectiveId ? ` · ${snapshot.objectives.find(o => o.id === item.objectiveId)?.title || item.objectiveId}` : ''}</small></span>
    <LocalizedTime value={item.timestamp} />
   </button>
  </li>)}</ol> : <div className="eid-empty"><h2>{snapshot.activity.length ? copy.noMatches : copy.noEvents}</h2><p>{runtime ? copy.eventsNote : t.organizationWork.legacyEventsEmpty}</p></div>}
  <p><Link to="/processes">{copy.logs}</Link></p>
  {event && <Inspector kind="event" onClose={() => setSelected(null)} title={event.text}>
   <p className="eid-eyebrow">{runtime ? copy.runtimeEvent : event.provenance === 'fictional' ? copy.fictionalEvent : copy.localEvent}</p><dl><dt>{copy.category}</dt><dd>{categories[eventCategory[event.kind]]}</dd><dt>{copy.eventType}</dt><dd>{event.kind}</dd><dt>{copy.recorded}</dt><dd><LocalizedTime value={event.timestamp} /></dd><dt>{copy.agent}</dt><dd>{snapshot.agents.find(item => item.id === event.agentId)?.name || event.agentId || copy.noAgent}</dd>{runtime && <><dt>{copy.eventId}</dt><dd>{event.id}</dd><dt>{copy.source}</dt><dd>{copy.gatewaySource}</dd></>}</dl>
   {event.description && <><h3>{copy.details}</h3><p>{event.description}</p></>}
   {event.objectiveId && <Link to={`/objectives/${event.objectiveId}`}>{copy.openObjective}</Link>}
   <h3>{copy.diagnostic}</h3>{runtime ? <><p>{snapshot.runtime?.scope || copy.noScope}</p><p>{copy.evidenceNote}</p></> : <p>{copy.localEvidenceNote}</p>}
  </Inspector>}
 </>
}
