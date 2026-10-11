import { useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { Button } from '@/components/ui/button'
import { SearchField } from '@/components/ui/search-field'
import { useI18n } from '@/i18n/context'

import { AgentContext } from './agent-context'
import { AgentAvatar } from './avatar'
import { OrganizationOwnerChat } from './runtime-owner-chat'
import type { OrganizationAgent, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

/** Only recorded ownership, assignment or current package membership links work. */
function linkedObjectives(agent: OrganizationAgent, snapshot: OrganizationSnapshot) {
  const taskObjectives = new Set(snapshot.tasks.filter(task =>
    !task.historical && task.currentRound !== false &&
    [task.ownerId, task.assignedAgentId, task.managingAgentId, task.reviewerId].includes(agent.id)
  ).map(task => task.objectiveId))

  return snapshot.objectives.filter(objective =>
    objective.ownerId === agent.id || objective.executiveId === agent.id ||
    objective.managerId === agent.id || objective.id === agent.objectiveId ||
    taskObjectives.has(objective.id) || objective.workPackages?.some(item =>
      item.objectiveId === objective.id && item.round === objective.acceptance?.round && item.managerId === agent.id
    )
  )
}

/** Selection and search never open, send to, or renew a durable member chat. */
export function Messages({
  adapter,
  snapshot
}: {
  adapter?: RuntimeOrganizationAdapter
  snapshot: OrganizationSnapshot
}) {
  const { t } = useI18n()
  const copy = t.organizationHome
  const messages = copy.memberMessages
  const [params] = useSearchParams()
  const executiveEntry = params.get('recipient') === 'executive'
  const [selected, setSelected] = useState<{ id: string; identityId?: string; scope?: string } | null>(null)
  const [query, setQuery] = useState('')
  const scope = snapshot.connection?.ownerScope ?? snapshot.connection?.scope

  const roles: Record<string, string> = {
    Executive: t.organizationRoster.executive,
    Manager: t.organizationRoster.manager,
    Worker: t.organizationRoster.worker
  }

  const agents = snapshot.agents.filter(
    agent => agent.persistent && agent.role !== 'Owner' && !agent.id.startsWith('control:') &&
      (!executiveEntry || (agent.role === 'Executive' && agent.lifecycle === 'active' && typeof agent.identityId === 'string' && agent.identityId.trim().length > 0))
  )

  const matches = agents.filter(agent =>
    [agent.name, agent.role, roles[agent.role], agent.team, agent.purpose].filter(Boolean).join(' ').toLowerCase().includes(query.trim().toLowerCase())
  )

  const agent = selected?.scope === scope
    ? agents.find(item => item.id === selected?.id && item.identityId === selected.identityId)
    : undefined

  const objectives = agent ? linkedObjectives(agent, snapshot) : []

  return (
    <div className="eid-messages-view">
      <div className="eid-messages-layout" data-member-selected={Boolean(agent)}>
        <section aria-label={messages.members} className="eid-message-members">
          <header className="eid-message-directory-heading">
            <h1>{executiveEntry ? copy.messageOrganization : copy.messages}</h1>
            <p>{executiveEntry ? copy.executiveMessageNote : copy.messagesNote}</p>
          </header>
          <SearchField aria-label={messages.search} containerClassName="opacity-100" onChange={setQuery} placeholder={messages.search} value={query} />
          <nav aria-label={copy.chooseAgent}>
            {matches.map(item => (
              <Button
                aria-pressed={item.id === agent?.id}
                key={item.id}
                onClick={() => setSelected({ id: item.id, identityId: item.identityId, scope })}
                variant="ghost"
              >
                <AgentAvatar name={item.name} />
                <span><strong>{item.name}</strong><small>{roles[item.role] ?? item.role}{item.team ? ` · ${item.team}` : ''}</small></span>
              </Button>
            ))}
          </nav>
          {!matches.length && <p role="status">{agents.length ? messages.noMatches : executiveEntry ? copy.noMessageExecutive : copy.noAgents}</p>}
          <p className="eid-note">{messages.directoryNote}</p>{executiveEntry && !agents.length && <Link to="/organization">{copy.reviewMessageRoster}</Link>}
        </section>
        <section aria-label={messages.conversation} className="eid-message-conversation">
          {agent ? (
            <>
              <header className="eid-message-recipient"><AgentAvatar name={agent.name} /><div><h2>{agent.name}</h2><p>{roles[agent.role] ?? agent.role}{agent.team ? ` · ${agent.team}` : ''}</p></div></header>
              <OrganizationOwnerChat
                adapter={adapter}
                agent={agent}
                key={`${scope}:${agent.identityId ?? agent.id}`}
                snapshot={snapshot}
              />
            </>
          ) : (
            <div className="eid-message-empty"><h2>{copy.chooseAgent}</h2><p>{agents.length ? messages.selectNote : executiveEntry ? copy.noMessageExecutive : copy.noAgents}</p></div>
          )}
        </section>
        {agent && <section aria-label={messages.memberContext} className="eid-message-context">
          <header className="eid-message-profile">
            <AgentAvatar name={agent.name} />
            <h2>{agent.name}</h2>
            <p>{roles[agent.role] ?? agent.role}{agent.team ? ` · ${agent.team}` : ''}</p>
          </header>
          <p className="eid-result-text">{agent.purpose || agent.summary}</p>
          {!!agent.responsibilities.length && <section aria-label={messages.responsibilities}><h3>{messages.responsibilities}</h3><ul>{agent.responsibilities.map((item, index) => <li key={index}>{item}</li>)}</ul></section>}
          <section aria-label={messages.linkedWork}>
            <h3>{messages.linkedWork}</h3>
            <p className="eid-note">{messages.linkedWorkNote}</p>
            {objectives.length ? <ul>{objectives.map(objective => <li key={objective.id}><Link to={`/objectives/${objective.id}`}>{objective.title}</Link></li>)}</ul> : <p>{messages.noLinkedWork}</p>}
          </section>
          <details><summary>{messages.retainedContext}</summary><AgentContext agent={agent} snapshot={snapshot} /></details>
        </section>}
      </div>
    </div>
  )
}
