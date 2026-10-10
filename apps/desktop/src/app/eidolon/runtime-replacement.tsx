import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { Button } from '@/components/ui/button'
import { controlVariants } from '@/components/ui/control'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n/context'

import { Inspector } from './inspector'
import type { Objective, ObjectiveReplacementDraft, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

function ReplacementReview({
  adapter,
  objective,
  onClose,
  unavailable
}: {
  adapter: RuntimeOrganizationAdapter
  objective: Objective
  onClose(): void
  unavailable: boolean
}) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const navigate = useNavigate()
  const [draft, setDraft] = useState<ObjectiveReplacementDraft | null>(null)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [criteria, setCriteria] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const active = useRef(true)
  const sending = useRef(false)

  // A closed review or a different profile must never receive a late navigation.
  // eslint-disable-next-line no-restricted-syntax -- component lifetime guard, not mirrored reactive state
  useEffect(() => {
    active.current = true
    let disposed = false
    void adapter.previewReplacement!(objective.id)
      .then(value => {
        if (disposed) {
          return
        }

        setDraft(value)
        setTitle(value.title)
        setDescription(value.description)
        setCriteria(value.acceptanceCriteria.join('\n'))
      })
      .catch(reason => {
        if (!disposed) {
          setError(String(reason))
        }
      })

    return () => {
      disposed = true
      active.current = false
    }
  }, [adapter, objective.id])

  const submit = () => {
    if (!draft || !confirmed || unavailable || sending.current) {
      return
    }

    sending.current = true
    setPending(true)
    setError('')
    void adapter.replaceObjective!({
      sourceId: draft.sourceId,
      sourceVersion: draft.sourceVersion,
      title,
      description,
      acceptanceCriteria: criteria
        .split('\n')
        .map(value => value.trim())
        .filter(Boolean),
      confirmed: true
    })
      .then(value => {
        if (active.current) {
          navigate(`/objectives/${value.id}`)
          onClose()
        }
      })
      .catch(reason => {
        if (active.current) {
          setError(String(reason))
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
    <Inspector kind="objective" onClose={onClose} title={copy.replacementReview}>
      <p>{copy.replacementNote}</p>
      {error && <p role="alert">{error}</p>}
      {draft && (
        <form
          aria-busy={pending}
          aria-label={copy.replacementReview}
          className="eid-resolution-form"
          onSubmit={event => {
            event.preventDefault()
            submit()
          }}
        >
          <fieldset className="eid-management-fields" disabled={pending || unavailable}>
            <label>
              Objective
              <input
                className={controlVariants()}
                maxLength={500}
                onChange={event => setTitle(event.target.value)}
                required
                value={title}
              />
            </label>
            <label>
              {copy.replacementScope}
              <Textarea
                maxLength={30000}
                onChange={event => setDescription(event.target.value)}
                required
                value={description}
              />
            </label>
            <label>
              {copy.replacementCriteria}
              <Textarea onChange={event => setCriteria(event.target.value)} required value={criteria} />
            </label>
            <h3>{copy.replacementAllowance}</h3>
            <dl>
              <dt>Model calls</dt>
              <dd>{draft.allowance.modelCalls}</dd>
              <dt>Reserved tokens</dt>
              <dd>{draft.allowance.tokens}</dd>
              <dt>Configured cost ceiling (USD)</dt>
              <dd>{draft.allowance.costUsd ?? 'Not configured'}</dd>
              <dt>{copy.replacementDeadline}</dt>
              <dd>{draft.allowance.durationSeconds / 3600}</dd>
              <dt>Stages / replans / project runs</dt>
              <dd>
                {draft.allowance.maxStages} / {draft.allowance.maxReplans} / {draft.allowance.projectRuns}
              </dd>
              <dt>Executive / manager</dt>
              <dd>
                {draft.executiveId} / {draft.managerId}
              </dd>
              <dt>Projects</dt>
              <dd>{draft.projectIds.join(', ') || 'None'}</dd>
              <dt>Required checks</dt>
              <dd>{draft.requiredChecks.join(', ') || 'None'}</dd>
            </dl>
            <label className="eid-inline">
              <input checked={confirmed} onChange={event => setConfirmed(event.target.checked)} type="checkbox" />
              {copy.replacementConsent}
            </label>
            <Button disabled={!confirmed || !title.trim() || !description.trim() || !criteria.trim()} type="submit">
              {copy.replacementConfirm}
            </Button>
          </fieldset>
          <p className="eid-note">{copy.replacementUncertain}</p>
        </form>
      )}
      <Button onClick={onClose} variant="secondary">
        Cancel
      </Button>
    </Inspector>
  )
}

export function RuntimeReplacement({
  adapter,
  objective,
  snapshot
}: {
  adapter: RuntimeOrganizationAdapter
  objective: Objective
  snapshot: OrganizationSnapshot
}) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const [open, setOpen] = useState(false)
  const unavailable = snapshot.connection?.state !== 'ready'

  return (
    <>
      {objective.replacesObjectiveId && (
        <p>
          <Link to={`/objectives/${objective.replacesObjectiveId}`}>{copy.replacementOriginal}</Link>
        </p>
      )}
      {objective.replacementObjectiveId && (
        <p>
          <Link to={`/objectives/${objective.replacementObjectiveId}`}>{copy.replacementSuccessor}</Link>
        </p>
      )}
      {objective.replacementEligible &&
        !objective.replacementObjectiveId &&
        adapter.previewReplacement &&
        adapter.replaceObjective && (
          <Button disabled={unavailable} onClick={() => setOpen(true)} variant="secondary">
            {copy.replacementReview}
          </Button>
        )}
      {open && (
        <ReplacementReview
          adapter={adapter}
          objective={objective}
          onClose={() => setOpen(false)}
          unavailable={unavailable}
        />
      )}
    </>
  )
}
