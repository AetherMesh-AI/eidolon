import { useState } from 'react'
import { Link } from 'react-router'

import { AgentAvatar } from './avatar'
import { Inspector } from './inspector'
import type { OrganizationAgent, OrganizationSnapshot } from './types'

function currentAssignment(snapshot: OrganizationSnapshot, agent: OrganizationAgent) {
  const tasks = snapshot.tasks.filter(task => task.ownerId === agent.id && task.status !== 'completed')

  if (tasks.length) {return tasks.map(task => `${task.title} (${task.status})`).join('; ')}

  return snapshot.objectives.find(objective => objective.id === agent.objectiveId)?.title || 'Unknown · no current assignment recorded'
}

export function Organization({ snapshot }: { snapshot: OrganizationSnapshot }) {
  const [selected, setSelected] = useState<string | null>(null)
  const [mode, setMode] = useState('Map')
  const [query, setQuery] = useState('')
  const agent = snapshot.agents.find(item => item.id === selected)
  const agents = snapshot.agents.filter(item => `${item.name} ${item.role} ${item.responsibilities.join(' ')} ${item.capabilities.join(' ')}`.toLowerCase().includes(query.toLowerCase()))

  return <>
    <p>People set direction. The Lead coordinates specialists. Organizational roles do not replace runtime profiles.</p>
    <div className="eid-toolbar"><label className="eid-filter">Find an agent<input onChange={event => setQuery(event.target.value)} placeholder="Name or responsibility…" value={query} /></label><div aria-label="Organization layout" className="eid-tabs">{['Map', 'Grid', 'List'].map(item => <button aria-pressed={mode === item} key={item} onClick={() => setMode(item)}>{item}</button>)}</div></div>
    <div aria-label={`${mode} of organization`} className={`eid-organization eid-organization-${mode.toLowerCase()}`}>
      {mode === 'Map' && <div className="eid-agent eid-human"><small>Direction & approval</small><strong>You</strong></div>}
      {agents.map(item => <button aria-label={`Inspect ${item.name}`} className={`eid-agent eid-agent-${item.status}`} key={item.id} onClick={() => setSelected(item.id)}>
        <small>{item.managerId ? `Reports to ${snapshot.agents.find(manager => manager.id === item.managerId)?.name || item.managerId}` : 'Reports to you'}</small>
        <AgentAvatar name={item.name} /><strong>{item.name}</strong><span>{item.role}</span><span>Team: {item.team?.trim() || 'Unknown · team not recorded'}</span><span>Current assignment: {currentAssignment(snapshot, item)}</span><span className={`eid-status eid-status-${item.status}`}>● {item.status}</span><p>{item.summary}</p>
      </button>)}
    </div>
    {!agents.length && <div className="eid-empty"><h2>{query ? 'No matching agents' : 'No connected organization agents'}</h2><p>Load the explicitly fictional example to explore the map, or inspect existing runtime configuration.</p></div>}
    <div className="eid-inline"><Link className="eid-button" to="/profiles">Manage runtime profiles</Link><Link to="/agents">Inspect live agents →</Link></div>
    {agent && <Inspector kind="agent" onClose={() => setSelected(null)} title={agent.name}><p className="eid-eyebrow">Prototype agent · Not live</p><AgentAvatar name={agent.name} /><p>{agent.role}</p><dl><dt>Status</dt><dd>● {agent.status}</dd><dt>Reports to</dt><dd>{snapshot.agents.find(item => item.id === agent.managerId)?.name || 'You'}</dd><dt>Direct reports</dt><dd>{snapshot.agents.filter(item => item.managerId === agent.id).map(item => item.name).join(', ') || 'None'}</dd><dt>Model</dt><dd>{agent.model || 'Unavailable · No runtime connection'}</dd><dt>Context usage / capacity</dt><dd>{agent.context ? `${agent.context.used ?? 'Unknown'} / ${agent.context.capacity ?? 'Unknown'} tokens` : 'Unavailable · No runtime connection'}</dd><dt>Tools</dt><dd>{agent.tools?.join(', ') || 'Unavailable · No runtime connection'}</dd></dl><p>{agent.summary}</p><h3>Responsibilities</h3><ul>{agent.responsibilities.map(item => <li key={item}>{item}</li>)}</ul><h3>Capabilities</h3><ul>{agent.capabilities.map(item => <li key={item}>{item}</li>)}</ul><h3>Current assignment</h3>{agent.objectiveId ? <Link to={`/objectives/${agent.objectiveId}`}>{snapshot.objectives.find(item => item.id === agent.objectiveId)?.title || 'Open objective'}</Link> : <p>No assignment</p>}<h3>Runtime identity</h3><p>{agent.profileName || 'No runtime profile linked'}</p><Link to="/profiles">Inspect runtime profiles →</Link><h3>Recent activity</h3>{snapshot.activity.filter(item => item.agentId === agent.id).map(item => <p key={item.id}>{item.text}</p>)}</Inspector>}
  </>
}
