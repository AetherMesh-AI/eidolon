import './messages.css'

import { useCallback, useState } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router'

import { ResponsiveMasterDetail } from '@/app/responsive-master-detail'
import { Button } from '@/components/ui/button'
import { SearchField } from '@/components/ui/search-field'
import { useI18n } from '@/i18n/context'

import { AgentContextDisclosure } from './agent-context'
import { AgentAvatar } from './avatar'
import { OrganizationOwnerChat } from './runtime-owner-chat'
import { OrganizationScenery } from './scenery'
import type { OrganizationAgent, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'
import { objectiveHref } from './workspace-navigation'

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
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const executiveEntry = params.get('recipient') === 'executive'
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

  const agent = scope && params.get('owner') === scope
    ? agents.find(item => item.id === params.get('member') && (item.identityId ?? '') === params.get('identity'))
    : undefined

  // Back changes only presentation. Browser navigation and owner/identity changes
  // get their own presentation while chat ownership stays with the URL tuple.
  const selection = JSON.stringify([location.key, scope, params.get('owner'), params.get('member'), params.get('identity')])
  const [presentation, setPresentation] = useState<{ selection: string; open: boolean } | null>(null)
  const detailOpen = Boolean(agent) && (presentation?.selection !== selection || presentation.open)
  const setDetailOpen = useCallback((open: boolean) => setPresentation({ selection, open }), [selection])
  const objectives = agent ? linkedObjectives(agent, snapshot) : []

  return (
    <div className="eid-messages-view" data-has-members={Boolean(agents.length)} data-member-selected={Boolean(agent)}>
      <ResponsiveMasterDetail
        backLabel={messages.backToMembers}
        className="eid-messages-layout"
        detailOpen={detailOpen}
        onDetailOpenChange={setDetailOpen}
        variant="messages"
      >
        <section aria-label={messages.members} className="eid-message-members">
          <header className="eid-message-directory-heading">
            <h1>{executiveEntry ? copy.messageOrganization : copy.messages}</h1>
          </header>
          <SearchField aria-label={messages.search} containerClassName="eid-message-search opacity-100" onChange={setQuery} placeholder={messages.search} value={query} />
          <nav aria-label={copy.chooseAgent}>
            {matches.map(item => (
              <Button
                aria-pressed={item.id === agent?.id}
                key={item.id}
                onClick={() => {
                  if (!scope) {return}
                  setDetailOpen(true)
                  const next = new URLSearchParams(params)
                  next.set('member', item.id)
                  next.set('identity', item.identityId ?? '')
                  next.set('owner', scope)
                  setParams(next)
                }}
                variant="ghost"
              >
                <AgentAvatar name={item.name} />
                <span><strong dir="auto">{item.name}</strong><small>{roles[item.role] ?? item.role}{item.team && <> · <bdi>{item.team}</bdi></>}</small></span>
              </Button>
            ))}
          </nav>
          {!!agents.length && !matches.length && <p role="status">{messages.noMatches}</p>}
          {!agents.length && <div className="eid-message-empty">
            <h2>{messages.noRecipients}</h2>
            <p>{executiveEntry ? copy.noMessageExecutive : copy.noAgents}</p>
            <Link to="/organization">{copy.reviewMessageRoster}</Link>
          </div>}
          <details className="eid-message-directory-details"><summary>{messages.directoryDetails}</summary><p>{executiveEntry ? copy.executiveMessageNote : copy.messagesNote}</p><p>{messages.directoryNote}</p></details>
        </section>
        <section aria-label={messages.conversation} className="eid-message-conversation">
          {agent ? (
            <OrganizationOwnerChat
              adapter={adapter}
              agent={agent}
              key={`${scope}:${agent.identityId ?? agent.id}`}
              layout="workspace"
              snapshot={snapshot}
            />
          ) : agents.length ? (
            <div className="eid-message-empty">
              <h2>{copy.chooseAgent}</h2>
              <p>{messages.selectNote}</p>
            </div>
          ) : null}
        </section>
        {agent && <section aria-label={messages.memberContext} className="eid-message-context">
          <header className="eid-message-profile">
            <OrganizationScenery className="eid-message-scenery" />
            <AgentAvatar name={agent.name} />
            <h2 dir="auto">{agent.name}</h2>
            <p>{roles[agent.role] ?? agent.role}{agent.team && <> · <bdi>{agent.team}</bdi></>}</p>
          </header>
          <p className="eid-result-text" dir="auto">{agent.purpose || agent.summary}</p>
          {!!agent.responsibilities.length && <section aria-label={messages.responsibilities}><h3>{messages.responsibilities}</h3><ul>{agent.responsibilities.map((item, index) => <li dir="auto" key={index}>{item}</li>)}</ul></section>}
          <section aria-label={messages.linkedWork}>
            <h3>{messages.linkedWork}</h3>
            <p className="eid-note">{messages.linkedWorkNote}</p>
            {objectives.length ? <ul>{objectives.map(objective => <li key={objective.id}><Link data-workspace-origin={JSON.stringify(['member-work', scope, agent.id, agent.identityId ?? '', objective.id])} dir="auto" to={objectiveHref(objective.id, `${location.pathname}${location.search}`, scope)}>{objective.title}</Link></li>)}</ul> : <p>{messages.noLinkedWork}</p>}
          </section>
          <AgentContextDisclosure agent={agent} snapshot={snapshot} summary={messages.retainedContext} />
        </section>}
      </ResponsiveMasterDetail>
    </div>
  )
}
