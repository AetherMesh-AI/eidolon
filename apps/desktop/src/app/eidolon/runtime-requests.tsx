import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { controlVariants } from '@/components/ui/control'
import { EmptyState } from '@/components/ui/empty-state'
import { SearchField } from '@/components/ui/search-field'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n/context'

import { Inspector } from './inspector'
import { RuntimeArtifact } from './runtime-artifact'
import { RuntimeAttentionInbox } from './runtime-attention'
import { RuntimeExecutionAudit } from './runtime-execution-audit'
import { hasCompleteManagementProposal, responseDecisions, RuntimeRequestContext } from './runtime-request-context'
import {
  amendmentCriteria,
  ScopeAmendmentAudit,
  type ScopeAmendmentDraft,
  ScopeAmendmentFields
} from './runtime-scope-amendment'
import { RuntimeRequestToolReceipts, ToolReceiptSummary } from './runtime-tool-receipts'
import type {
  Objective,
  OrganizationRequest,
  OrganizationResolutionAction,
  OrganizationSnapshot,
  RuntimeOrganizationAdapter
} from './types'
import { useRequestNavigation } from './use-request-navigation'

export interface RequestFilters {
  status: string
  team: string
  priority: string
  type: string
  query: string
}

export function filterOrganizationRequests(
  requests: OrganizationRequest[],
  filters: RequestFilters,
  snapshot: OrganizationSnapshot
) {
  return requests
    .filter(
      request =>
        (filters.status === 'all' || request.status === filters.status) &&
        (filters.team === 'all' || request.team === filters.team) &&
        (filters.priority === 'all' || String(request.priority) === filters.priority) &&
        (filters.type === 'all' || request.type === filters.type) &&
        `${request.type} ${request.team} ${request.reason ?? ''} ${request.requestedOutcome ?? ''} ${request.requesterId ?? ''} ${snapshot.objectives.find(item => item.id === request.objectiveId)?.title ?? ''}`
          .toLowerCase()
          .includes(filters.query.toLowerCase())
    )
    .sort((a, b) => b.priority - a.priority || a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id))
}

export function OwnerResolutionHistory({
  snapshot,
  objectiveId
}: {
  snapshot: OrganizationSnapshot
  objectiveId?: string
}) {
  const { t } = useI18n()
  const copy = t.organizationWork

  const entries = snapshot.objectives
    .filter(item => !objectiveId || item.id === objectiveId)
    .flatMap(objective => (objective.ownerResolutions ?? []).map(entry => ({ ...entry, objective })))
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))

  return (
    <details className="eid-owner-history">
      <summary>
        {copy.history} ({entries.length})
      </summary>
      {entries.length ? (
        <ol className="eid-list">
          {entries.map(entry => (
            <li key={entry.id}>
              <strong>{copy[entry.action]}</strong>
              <p className="eid-result-text">{entry.text}</p>
              <time dateTime={entry.createdAt}>{new Date(entry.createdAt).toLocaleString()}</time>
              <p>
                {copy.requests}: {entry.requestId}
              </p>
              {entry.evidenceIds.length > 0 && (
                <p>
                  {copy.evidence}: {entry.evidenceIds.join(', ')}
                </p>
              )}
              {entry.scopeAmendment && <ScopeAmendmentAudit amendment={entry.scopeAmendment} />}
              {!objectiveId && <Link to={`/objectives/${entry.objective.id}`}>{entry.objective.title}</Link>}
            </li>
          ))}
        </ol>
      ) : (
        <p>{copy.noHistory}</p>
      )}
    </details>
  )
}

function ResolutionForm({
  adapter,
  request,
  snapshot,
  onClose
}: {
  adapter: RuntimeOrganizationAdapter
  request: OrganizationRequest
  snapshot: OrganizationSnapshot
  onClose(): void
}) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const resolutions = request.allowedResolutions ?? []
  const [action, setAction] = useState<OrganizationResolutionAction | ''>(resolutions[0]?.action ?? '')
  const [text, setText] = useState('')
  const [amendment, setAmendment] = useState<ScopeAmendmentDraft>({ criteria: '' })
  const [artifactId, setArtifactId] = useState<string | null>(null)
  const [evidenceIds, setEvidenceIds] = useState<string[]>([])
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const sending = useRef(false)
  const active = useRef(true)
  const resolution = resolutions.find(item => item.action === action)
  const evidence = resolution?.evidenceIds ?? []
  const permittedEvidence = [...new Set(evidence)]
  const selectedEvidence = evidenceIds.filter(id => permittedEvidence.includes(id))
  const unavailable = snapshot.connection?.state !== 'ready' || request.status !== 'pending_intervention'
  const criteria = amendmentCriteria(amendment, text)
  const amendmentInvalid = action === 'amend_scope' && criteria.invalid

  const currentChecks =
    snapshot.objectives.find(objective => objective.id === request.objectiveId)?.requiredChecks ?? []

  const approvalBlocked =
    request.type === 'request.hire' &&
    action === 'approve_request' &&
    !hasCompleteManagementProposal(request.managementProposal)

  // eslint-disable-next-line no-restricted-syntax -- fence a dismissed response, not shared state
  useEffect(() => {
    active.current = true

    return () => {
      active.current = false
    }
  }, [])

  const submit = () => {
    if (
      !resolution ||
      sending.current ||
      unavailable ||
      approvalBlocked ||
      amendmentInvalid ||
      (resolution.requiresText && !text.trim()) ||
      (resolution.requiresEvidence && !selectedEvidence.length)
    ) {
      return
    }

    sending.current = true
    setPending(true)
    setError('')
    const decision = responseDecisions[resolution.action as keyof typeof responseDecisions]

    const response = decision
      ? adapter.respondRequest({ id: request.id, text, decision })
      : adapter.resolveRequest({
          id: request.id,
          action: resolution.action,
          text,
          evidenceIds: selectedEvidence,
          ...(action === 'amend_scope'
            ? { requiredChecks: amendment.requiredChecks, acceptanceCriteria: criteria.criteria }
            : {})
        })

    void response
      .then(() => {
        if (active.current) {
          onClose()
        }
      })
      .catch(reason => {
        if (active.current) {
          setError(reason instanceof Error ? reason.message : copy.responseError)
        }
      })
      .finally(() => {
        sending.current = false

        if (active.current) {
          setPending(false)
        }
      })
  }

  return (
    <>
      <form
        aria-busy={pending}
        aria-label={copy.resolve}
        className="eid-resolution-form"
        onSubmit={event => {
          event.preventDefault()
          submit()
        }}
      >
        <p className="eid-note">{copy.queueNote}</p>
        {approvalBlocked && <p role="alert">{copy.proposalMissing}</p>}
        {resolutions.length ? (
          <>
            <label>
              {copy.resolutionAction}
              <select
                className={controlVariants()}
                disabled={pending || unavailable}
                onChange={event => {
                  setAction(event.target.value as OrganizationResolutionAction)
                  setEvidenceIds([])
                  setError('')
                }}
                value={action}
              >
                {resolutions.map(item => (
                  <option key={item.action} value={item.action}>
                    {copy[item.action]}
                  </option>
                ))}
              </select>
            </label>
            {action === 'amend_scope' && (
              <ScopeAmendmentFields
                currentChecks={currentChecks}
                disabled={pending || unavailable}
                draft={amendment}
                onChange={setAmendment}
              />
            )}
            {amendmentInvalid && <p role="alert">{copy.criteriaInvalid}</p>}
            <label>
              {copy.response}
              <Textarea
                disabled={pending || unavailable}
                maxLength={12000}
                onChange={event => {
                  setText(event.target.value)
                  setError('')
                }}
                required={resolution?.requiresText}
                rows={4}
                value={text}
              />
            </label>
            {action === 'record_handoff' && resolution?.sourceManifest?.length ? (
              <section aria-label={copy.expectedSourceFiles}>
                <h4>{copy.expectedSourceFiles}</h4>
                <p className="eid-note">{copy.sourceHandoffNote}</p>
                <ul>
                  {resolution.sourceManifest.map(file => (
                    <li className="eid-result-text" key={file.path}>
                      <p>
                        {file.path} · {file.revision} · {file.sha256}
                      </p>
                      <Button
                        onClick={() => setArtifactId(file.evidenceId)}
                        size="sm"
                        type="button"
                        variant="secondary"
                      >
                        {copy.evidence}: {file.path}
                      </Button>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
            {(resolution?.requiresEvidence || permittedEvidence.length > 0) && (
              <fieldset disabled={pending || unavailable}>
                <legend>{copy.evidence}</legend>
                {permittedEvidence.map(id => (
                  <label className="eid-inline" key={id}>
                    <input
                      checked={selectedEvidence.includes(id)}
                      onChange={event =>
                        setEvidenceIds(
                          event.target.checked
                            ? [...selectedEvidence, id]
                            : selectedEvidence.filter(item => item !== id)
                        )
                      }
                      type="checkbox"
                    />
                    <span>{snapshot.knowledge.find(item => item.id === id)?.title ?? id}</span>
                  </label>
                ))}
              </fieldset>
            )}
            <div className="eid-inline">
              <Button
                disabled={
                  pending ||
                  unavailable ||
                  approvalBlocked ||
                  amendmentInvalid ||
                  !resolution ||
                  (resolution.requiresText && !text.trim()) ||
                  (resolution.requiresEvidence && !selectedEvidence.length)
                }
                type="submit"
              >
                {pending ? copy.submitting : copy.submit}
              </Button>
              <Button onClick={onClose} type="button" variant="text">
                {copy.closeForm}
              </Button>
            </div>
          </>
        ) : (
          <>
            <p>{copy.noActions}</p>
            <Button disabled={unavailable} onClick={() => void adapter.refresh()} type="button" variant="secondary">
              {copy.refresh}
            </Button>
          </>
        )}
        {error && <p role="alert">{error}</p>}
      </form>
      {artifactId && (
        <RuntimeArtifact
          adapter={adapter}
          evidenceId={artifactId}
          key={artifactId}
          onClose={() => setArtifactId(null)}
          snapshot={snapshot}
          title={copy.evidence}
        />
      )}
    </>
  )
}

export function RuntimeRequests({
  adapter,
  snapshot,
  objective
}: {
  adapter: RuntimeOrganizationAdapter
  snapshot: OrganizationSnapshot
  objective?: Objective
}) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const runtimeCopy = t.organizationRuntime
  const allRequests = (snapshot.requests ?? []).filter(request => !objective || request.objectiveId === objective.id)

  const [filters, setFilters] = useState<RequestFilters>({
    status: objective ? 'all' : 'pending_intervention',
    team: 'all',
    priority: 'all',
    type: 'all',
    query: ''
  })

  const [groupBy, setGroupBy] = useState<'team' | 'priority' | 'type' | 'status'>('team')
  const [view, setView] = useState<'inbox' | 'queue'>('inbox')

  const navigation = useRequestNavigation(
    allRequests,
    JSON.stringify([snapshot.connection?.ownerScope ?? snapshot.connection?.scope, objective?.id])
  )

  const [artifactId, setArtifactId] = useState<string | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState('')
  const active = useRef(true)
  const busy = useRef(false)
  const terminal = objective?.status === 'completed' || objective?.status === 'cancelled'
  const disconnected = snapshot.connection && snapshot.connection.state !== 'ready'
  const requests = filterOrganizationRequests(allRequests, filters, snapshot)

  const narrowed =
    filters.team !== 'all' || filters.priority !== 'all' || filters.type !== 'all' || Boolean(filters.query)

  const authoritative = snapshot.connection?.state === 'ready'
  const groups = new Map<string, OrganizationRequest[]>()
  const priorities = [copy.lowest, copy.low, copy.normal, copy.high, copy.highest]
  const supportsInbox = !objective && !!snapshot.attention && !!adapter.getAttention
  const inbox = supportsInbox && view === 'inbox'
  const priorityLabel = (value: number) => priorities[value - 1] ?? String(value)

  const groupLabel = (item: OrganizationRequest) =>
    groupBy === 'priority' ? priorityLabel(item.priority) : groupBy === 'status' ? copy[item.status] : item[groupBy]

  requests.forEach(item => {
    const key = groupLabel(item)
    groups.set(key, [...(groups.get(key) ?? []), item])
  })

  // eslint-disable-next-line no-restricted-syntax -- ignore late mutation UI after route/scope replacement
  useEffect(() => {
    active.current = true

    return () => {
      active.current = false
    }
  }, [])

  const run = (id: string, action: () => Promise<void>) => {
    if (busy.current) {
      return
    }

    busy.current = true
    setPending(id)
    setError('')
    void action()
      .catch(reason => {
        if (active.current) {
          setError(reason instanceof Error ? reason.message : copy.responseError)
        }
      })
      .finally(() => {
        busy.current = false

        if (active.current) {
          setPending(null)
        }
      })
  }

  return (
    <section aria-label="Runtime requests">
      <div className="eid-toolbar">
        <h2>{copy.requests}</h2>
        {objective && !terminal && (
          <Button
            disabled={!!pending || Boolean(disconnected)}
            onClick={() => run('cancel', () => adapter.cancelObjective(objective.id))}
            size="sm"
            variant="secondary"
          >
            {pending === 'cancel' ? 'Cancelling…' : 'Cancel objective'}
          </Button>
        )}
      </div>
      {supportsInbox && <SegmentedControl onChange={setView} options={[
        { id: 'inbox', label: copy.attentionInbox }, { id: 'queue', label: copy.attentionQueue }
      ]} value={view} />}
      {inbox && <RuntimeAttentionInbox adapter={adapter} onInspect={navigation.open} snapshot={snapshot} />}
      {!inbox && <>
      {!objective && <p className="eid-note">{copy.queueNote}</p>}
      <div className="eid-toolbar">
        <label>
          {copy.status}
          <select
            className={controlVariants()}
            onChange={event => setFilters({ ...filters, status: event.target.value })}
            value={filters.status}
          >
            <option value="pending_intervention">{copy.needsYou}</option>
            <option value="all">{copy.allRequests}</option>
            {(['queued', 'running', 'waiting_response', 'completed', 'cancelled'] as const).map(value => (
              <option key={value} value={value}>
                {copy[value]}
              </option>
            ))}
          </select>
        </label>
        {(['team', 'priority', 'type'] as const).map(field => (
          <label key={field}>
            {copy[field]}
            <select
              className={controlVariants()}
              onChange={event => setFilters({ ...filters, [field]: event.target.value })}
              value={filters[field]}
            >
              <option value="all">{copy.all}</option>
              {[...new Set(allRequests.map(item => String(item[field])))].sort().map(value => (
                <option key={value} value={value}>
                  {field === 'priority' ? priorityLabel(Number(value)) : value}
                </option>
              ))}
            </select>
          </label>
        ))}
        <label>
          {copy.groupBy}
          <select
            className={controlVariants()}
            onChange={event => setGroupBy(event.target.value as typeof groupBy)}
            value={groupBy}
          >
            {(['team', 'priority', 'type', 'status'] as const).map(value => (
              <option key={value} value={value}>
                {copy[value]}
              </option>
            ))}
          </select>
        </label>
        {allRequests.length > 0 && (
          <SearchField
            aria-label={copy.search}
            onChange={query => setFilters({ ...filters, query })}
            placeholder={copy.search}
            value={filters.query}
          />
        )}
      </div>
      {error && <p role="alert">{error}</p>}
      {!requests.length && (
        <EmptyState
          description={copy.queueEmptyNote}
          title={!authoritative ? copy.requestsUnverified : narrowed ? copy.noMatchingRequests : filters.status === 'pending_intervention' ? copy.noNeeds : copy.noRequests}
        />
      )}
      {[...groups].map(([group, items]) => (
        <section aria-label={group} key={group}>
          <h3>{group}</h3>
          <ol className="eid-list">
            {items.map(item => (
              <li key={item.id}>
                <button
                  aria-label={`${copy.inspectRequest}: ${item.type}`}
                  className="eid-row"
                  onClick={() => navigation.open(item.id)}
                >
                  <span>
                    <strong>{item.type}</strong>
                    {!objective && (
                      <small>
                        {snapshot.objectives.find(value => value.id === item.objectiveId)?.title ?? item.objectiveId}
                      </small>
                    )}
                    <small>
                      {copy.team}: {item.team} · {copy.priority}: {priorityLabel(item.priority)} · {item.attempts}
                    </small>
                    {item.requestedOutcome && <small>{item.requestedOutcome}</small>}
                    {item.reason && (
                      <small>
                        {runtimeCopy.routeReason}: <span>{item.reason}</span>
                      </small>
                    )}
                    <ToolReceiptSummary receipts={item.toolReceipts} />
                    {(item.toolReceiptsTruncated || (item.toolReceiptCount ?? 0) > 20) && (
                      <small>
                        {runtimeCopy.moreReceipts}: {item.toolReceiptCount ?? runtimeCopy.notReported}
                      </small>
                    )}
                    {item.requestedRoutes?.length ? (
                      <small>
                        {runtimeCopy.requestedRoutes}:{' '}
                        {item.requestedRoutes.map(route => `${route.type} → ${route.team}`).join('; ')}
                      </small>
                    ) : null}
                  </span>
                  <span className={`eid-status eid-request-${item.status}`}>{copy[item.status]}</span>
                </button>
                {item.status === 'pending_intervention' &&
                  !terminal &&
                  (item.allowedResolutions?.length ? (
                    <Button
                      disabled={!!pending || Boolean(disconnected)}
                      onClick={() => navigation.open(item.id)}
                      size="sm"
                      variant="secondary"
                    >
                      {copy.resolve}
                    </Button>
                  ) : item.allowedResolutions === undefined && item.type !== 'request.merge' ? (
                    <Button
                      disabled={!!pending || Boolean(disconnected)}
                      onClick={() => run(item.id, () => adapter.retryRequest(item.id))}
                      size="sm"
                      variant="secondary"
                    >
                      {pending === item.id ? 'Retrying…' : `Retry ${item.type}`}
                    </Button>
                  ) : null)}
              </li>
            ))}
          </ol>
        </section>
      ))}
      {allRequests.some(item => item.type === 'request.merge' && item.status === 'pending_intervention') && (
        <p className="eid-note">
          {runtimeCopy.edits.mergePending} {runtimeCopy.edits.mergeNote}
        </p>
      )}
      </>}
      {navigation.trail.map((request, index) => (
        <div hidden={index !== navigation.trail.length - 1} key={request.id}>
          <Inspector kind="request" onClose={navigation.close} title={request.type}>
            {index > 0 && (
              <Button onClick={navigation.back} size="sm" variant="secondary">
                {t.common.back}
              </Button>
            )}
            <dl>
              <dt>{copy.status}</dt>
              <dd>{copy[request.status]}</dd>
              <dt>{copy.team}</dt>
              <dd>{request.team}</dd>
              <dt>{copy.priority}</dt>
              <dd>{priorityLabel(request.priority)}</dd>
              <dt>Claimed by</dt>
              <dd>
                {request.agentId
                  ? (snapshot.agents.find(agent => agent.id === request.agentId)?.name ?? request.agentId)
                  : 'No worker claimed this request'}
              </dd>
              <dt>{runtimeCopy.routeReason}</dt>
              <dd>{request.reason || runtimeCopy.routeUnknown}</dd>
              {typeof request.requestedWorkers === 'number' && (
                <>
                  <dt>{runtimeCopy.requestedWorkers}</dt>
                  <dd>{request.requestedWorkers}</dd>
                </>
              )}
              {request.requestedRoutes?.length ? (
                <>
                  <dt>{runtimeCopy.requestedRoutes}</dt>
                  <dd>{request.requestedRoutes.map(route => `${route.type} → ${route.team}`).join('; ')}</dd>
                </>
              ) : null}
              <dt>Request ID</dt>
              <dd>{request.id}</dd>
              <dt>Task ID</dt>
              <dd>{request.taskId || runtimeCopy.notReported}</dd>
            </dl>
            {(request.taskId ||
              request.parentRequestId ||
              request.requesterId ||
              request.requestedOutcome ||
              request.response ||
              request.managementProposal) && (
              <RuntimeRequestContext
                onEvidence={setArtifactId}
                onParent={navigation.openParent}
                parent={index === navigation.trail.length - 1 ? navigation.parent : navigation.trail[index + 1]}
                request={request}
                snapshot={snapshot}
              />
            )}
            <Link to={`/objectives/${request.objectiveId}`}>{copy.openObjective}</Link>
            {request.type === 'request.merge' && <p className="eid-note">{runtimeCopy.edits.mergeNote}</p>}
            {request.status === 'pending_intervention' && !terminal && (
              <ResolutionForm
                adapter={adapter}
                key={`${snapshot.connection?.ownerScope ?? snapshot.connection?.scope}:${request.id}`}
                onClose={navigation.closeOnResponse}
                request={request}
                snapshot={snapshot}
              />
            )}
            <RuntimeRequestToolReceipts adapter={adapter} key={request.id} request={request} />
            {adapter.getExecutionAudit && (
              <RuntimeExecutionAudit
                adapter={adapter}
                key={`${snapshot.connection?.scope}:${request.id}`}
                request={request}
              />
            )}
          </Inspector>
        </div>
      ))}
      {artifactId && (
        <RuntimeArtifact
          adapter={adapter}
          evidenceId={artifactId}
          key={artifactId}
          onClose={() => setArtifactId(null)}
          snapshot={snapshot}
          title={copy.evidence}
        />
      )}
      <OwnerResolutionHistory objectiveId={objective?.id} snapshot={snapshot} />
    </section>
  )
}
