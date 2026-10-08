import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { Button } from '@/components/ui/button'
import { controlVariants } from '@/components/ui/control'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n/context'

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
  const [criteria, setCriteria] = useState('')
  const [goal, setGoal] = useState('')
  const [metadata, setMetadata] = useState<ObjectiveMetadata>({})
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const active = useRef(true)
  const sending = useRef(false)
  const navigate = useNavigate()
  const unavailable = snapshot.connection && snapshot.connection.state !== 'ready'
  // Ownership selection is a capability of persistent-identity runtimes. Older
  // snapshots continue using their established default owner contract.
  const hasOwnership = snapshot.agents.some(agent => agent.persistent)
  const executives = snapshot.agents.filter(agent => agent.role === 'Executive' && agent.lifecycle === 'active' && agent.capabilities.includes('request.accept'))
  const executiveId = metadata.executiveId ?? (executives.some(agent => agent.id === 'executive') ? 'executive' : '')
  const selectedExecutive = executives.find(agent => agent.id === executiveId)
  const managers = snapshot.agents.filter(agent => agent.role === 'Manager' && agent.lifecycle === 'active' && agent.managerId === executiveId && ['request.plan', 'request.integrate'].every(capability => agent.capabilities.includes(capability)))
  const managerId = metadata.managerId ?? (managers.some(agent => agent.id === 'manager') ? 'manager' : '')
  const availableProjects = snapshot.runtime?.availableProjects ?? []
  const validOwnership = executives.some(agent => agent.id === executiveId) && managers.some(agent => agent.id === managerId)

  // eslint-disable-next-line no-restricted-syntax -- component lifetime guard, not a mirrored atom
  useEffect(() => {
    active.current = true

    return () => {
      active.current = false
    }
  }, [])

  const submit = () => {
    if (sending.current) {
      return
    }

    setError('')

    if (hasOwnership && !validOwnership) {
      setError(roster.ownershipRequired)

      return
    }

    if (availableProjects.length && (metadata.deliveryMode ?? 'source_project') === 'source_project' && !metadata.projectIds?.length) {
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

    sending.current = true
    setSubmitting(true)
    void adapter
      .createObjective(goal, submitted)
      .then(objective => {
        if (active.current) {
          navigate(`/objectives/${objective.id}`)
        }
      })
      .catch(reason => {
        if (active.current) {
          setError(reason instanceof Error ? reason.message : copy.createError)
        }
      })
      .finally(() => {
        sending.current = false

        if (active.current) {
          setSubmitting(false)
        }
      })
  }

  const priorities = [copy.lowest, copy.low, copy.normal, copy.high, copy.highest]
  const priorityKeys = ['P5', 'P4', 'P3', 'P2', 'P1'] as const

  return (
    <div className="eid-command">
      <div aria-hidden="true" className="eid-mark">
        ◈
      </div>
      <p className="eid-eyebrow">Eidolon</p>
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
          disabled={submitting}
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
        {hasOwnership && <>
          <label htmlFor="eid-executive">{roster.objectiveExecutive}</label>
          <select className={controlVariants()} disabled={submitting} id="eid-executive" onChange={event => setMetadata({ ...metadata, executiveId: event.target.value, managerId: '' })} value={executives.some(agent => agent.id === executiveId) ? executiveId : ''}>
            <option value="">{roster.chooseExecutive}</option>
            {executives.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
          </select>
          <label htmlFor="eid-manager">{roster.objectiveManager}</label>
          <select className={controlVariants()} disabled={submitting || !executiveId} id="eid-manager" onChange={event => setMetadata({ ...metadata, managerId: event.target.value })} value={managers.some(agent => agent.id === managerId) ? managerId : ''}>
            <option value="">{roster.chooseManager}</option>
            {managers.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
          </select>
          <p className="eid-note">{roster.ownershipNote}</p>
          {selectedExecutive && <p className="eid-note">{selectedExecutive.capabilities.includes('request.decompose') ? copy.packageExecutiveAdmission : copy.packageLegacyAdmission}</p>}
        </>}
        {availableProjects.length > 0 && <fieldset disabled={submitting}>
          <legend>{copy.objectiveProjects}</legend>
          <p className="eid-note">{copy.projectSelectionNote}</p>
          {availableProjects.map(project => <label className="eid-inline" key={project.id}>
            <input checked={metadata.projectIds?.includes(project.id) ?? false} onChange={event => setMetadata({ ...metadata, projectIds: event.target.checked
                ? [...(metadata.projectIds ?? []), project.id]
                : metadata.projectIds?.filter(id => id !== project.id) })}
              type="checkbox" />
            <span>{project.id} · {project.root} · {project.team}</span>
          </label>)}
        </fieldset>}
        <label htmlFor="eid-context">{copy.submittedContext}</label>
        <Textarea
          disabled={submitting}
          id="eid-context"
          maxLength={12000}
          onChange={event => setMetadata({ ...metadata, description: event.target.value })}
          placeholder={copy.contextPlaceholder}
          rows={3}
          value={metadata.description ?? ''}
        />
        <label htmlFor="eid-criteria">{copy.acceptanceCriteria}</label>
        <Textarea
          aria-describedby="eid-criteria-note"
          disabled={submitting}
          id="eid-criteria"
          maxLength={24012}
          onChange={event => setCriteria(event.target.value)}
          rows={3}
          value={criteria}
        />
        <p className="eid-note" id="eid-criteria-note">
          {copy.criteriaHint}
        </p>
        <label>
          {copy.deliveryMode}
          <select
            className={controlVariants()}
            disabled={submitting}
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
        <fieldset disabled={submitting}>
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
              disabled={submitting}
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
              disabled={submitting}
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
        <div className="eid-composer-tools">
          <span>{copy.providerScope}</span>
          <Button disabled={!goal.trim() || submitting || Boolean(unavailable)} type="submit">
            {submitting ? copy.submittingObjective : copy.createObjective} <span aria-hidden="true">↑</span>
          </Button>
        </div>
      </form>
      {error && <p role="alert">{error}</p>}
      <div className="eid-inline">
        <Link to="/">{copy.askQuestion}</Link>
        <span>{copy.connectedSession}</span>
      </div>
      <p className="eid-note">{copy.composerNote}</p>
    </div>
  )
}
