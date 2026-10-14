import './organization-owner-policy.css'

import { useEffect, useState } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'
import { ownerPolicyCopy } from '@/i18n/organization-owner-policy'

import {
  ownerPolicyAction,
  OwnerPolicyEditor,
  type OwnerPolicyTarget,
  ownerPolicyTitle
} from './organization-owner-policy-editor'
import type { OwnerPolicyDescriptor } from './organization-owner-policy-types'
import { useOrganizationOwnerPolicy } from './use-organization-owner-policy'

export function OwnerPolicyEntry({ target }: { target: OwnerPolicyTarget }) {
  const policy = useOrganizationOwnerPolicy()
  const { locale } = useI18n()
  const copy = ownerPolicyCopy[locale]
  const [openFor, setOpenFor] = useState<{ ownerKey: string; descriptor: OwnerPolicyDescriptor } | null>(null)
  const ownerKey = policy.scope.ownerKey
  const data = policy.view.descriptor
  const organizationId = data?.state === 'ready' ? data.organizationId : null
  const ownerId = data?.state === 'ready' ? data.ownerId : null
  useEffect(() => {
    setOpenFor(open => open && (open.ownerKey !== ownerKey || (organizationId && (open.descriptor.organizationId !== organizationId || open.descriptor.ownerId !== ownerId))) ? null : open)
  }, [ownerKey, organizationId, ownerId])

  const available =
    data?.state === 'ready' &&
    data.capabilities[ownerPolicyAction(target)] &&
    (target.kind === 'assignment'
      ? !['owner', 'control:apply', 'control:project'].includes(target.agentId) &&
        data.agents.some(agent => agent.id === target.agentId)
      : target.kind === 'observation'
        ? data.observations.some(item => item.objectiveId === target.objectiveId && item.revision < 1000)
        : true)

  // Presence of read-only routing/pacing details never grants write capability.
  if (!available && !openFor) {
    return null
  }

  return (
    <>
      <Button
        disabled={policy.view.busy || !available}
        onClick={() => {
          if (data) {
            setOpenFor({ ownerKey: policy.scope.ownerKey, descriptor: data })
          }
        }}
        size="sm"
        variant="secondary"
      >
        {copy[ownerPolicyTitle(target)]}
      </Button>
      {openFor?.ownerKey === policy.scope.ownerKey && (
        <OwnerPolicyEditor
          client={policy.client}
          descriptor={openFor.descriptor}
          key={JSON.stringify([policy.scope.ownerKey, openFor.descriptor.organizationId, target])}
          onClose={() => setOpenFor(null)}
          ownerKey={policy.scope.ownerKey}
          target={target}
        />
      )}
    </>
  )
}

export function OwnerPolicySettings() {
  const policy = useOrganizationOwnerPolicy()
  const { locale } = useI18n()
  const copy = ownerPolicyCopy[locale]

  const [target, setTarget] = useState<{
    ownerKey: string
    target: OwnerPolicyTarget
    descriptor: OwnerPolicyDescriptor
  } | null>(null)

  const ownerKey = policy.scope.ownerKey
  const data = policy.view.descriptor
  const organizationId = data?.state === 'ready' ? data.organizationId : null
  const ownerId = data?.state === 'ready' ? data.ownerId : null
  useEffect(() => {
    setTarget(open => open && (open.ownerKey !== ownerKey || (organizationId && (open.descriptor.organizationId !== organizationId || open.descriptor.ownerId !== ownerId))) ? null : open)
  }, [ownerKey, organizationId, ownerId])
  const ready = data?.state === 'ready'
  const metadataState = policy.metadata.descriptor?.state

  const error =
    metadataState === 'unconfigured'
      ? 'unconfigured'
      : metadataState === 'unsupported'
        ? 'unsupported'
        : policy.view.error

  return (
    <div className="eid-policy-settings">
      <h3>{copy.title}</h3>
      <p>{copy.routingNote}</p>
      <p>{copy.budgetNote}</p>
      {policy.view.busy || policy.metadata.busy ? <p role="status">{copy.loading}</p> : null}
      {error && (
        <p role="alert">{data?.reason === 'execution_not_initialized' ? copy.executionNotInitialized : copy[error]}</p>
      )}
      {data?.reason === 'execution_not_initialized' && <Link to="/organization">{copy.openOrganization}</Link>}
      <Button disabled={policy.view.busy || policy.metadata.busy} onClick={() => void policy.refresh()} variant="ghost">
        {copy.refresh}
      </Button>
      {ready && (
        <>
          <p>
            {copy.scope}: <bdi>{data.scope.profile}</bdi> · {copy.generation}: {data.policyGeneration}
          </p>
          <p className="eid-policy-source">
            {copy.source}: <bdi>{data.sourceRevision}</bdi>
          </p>
          <section aria-label={copy.routes}>
            <h3>{copy.routes}</h3>
            <ul>
              {data.configuration.model_routes.map(route => (
                <li key={route.id}>
                  <bdi>{route.id}</bdi> ·{' '}
                  <bdi>
                    {route.provider} / {route.model}
                  </bdi>
                </li>
              ))}
            </ul>
            <Button
              disabled={!data.capabilities.modelRouting || policy.view.busy}
              onClick={() =>
                setTarget({ ownerKey: policy.scope.ownerKey, descriptor: data, target: { kind: 'routes' } })
              }
              variant="secondary"
            >
              {copy.editRoutes}
            </Button>
          </section>
          <section aria-label={copy.allowance}>
            <h3>{copy.allowance}</h3>
            <dl>
              {(['max_model_calls', 'max_total_tokens', 'max_cost_usd', 'objective_timeout_seconds'] as const).map(
                field => (
                  <div key={field}>
                    <dt>{copy[field]}</dt>
                    <dd>{data.configuration[field] ?? copy.disabled}</dd>
                  </div>
                )
              )}
            </dl>
            <p>{copy.separateNote}</p>
            <Button
              disabled={!data.capabilities.budgetPolicy || policy.view.busy}
              onClick={() =>
                setTarget({ ownerKey: policy.scope.ownerKey, descriptor: data, target: { kind: 'budget' } })
              }
              variant="secondary"
            >
              {copy.editBudget}
            </Button>
          </section>
          {data.managedFields.length > 0 && (
            <section aria-label={copy.managedFields}>
              <h3>{copy.managedFields}</h3>
              <p>{copy.managed}</p>
              <ul>
                {data.managedFields.map(field => (
                  <li key={field}>{copy[field as keyof typeof copy] ?? field}</li>
                ))}
              </ul>
            </section>
          )}
          {policy.view.intent && (
            <Button
              disabled={policy.view.busy}
              onClick={() =>
                setTarget({
                  ownerKey: policy.scope.ownerKey,
                  descriptor: data,
                  target:
                    policy.view.intent!.action === 'budgetObservation'
                      ? { kind: 'observation', objectiveId: policy.view.intent!.observation!.objectiveId }
                      : { kind: policy.view.intent!.action === 'budgetPolicy' ? 'budget' : 'routes' }
                })
              }
              variant="secondary"
            >
              {copy.reconcile}
            </Button>
          )}
        </>
      )}
      {target?.ownerKey === policy.scope.ownerKey && (
        <OwnerPolicyEditor
          client={policy.client}
          descriptor={target.descriptor}
          key={JSON.stringify([policy.scope.ownerKey, target.descriptor.organizationId, target.target])}
          onClose={() => setTarget(null)}
          ownerKey={policy.scope.ownerKey}
          target={target.target}
        />
      )}
    </div>
  )
}

export function OwnerPolicySettingsLink() {
  const { locale } = useI18n()

  return <Link to="/settings?tab=organization">{ownerPolicyCopy[locale].settingsLink}</Link>
}
