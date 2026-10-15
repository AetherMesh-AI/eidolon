import { useEffect, useId, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n/context'
import { folderAccessCopy } from '@/i18n/organization-folder-access'

import { createFolderAccessClient, type FolderAccess, FolderAccessError, type FolderAccessIntent, type FolderGrant } from './organization-folder-access-client'
import { organizationMetadataClient, readOrganizationMetadataScope, useOrganizationMetadata } from './use-organization-metadata'

function source() {
  const scope = readOrganizationMetadataScope()
  const metadata = organizationMetadataClient.getSnapshot().descriptor?.metadata

  return { ...scope, organizationId: metadata?.organizationId ?? null, ownerId: metadata?.owner.ownerId ?? null }
}

interface FolderAccessProps {
  onChanged(): void
  onConfirmationChange?: (cancel: (() => void) | null) => void
}
interface Confirmation { action: 'revoke' | 'review'; grant: FolderGrant }

/** This remains available even when missing source folders break folder setup. */
export function OrganizationFolderAccess({ onChanged, onConfirmationChange }: FolderAccessProps) {
  const { locale } = useI18n()
  const copy = folderAccessCopy[locale]
  const metadata = useOrganizationMetadata()
  const client = useRef(createFolderAccessClient(source)).current
  const reportConfirmation = useRef(onConfirmationChange).current
  const [descriptor, setDescriptor] = useState<FolderAccess | null>(null)
  const [retained, setRetained] = useState<FolderAccessIntent | null>(null)
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState<'revoke' | 'review' | null>(null)
  const active = useRef(true)
  const pending = useRef(false)
  const started = useRef(false)
  const acknowledgmentId = useId()
  const eligible = metadata.scope.eligible && !!metadata.view.descriptor?.metadata
  const current = !started.current || client.current()
  const blocked = !!descriptor && (descriptor.executionStillStopping || descriptor.unsettledProjectRuns.length > 0)
  const hasUncertain = !!descriptor && (descriptor.uncertainToolReceipts.length > 0 || descriptor.uncertainSourceRequests.length > 0)
  const unavailable = !eligible || !current || busy
  const errorMessage = (reason: unknown) => reason instanceof FolderAccessError ? copy[reason.code] : reason instanceof Error ? reason.message : copy.failure

  const read = async () => {
    if (pending.current || !eligible) { return }
    started.current = true
    pending.current = true
    setBusy(true); setError(''); setDescriptor(null); setAcknowledged(false)

    try {
      const value = await client.read()

      if (active.current && client.current()) { setDescriptor(value) }
    } catch (reason) {
      if (active.current && client.current()) { setError(errorMessage(reason)) }
    } finally {
      pending.current = false

      if (active.current) { setBusy(false) }
    }
  }

  // eslint-disable-next-line no-restricted-syntax -- mount lifetime fences late async results
  useEffect(() => {
    active.current = true

    return () => { active.current = false; reportConfirmation?.(null) }
  }, [reportConfirmation])

  useEffect(() => {
    if (eligible && !started.current) { void read() }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- first eligible read only; no automatic owner rebinding
  }, [eligible])

  const closeConfirmation = () => { setConfirmation(null); reportConfirmation?.(null) }

  const openConfirmation = (action: Confirmation['action'], grant: FolderGrant) => {
    if (unavailable || retained || !client.current()) { return }
    setConfirmation({ action, grant })
    reportConfirmation?.(closeConfirmation)
  }

  const execute = async (operation: () => Promise<'revoke' | 'review'>) => {
    if (pending.current || !active.current || !client.current()) { return }
    pending.current = true
    setBusy(true); setError(''); setSaved(null)
    closeConfirmation()

    try {
      const action = await operation()

      if (!active.current || !client.current()) { return }
      setSaved(action); setRetained(null); setDescriptor(null); setAcknowledged(false)
      onChanged()
      const value = await client.read()

      if (active.current && client.current()) { setDescriptor(value) }
    } catch (reason) {
      if (active.current && client.current()) {
        setError(errorMessage(reason)); setRetained(client.pending())

        if (reason instanceof FolderAccessError && reason.code === 'conflict') { setDescriptor(null); setAcknowledged(false) }
      }
    } finally {
      pending.current = false

      if (active.current) { setBusy(false) }
    }
  }

  return <section aria-busy={busy} aria-label={copy.title} className="eid-resolution-form">
    <h3>{copy.title}</h3>
    <p className="eid-note">{copy.note}</p>
    {!eligible && <p role="status">{copy.unavailable}</p>}
    {!current && <p role="alert">{copy.changed}</p>}
    {busy && <Loader label={copy.loading} />}
    {error && current && <div role="alert"><ErrorState description={error} title={copy.failure} /></div>}
    {saved && current && <p role="status">{saved === 'revoke' ? copy.revokeSaved : copy.reviewSaved}</p>}
    {retained && current && <div>
      <p role="status">{copy.uncertain}</p>
      <p dir="auto">{retained.grant.alias} · {retained.grant.path}</p>
      <p>{copy.expectedGeneration}: {retained.params.expectedGeneration}</p>
      <Button disabled={unavailable || (retained.action === 'review' && (!descriptor || blocked))} onClick={() => void execute(client.retry)} type="button">{copy.retry}</Button>
    </div>}
    {descriptor && current && <>
      <p className="eid-note">{copy.generation}: {descriptor.policyGeneration}</p>
      {descriptor.paused && <p role="status">{copy.paused}</p>}
      {descriptor.executionStillStopping && <p role="status">{copy.stopping}</p>}
      {!!descriptor.unsettledProjectRuns.length && <>
        <p role="status">{copy.unsettled}</p>
        <ul aria-label={copy.unsettled}>{descriptor.unsettledProjectRuns.map(run => <li dir="auto" key={run.id}>{run.id} · {run.request_id}</li>)}</ul>
      </>}
      {hasUncertain && <>
        {!!descriptor.uncertainToolReceipts.length && <h4>{copy.unknown}</h4>}
        <p className="eid-note">{copy.unknownNote}</p>
        <ul aria-label={copy.unknown}>{descriptor.uncertainToolReceipts.map(receipt => <li dir="auto" key={receipt.id}>{receipt.id} · {receipt.request_id}</li>)}</ul>
        {!!descriptor.uncertainSourceRequests.length && <>
          <h4>{copy.unknownSource}</h4>
          <ul aria-label={copy.unknownSource}>{descriptor.uncertainSourceRequests.map(request => <li dir="auto" key={request.id}>{request.id} · {request.request_id}</li>)}</ul>
        </>}
        {descriptor.grants.some(grant => grant.revoked && !grant.reviewed) && <label className="flex items-start gap-2" htmlFor={acknowledgmentId}>
          <Checkbox checked={acknowledged} disabled={unavailable || !!retained || !!confirmation} id={acknowledgmentId} onCheckedChange={value => setAcknowledged(value === true)} />{copy.acknowledge}
        </label>}
      </>}
      {descriptor.grants.length ? <ul aria-label={copy.title}>{descriptor.grants.map(grant => <li className="space-y-2" key={grant.alias}>
        <p dir="auto">{grant.alias} · {grant.path}</p>
        <p>{grant.reviewed ? copy.reviewed : grant.revoked ? copy.revoked : copy.active}</p>
        <details>
          <summary>{copy.identity}</summary>
          <p>{copy.device}: {grant.device} · {copy.inode}: {grant.inode}</p>
          <p className="break-all">{copy.grantHash}: <bdi>{grant.grantHash}</bdi></p>
          {!!grant.sourcePrefix.length && <p>{copy.sourcePrefix}: <bdi>{grant.sourcePrefix.join(', ')}</bdi></p>}
        </details>
        {!grant.revoked && <Button disabled={unavailable || !!retained || !!confirmation} onClick={() => openConfirmation('revoke', grant)} type="button" variant="destructive">{copy.revoke}</Button>}
        {grant.revoked && !grant.reviewed && <Button disabled={unavailable || !!retained || !!confirmation || blocked || (hasUncertain && !acknowledged)} onClick={() => openConfirmation('review', grant)} type="button">{copy.resume}</Button>}
      </li>)}</ul> : <p>{copy.empty}</p>}
    </>}
    <Button disabled={unavailable || !!confirmation} onClick={() => void read()} type="button" variant="secondary">{copy.read}</Button>
    {confirmation && current && <ConfirmDialog
      confirmLabel={confirmation.action === 'revoke' ? copy.revokeConfirm : copy.resume}
      description={<>
        {confirmation.action === 'revoke' ? copy.revokeDisclosure : copy.reviewDisclosure}<br />
        <bdi>{confirmation.grant.alias} · {confirmation.grant.path}</bdi><br />
        {copy.device}: {confirmation.grant.device} · {copy.inode}: {confirmation.grant.inode}<br />
        {copy.expectedGeneration}: {descriptor?.policyGeneration}
      </>}
      destructive={confirmation.action === 'revoke'}
      dismissOnConfirm
      onClose={closeConfirmation}
      onConfirm={() => void execute(() => confirmation.action === 'revoke' ? client.revoke(confirmation.grant.alias, true) : client.review(confirmation.grant.alias, true, acknowledged))}
      open
      title={confirmation.action === 'revoke' ? copy.revokeConfirm : copy.resume}
    />}
  </section>
}
