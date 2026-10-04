import { useState } from 'react'
import { Link } from 'react-router'

import { useI18n } from '@/i18n/context'

import { AgentAvatar } from './avatar'
import { Inspector } from './inspector'
import type { OrganizationAgent, OrganizationSnapshot } from './types'

function currentAssignment(snapshot: OrganizationSnapshot, agent: OrganizationAgent) {
  const tasks = snapshot.tasks.filter(task => (task.ownerId === agent.id || task.reviewerId === agent.id) && task.status !== 'completed' && task.status !== 'cancelled')

  if (tasks.length) {return tasks.map(task => `${task.reviewerId === agent.id && task.ownerId !== agent.id ? 'Review: ' : ''}${task.title} (${task.status})`).join('; ')}

  return snapshot.source === 'runtime' ? 'No current task assignment recorded' : snapshot.objectives.find(objective => objective.id === agent.objectiveId)?.title || 'Unknown · no current assignment recorded'
}

export function Organization({ snapshot }: { snapshot: OrganizationSnapshot }) {
  const { t } = useI18n()
  const copy = t.organizationRuntime
  const [selected, setSelected] = useState<string | null>(null)
  const [mode, setMode] = useState('List')
  const [query, setQuery] = useState('')
  const runtime = snapshot.source === 'runtime'
  const agent = snapshot.agents.find(item => item.id === selected)
  const agents = snapshot.agents.filter(item => `${item.name} ${item.role} ${item.team || ''} ${item.provider || ''} ${item.model || ''} ${item.lifecycle || ''} ${item.tools?.join(' ') || ''} ${item.requestTypes?.join(' ') || ''} ${item.responsibilities.join(' ')} ${item.capabilities.join(' ')}`.toLowerCase().includes(query.toLowerCase()))
  const assignedTasks = agent ? snapshot.tasks.filter(item => item.ownerId === agent.id || item.reviewerId === agent.id) : []
  const managerName = (item: OrganizationAgent) => item.managerId ? snapshot.agents.find(manager => manager.id === item.managerId)?.name || item.managerId : runtime ? 'No manager recorded' : 'You'
  const unavailable = runtime ? 'Not reported by runtime' : 'Unavailable · No runtime connection'

  return <>
    <p>{runtime ? 'Runtime organization roster and assignments from the connected gateway. Organizational roles do not replace runtime profiles.' : 'People set direction. The Lead coordinates specialists. Organizational roles do not replace runtime profiles.'}</p>
    {runtime && <p className="eid-note">{copy.rosterNote}</p>}
    <div className="eid-toolbar"><label className="eid-filter">Find an agent<input onChange={event => setQuery(event.target.value)} placeholder="Name or responsibility…" value={query} /></label><div aria-label="Organization layout" className="eid-tabs">{['Grid', 'List'].map(item => <button aria-pressed={mode === item} key={item} onClick={() => setMode(item)}>{item}</button>)}</div></div>
    <div aria-label={`${mode} of organization`} className={`eid-organization eid-organization-${mode.toLowerCase()}`}>
      {agents.map(item => <button aria-label={`Inspect ${item.name}`} className={`eid-agent eid-agent-${item.status}`} key={item.id} onClick={() => setSelected(item.id)}>
        <small>{runtime ? 'Runtime agent · ' : ''}{item.managerId ? `Reports to ${managerName(item)}` : runtime ? managerName(item) : 'Reports to you'}</small>
        <AgentAvatar name={item.name} /><strong>{item.name}</strong><span>{item.role}</span><span>Team: {item.team?.trim() || 'Unknown · team not recorded'}</span>{runtime && <><span>{copy.lifecycle}: {item.lifecycle ? copy.lifecycleLabels[item.lifecycle] : copy.notReported}</span><span>{copy.provider}: {item.provider || copy.notReported} · {copy.model}: {item.model || copy.notReported}</span><span>{copy.tools}: {item.tools ? item.tools.join(', ') || copy.noTools : copy.notReported}</span><span>{copy.capabilities}: {item.capabilities.join(', ') || copy.noCapabilities}</span><span>Request types: {item.requestTypes?.join(', ') || 'Not reported'}</span></>}<span>Current assignment: {currentAssignment(snapshot, item)}</span><span className={`eid-status eid-status-${item.status}`}>● {item.status}</span><p>{item.summary}</p>
      </button>)}
    </div>
    {!agents.length && <div className="eid-empty"><h2>{query ? 'No matching agents' : 'No connected organization agents'}</h2><p>{query ? 'Try another name, team, or responsibility.' : runtime ? 'The connected gateway has not reported any organization agents.' : 'Load the explicitly fictional example to explore the map, or inspect existing runtime configuration.'}</p></div>}
    <div className="eid-inline"><Link className="eid-button" to="/profiles">Manage runtime profiles</Link><Link to="/agents">Inspect live agents →</Link></div>
    {agent && <Inspector kind="agent" onClose={() => setSelected(null)} title={agent.name}>
      <p className="eid-eyebrow">{runtime ? 'Runtime agent · Gateway record' : 'Prototype agent · Not live'}</p><AgentAvatar name={agent.name} /><p>{agent.role}</p>
      <dl>{runtime && <><dt>{copy.lifecycle}</dt><dd>{agent.lifecycle ? copy.lifecycleLabels[agent.lifecycle] : copy.notReported}</dd><dt>{copy.provider}</dt><dd>{agent.provider || copy.notReported}</dd></>}<dt>Status</dt><dd>● {agent.status}</dd><dt>Team</dt><dd>{agent.team?.trim() || 'Unknown · team not recorded'}</dd><dt>Reports to</dt><dd>{managerName(agent)}</dd><dt>Direct reports</dt><dd>{snapshot.agents.filter(item => item.managerId === agent.id).map(item => item.name).join(', ') || 'None'}</dd><dt>Model</dt><dd>{agent.model || unavailable}</dd><dt>Context usage / capacity</dt><dd>{agent.context ? `${agent.context.used ?? 'Unknown'} / ${agent.context.capacity ?? 'Unknown'} tokens` : unavailable}</dd><dt>Tools</dt><dd>{agent.tools?.join(', ') || (runtime && agent.tools ? copy.noTools : unavailable)}</dd>{runtime && <><dt>Request types</dt><dd>{agent.requestTypes?.join(', ') || 'Not reported by runtime'}</dd></>}</dl>
      <p>{agent.summary}</p><h3>Responsibilities</h3><ul>{agent.responsibilities.map(item => <li key={item}>{item}</li>)}</ul><h3>Capabilities</h3><ul>{agent.capabilities.map(item => <li key={item}>{item}</li>)}</ul>
      <h3>Current assignment</h3>{runtime ? <p>{currentAssignment(snapshot, agent)}</p> : agent.objectiveId ? <Link to={`/objectives/${agent.objectiveId}`}>{snapshot.objectives.find(item => item.id === agent.objectiveId)?.title || 'Open objective'}</Link> : <p>No assignment</p>}
      {runtime && <><h3>Assigned work and review</h3>{assignedTasks.length ? <ul>{assignedTasks.map(item => <li key={item.id}><Link to={`/objectives/${item.objectiveId}`}>{item.title}</Link> · {item.reviewerId === agent.id ? 'Review assignment' : item.requestType || 'Request type not recorded'} · {item.status}{item.requestType === 'review' && <> · Reviews: {item.dependsOn.map(id => snapshot.tasks.find(dependency => dependency.id === id)?.title || id).join(', ') || 'No task dependency recorded'}</>}</li>)}</ul> : <p>No task assignments recorded.</p>}</>}
      <h3>{t.organizationWork.executionConfig}</h3>{runtime && <p>Agent ID: {agent.id}</p>}<p>{agent.profileName || 'No runtime profile linked'}</p><Link to="/profiles">{t.organizationWork.executionConfig} →</Link><p><Link to="/activity">{t.organizationWork.executionHistory} →</Link></p>
      {runtime && <><h3>Execution scope</h3><p>{snapshot.runtime?.scope || 'Execution scope not reported by runtime.'}</p></>}
      <h3>Recent activity</h3>{snapshot.activity.filter(item => item.agentId === agent.id).map(item => <p key={item.id}>{item.text}</p>)}
    </Inspector>}
  </>
}
