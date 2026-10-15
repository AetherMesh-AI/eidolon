import { useId } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { controlVariants } from '@/components/ui/control'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n/context'
import { requestIntakeCopy } from '@/i18n/organization-request-intake'

import type { IntakePriority } from './organization-request-intake-types'
import type { OrganizationSnapshot } from './types'
import { useOrganizationRequestIntake } from './use-organization-request-intake'

interface OrganizationRequestIntakeProps {
  snapshot: OrganizationSnapshot
}

export function OrganizationRequestIntake({ snapshot }: OrganizationRequestIntakeProps) {
  const { locale, t } = useI18n()
  const copy = requestIntakeCopy[locale]
  const { client, view, eligible } = useOrganizationRequestIntake()
  const id = useId()
  const { record, intent, busy, error } = view
  const proposal = record?.proposal
  const disabled = !eligible || busy || Boolean(intent)
  const agentName = (agentId: string | null) => snapshot.agents.find(agent => agent.id === agentId)?.name ?? agentId

  return (
    <section aria-label={copy.title} className="grid gap-3">
      <h3>{copy.title}</h3>
      <p className="eid-note">{copy.note}</p>
      <form
        aria-busy={busy}
        className="grid gap-3"
        onSubmit={event => {
          event.preventDefault()
          void client.submit()
        }}
      >
        <label htmlFor={`${id}-request`}>{copy.request}</label>
        <Textarea
          dir="auto"
          disabled={disabled || Boolean(record)}
          id={`${id}-request`}
          maxLength={12000}
          onChange={event => client.edit({ text: event.target.value })}
          rows={3}
          value={record?.request ?? view.text}
        />
        <label htmlFor={`${id}-priority`}>{copy.priority}</label>
        <select
          className={controlVariants()}
          disabled={disabled || Boolean(record)}
          id={`${id}-priority`}
          onChange={event =>
            client.edit({ ownerPriority: (event.target.value || undefined) as IntakePriority | undefined })
          }
          value={view.ownerPriority ?? ''}
        >
          <option value="">{copy.infer}</option>
          {(['P5', 'P4', 'P3', 'P2', 'P1'] as const).map(priority => (
            <option key={priority} value={priority}>
              {priority} · {t.organizationFoundation.priorities[priority]}
            </option>
          ))}
        </select>
        {!record && !intent && (
          <div>
            <Button disabled={disabled || !view.text.trim()} type="submit">
              {copy.submit}
            </Button>
          </div>
        )}
      </form>
      {!eligible && <p role="status">{copy.unavailable}</p>}
      {eligible && (
        <details className="eid-form-disclosure" open={view.history.items.length > 0 && !record && !intent}>
          <summary>{copy.recent}</summary>
          {view.history.busy && <Loader />}
          {view.history.error && <p role="alert">{copy.historyFailed}</p>}
          <ul className="grid gap-2">
            {view.history.items.map(item => (
              <li key={item.id}>
                <Button
                  disabled={disabled || Boolean(view.answer.trim())}
                  onClick={() => {
                    if (client.select(item.id)) {
                      void client.read()
                    }
                  }}
                  variant="text"
                >
                  <span className="max-w-prose truncate" dir="auto">
                    {item.request}
                  </span>{' '}
                  · {copy[item.status]}
                </Button>
              </li>
            ))}
          </ul>
          <Button disabled={view.history.busy} onClick={() => void client.list()} variant="text">
            {copy.refreshRecent}
          </Button>
        </details>
      )}
      {busy && <Loader />}
      {error && (
        <div role="alert">
          <ErrorState title={copy[error]} />
        </div>
      )}
      {intent && (
        <div>
          <Button disabled={busy || !eligible} onClick={() => void client.retry()} variant="secondary">
            {copy.retry}
          </Button>
        </div>
      )}
      {record && (
        <div className="grid gap-3">
          <p aria-live="polite" role="status">
            {copy[record.status]}
          </p>
          <p className="eid-note">
            {copy.retained}: <bdi>{record.id}</bdi>
          </p>
          {record.reason && <p dir="auto">{record.reason}</p>}
          {record.status === 'admitted' && record.objectiveId && (
            <Link to={`/objectives/${encodeURIComponent(record.objectiveId)}`}>
              {t.organizationFoundation.openObjective}
            </Link>
          )}
          {proposal && (
            <div className="grid gap-2">
              <p>
                {t.organizationFoundation.priority}: {proposal.effectivePriority} ·{' '}
                {t.organizationFoundation.priorities[proposal.effectivePriority]}
              </p>
              {proposal.ownerOverride && (
                <p>
                  {copy.override} · {copy.inferred}: {proposal.inferredPriority}
                </p>
              )}
              <div>
                <h4>{copy.rationale}</h4>
                <p dir="auto">{proposal.rationale}</p>
              </div>
              {proposal.routeId && (
                <div>
                  <h4>{copy.route}</h4>
                  <p>
                    <bdi>{agentName(proposal.executiveId)}</bdi> · <bdi>{agentName(proposal.managerId)}</bdi>
                  </p>
                  <p className="eid-note">
                    <bdi>{proposal.routeId}</bdi>
                  </p>
                </div>
              )}
              <p>
                {t.organizationWork.deliveryMode}:{' '}
                {t.organizationWork[proposal.deliveryMode === 'source_project' ? 'sourceProject' : 'managedArtifact']}
              </p>
              {proposal.projectIds.length > 0 && (
                <p>
                  {t.organizationWork.objectiveProjects}: <bdi>{proposal.projectIds.join(', ')}</bdi>
                </p>
              )}
              {(
                [
                  [t.organizationWork.acceptanceCriteria, proposal.acceptanceCriteria],
                  [copy.assumptions, proposal.assumptions],
                  [copy.duplicates, proposal.duplicateCandidateIds],
                  [copy.dependencies, proposal.dependencyCandidateIds]
                ] as const
              ).map(
                ([label, values]) =>
                  values.length > 0 && (
                    <div key={label}>
                      <h4>{label}</h4>
                      <ul className="list-inside list-disc">
                        {values.map(value => (
                          <li dir="auto" key={value}>
                            {value}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )
              )}
            </div>
          )}
          {record.status === 'needs_answer' && proposal?.question && (
            <form
              className="grid gap-2"
              onSubmit={event => {
                event.preventDefault()
                void client.answer()
              }}
            >
              <h4>{copy.question}</h4>
              <p dir="auto">{proposal.question.text}</p>
              <p className="eid-note" dir="auto">
                {proposal.question.neededFor}
              </p>
              <label htmlFor={`${id}-answer`}>{copy.answer}</label>
              <Textarea
                dir="auto"
                disabled={disabled}
                id={`${id}-answer`}
                maxLength={12000}
                onChange={event => client.edit({ answer: event.target.value })}
                rows={2}
                value={view.answer}
              />
              <div>
                <Button disabled={disabled || !view.answer.trim()} type="submit">
                  {copy.sendAnswer}
                </Button>
              </div>
            </form>
          )}
          {['needs_review', 'uncertain', 'failed'].includes(record.status) && <p>{copy.reviewNote}</p>}
          <details className="eid-form-disclosure">
            <summary>{copy.usage}</summary>
            <p>
              <bdi>{record.usage.provider ?? t.organizationFoundation.notRecorded}</bdi> ·{' '}
              <bdi>{record.usage.model ?? t.organizationFoundation.notRecorded}</bdi>
            </p>
            <p>
              {copy.reserved}: {record.usage.reservedTokens}
            </p>
            <p>
              {copy.input}: {record.usage.inputTokens ?? t.organizationFoundation.notRecorded}
            </p>
            <p>
              {copy.output}: {record.usage.outputTokens ?? t.organizationFoundation.notRecorded}
            </p>
            <p>
              {copy.cost}: {record.usage.reservedCostUsd ?? t.organizationFoundation.notRecorded}
            </p>
          </details>
          <div className="flex flex-wrap gap-2">
            <Button disabled={disabled} onClick={() => void client.read()} variant="secondary">
              {copy.read}
            </Button>
            {
              <Button
                disabled={disabled || Boolean(view.answer.trim())}
                onClick={() => client.newRequest()}
                variant="text"
              >
                {copy.next}
              </Button>
            }
          </div>
        </div>
      )}
      <p className="eid-note">{copy.manual}</p>
    </section>
  )
}
