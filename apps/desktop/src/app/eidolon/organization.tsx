import { useEffect, useState } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'
import type { OrganizationRosterCopy } from '@/i18n/organization-roster'
import type { OrganizationWorkCopy } from '@/i18n/organization-work'

import { AgentContext } from './agent-context'
import { AgentAvatar } from './avatar'
import { Inspector } from './inspector'
import { OrganizationConversations } from './runtime-conversations'
import { OrganizationManagementForm } from './runtime-management-form'
import { OrganizationManagementHistory } from './runtime-management-history'
import { OrganizationProjectSetupForm } from './runtime-project-setup'
import { inheritsProfileModel } from './runtime-setup'
import { TaskCoordination, taskCoordinationLabel } from './task-coordination'
import type { OrganizationAgent, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

function currentAssignment(
  snapshot: OrganizationSnapshot,
  agent: OrganizationAgent,
  copy: OrganizationRosterCopy,
  workCopy: OrganizationWorkCopy
) {
  const tasks = snapshot.tasks.filter(
    task =>
      (task.ownerId === agent.id ||
        task.reviewerId === agent.id ||
        task.managingAgentId === agent.id ||
        task.assignedById === agent.id) &&
      task.status !== 'completed' &&
      task.status !== 'cancelled'
  )

  if (tasks.length) {
    return tasks
      .map(
        task =>
          `${task.reviewerId === agent.id && task.ownerId !== agent.id ? 'Review: ' : task.managingAgentId === agent.id ? `${copy.managementAssignment}: ` : task.assignedById === agent.id && task.ownerId !== agent.id ? `${copy.plannedAssignment}: ` : ''}${task.title} (${task.status}${snapshot.source === 'runtime' && task.coordination ? ` · ${taskCoordinationLabel(task, workCopy)}` : ''})`
      )
      .join('; ')
  }

  return snapshot.source === 'runtime'
    ? 'No current task assignment recorded'
    : snapshot.objectives.find(objective => objective.id === agent.objectiveId)?.title ||
        'Unknown · no current assignment recorded'
}

export function Organization({
  snapshot,
  adapter
}: {
  snapshot: OrganizationSnapshot
  adapter?: RuntimeOrganizationAdapter
}) {
  const { t } = useI18n()
  const copy = t.organizationRuntime
  const roster = t.organizationRoster

  const roles: Record<string, string> = {
    Owner: roster.owner,
    Executive: roster.executive,
    Manager: roster.manager,
    Worker: roster.worker,
    Director: roster.manager,
    Employee: roster.worker
  }

  const [selected, setSelected] = useState<{ id: string; scope: string | undefined } | null>(null)
  const [managing, setManaging] = useState(false)
  const [projectScope, setProjectScope] = useState<string | null>(null)

  useEffect(() => {
    setProjectScope(null)
  }, [snapshot.connection?.scope])
  const [mode, setMode] = useState('List')
  const [query, setQuery] = useState('')
  const runtime = snapshot.source === 'runtime'

  const agent =
    selected?.scope === snapshot.connection?.scope ? snapshot.agents.find(item => item.id === selected?.id) : undefined

  useEffect(() => {
    if (!agent) {
      setSelected(null)
    }
  }, [agent])

  const agents = snapshot.agents.filter(item =>
    `${item.name} ${item.role} ${roles[item.role] || ''} ${item.purpose || ''} ${item.team || ''} ${item.provider || ''} ${item.model || ''} ${item.lifecycle || ''} ${item.tools?.join(' ') || ''} ${item.requestTypes?.join(' ') || ''} ${item.responsibilities.join(' ')} ${item.capabilities.join(' ')}`
      .toLowerCase()
      .includes(query.toLowerCase())
  )

  const assignedTasks = agent
    ? snapshot.tasks.filter(
        item =>
          item.ownerId === agent.id ||
          item.reviewerId === agent.id ||
          item.managingAgentId === agent.id ||
          item.assignedById === agent.id
      )
    : []

  const managerName = (item: OrganizationAgent) =>
    item.managerId
      ? snapshot.agents.find(manager => manager.id === item.managerId)?.name || item.managerId
      : runtime
        ? 'No manager recorded'
        : 'You'

  const modelFallback = (item: OrganizationAgent) =>
    runtime && inheritsProfileModel(snapshot, item) ? roster.inheritedModel : copy.notReported

  const unavailable = runtime ? 'Not reported by runtime' : 'Unavailable · No runtime connection'

  return (
    <>
      <p>
        {runtime
          ? roster.introduction
          : 'People set direction. The Lead coordinates specialists. Organizational roles do not replace runtime profiles.'}
      </p>
      {runtime && (
        <>
          <details className="eid-roster-details">
            <summary>{roster.roster}</summary>
            <p className="eid-note">{roster.persistenceNote}</p>
            <p className="eid-note">{roster.collaborationNote}</p>
            <p className="eid-note">{copy.rosterNote}</p>
            <dl aria-label={roster.roster} className="eid-runtime-facts">
              <dt>{roster.rosterCount}</dt>
              <dd>{snapshot.runtime?.rosterCount ?? copy.notReported}</dd>
              <dt>{roster.workingCount}</dt>
              <dd>{snapshot.runtime?.workingCount ?? copy.notReported}</dd>
              <dt>{roster.maxInflight}</dt>
              <dd>{snapshot.runtime?.maxInflight ?? copy.notReported}</dd>
              <dt>{roster.executionState}</dt>
              <dd>{snapshot.runtime?.state ?? copy.notReported}</dd>
            </dl>
          </details>
        </>
      )}
      {adapter && snapshot.runtime?.management && (
        <Button
          disabled={snapshot.connection?.state !== 'ready'}
          onClick={() => {
            setSelected(null)
            setManaging(true)
          }}
          variant="secondary"
        >
          {roster.manage}
        </Button>
      )}
      {runtime && adapter?.getProjectSetup && adapter.prepareProjectDraft && (
        <Button
          disabled={snapshot.connection?.state !== 'ready'}
          onClick={() => {
            setSelected(null)
            setManaging(false)
            setProjectScope(snapshot.connection!.scope)
          }}
          variant="secondary"
        >
          {t.organizationWork.projectSetup}
        </Button>
      )}
      {adapter && projectScope !== null && projectScope === snapshot.connection?.scope && (
        <OrganizationProjectSetupForm
          adapter={adapter}
          key={projectScope}
          onClose={() => setProjectScope(null)}
          snapshot={snapshot}
        />
      )}
      <div className="eid-toolbar">
        <label className="eid-filter">
          Find an agent
          <input onChange={event => setQuery(event.target.value)} placeholder="Name or responsibility…" value={query} />
        </label>
        <div aria-label="Organization layout" className="eid-tabs">
          {['Grid', 'List'].map(item => (
            <button aria-pressed={mode === item} key={item} onClick={() => setMode(item)}>
              {item}
            </button>
          ))}
        </div>
      </div>
      <div aria-label={`${mode} of organization`} className={`eid-organization eid-organization-${mode.toLowerCase()}`}>
        {agents.map(item => (
          <button
            aria-label={`Inspect ${item.name}`}
            className={`eid-agent eid-agent-${item.status}`}
            key={item.id}
            onClick={() => setSelected({ id: item.id, scope: snapshot.connection?.scope })}
          >
            <AgentAvatar name={item.name} />
            <span className="eid-agent-identity">
              <strong>{item.name}</strong>
              <span className="eid-agent-meta">
                <span>{roles[item.role] || item.role}</span><small>Team: {item.team?.trim() || 'Unknown · team not recorded'}</small>
              </span>
              <small>
                {item.managerId ? `Reports to ${managerName(item)}` : runtime ? managerName(item) : 'Reports to you'}
              </small>
            </span>
            <span className="eid-agent-assignment">
              Current assignment: {currentAssignment(snapshot, item, roster, t.organizationWork)}
            </span>
            <span className="eid-agent-state">
              <span className={`eid-status eid-status-${item.status}`}>● {item.status}</span>
              {runtime && <small>{copy.lifecycle}: {item.lifecycle ? copy.lifecycleLabels[item.lifecycle] : copy.notReported}</small>}
            </span>
          </button>
        ))}
      </div>
      {!agents.length && (
        <div className="eid-empty">
          <h2>{query ? 'No matching agents' : 'No connected organization agents'}</h2>
          <p>
            {query
              ? 'Try another name, team, or responsibility.'
              : runtime
                ? 'The connected gateway has not reported any organization agents.'
                : 'Load the explicitly fictional example to explore the map, or inspect existing runtime configuration.'}
          </p>
        </div>
      )}
      <div className="eid-inline">
        <Link className="eid-button" to="/profiles">
          Manage runtime profiles
        </Link>
        <Link to="/agents">Inspect live agents →</Link>
      </div>
      {runtime && <OrganizationConversations key={`conversations:${snapshot.connection?.scope}`} snapshot={snapshot} />}
      {runtime && <OrganizationManagementHistory snapshot={snapshot} />}
      {managing && adapter && snapshot.runtime?.management && (
        <OrganizationManagementForm
          adapter={adapter}
          key={snapshot.connection?.ownerScope ?? snapshot.connection?.scope}
          management={snapshot.runtime.management}
          onClose={() => setManaging(false)}
          snapshot={snapshot}
        />
      )}
      {agent && (
        <Inspector
          key={`${snapshot.connection?.scope}:${agent.id}`}
          kind="agent"
          onClose={() => setSelected(null)}
          title={agent.name}
        >
          <p className="eid-eyebrow">{runtime ? 'Runtime agent · Gateway record' : 'Prototype agent · Not live'}</p>
          <AgentAvatar name={agent.name} />
          <p>{roles[agent.role] || agent.role}</p>
          <dl>
            {runtime && (
              <>
                <dt>{copy.lifecycle}</dt>
                <dd>{agent.lifecycle ? copy.lifecycleLabels[agent.lifecycle] : copy.notReported}</dd>
                <dt>{copy.provider}</dt>
                <dd>{agent.provider || modelFallback(agent)}</dd>
              </>
            )}
            <dt>Status</dt>
            <dd>● {agent.status}</dd>
            <dt>Team</dt>
            <dd>{agent.team?.trim() || 'Unknown · team not recorded'}</dd>
            <dt>Reports to</dt>
            <dd>{managerName(agent)}</dd>
            <dt>Direct reports</dt>
            <dd>
              {snapshot.agents
                .filter(item => item.managerId === agent.id)
                .map(item => item.name)
                .join(', ') || 'None'}
            </dd>
            <dt>Model</dt>
            <dd>{agent.model || (runtime ? modelFallback(agent) : unavailable)}</dd>
            <dt>Context usage / capacity</dt>
            <dd>
              {agent.context?.used !== undefined || agent.context?.capacity !== undefined
                ? `${agent.context.used ?? 'Unknown'} / ${agent.context.capacity ?? 'Unknown'} tokens`
                : unavailable}
            </dd>
            <dt>Tools</dt>
            <dd>{agent.tools?.join(', ') || (runtime && agent.tools ? copy.noTools : unavailable)}</dd>
            {runtime && (
              <>
                <dt>Request types</dt>
                <dd>{agent.requestTypes?.join(', ') || 'Not reported by runtime'}</dd>
              </>
            )}
          </dl>
          <p>{agent.summary}</p>
          {runtime && <AgentContext agent={agent} snapshot={snapshot} />}
          <h3>Responsibilities</h3>
          <ul>
            {agent.responsibilities.map(item => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <h3>Capabilities</h3>
          <ul>
            {agent.capabilities.map(item => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <h3>Current assignment</h3>
          {runtime ? (
            <p>{currentAssignment(snapshot, agent, roster, t.organizationWork)}</p>
          ) : agent.objectiveId ? (
            <Link to={`/objectives/${agent.objectiveId}`}>
              {snapshot.objectives.find(item => item.id === agent.objectiveId)?.title || 'Open objective'}
            </Link>
          ) : (
            <p>No assignment</p>
          )}
          {runtime && (
            <>
              <h3>Assigned work and review</h3>
              {assignedTasks.length ? (
                <ul>
                  {assignedTasks.map(item => (
                    <li key={item.id}>
                      <Link to={`/objectives/${item.objectiveId}`}>{item.title}</Link> ·{' '}
                      {item.reviewerId === agent.id
                        ? 'Review assignment'
                        : item.managingAgentId === agent.id
                          ? roster.managementAssignment
                          : item.assignedById === agent.id && item.ownerId !== agent.id
                            ? roster.plannedAssignment
                            : item.requestType || 'Request type not recorded'}{' '}
                      · {item.status}
                      {item.requestType === 'review' && (
                        <>
                          {' '}
                          · Reviews:{' '}
                          {item.dependsOn
                            .map(id => snapshot.tasks.find(dependency => dependency.id === id)?.title || id)
                            .join(', ') || 'No task dependency recorded'}
                        </>
                      )}
                      <TaskCoordination task={item} tasks={snapshot.tasks} />
                    </li>
                  ))}
                </ul>
              ) : (
                <p>No task assignments recorded.</p>
              )}
            </>
          )}
          <h3>{t.organizationWork.executionConfig}</h3>
          {runtime && <p>Agent ID: {agent.id}</p>}
          <p>{agent.profileName || 'No runtime profile linked'}</p>
          <Link to="/profiles">{t.organizationWork.executionConfig} →</Link>
          <p>
            <Link to="/activity">{t.organizationWork.executionHistory} →</Link>
          </p>
          {runtime && (
            <>
              <h3>Execution scope</h3>
              <p>{snapshot.runtime?.scope || 'Execution scope not reported by runtime.'}</p>
            </>
          )}
          {runtime && <OrganizationConversations agentId={agent.id} snapshot={snapshot} />}
          <h3>Recent activity</h3>
          {snapshot.activity
            .filter(item => item.agentId === agent.id)
            .map(item => (
              <p key={item.id}>{item.text}</p>
            ))}
        </Inspector>
      )}
    </>
  )
}
