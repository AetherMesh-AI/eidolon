import { Link, useLocation } from 'react-router'

import { controlVariants } from '@/components/ui/control'
import { Input } from '@/components/ui/input'
import { useI18n } from '@/i18n/context'
import type { OrganizationFoundationCopy } from '@/i18n/organization-foundation'
import { observationCopy } from '@/i18n/organization-observation'

import { AgentAvatar } from './avatar'
import { Inspector } from './inspector'
import { LocalizedTime } from './localized-time'
import { observationState } from './observation-state'
import type { ActivityEvent, OrganizationSnapshot } from './types'
import { contextualObjectiveHref, useWorkspaceRouteState } from './workspace-navigation'


const eventCategory: Record<ActivityEvent['kind'], keyof OrganizationFoundationCopy['categories']> = {
 question: 'decision', delegation: 'assignment', completion: 'assignment', decision: 'decision', tool: 'tool', file: 'file',
 approval: 'review', blocker: 'review', review: 'review', planning: 'system', knowledge: 'system', message: 'system', system: 'system',
}

export function Activity({ snapshot, objectiveId }: { snapshot: OrganizationSnapshot; objectiveId?: string }) {
 const { t, locale } = useI18n()
 const copy = t.organizationFoundation
 const categories = copy.categories
 const location = useLocation()
 const scope = snapshot.connection?.ownerScope ?? snapshot.connection?.scope
 const state = useWorkspaceRouteState('activity', scope)
 const query = state.read('Query')
 const kind = Object.hasOwn(categories, state.read('Kind')) ? state.read('Kind') : 'all'
 const objective = snapshot.objectives.some(item => item.id === state.read('Objective')) ? state.read('Objective') : 'all'
 const agent = snapshot.agents.some(item => item.id === state.read('Agent')) ? state.read('Agent') : 'all'
 const eventIdentity = (item: ActivityEvent) => JSON.stringify([item.timestamp, item.objectiveId, item.agentId])
 const rowIdentity = (item: ActivityEvent) => JSON.stringify(['activity', scope, item.id, eventIdentity(item)])

 const setSelected = (id: string | null) => {
   const item = snapshot.activity.find(value => value.id === id)
   state.write({ Event: item?.id ?? null, Identity: item ? eventIdentity(item) : null })
 }

 const runtime = snapshot.source === 'runtime'
 const observation = observationState(snapshot, snapshot.activity.length)
 const stateCopy = observationCopy[locale]
 const retained = observation === 'retained'

 const items = snapshot.activity.filter(item =>
   (!objectiveId || item.objectiveId === objectiveId) && (kind === 'all' || eventCategory[item.kind] === kind) && (objective === 'all' || item.objectiveId === objective) &&
   (agent === 'all' || item.agentId === agent) && item.text.toLowerCase().includes(query.toLowerCase()))

 const event = items.find(item => item.id === state.read('Event') && eventIdentity(item) === state.read('Identity'))

 return <><h2>{copy.timeline}</h2>
  <p>{runtime ? copy.runtimeTimeline : copy.localTimeline}</p>
  {retained && <p role="status">{stateCopy.stale}</p>}
  <div className="eid-toolbar eid-activity-filters">
   <label className="eid-filter">{copy.searchActivity}<Input onChange={e => state.write({ Query: e.target.value })} placeholder={copy.searchEvents} type="search" value={query} /></label>
   <label className="eid-filter">{copy.eventType}<select className={controlVariants()} onChange={e => state.write({ Kind: e.target.value })} value={kind}><option value="all">{copy.all}</option>{Object.entries(categories).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
   {/* Scoped details lock this selector; mounting unrelated options defeats list pagination at large scale. */}
   <label className="eid-filter">{copy.activityObjective}<select className={controlVariants()} disabled={!!objectiveId} onChange={e => state.write({ Objective: e.target.value })} value={objectiveId || objective}>{objectiveId ? <option value={objectiveId}>{snapshot.objectives.find(item => item.id === objectiveId)?.title ?? objectiveId}</option> : <><option value="all">{copy.allObjectives}</option>{snapshot.objectives.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</>}</select></label>
   <label className="eid-filter">{copy.activityAgent}<select className={controlVariants()} onChange={e => state.write({ Agent: e.target.value })} value={agent}><option value="all">{copy.allAgents}</option>{snapshot.agents.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
  </div>
  {(observation === 'current' || retained) && <p>{copy.eventCount(items.length, new Intl.NumberFormat(locale).format(items.length))}</p>}
  {observation === 'loading' || observation === 'unavailable' ? <div className="eid-empty" role="status"><h2>{stateCopy[observation]}</h2><p>{stateCopy.unknown}</p></div> : items.length ? <ol className="eid-list">{items.map(item => <li key={item.id}>
   <button aria-label={copy.inspectEvent(item.text)} className="eid-row eid-activity-row" data-inspector-origin={rowIdentity(item)} onClick={() => setSelected(item.id)}>
    <AgentAvatar name={snapshot.agents.find(a => a.id === item.agentId)?.name || copy.organization} /><span className="eid-activity-copy"><small>{categories[eventCategory[item.kind]]} · {runtime ? copy.runtimeKind : item.provenance === 'fictional' ? copy.fictionalKind : copy.localKind}</small><strong dir="auto">{item.text}</strong><small><bdi>{item.agentId ? snapshot.agents.find(a => a.id === item.agentId)?.name || item.agentId : copy.organization}</bdi>{item.objectiveId && <> · <bdi>{snapshot.objectives.find(o => o.id === item.objectiveId)?.title || item.objectiveId}</bdi></>}</small></span>
    <LocalizedTime value={item.timestamp} />
   </button>
  </li>)}</ol> : <div className="eid-empty"><h2>{retained ? (snapshot.activity.length ? stateCopy.noMatches : stateCopy.empty) : snapshot.activity.length ? copy.noMatches : copy.noEvents}</h2>{!retained && <p>{runtime ? copy.eventsNote : t.organizationWork.legacyEventsEmpty}</p>}</div>}
  <p><Link to="/processes">{copy.logs}</Link></p>
  {event && <Inspector focusReturnTarget={() => Array.from(globalThis.document.querySelectorAll<HTMLElement>('[data-inspector-origin]')).find(row => row.dataset.inspectorOrigin === rowIdentity(event)) ?? null} kind="event" onClose={() => setSelected(null)} restorationScope={scope ? { owner: scope, route: location.pathname + location.search } : undefined} title={event.text}>
   <p className="eid-eyebrow">{runtime ? copy.runtimeEvent : event.provenance === 'fictional' ? copy.fictionalEvent : copy.localEvent}</p><dl><dt>{copy.category}</dt><dd>{categories[eventCategory[event.kind]]}</dd><dt>{copy.eventType}</dt><dd>{event.kind}</dd><dt>{copy.recorded}</dt><dd><LocalizedTime value={event.timestamp} /></dd><dt>{copy.agent}</dt><dd>{snapshot.agents.find(item => item.id === event.agentId)?.name || event.agentId || copy.noAgent}</dd>{runtime && <><dt>{copy.eventId}</dt><dd>{event.id}</dd><dt>{copy.source}</dt><dd>{copy.gatewaySource}</dd></>}</dl>
   {event.description && <><h3>{copy.details}</h3><p dir="auto">{event.description}</p></>}
   {event.objectiveId && <Link to={contextualObjectiveHref(event.objectiveId, location.pathname, location.search, scope)}>{copy.openObjective}</Link>}
   <h3>{copy.diagnostic}</h3>{runtime ? <><p>{snapshot.runtime?.scope || copy.noScope}</p><p>{copy.evidenceNote}</p></> : <p>{copy.localEvidenceNote}</p>}
  </Inspector>}
 </>
}
