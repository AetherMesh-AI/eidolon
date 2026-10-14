import './organization-shared-allowance.css'

import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'
import { ownerPolicyCopy } from '@/i18n/organization-owner-policy'
import { sharedAllowanceCopy } from '@/i18n/organization-shared-allowance'

import { LocalizedTime } from './localized-time'
import { SharedAllowanceEditor } from './organization-shared-allowance-editor'
import type {
  AllowanceProjection,
  SharedAllowanceAction,
  SharedAllowanceDescriptor
} from './organization-shared-allowance-types'
import { useOrganizationSharedAllowance } from './use-organization-shared-allowance'

function Projection({ value, title }: { value: AllowanceProjection; title: string }) {
  const { locale } = useI18n()
  const copy = sharedAllowanceCopy[locale]
  const common = ownerPolicyCopy[locale]

  const labels = {
    limits: copy.limits,
    reserved: copy.reserved,
    remaining: copy.remaining,
    protectedRemaining: copy.protected,
    ordinaryRemaining: copy.ordinary
  }

  return (
    <section aria-label={title}>
      <h3>{title}</h3>
      <dl className="eid-allowance-projection">
        {(Object.keys(labels) as (keyof typeof labels)[]).map(field => (
          <div key={field}>
            <dt>{labels[field]}</dt>
            <dd>
              {copy.calls}: {value[field].modelCalls.toLocaleString(locale)}
              <br />
              {copy.tokens}: {value[field].tokens.toLocaleString(locale)}
              <br />
              {copy.cost}: {value[field].configuredCostUsd ?? common.disabled}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

export function SharedAllowanceSettings() {
  const policy = useOrganizationSharedAllowance()
  const { locale } = useI18n()
  const copy = sharedAllowanceCopy[locale]
  const common = ownerPolicyCopy[locale]

  const [openFor, setOpenFor] = useState<{
    ownerKey: string
    scopeKey: string
    action: SharedAllowanceAction
    descriptor: SharedAllowanceDescriptor
  } | null>(null)

  const data = policy.view.descriptor
  const ownerKey = policy.scope.ownerKey
  const scopeKey = policy.scope.key
  const organizationId = data?.organizationId
  const ownerId = data?.ownerId
  useEffect(() => {
    setOpenFor(open =>
      open &&
      (open.ownerKey !== ownerKey ||
        open.scopeKey !== scopeKey ||
        (organizationId && (open.descriptor.organizationId !== organizationId || open.descriptor.ownerId !== ownerId)))
        ? null
        : open
    )
  }, [ownerKey, scopeKey, organizationId, ownerId])

  const edit = (action: SharedAllowanceAction) => {
    if (data?.authority === 'installation-owner' && data.capabilities[action]) {
      setOpenFor({ ownerKey, scopeKey, action, descriptor: data })
    }
  }

  const metadataState = policy.metadata.descriptor?.state
  const error = metadataState === 'unconfigured' || metadataState === 'unsupported' ? metadataState : policy.view.error
  const observation = data?.observation
  const configuration = data?.configuration

  const periodState =
    configuration &&
    (Date.parse(configuration.startsAt) > Date.now()
      ? 'scheduled'
      : Date.parse(configuration.endsAt) <= Date.now()
        ? 'ended'
        : 'active')

  return (
    <section aria-label={copy.title} className="eid-shared-allowance">
      <h3>{copy.title}</h3>
      <p>{copy.description}</p>
      <p>{copy.disclaimer}</p>
      {policy.view.busy || policy.metadata.busy ? <p role="status">{common.loading}</p> : null}
      {error && <p role="alert">{error in copy ? copy[error as keyof typeof copy] : common[error]}</p>}
      {data && (
        <>
          <p>
            {common.scope}: <bdi>{data.scope.profile}</bdi> · {common.generation}: {data.generation}
          </p>
          {data.state === 'unconfigured' && <p>{copy.disabled}</p>}
          {data.authority === 'member' && <p>{copy.readonly}</p>}
          {data.managed && <p>{common.managed}</p>}
          {configuration && (
            <section aria-label={copy.period}>
              <h3>{copy.period}</h3>
              <p>
                <bdi>{configuration.periodId}</bdi> · {periodState && copy[periodState]}
              </p>
              <p>
                <LocalizedTime value={configuration.startsAt} /> – <LocalizedTime value={configuration.endsAt} />
              </p>
            </section>
          )}
          {data.authority === 'installation-owner' && data.pool && (
            <Projection title={copy.projection} value={data.pool} />
          )}
          {data.member && <Projection title={copy.allocation} value={data.member} />}
          {data.pacing && (
            <section aria-label={copy.pacing}>
              <h3>{copy.pacing}</h3>
              <p>
                {copy[data.pacing.mode]} · {copy.spacing}: {data.pacing.minIntervalSeconds}
              </p>
              {data.pacing.deferUntil && (
                <p>
                  {copy.deferUntil}: <LocalizedTime value={data.pacing.deferUntil} />
                </p>
              )}
              <p>{copy.pacingNote}</p>
            </section>
          )}
          {observation && (
            <section aria-label={copy.manual}>
              <h3>{copy.manual}</h3>
              <p>
                {common.remainingPercent}: {observation.remainingPercent.toLocaleString(locale)}%
              </p>
              <p>
                {common.asOf}: <LocalizedTime value={observation.asOf} />
                <br />
                {common.expiresAt}: <LocalizedTime value={observation.expiresAt} />
              </p>
              {observation.status && <p>{copy[observation.status]}</p>}
              <p>{copy.observationNote}</p>
            </section>
          )}
        </>
      )}
      <div className="eid-actions">
        <Button
          disabled={policy.view.busy || policy.metadata.busy}
          onClick={() => void policy.refresh()}
          variant="ghost"
        >
          {common.refresh}
        </Button>
        {data?.authority === 'installation-owner' &&
          (['configuration', 'observation'] as const).map(
            action =>
              data.capabilities[action] && (
                <Button
                  disabled={policy.view.busy || Boolean(policy.view.intent)}
                  key={action}
                  onClick={() => edit(action)}
                  variant="secondary"
                >
                  {action === 'configuration' ? copy.edit : copy.observe}
                </Button>
              )
          )}
        {data?.authority === 'installation-owner' && policy.view.intent && (
          <Button
            disabled={policy.view.busy || !data.capabilities[policy.view.intent.action]}
            onClick={() => edit(policy.view.intent!.action)}
            variant="secondary"
          >
            {common.reconcile}
          </Button>
        )}
      </div>
      {openFor?.ownerKey === ownerKey && openFor.scopeKey === scopeKey && (
        <SharedAllowanceEditor
          action={openFor.action}
          client={policy.client}
          descriptor={openFor.descriptor}
          key={JSON.stringify([
            ownerKey,
            openFor.descriptor.organizationId,
            openFor.descriptor.ownerId,
            openFor.action
          ])}
          onClose={() => setOpenFor(null)}
          ownerKey={ownerKey}
        />
      )}
    </section>
  )
}
