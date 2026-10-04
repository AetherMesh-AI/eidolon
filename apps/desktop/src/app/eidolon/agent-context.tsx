import { Link } from 'react-router'

import { useI18n } from '@/i18n/context'

import type { OrganizationAgent, OrganizationSnapshot } from './types'

interface AgentContextProps {
  agent: OrganizationAgent
  snapshot: OrganizationSnapshot
}

/** A read-only view of backend-owned identity memory, never a second memory store. */
export function AgentContext({ agent, snapshot }: AgentContextProps) {
  const { t } = useI18n()
  const copy = t.organizationRoster
  const runtimeCopy = t.organizationRuntime
  const context = agent.context

  return <>
    {agent.persistent && <>
      <h3>{copy.persistentIdentity}</h3>
      <dl>
        <dt>{copy.identityId}</dt><dd>{agent.identityId || agent.id}</dd>
        <dt>{copy.createdAt}</dt><dd>{agent.createdAt ? <time dateTime={agent.createdAt}>{new Date(agent.createdAt).toLocaleString()}</time> : runtimeCopy.notReported}</dd>
      </dl>
    </>}
    {agent.purpose && <><h3>{copy.purpose}</h3><p>{agent.purpose}</p></>}
    <h3>{copy.contextSummary}</h3>
    <p>{context?.contextSummary || copy.contextUnavailable}</p>
    <section aria-label={copy.memory}>
      <h3>{copy.memory}</h3>
      <p className="eid-note">{copy.memoryNote}</p>
      {context?.memory ? <>
        <dl>
          {context.revision !== undefined && <><dt>{copy.revision}</dt><dd>{context.revision}</dd></>}
          {context.updatedAt && <><dt>{copy.updatedAt}</dt><dd><time dateTime={context.updatedAt}>{new Date(context.updatedAt).toLocaleString()}</time></dd></>}
        </dl>
        {(['facts', 'decisions', 'lessons', 'openQuestions'] as const).map(category => <section aria-label={copy[category]} key={category}>
          <h4>{copy[category]}</h4>
          {context.memory![category].length ? <ul>{context.memory![category].map((entry, index) => <li className="eid-result-text" key={`${category}-${index}`}>{entry}</li>)}</ul> : <p>{copy.noMemory}</p>}
        </section>)}
      </> : <p>{copy.contextUnavailable}</p>}
    </section>
    <section aria-label={copy.history}>
      <h3>{copy.history}</h3>
      <p className="eid-note">{copy.historyNote}</p>
      {context?.recentHistory ? context.recentHistory.length ? <ol className="eid-list">{context.recentHistory.map(entry => <li key={entry.requestId}>
        {snapshot.objectives.some(objective => objective.id === entry.objectiveId)
          ? <Link to={`/objectives/${entry.objectiveId}`}>{entry.objectiveTitle || entry.objectiveId}</Link>
          : <strong>{entry.objectiveTitle || entry.objectiveId}</strong>}
        <p className="eid-result-text">{entry.summary}</p>
        <details>
          <summary>{runtimeCopy.requestId}: {entry.requestId}</summary>
          <dl>
            <dt>{copy.requestType}</dt><dd>{entry.requestType}</dd>
            {entry.taskId && <><dt>{copy.taskId}</dt><dd>{entry.taskId}</dd></>}
            {!!entry.evidenceIds.length && <><dt>{copy.evidenceIds}</dt><dd>{entry.evidenceIds.join(', ')}</dd></>}
          </dl>
        </details>
        <time dateTime={entry.createdAt}>{new Date(entry.createdAt).toLocaleString()}</time>
      </li>)}</ol> : <p>{copy.noHistory}</p> : <p>{copy.contextUnavailable}</p>}
    </section>
  </>
}
