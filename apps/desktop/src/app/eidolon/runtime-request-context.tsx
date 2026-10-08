import { Fragment } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'

import type { OrganizationManagementProposal, OrganizationRequest, OrganizationSnapshot } from './types'

export const responseDecisions = {
  answer_request: 'answered',
  approve_request: 'approved',
  deny_request: 'denied'
} as const

export function hasCompleteManagementProposal(
  proposal?: OrganizationManagementProposal | null
): proposal is OrganizationManagementProposal {
  return Boolean(
    proposal &&
    Array.isArray(proposal.members) &&
    Array.isArray(proposal.transfers) &&
    (proposal.members.length || proposal.transfers.length) &&
    proposal.members.every(
      member =>
        member &&
        ['id', 'name', 'role', 'manager_id', 'team', 'purpose'].every(
          key => typeof member[key as keyof typeof member] === 'string'
        ) &&
        typeof member.enabled === 'boolean' &&
        ['capabilities', 'tool_grants', 'responsibilities'].every(key =>
          Array.isArray(member[key as keyof typeof member])
        ) &&
        (member.provider === null || typeof member.provider === 'string') &&
        (member.model === null || typeof member.model === 'string')
    ) &&
    proposal.transfers.every(
      transfer =>
        transfer &&
        typeof transfer.fromAgentId === 'string' &&
        typeof transfer.toAgentId === 'string' &&
        Array.isArray(transfer.taskIds) &&
        (transfer.objectiveIds === undefined ||
          (Array.isArray(transfer.objectiveIds) &&
            transfer.objectiveIds.every(id => typeof id === 'string' && Boolean(id.trim())) &&
            new Set(transfer.objectiveIds).size === transfer.objectiveIds.length)) &&
        typeof transfer.includeMemory === 'boolean'
    )
  )
}

export function RuntimeRequestContext({
  request,
  snapshot,
  onEvidence
}: {
  request: OrganizationRequest
  snapshot: OrganizationSnapshot
  onEvidence(id: string): void
}) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const person = (id?: string) => snapshot.agents.find(agent => agent.id === id)?.name || id || copy.notRecorded

  return (
    <>
      <dl className="eid-runtime-facts">
        <dt>{copy.requester}</dt>
        <dd>{person(request.requesterId)}</dd>
        <dt>{copy.requestedOutcome}</dt>
        <dd className="eid-result-text">{request.requestedOutcome || request.reason || copy.notRecorded}</dd>
        <dt>{copy.requiredAuthority}</dt>
        <dd>{request.requiredAuthority || copy.notRecorded}</dd>
        <dt>{copy.parentRequest}</dt>
        <dd>{request.parentRequestId || copy.notRecorded}</dd>
        <dt>{copy.dependencies}</dt>
        <dd>{request.dependencyIds?.join(', ') || copy.notRecorded}</dd>
      </dl>
      {request.evidenceIds?.length ? (
        <section aria-label={copy.evidence}>
          <h3>{copy.evidence}</h3>
          {request.evidenceIds.map(id => (
            <Button key={id} onClick={() => onEvidence(id)} size="sm" variant="secondary">
              {snapshot.knowledge.find(item => item.id === id)?.title || id}
            </Button>
          ))}
        </section>
      ) : null}
      {request.type === 'request.permission' && <p className="eid-note">{copy.permissionNote}</p>}
      {request.managementProposal && <ManagementProposal proposal={request.managementProposal} />}
      {request.response && (
        <section aria-label={copy.responseRecorded}>
          <h3>{copy.responseRecorded}</h3>
          <p className="eid-result-text">{request.response.text}</p>
          <p>
            {copy.responder}: {person(request.response.responderId)}
          </p>
          {request.response.decision && <p>{copy[request.response.decision]}</p>}
          <time dateTime={request.response.createdAt}>{new Date(request.response.createdAt).toLocaleString()}</time>
        </section>
      )}
    </>
  )
}

export function ManagementProposal({ proposal }: { proposal: OrganizationManagementProposal }) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const roster = t.organizationRoster
  const runtime = t.organizationRuntime

  if (!hasCompleteManagementProposal(proposal)) {
    return (
      <section aria-label={copy.managementProposal}>
        <h3>{copy.managementProposal}</h3>
        <p>{copy.proposalMissing}</p>
        <pre className="eid-result-text">{JSON.stringify(proposal, null, 2).slice(0, 24000)}</pre>
      </section>
    )
  }

  const labels: Record<string, string> = {
    id: roster.identityId,
    name: roster.name,
    role: roster.role,
    manager_id: roster.reportsTo,
    scope: roster.scope,
    team: copy.team,
    purpose: roster.purpose,
    responsibilities: roster.responsibilities,
    capabilities: roster.acceptedTypes,
    enabled: roster.enabled,
    provider: runtime.provider,
    model: runtime.model,
    tool_grants: runtime.tools,
    authority: roster.authority,
    managed_teams: roster.managedTeams
  }

  return (
    <section aria-label={copy.managementProposal}>
      <h3>{copy.managementProposal}</h3>
      <p className="eid-note">{copy.proposalNote}</p>
      {proposal.members?.map(member => (
        <section aria-label={member.name} key={member.id}>
          <h4>{member.name}</h4>
          <dl className="eid-runtime-facts">
            {Object.entries({
              ...member,
              authority: member.authority ?? [],
              managed_teams: member.managed_teams ?? [],
              scope: member.scope ?? ''
            }).map(([key, value]) => (
              <Fragment key={key}>
                <dt>{labels[key] || key}</dt>
                <dd className="eid-result-text">
                  {Array.isArray(value) ? value.join(', ') || '∅' : value === null ? '∅' : String(value)}
                </dd>
              </Fragment>
            ))}
          </dl>
        </section>
      ))}
      {proposal.transfers?.length > 0 && (
        <section aria-label={copy.transfers}>
          <h4>{copy.transfers}</h4>
          <ul>
            {proposal.transfers.map((transfer, index) => (
              <li key={index}>
                <p>
                  {transfer.fromAgentId} → {transfer.toAgentId}
                </p>
                <p>
                  {roster.taskId}: {transfer.taskIds.join(', ') || '∅'}
                </p>
                {transfer.objectiveIds !== undefined && (
                  <p>
                    {roster.objectiveLeadershipIds}: {transfer.objectiveIds.join(', ') || '∅'}
                  </p>
                )}
                <p>
                  {copy.includeMemory}: {String(transfer.includeMemory)}
                </p>
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  )
}
