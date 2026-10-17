import { Link, useLocation } from 'react-router'

import { useI18n } from '@/i18n/context'

import { LocalizedTime } from './localized-time'
import type { OrganizationAgent, OrganizationSnapshot } from './types'
import { objectiveHref, useWorkspaceRouteState } from './workspace-navigation'

interface AgentContextProps {
  agent: OrganizationAgent
  snapshot: OrganizationSnapshot
}

/** Keep the history origin visible after Back without transferring it to another member. */
export function AgentContextDisclosure({ agent, snapshot, summary }: AgentContextProps & { summary: string }) {
  const ownerScope = snapshot.connection?.ownerScope ?? snapshot.connection?.scope
  const route = useWorkspaceRouteState('context', ownerScope)
  const identity = agent.identityId ?? ''
  const open = route.read('Member') === agent.id && route.read('Identity') === identity

  // Native toggle events are queued, so they must stay with the member that opened them.
  return <details key={JSON.stringify([ownerScope, agent.id, identity])} onToggle={event => {
    const nextOpen = event.currentTarget.open

    if (nextOpen !== open) {
      route.write({ Member: nextOpen ? agent.id : null, Identity: nextOpen ? identity : null })
    }
  }} open={open}><summary>{summary}</summary><AgentContext agent={agent} snapshot={snapshot} /></details>
}

/** A read-only view of backend-owned identity memory, never a second memory store. */
export function AgentContext({ agent, snapshot }: AgentContextProps) {
  const { t } = useI18n()
  const { pathname, search } = useLocation()
  const ownerScope = snapshot.connection?.ownerScope ?? snapshot.connection?.scope
  const copy = t.organizationRoster
  const runtimeCopy = t.organizationRuntime
  const context = agent.context

  return <>
    {agent.persistent && <>
      <h3>{copy.persistentIdentity}</h3>
      <dl>
        <dt>{copy.identityId}</dt><dd><bdi>{agent.identityId || agent.id}</bdi></dd>
        <dt>{copy.createdAt}</dt><dd>{agent.createdAt ? <LocalizedTime value={agent.createdAt} /> : runtimeCopy.notReported}</dd>
      </dl>
    </>}
    {agent.purpose && <><h3>{copy.purpose}</h3><p dir="auto">{agent.purpose}</p></>}
    <h3>{copy.contextSummary}</h3>
    <p dir={context?.contextSummary ? "auto" : undefined}>{context?.contextSummary || copy.contextUnavailable}</p>
    <section aria-label={copy.memory}>
      <h3>{copy.memory}</h3>
      <p className="eid-note">{copy.memoryNote}</p>
      {context?.memory ? <>
        <dl>
          {context.revision !== undefined && <><dt>{copy.revision}</dt><dd>{context.revision}</dd></>}
          {context.updatedAt && <><dt>{copy.updatedAt}</dt><dd><LocalizedTime value={context.updatedAt} /></dd></>}
        </dl>
        {(['facts', 'decisions', 'lessons', 'openQuestions'] as const).map(category => <section aria-label={copy[category]} key={category}>
          <h4>{copy[category]}</h4>
          {context.memory![category].length ? <ul>{context.memory![category].map((entry, index) => <li className="eid-result-text" dir="auto" key={`${category}-${index}`}>{entry}</li>)}</ul> : <p>{copy.noMemory}</p>}
        </section>)}
      </> : <p>{copy.contextUnavailable}</p>}
    </section>
    <section aria-label={copy.history}>
      <h3>{copy.history}</h3>
      <p className="eid-note">{copy.historyNote}</p>
      {context?.recentHistory ? context.recentHistory.length ? <ol className="eid-list">{context.recentHistory.map(entry => <li key={entry.requestId}>
        {snapshot.objectives.some(objective => objective.id === entry.objectiveId)
          ? <Link dir="auto" to={objectiveHref(entry.objectiveId, pathname + search, ownerScope)}>{entry.objectiveTitle || entry.objectiveId}</Link>
          : <strong dir="auto">{entry.objectiveTitle || entry.objectiveId}</strong>}
        <p className="eid-result-text" dir="auto">{entry.summary}</p>
        <details>
          <summary>{runtimeCopy.requestId}: {entry.requestId}</summary>
          <dl>
            <dt>{copy.requestType}</dt><dd>{entry.requestType}</dd>
            {entry.taskId && <><dt>{copy.taskId}</dt><dd>{entry.taskId}</dd></>}
            {!!entry.evidenceIds.length && <><dt>{copy.evidenceIds}</dt><dd>{entry.evidenceIds.join(', ')}</dd></>}
          </dl>
        </details>
        <LocalizedTime value={entry.createdAt} />
      </li>)}</ol> : <p>{copy.noHistory}</p> : <p>{copy.contextUnavailable}</p>}
    </section>
  </>
}
