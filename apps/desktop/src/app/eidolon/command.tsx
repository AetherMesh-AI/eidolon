import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { controlVariants } from '@/components/ui/control'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n/context'

import {
  $intakeDrafts,
  acknowledgeIntake,
  discardIntakeDraft,
  emptyIntake,
  setIntakeDraft
} from './runtime-intake-drafts'
import type { ObjectiveMetadata, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

export function Command({
  adapter,
  snapshot
}: {
  adapter: RuntimeOrganizationAdapter
  snapshot: OrganizationSnapshot
}) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const roster = t.organizationRoster
  const drafts = useStore($intakeDrafts)
  const localKey = useRef(crypto.randomUUID())
  const owner = snapshot.connection?.ownerScope ?? snapshot.connection?.scope
  const unavailable = snapshot.connection && snapshot.connection.state !== 'ready'
  const draftKey = owner ? JSON.stringify(['intake', owner]) : localKey.current
  const hidden = snapshot.connection?.state === 'connecting'
  const draft = hidden ? emptyIntake : (drafts[draftKey] ?? emptyIntake)
  const { criteria, goal, metadata } = draft
  const stored = hidden ? undefined : drafts[draftKey]
  const error = stored?.error ?? ''
  const submitting = stored?.busy ?? false
  const disabled = submitting || Boolean(unavailable)
  const [discarding, setDiscarding] = useState(false)
  const completionDetails = useRef<HTMLDetailsElement>(null)
  const active = useRef(true)
  const navigate = useNavigate()

  const update = (fields: Partial<typeof draft> & { error?: string }) => {
    if (!unavailable) {
      setIntakeDraft(draftKey, { ...($intakeDrafts.get()[draftKey] ?? emptyIntake), ...fields })
    }
  }

  const setCriteria = (value: string) => update({ criteria: value })
  const setGoal = (value: string) => update({ goal: value })
  const setMetadata = (value: ObjectiveMetadata) => update({ metadata: value })
  const setError = (value: string) => update({ error: value })
  // Ownership selection is a capability of persistent-identity runtimes. Older
  // snapshots continue using their established default owner contract.
  const hasOwnership = snapshot.agents.some(agent => agent.persistent)

  const executives = snapshot.agents.filter(
    agent => agent.role === 'Executive' && agent.lifecycle === 'active' && agent.capabilities.includes('request.accept')
  )

  const executiveId = metadata.executiveId ?? (executives.some(agent => agent.id === 'executive') ? 'executive' : '')
  const selectedExecutive = executives.find(agent => agent.id === executiveId)

  const managers = snapshot.agents.filter(
    agent =>
      agent.role === 'Manager' &&
      agent.lifecycle === 'active' &&
      agent.managerId === executiveId &&
      ['request.plan', 'request.integrate'].every(capability => agent.capabilities.includes(capability))
  )

  const managerId = metadata.managerId ?? (managers.some(agent => agent.id === 'manager') ? 'manager' : '')
  const availableProjects = snapshot.runtime?.availableProjects ?? []

  const missingProjects = metadata.projectIds?.filter(id => !availableProjects.some(project => project.id === id)) ?? []

  const validOwnership =
    executives.some(agent => agent.id === executiveId) && managers.some(agent => agent.id === managerId)

  // eslint-disable-next-line no-restricted-syntax -- component lifetime guard, not a mirrored atom
  useEffect(() => {
    active.current = true

    return () => {
      active.current = false
    }
  }, [])

  const submit = () => {
    if (unavailable || $intakeDrafts.get()[draftKey]?.busy) {
      return
    }

    setError('')

    if (hasOwnership && !validOwnership) {
      setError(roster.ownershipRequired)

      return
    }

    if (
      availableProjects.length &&
      (metadata.deliveryMode ?? 'source_project') === 'source_project' &&
      !metadata.projectIds?.length
    ) {
      setError(copy.projectSelectionRequired)

      return
    }

    if (metadata.projectIds?.some(id => !availableProjects.some(project => project.id === id))) {
      setError(copy.projectSelectionChanged)

      return
    }

    const acceptanceCriteria = criteria
      .split('\n')
      .map(value => value.trim())
      .filter(Boolean)

    if (acceptanceCriteria.length > 12 || acceptanceCriteria.some(value => value.length > 2000)) {
      if (completionDetails.current) {
        completionDetails.current.open = true
      }

      setError(copy.criteriaInvalid)

      return
    }

    const submitted = {
      ...metadata,
      executiveId: hasOwnership ? executiveId : undefined,
      managerId: hasOwnership ? managerId : undefined,
      acceptanceCriteria: acceptanceCriteria.length ? acceptanceCriteria : undefined,
      deliveryMode: metadata.deliveryMode ?? ('source_project' as const)
    }

    // Keep this key even after errors and edits. A response can be lost after
    // admission; a changed payload must conflict with that receipt, not mint a
    // second objective. Only confirmed success or explicit discard releases it.
    const idempotencyKey = stored?.idempotencyKey ?? crypto.randomUUID()
    const sent = { goal, criteria, metadata: submitted }
    setIntakeDraft(draftKey, { ...sent, idempotencyKey, submitted: stored?.submitted ?? sent, busy: true })
    void adapter
      .createObjective(goal, submitted, idempotencyKey)
      .then(objective => {
        if (!objective || typeof objective.id !== 'string' || !objective.id || objective.title !== goal.trim()) {
          throw new Error(copy.createError)
        }

        acknowledgeIntake(draftKey, idempotencyKey, sent)

        if (active.current) {
          navigate(`/objectives/${objective.id}`)
        }
      })
      .catch(reason => {
        const current = $intakeDrafts.get()[draftKey]

        if (current?.idempotencyKey === idempotencyKey) {
          setIntakeDraft(draftKey, {
            ...current,
            busy: false,
            error: reason instanceof Error ? reason.message : copy.createError
          })
        }
      })
  }

  const priorities = [copy.lowest, copy.low, copy.normal, copy.high, copy.highest]
  const priorityKeys = ['P5', 'P4', 'P3', 'P2', 'P1'] as const

  return (
    <div className="eid-command">
      <h1>{copy.mainQuestion}</h1>
      <p className="eid-subtitle">{copy.composerSubtitle}</p>
      <form
        aria-busy={submitting}
        className="eid-composer"
        onSubmit={event => {
          event.preventDefault()
          submit()
        }}
      >
        <label className="sr-only" htmlFor="eid-objective">
          {copy.objective}
        </label>
        <Textarea
          disabled={disabled}
          id="eid-objective"
          maxLength={500}
          onChange={event => {
            setGoal(event.target.value)
            setError('')
          }}
          placeholder={copy.goalPlaceholder}
          rows={3}
          value={goal}
        />
        {hasOwnership && (
          <div className="eid-command-ownership">
            <label htmlFor="eid-executive">{roster.objectiveExecutive}</label>
            <select
              className={controlVariants()}
              disabled={disabled}
              id="eid-executive"
              onChange={event => setMetadata({ ...metadata, executiveId: event.target.value, managerId: '' })}
              value={executives.some(agent => agent.id === executiveId) ? executiveId : ''}
            >
              <option value="">{roster.chooseExecutive}</option>
              {executives.map(agent => (
                <option key={agent.id} value={agent.id}>
                  {agent.name}
                </option>
              ))}
            </select>
            <label htmlFor="eid-manager">{roster.objectiveManager}</label>
            <select
              className={controlVariants()}
              disabled={disabled || !executiveId}
              id="eid-manager"
              onChange={event => setMetadata({ ...metadata, managerId: event.target.value })}
              value={managers.some(agent => agent.id === managerId) ? managerId : ''}
            >
              <option value="">{roster.chooseManager}</option>
              {managers.map(agent => (
                <option key={agent.id} value={agent.id}>
                  {agent.name}
                </option>
              ))}
            </select>
            <details className="eid-command-planning">
              <summary>
                {copy.packagePlanningMode}
                {selectedExecutive && (
                  <>
                    {' '}
                    ·{' '}
                    {selectedExecutive.capabilities.includes('request.decompose')
                      ? copy.packageExecutiveMode
                      : copy.packageLegacyMode}
                  </>
                )}
              </summary>
              <p className="eid-note">{roster.ownershipNote}</p>
              {selectedExecutive && (
                <p className="eid-note">
                  {selectedExecutive.capabilities.includes('request.decompose')
                    ? copy.packageExecutiveAdmission
                    : copy.packageLegacyAdmission}
                </p>
              )}
            </details>
          </div>
        )}
        {missingProjects.length > 0 && (
          <div role="alert">
            <p>{copy.projectSelectionChanged}</p>
            {missingProjects.map(id => (
              <label className="eid-inline" key={id}>
                <input
                  checked
                  disabled={disabled}
                  onChange={() =>
                    setMetadata({ ...metadata, projectIds: metadata.projectIds?.filter(value => value !== id) })
                  }
                  type="checkbox"
                />
                {id}
              </label>
            ))}
          </div>
        )}
        {availableProjects.length > 0 && (
          <fieldset disabled={disabled}>
            <legend>{copy.objectiveProjects}</legend>
            <p className="eid-note">{copy.projectSelectionNote}</p>
            {availableProjects.map(project => (
              <label className="eid-inline" key={project.id}>
                <input
                  checked={metadata.projectIds?.includes(project.id) ?? false}
                  onChange={event =>
                    setMetadata({
                      ...metadata,
                      projectIds: event.target.checked
                        ? [...(metadata.projectIds ?? []), project.id]
                        : metadata.projectIds?.filter(id => id !== project.id)
                    })
                  }
                  type="checkbox"
                />
                <span>
                  {project.id} · {project.root} · {project.team}
                </span>
              </label>
            ))}
          </fieldset>
        )}
        <details className="eid-form-disclosure">
          <summary>
            {copy.addContext}
            {metadata.description?.trim() && <span className="eid-disclosure-value">{copy.detailsAdded}</span>}
          </summary>
          <label htmlFor="eid-context">{copy.submittedContext}</label>
          <Textarea
            disabled={disabled}
            id="eid-context"
            maxLength={12000}
            onChange={event => setMetadata({ ...metadata, description: event.target.value })}
            placeholder={copy.contextPlaceholder}
            rows={3}
            value={metadata.description ?? ''}
          />
        </details>
        <details className="eid-form-disclosure" ref={completionDetails}>
          <summary>
            {copy.completionOptions}
            {criteria.trim() && <span className="eid-disclosure-value">{copy.detailsAdded}</span>}
          </summary>
          <label htmlFor="eid-criteria">{copy.acceptanceCriteria}</label>
          <Textarea
            aria-describedby="eid-criteria-note"
            disabled={disabled}
            id="eid-criteria"
            maxLength={24012}
            onChange={event => setCriteria(event.target.value)}
            rows={3}
            value={criteria}
          />
          <p className="eid-note" id="eid-criteria-note">
            {copy.criteriaHint}
          </p>
        </details>
        <label className="eid-delivery-scope">
          {copy.deliveryMode}
          <select
            className={controlVariants()}
            disabled={disabled}
            onChange={event =>
              setMetadata({ ...metadata, deliveryMode: event.target.value as ObjectiveMetadata['deliveryMode'] })
            }
            value={metadata.deliveryMode ?? 'source_project'}
          >
            <option value="source_project">{copy.sourceProject}</option>
            <option value="managed_artifact">{copy.managedArtifact}</option>
          </select>
        </label>
        <p className="eid-note">{copy.deliveryNote}</p>
        <details className="eid-form-disclosure">
          <summary>
            {copy.verificationOptions}
            {Boolean(metadata.requiredChecks?.length || metadata.priority) && (
              <span className="eid-disclosure-value">{copy.detailsAdded}</span>
            )}
          </summary>
          <fieldset disabled={disabled}>
            <legend>{copy.requiredChecks}</legend>
            {(['project_tests', 'managed_validation', 'source_integration'] as const).map(check => (
              <label className="eid-inline" key={check}>
                <input
                  checked={metadata.requiredChecks?.includes(check) ?? false}
                  onChange={event =>
                    setMetadata({
                      ...metadata,
                      requiredChecks: event.target.checked
                        ? [...(metadata.requiredChecks ?? []), check]
                        : metadata.requiredChecks?.filter(value => value !== check)
                    })
                  }
                  type="checkbox"
                />
                {copy[check]}
              </label>
            ))}
          </fieldset>
          <div className="eid-composer-tools">
            <label>
              <input
                checked={metadata.priority !== undefined}
                disabled={disabled}
                onChange={event => setMetadata({ ...metadata, priority: event.target.checked ? 'P3' : undefined })}
                type="checkbox"
              />{' '}
              {copy.priority}
            </label>
          </div>
          {metadata.priority !== undefined && (
            <label className="eid-priority">
              {copy.priorityLevel}
              <input
                aria-label={copy.priorityLevel}
                aria-valuetext={priorities[priorityKeys.indexOf(metadata.priority as (typeof priorityKeys)[number])]}
                disabled={disabled}
                max={4}
                min={0}
                onChange={event => setMetadata({ ...metadata, priority: priorityKeys[Number(event.target.value)] })}
                step={1}
                type="range"
                value={priorityKeys.indexOf(metadata.priority as (typeof priorityKeys)[number])}
              />
              <span aria-hidden="true" className="eid-priority-endpoints">
                <span>{copy.lowest}</span>
                <span>{copy.highest}</span>
              </span>
            </label>
          )}
        </details>
        <div className="eid-composer-tools">
          <span>{copy.providerScope}</span>
          <Button disabled={!goal.trim() || submitting || Boolean(unavailable)} type="submit">
            {submitting ? copy.submittingObjective : copy.createObjective} <span aria-hidden="true">↑</span>
          </Button>
        </div>
      </form>
      <p className="eid-note">{copy.intakeMemory}</p>
      {stored?.idempotencyKey && !submitting && <p role="status">{copy.intakeUnconfirmed}</p>}
      {error && <p role="alert">{error}</p>}
      <div className="eid-inline">
        <Button disabled={disabled || !stored} onClick={() => setDiscarding(true)} variant="text">
          {copy.intakeDiscard}
        </Button>
        {stored?.submitted && !submitting && (
          <Button disabled={disabled} onClick={() => update(stored.submitted!)} variant="text">
            {copy.intakeRestore}
          </Button>
        )}
      </div>
      <ConfirmDialog
        confirmLabel={copy.intakeDiscard}
        description={stored?.idempotencyKey ? copy.intakeDiscardUnconfirmed : copy.intakeDiscardNote}
        destructive
        dismissOnConfirm
        onClose={() => setDiscarding(false)}
        onConfirm={() => {
          if (!$intakeDrafts.get()[draftKey]?.busy) {
            discardIntakeDraft(draftKey)
          }
        }}
        open={discarding && !unavailable}
        title={copy.intakeDiscard}
      />
      <div className="eid-inline">
        <Link to="/">{copy.askQuestion}</Link>
        <span>{copy.connectedSession}</span>
      </div>
      <p className="eid-note">{copy.composerNote}</p>
    </div>
  )
}
