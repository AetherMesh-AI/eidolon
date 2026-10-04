import { responsibleAgents, responsibleTeams } from './objective-context'
import type { Objective, OrganizationAgent, OrganizationSnapshot } from './types'

export function ObjectiveOverview({ snapshot, objective }: { snapshot: OrganizationSnapshot; objective: Objective }) {
  const direct = responsibleAgents(snapshot, objective)
  const directIds = new Set(direct.map(agent => agent.id))
  const included = new Map(direct.map(agent => [agent.id, agent]))

  for (const agent of direct) {
    let managerId = agent.managerId
    const seen = new Set([agent.id])

    while (managerId && !seen.has(managerId)) {
      seen.add(managerId)
      const manager = snapshot.agents.find(item => item.id === managerId)

      if (!manager) {break}
      included.set(manager.id, manager)
      managerId = manager.managerId
    }
  }

  const agents = [...included.values()]
  const roots = agents.filter(agent => !agent.managerId || !included.has(agent.managerId))

  const renderAgent = (agent: OrganizationAgent, ancestors: Set<string>): React.ReactNode => {
    if (ancestors.has(agent.id)) {return <li key={agent.id}>Unknown · cyclic reporting relationship</li>}
    const next = new Set([...ancestors, agent.id])
    const children = agents.filter(child => child.managerId === agent.id)

    return <li key={agent.id}><strong>{agent.name}</strong> · {directIds.has(agent.id) ? 'Responsible agent' : 'Reporting context'}<p className="eid-note">Team: {agent.team?.trim() || 'Unknown · team not recorded'} · {agent.role}</p>{agent.managerId && !included.has(agent.managerId) && <p className="eid-note">Manager unknown · not in scoped data</p>}{children.length > 0 && <ul>{children.map(child => renderAgent(child, next))}</ul>}</li>
  }

  const decisions = (snapshot.decisions || []).filter(item => item.objectiveId === objective.id)
  const recentEvents = snapshot.activity.filter(item => item.objectiveId === objective.id && item.kind === 'decision').sort((a, b) => b.timestamp.localeCompare(a.timestamp)).slice(0, 3)
  const artifacts = snapshot.knowledge.filter(item => item.objectiveId === objective.id && item.kind === 'artifact')

  return <div className="eid-overview-context">
    <section aria-label="Responsible organization subtree" className="eid-card"><h3>Responsible organization subtree</h3><p>Responsible team: {responsibleTeams(snapshot, objective)}</p><p className="eid-note">Objective owner, assigned agents and task owners; their managers appear only as reporting context.</p>{agents.length ? <ul>{(roots.length ? roots : direct).map(agent => renderAgent(agent, new Set()))}</ul> : <p>Unknown · no responsible agents recorded in this scoped snapshot.</p>}</section>
    <section aria-label="Recent decisions" className="eid-card"><h3>Recent decisions</h3>{recentEvents.map(item => <article key={item.id}><p>{item.text}</p><small>{item.timestamp}</small></article>)}{decisions.length > 0 && <><p className="eid-note">Recency unknown · decision records have no timestamps. Recorded decisions for this objective:</p>{decisions.map(item => <article key={item.id}><strong>{item.title}</strong><small> · {item.status}</small><p>{item.summary}</p></article>)}</>}{!decisions.length && !recentEvents.length && <p>Unknown · no decisions recorded for this objective.</p>}</section>
    <section aria-label="Major artifacts" className="eid-card"><h3>Major artifacts</h3>{artifacts.length ? <><p className="eid-note">Importance unknown · no major-artifact ranking recorded. Artifacts linked to this objective:</p>{artifacts.map(item => <article key={item.id}><strong>{item.title}</strong><p>{item.body}</p></article>)}</> : <p>Unknown · no artifacts recorded for this objective.</p>}</section>
  </div>
}
