import './organization-owner-policy.css'
import './organization-shared-allowance.css'

import { useEffect, useRef, useState, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { useI18n } from '@/i18n/context'
import { ownerPolicyCopy } from '@/i18n/organization-owner-policy'
import { sharedAllowanceCopy } from '@/i18n/organization-shared-allowance'

import { policyEqual } from './organization-owner-policy-contract'
import type { SharedAllowanceClient } from './organization-shared-allowance-client'
import {
  emptySharedAllowance,
  SharedAllowanceFields,
  SharedObservationFields
} from './organization-shared-allowance-fields'
import type {
  AllowanceAllocation,
  SharedAllowanceAction,
  SharedAllowanceConfiguration,
  SharedAllowanceDescriptor,
  SharedAllowanceRequest
} from './organization-shared-allowance-types'

interface AllowanceDraft {
  candidate: Pick<SharedAllowanceRequest, 'configuration' | 'observation'>
  base: SharedAllowanceConfiguration | null
}
const drafts = new Map<string, AllowanceDraft>()

const initialDraft = (data: SharedAllowanceDescriptor, action: SharedAllowanceAction): AllowanceDraft => ({
  candidate:
    action === 'configuration'
      ? { configuration: structuredClone(data.configuration ?? emptySharedAllowance()) }
      : { observation: { remainingPercent: NaN, asOf: '', expiresAt: '', resetAt: null } },
  base: structuredClone(data.configuration)
})

const allocationIdentity = (item: AllowanceAllocation) =>
  JSON.stringify([item.profile, item.organizationId, item.ownerId])

/** Preserve fresh unedited values without silently re-enrolling a removed member. */
export function sharedAllowanceCandidate(draft: AllowanceDraft, data: SharedAllowanceDescriptor) {
  const next = draft.candidate.configuration
  const before = draft.base
  const now = data.configuration

  if (!next || !before || !now) {
    return draft.candidate
  }

  const overlay = <T extends object>(candidate: T, base: T, fresh: T): T =>
    Object.fromEntries(
      Object.entries(candidate).map(([key, value]) => [
        key,
        policyEqual(value, base[key as keyof T]) ? fresh[key as keyof T] : value
      ])
    ) as T

  const allocations: AllowanceAllocation[] = []
  const identities = new Set([...next.allocations, ...now.allocations].map(allocationIdentity))

  for (const id of identities) {
    const candidate = next.allocations.find(item => allocationIdentity(item) === id)
    const base = before.allocations.find(item => allocationIdentity(item) === id)
    const fresh = now.allocations.find(item => allocationIdentity(item) === id)

    if (candidate && !base) {
      allocations.push(candidate)
    } else if (candidate && base && fresh) {
      allocations.push({
        ...fresh,
        limits: overlay(candidate.limits, base.limits, fresh.limits),
        protected: overlay(candidate.protected, base.protected, fresh.protected)
      })
    } else if (candidate && base && !fresh && !policyEqual(candidate, base)) {
      allocations.push(candidate)
    } else if (!base && fresh) {
      allocations.push(fresh)
    }
  }

  return {
    configuration: {
      ...overlay(next, before, now),
      limits: overlay(next.limits, before.limits, now.limits),
      protected: overlay(next.protected, before.protected, now.protected),
      allocations
    }
  }
}

export function SharedAllowanceEditor({
  client,
  action,
  ownerKey,
  descriptor,
  onClose
}: {
  client: SharedAllowanceClient
  action: SharedAllowanceAction
  ownerKey: string
  descriptor: SharedAllowanceDescriptor
  onClose: () => void
}) {
  const { locale } = useI18n()
  const copy = sharedAllowanceCopy[locale]
  const common = ownerPolicyCopy[locale]
  const view = useSyncExternalStore(client.subscribe, client.getSnapshot)
  const draftKey = JSON.stringify([ownerKey, descriptor.organizationId, descriptor.ownerId, action])
  const [draft, setDraft] = useState(() => drafts.get(draftKey) ?? initialDraft(descriptor, action))
  const lifecycle = useRef({ active: true })
  const closing = useRef(false)
  const returnFocus = useRef(document.activeElement instanceof HTMLElement ? document.activeElement : null)
  const reviewFocus = useRef<HTMLElement | null>(null)
  const focusRequested = useRef({ requested: false })
  useEffect(() => {
    const mounted = lifecycle.current
    mounted.active = true

    return () => {
      mounted.active = false
      client.discardPreview()
    }
  }, [client])
  const data = view.descriptor

  const matching = Boolean(
    data &&
    data.organizationId === descriptor.organizationId &&
    data.ownerId === descriptor.ownerId &&
    data.authority === 'installation-owner'
  )

  const disabled = !matching || view.busy || Boolean(view.intent) || !data?.capabilities[action]
  const preview = view.preview?.action === action ? view.preview : null
  useEffect(() => {
    if (preview && focusRequested.current.requested && reviewFocus.current) {
      focusRequested.current.requested = false
      reviewFocus.current.focus()
      reviewFocus.current.scrollIntoView?.({ block: 'start' })
    }
  }, [preview])

  const update = (candidate: AllowanceDraft['candidate']) => {
    const next = { ...draft, candidate }
    drafts.set(draftKey, next)
    setDraft(next)
    client.discardPreview()
  }

  const close = () => {
    closing.current = true
    client.discardPreview()
    onClose()
  }

  const apply = async () => {
    const receipt = await client.apply()

    if (!receipt) {
      return
    }

    drafts.delete(draftKey)
    const fresh = client.getSnapshot().descriptor

    if (
      lifecycle.current.active &&
      fresh?.organizationId === descriptor.organizationId &&
      fresh.ownerId === descriptor.ownerId
    ) {
      setDraft(initialDraft(fresh, action))
    }
  }

  const warningText = {
    owner_declared_allowance: copy.disclaimer,
    configured_prices_not_invoice: common.configured_prices_not_invoice,
    no_refunds_or_auto_reset: copy.noRollover,
    existing_local_limits_still_apply: copy.description,
    enrollment_requires_idle: copy.enrollmentRequiresIdle
  }

  const pending = view.intent

  const pendingMatches =
    pending?.expectedOrganizationId === data?.organizationId &&
    pending?.expectedOwnerId === data?.ownerId &&
    pending?.action === action

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
        className="eidolon eid-owner-editor eid-policy-editor eid-allowance-editor"
        onCloseAutoFocus={event => {
          event.preventDefault()

          if (closing.current && returnFocus.current?.isConnected) {
            returnFocus.current.focus()
          }
        }}
        showCloseButton={false}
      >
        <DialogTitle>{action === 'configuration' ? copy.edit : copy.observe}</DialogTitle>
        <DialogDescription>{common.closeNote}</DialogDescription>
        <form
          onSubmit={event => {
            event.preventDefault()

            if (!disabled && data && !view.error) {
              focusRequested.current.requested = true
              void client.preview(action, sharedAllowanceCandidate(draft, data))
            }
          }}
        >
          <div className="eid-policy-scroll">
            <p>{copy.disclaimer}</p>
            <p>
              {common.scope}: <bdi>{descriptor.scope.profile}</bdi> · {common.generation}:{' '}
              {data?.generation ?? descriptor.generation}
            </p>
            <p>
              {common.organizationId}: <bdi>{descriptor.organizationId}</bdi>
              <br />
              {common.ownerId}: <bdi>{descriptor.ownerId}</bdi>
            </p>
            <p className="eid-policy-source">
              {common.source}: <bdi>{data?.sourceRevision ?? descriptor.sourceRevision}</bdi>
            </p>
            <fieldset disabled={disabled}>
              {action === 'configuration' ? (
                <SharedAllowanceFields
                  descriptor={matching ? data! : descriptor}
                  update={configuration => update({ configuration })}
                  value={draft.candidate.configuration!}
                />
              ) : (
                <SharedObservationFields
                  update={observation => update({ observation })}
                  value={draft.candidate.observation!}
                />
              )}
            </fieldset>
            {preview && (
              <section aria-label={common.review} className="eid-policy-review" ref={reviewFocus} tabIndex={-1}>
                <h3>{common.review}</h3>
                <p>
                  {common.generation}: {preview.value.generation}
                </p>
                <dl className="eid-policy-changes">
                  {preview.value.changes.map(change => (
                    <div key={change.field}>
                      <dt>{action === 'configuration' ? copy.title : copy.manual}</dt>
                      <dd>
                        <strong>{common.before}</strong>
                        <pre dir="ltr">{JSON.stringify(change.before, null, 2)}</pre>
                        <strong>{common.after}</strong>
                        <pre dir="ltr">{JSON.stringify(change.after, null, 2)}</pre>
                      </dd>
                    </div>
                  ))}
                </dl>
                <ul>
                  {preview.value.warnings.map(warning => (
                    <li key={warning.code}>{warningText[warning.code]}</li>
                  ))}
                </ul>
              </section>
            )}
            {pending && (
              <section aria-label={common.pendingChange} className="eid-policy-review">
                <h3>{common.pendingChange}</h3>
                <p>
                  {common.generation}: {pending.expectedGeneration}
                </p>
                <pre dir="ltr">{JSON.stringify(pending.configuration ?? pending.observation, null, 2)}</pre>
              </section>
            )}
            {view.busy && <p role="status">{pending ? common.saving : common.loading}</p>}
            {view.error && (
              <p role="alert">{view.error in copy ? copy[view.error as keyof typeof copy] : common[view.error]}</p>
            )}
            {pending && !pendingMatches && <p role="alert">{common.unavailable}</p>}
            {view.receipt && !pending && <p role="status">{common.saved}</p>}
          </div>
          <div className="eid-actions">
            <Button disabled={view.busy} onClick={() => void client.read()} type="button" variant="ghost">
              {common.refresh}
            </Button>
            <Button onClick={close} type="button" variant="ghost">
              {common.close}
            </Button>
            {pending ? (
              <Button
                disabled={view.busy || !matching || !pendingMatches || !data?.capabilities[action]}
                onClick={() => void apply()}
                type="button"
              >
                {common.reconcile}
              </Button>
            ) : (
              <>
                <Button disabled={disabled || Boolean(view.error)} type="submit" variant="secondary">
                  {common.preview}
                </Button>
                <Button
                  disabled={disabled || !preview || Boolean(view.error)}
                  onClick={() => void apply()}
                  type="button"
                >
                  {common.apply}
                </Button>
              </>
            )}
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
