import './organization-owner-policy.css'

import { useEffect, useRef, useState, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { useI18n } from '@/i18n/context'
import { ownerPolicyCopy } from '@/i18n/organization-owner-policy'

import { LocalizedTime } from './localized-time'
import type { OwnerPolicyClient } from './organization-owner-policy-client'
import { policyEqual } from './organization-owner-policy-contract'
import { AssignmentFields, BudgetFields, ObservationFields, RoutingFields } from './organization-owner-policy-fields'
import type {
  OwnerBudgetConfiguration,
  OwnerPolicyAction,
  OwnerPolicyDescriptor,
  OwnerPolicyRequest,
  OwnerRoutingConfiguration
} from './organization-owner-policy-types'

export type OwnerPolicyTarget =
  | { kind: 'routes' }
  | { kind: 'budget' }
  | { kind: 'assignment'; agentId: string }
  | { kind: 'observation'; objectiveId: string }
type Candidate = Pick<OwnerPolicyRequest, 'configuration' | 'observation'>
interface Draft {
  candidate: Candidate
  base: Candidate
}
const drafts = new Map<string, Draft>()
export const ownerPolicyAction = (target: OwnerPolicyTarget): OwnerPolicyAction =>
  target.kind === 'budget' ? 'budgetPolicy' : target.kind === 'observation' ? 'budgetObservation' : 'modelRouting'
export const ownerPolicyTitle = (target: OwnerPolicyTarget) =>
  (
    ({
      routes: 'editRoutes',
      budget: 'editBudget',
      assignment: 'editAssignment',
      observation: 'editObservation'
    }) as const
  )[target.kind]

function initialDraft(data: OwnerPolicyDescriptor, target: OwnerPolicyTarget): Draft {
  const { model_routes, model_assignments, ...budget } = data.configuration

  const observed =
    target.kind === 'observation' ? data.observations.find(item => item.objectiveId === target.objectiveId) : undefined

  const candidate: Candidate =
    target.kind === 'observation'
      ? {
          observation: {
            objectiveId: target.objectiveId,
            expectedRevision: observed?.revision ?? 0,
            remainingPercent: observed?.observation?.remainingPercent ?? NaN,
            asOf: '',
            expiresAt: '',
            resetAt: null
          }
        }
      : { configuration: target.kind === 'budget' ? budget : { model_routes, model_assignments } }

  const base = structuredClone(candidate)

  if (target.kind === 'assignment' && target.agentId === 'executive') {
    const routing = candidate.configuration as OwnerRoutingConfiguration

    if (!routing.model_assignments.some(item => item.agent_id === 'executive')) {
      candidate.configuration = {
        ...routing,
        model_assignments: [
          ...routing.model_assignments,
          { agent_id: 'executive', mode: 'fixed', route_ids: [], minimum_evidence: 1, max_fallbacks: 0 }
        ]
      }
    }
  }

  return { candidate: structuredClone(candidate), base }
}

/** Rebase only explicitly edited fields; hidden assignments and routes always come from the fresh descriptor. */
export function ownerPolicyCandidate(draft: Draft, target: OwnerPolicyTarget, data: OwnerPolicyDescriptor): Candidate {
  if (target.kind === 'observation') {
    return {
      observation: {
        ...draft.candidate.observation!,
        expectedRevision: data.observations.find(item => item.objectiveId === target.objectiveId)?.revision ?? 0
      }
    }
  }

  if (target.kind === 'budget') {
    return {
      configuration: Object.fromEntries(
        Object.entries(draft.candidate.configuration!).filter(
          ([key, value]) => !policyEqual(value, (draft.base.configuration as Record<string, unknown>)[key])
        )
      )
    }
  }

  const current = data.configuration
  const candidate = draft.candidate.configuration as OwnerRoutingConfiguration

  if (target.kind === 'routes') {
    return { configuration: { model_routes: candidate.model_routes, model_assignments: current.model_assignments } }
  }

  const assignment = candidate.model_assignments.find(item => item.agent_id === target.agentId)

  return {
    configuration: {
      model_routes: current.model_routes,
      model_assignments: [
        ...current.model_assignments.filter(item => item.agent_id !== target.agentId),
        ...(assignment ? [assignment] : [])
      ]
    }
  }
}

export function OwnerPolicyEditor({
  client,
  target,
  ownerKey,
  descriptor,
  onClose
}: {
  client: OwnerPolicyClient
  target: OwnerPolicyTarget
  ownerKey: string
  descriptor: OwnerPolicyDescriptor
  onClose: () => void
}) {
  const { locale } = useI18n()
  const copy = ownerPolicyCopy[locale]
  const view = useSyncExternalStore(client.subscribe, client.getSnapshot)
  const draftKey = JSON.stringify([ownerKey, descriptor.organizationId, descriptor.ownerId, target])
  const [draft, setDraft] = useState(() => drafts.get(draftKey) ?? initialDraft(descriptor, target))
  const mounted = useRef({ active: true })
  const returnFocus = useRef(document.activeElement instanceof HTMLElement ? document.activeElement : null)
  const closing = useRef(false)
  const reviewFocus = useRef<HTMLElement | null>(null)
  const requestedReviewFocus = useRef({ requested: false })
  useEffect(() => {
    const lifecycle = mounted.current
    lifecycle.active = true

    return () => {
      lifecycle.active = false
      client.discardPreview()
    }
  }, [client])
  const current = view.descriptor

  const matching = Boolean(
    current?.state === 'ready' &&
    current.organizationId === descriptor.organizationId &&
    current.ownerId === descriptor.ownerId
  )

  const unavailableTarget =
    current?.state === 'ready' &&
    (target.kind === 'assignment'
      ? !current.agents.some(item => item.id === target.agentId)
      : target.kind === 'observation'
        ? !current.observations.some(item => item.objectiveId === target.objectiveId && item.revision < 1000)
        : false)

  const disabled = !matching || view.busy || Boolean(view.intent) || Boolean(unavailableTarget)
  const action = ownerPolicyAction(target)

  const update = (candidate: Candidate) => {
    const next = { ...draft, candidate }
    drafts.set(draftKey, next)
    setDraft(next)
    client.discardPreview()
  }

  const preview = view.preview?.action === action ? view.preview : null
  useEffect(() => {
    if (preview && requestedReviewFocus.current.requested && reviewFocus.current) {
      requestedReviewFocus.current.requested = false
      reviewFocus.current.focus()
      reviewFocus.current.scrollIntoView?.({ block: 'start' })
    }
  }, [preview])

  const apply = async () => {
    const saved = await client.apply()

    if (saved) {
      drafts.delete(draftKey)

      if (
        mounted.current.active &&
        client.getSnapshot().descriptor?.state === 'ready' &&
        client.getSnapshot().descriptor?.organizationId === descriptor.organizationId &&
        client.getSnapshot().descriptor?.ownerId === descriptor.ownerId
      ) {
        setDraft(initialDraft(client.getSnapshot().descriptor!, target))
      }
    }
  }

  const close = () => {
    closing.current = true
    client.discardPreview()
    onClose()
  }

  return (
    <Dialog
      onOpenChange={open => {
        if (!open) {
          close()
        }
      }}
      open
    >
      <DialogContent
        bodyClassName="eid-policy-body"
        className="eidolon eid-owner-editor eid-policy-editor"
        onCloseAutoFocus={event => {
          event.preventDefault()

          if (closing.current && returnFocus.current?.isConnected) {
            returnFocus.current.focus()
          }
        }}
        showCloseButton={false}
      >
        <DialogTitle>{copy[ownerPolicyTitle(target)]}</DialogTitle>
        <DialogDescription>{copy.closeNote}</DialogDescription>
        <form
          onSubmit={event => {
            event.preventDefault()

            if (!disabled && current && !view.error) {
              requestedReviewFocus.current.requested = true
              void client.preview(action, ownerPolicyCandidate(draft, target, current))
            }
          }}
        >
          <div className="eid-policy-scroll">
            <p>
              {copy.scope}: <bdi>{descriptor.scope.profile}</bdi> · {copy.generation}:{' '}
              {current?.policyGeneration ?? descriptor.policyGeneration}
            </p>
            <p>
              {copy.organizationId}: <bdi>{descriptor.organizationId}</bdi> · {copy.ownerId}:{' '}
              <bdi>{descriptor.ownerId}</bdi>
            </p>
            <p className="eid-policy-source">
              {copy.source}: <bdi>{current?.sourceRevision ?? descriptor.sourceRevision}</bdi>
            </p>
            <fieldset disabled={disabled}>
              {target.kind === 'routes' && (
                <RoutingFields
                  descriptor={matching ? current! : descriptor}
                  update={configuration => update({ configuration })}
                  value={draft.candidate.configuration as OwnerRoutingConfiguration}
                />
              )}
              {target.kind === 'assignment' && (
                <AssignmentFields
                  agentId={target.agentId}
                  descriptor={matching ? current! : descriptor}
                  update={configuration => update({ configuration })}
                  value={draft.candidate.configuration as OwnerRoutingConfiguration}
                />
              )}
              {target.kind === 'budget' && (
                <BudgetFields
                  descriptor={matching ? current! : descriptor}
                  update={configuration => update({ configuration })}
                  value={draft.candidate.configuration as OwnerBudgetConfiguration}
                />
              )}
              {target.kind === 'observation' && (
                <ObservationFields
                  update={observation => update({ observation })}
                  value={draft.candidate.observation!}
                />
              )}
            </fieldset>
            {preview && (
              <section aria-label={copy.review} className="eid-policy-review" ref={reviewFocus} tabIndex={-1}>
                <h3>{copy.review}</h3>
                <p>
                  {copy.scope}: <bdi>{preview.value.scope.profile}</bdi> · {copy.generation}:{' '}
                  {preview.value.policyGeneration}
                </p>
                <p>
                  {
                    copy[
                      target.kind === 'budget'
                        ? 'budgetNote'
                        : target.kind === 'observation'
                          ? 'observationNote'
                          : 'assignmentNote'
                    ]
                  }
                </p>
                <PolicyChanges changes={preview.value.changes} />
                <h4>{copy.impact}</h4>
                <p>
                  {copy.affectedScope}:{' '}
                  {
                    copy[
                      preview.value.impact.scope === 'objective-defaults'
                        ? 'objectiveDefaults'
                        : preview.value.impact.scope === 'persistent-agent-routing'
                          ? 'persistentRouting'
                          : 'objectiveAdvisory'
                    ]
                  }
                </p>
                <p>
                  {copy.activeObjectives}: {preview.value.impact.activeObjectiveCount} · {copy.evaluatedAt}:{' '}
                  <LocalizedTime value={preview.value.impact.evaluatedAt} />
                </p>
                <p>{copy.notEstimated}</p>
                <ul>
                  {preview.value.warnings.map(warning => (
                    <li key={warning.code}>
                      {copy[warning.code]}
                      {warning.values.activeAttempts !== undefined && <> {warning.values.activeAttempts}</>}
                      {warning.values.fields && (
                        <> {warning.values.fields.map(field => copy[field as keyof typeof copy] ?? field).join(', ')}</>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {view.intent && (
              <section aria-label={copy.review} className="eid-policy-review">
                <h3>{copy.pendingChange}</h3>
                <p>
                  {copy.organizationId}: <bdi>{view.intent.expectedOrganizationId}</bdi> · {copy.ownerId}:{' '}
                  <bdi>{view.intent.ownerId}</bdi>
                </p>
                <p>
                  {copy.generation}: {view.intent.expectedGeneration}
                </p>
                <p>
                  {copy.source}: <bdi>{view.intent.sourceRevision}</bdi>
                </p>
                <pre>{JSON.stringify(view.intent.configuration ?? view.intent.observation, null, 2)}</pre>
              </section>
            )}
            {view.busy && <p role="status">{view.intent ? copy.saving : copy.loading}</p>}
            {view.error && <p role="alert">{copy[view.error]}</p>}
            {(unavailableTarget ||
              (view.intent &&
                (view.intent.expectedOrganizationId !== current?.organizationId ||
                  view.intent.ownerId !== current?.ownerId))) && <p role="alert">{copy.unavailable}</p>}
            {view.receipt && !view.intent && <p role="status">{copy.saved}</p>}
          </div>
          <div className="eid-actions">
            <Button disabled={view.busy} onClick={() => void client.read()} type="button" variant="ghost">
              {copy.refresh}
            </Button>
            <Button onClick={close} type="button" variant="ghost">
              {copy.close}
            </Button>
            {view.intent ? (
              <Button
                disabled={
                  view.busy ||
                  !matching ||
                  view.intent.expectedOrganizationId !== current?.organizationId ||
                  view.intent.ownerId !== current?.ownerId
                }
                onClick={() => void apply()}
                type="button"
              >
                {copy.reconcile}
              </Button>
            ) : (
              <>
                <Button
                  disabled={disabled || Boolean(view.error) || !current?.capabilities[action]}
                  type="submit"
                  variant="secondary"
                >
                  {copy.preview}
                </Button>
                <Button
                  disabled={disabled || !preview || Boolean(view.error)}
                  onClick={() => void apply()}
                  type="button"
                >
                  {copy.apply}
                </Button>
              </>
            )}
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function PolicyChanges({ changes }: { changes: { field: string; before: unknown; after: unknown }[] }) {
  const { locale } = useI18n()
  const copy = ownerPolicyCopy[locale]

  return changes.length ? (
    <dl className="eid-policy-changes">
      {changes.map(({ field, before, after }) => (
        <div key={field}>
          <dt>{copy[field as keyof typeof copy] ?? field}</dt>
          <dd>
            <strong>{copy.before}</strong>
            <pre>{JSON.stringify(before, null, 2)}</pre>
            <strong>{copy.after}</strong>
            <pre>{JSON.stringify(after, null, 2)}</pre>
          </dd>
        </div>
      ))}
    </dl>
  ) : (
    <p>{copy.noChanges}</p>
  )
}
