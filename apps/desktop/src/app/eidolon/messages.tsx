import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'

import { AgentAvatar } from './avatar'
import { OrganizationOwnerChat } from './runtime-owner-chat'
import type { OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

/** Reuse the durable owner/member chat contract, including explicit opening and renewal. */
export function Messages({
  adapter,
  snapshot
}: {
  adapter?: RuntimeOrganizationAdapter
  snapshot: OrganizationSnapshot
}) {
  const { t } = useI18n()
  const copy = t.organizationHome
  const [selected, setSelected] = useState<string | null>(null)

  const agents = snapshot.agents.filter(
    agent => agent.persistent && agent.role !== 'Owner' && !agent.id.startsWith('control:')
  )

  const agent = agents.find(item => item.id === selected)

  return (
    <>
      <header className="eid-home-hero">
        <div>
          <h1>{copy.messages}</h1>
          <p>{copy.messagesNote}</p>
        </div>
      </header>
      <div className="eid-messages-layout">
        <nav aria-label={copy.chooseAgent}>
          {agents.map(item => (
            <Button
              aria-pressed={item.id === selected}
              key={item.id}
              onClick={() => setSelected(item.id)}
              variant="ghost"
            >
              <AgentAvatar name={item.name} />
              <span>
                {item.name}
                <small>{item.team}</small>
              </span>
            </Button>
          ))}
        </nav>
        <section aria-label={copy.messages}>
          {agent ? (
            <>
              <h2>{agent.name}</h2>
              <p>{agent.summary}</p>
              <OrganizationOwnerChat
                adapter={adapter}
                agent={agent}
                key={`${snapshot.connection?.ownerScope ?? snapshot.connection?.scope}:${agent.identityId ?? agent.id}`}
                snapshot={snapshot}
              />
            </>
          ) : (
            <p>{agents.length ? copy.chooseAgent : copy.noAgents}</p>
          )}
        </section>
      </div>
    </>
  )
}
