import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n/context'
import { organizationCredentialsCopy } from '@/i18n/organization-credentials'

import { LocalizedTime } from './localized-time'
import { CredentialHandoffPanel } from './organization-credential-handoff'
import { CredentialCreationGrantEditor } from './organization-credentials-creation-grant'
import { CredentialMetadataEditor, CredentialValueEditor } from './organization-credentials-editor'
import { CredentialGrantEditor } from './organization-credentials-grants'
import { useOrganizationCredentials } from './organization-credentials-hook'
import type { CredentialEntry } from './organization-credentials-types'

interface EditorTarget {
  kind: 'metadata' | 'value' | 'revoke' | 'remove' | 'grant' | 'createGrant' | 'cancelPending'
  entry: CredentialEntry | null
  operationId?: string
}

function CredentialCatalogPanel({ credentials }: { credentials: ReturnType<typeof useOrganizationCredentials> }) {
  const { locale } = useI18n()
  const copy = organizationCredentialsCopy[locale]
  const { view, client } = credentials
  const catalog = view.catalog
  const [target, setTarget] = useState<EditorTarget | null>(null)
  const close = () => setTarget(null)
  const blocked = view.busy || Boolean(view.intent)
  const canSubmit = catalog?.secureInputAvailable && catalog.backend.state === 'available'

  return <>
    <div className="flex flex-wrap items-center gap-2">
      <Button disabled={view.busy} onClick={() => void credentials.refresh()} variant="ghost">{copy.refresh}</Button>
      <Button disabled={blocked || !catalog?.canCreate} onClick={() => setTarget({ kind: 'metadata', entry: null })} variant="secondary">{copy.create}</Button>
    </div>
    {view.busy && <Loader aria-label={copy.title} className="size-6" />}
    {view.error && <div role="alert"><ErrorState title={copy[view.error === 'unavailable' ? 'unavailableError' : view.error]} /></div>}
    {view.intent && <div className="grid gap-2 text-sm" role="status">
      <p className="break-words">{copy.operation}: <bdi>{view.intent.operationId}</bdi></p>
      <Button disabled={view.busy} onClick={() => void client.reconcile()} variant="secondary">{copy.reconcile}</Button>
      <Button disabled={view.busy} onClick={() => setTarget({ kind: 'cancelPending', entry: null, operationId: view.intent!.operationId })} variant="ghost">{copy.cancelPending}</Button>
    </div>}
    {view.cancelledOperationId && <p className="break-words text-sm" role="status">{copy.cancelled}: <bdi>{view.cancelledOperationId}</bdi></p>}
    {view.receipt && <div className="grid gap-1 text-sm" role="status">
      <p>{copy.saved}</p>
      <p className="break-words">{copy.operation}: <bdi>{view.receipt.operationId}</bdi></p>
      {view.receipt.inFlight !== undefined && <p>{copy.inFlight}: {view.receipt.inFlight}</p>}
    </div>}
    {catalog && <>
      <p className="flex flex-wrap items-center gap-2 text-sm">
        {copy.backend}: <bdi>{catalog.backend.provider}</bdi>
        <Badge variant={catalog.backend.state === 'available' ? 'muted' : 'warn'}>{copy[catalog.backend.state]}</Badge>
      </p>
      {!canSubmit && <p className="text-sm text-muted-foreground">{copy.backendBlocked}</p>}
      {catalog.entries.length === 0 && <EmptyState description={copy.emptyNote} title={copy.noEntries} />}
      <ul className="grid gap-6">
        {catalog.entries.map(entry => <li className="grid min-w-0 gap-2" key={entry.id}>
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="min-w-0 break-words font-medium" dir="auto">{entry.name}</h4>
            <Badge variant={entry.status === 'available' ? 'muted' : 'warn'}>{copy[entry.status]}</Badge>
            <Badge variant="outline">{copy[entry.kind]}</Badge>
          </div>
          {entry.description && <p className="whitespace-pre-wrap break-words text-sm" dir="auto">{entry.description}</p>}
          <dl className="grid gap-1 text-xs text-muted-foreground">
            <div className="break-words"><dt className="inline">{copy.id}: </dt><dd className="inline"><bdi>{entry.id}</bdi></dd></div>
            <div><dt className="inline">{copy.version}: </dt><dd className="inline">{entry.version}</dd></div>
            <div><dt className="inline">{copy.revision}: </dt><dd className="inline">{entry.revision}</dd></div>
            <div><dt className="inline">{copy.inFlight}: </dt><dd className="inline">{entry.inFlight}</dd></div>
            <div><dt className="inline">{copy.updated}: </dt><dd className="inline"><LocalizedTime value={entry.updatedAt * 1000} /></dd></div>
          </dl>
          <p className="text-xs text-muted-foreground">{entry.expiresAt ? <LocalizedTime value={entry.expiresAt * 1000} /> : copy.noExpiry}</p>
          <div className="flex flex-wrap gap-2">
            <Button disabled={blocked || !entry.permissions.includes('edit_metadata')} onClick={() => setTarget({ kind: 'metadata', entry })} size="sm" variant="secondary">{copy.edit}</Button>
            <Button disabled={blocked || !canSubmit || !entry.permissions.includes('replace_value') || entry.status === 'revoked'} onClick={() => setTarget({ kind: 'value', entry })} size="sm" variant="secondary">{entry.version > 0 ? copy.replaceValue : copy.submitValue}</Button>
            <Button disabled={blocked || !entry.permissions.includes('revoke') || entry.status === 'revoked'} onClick={() => setTarget({ kind: 'revoke', entry })} size="sm" variant="ghost">{copy.revoke}</Button>
            <Button disabled={blocked || !entry.permissions.includes('remove') || entry.status !== 'revoked'} onClick={() => setTarget({ kind: 'remove', entry })} size="sm" variant="ghost">{copy.remove}</Button>
          </div>
        </li>)}
      </ul>
      <Button disabled={blocked || catalog.agents.length === 0 || catalog.entries.length === 0} onClick={() => setTarget({ kind: 'grant', entry: null })} variant="secondary">{copy.manageGrants}</Button>
      <Button disabled={blocked || catalog.agents.length === 0} onClick={() => setTarget({ kind: 'createGrant', entry: null })} variant="secondary">{copy.manageCreation}</Button>
      {catalog.agents.length === 0 && <p className="text-sm text-muted-foreground">{copy.noAgents}</p>}
      <details>
        <summary className="cursor-pointer text-sm font-medium">{copy.permissions}</summary>
        <ul className="mt-3 grid gap-4 text-xs">
          {catalog.grants.map((grant, index) => <li className="grid gap-1 break-words" key={JSON.stringify([grant.actor, grant.scope, index])}>
            <p>{copy.actor}: <bdi>{grant.actor}</bdi></p>
            <p>{copy.scope}: <bdi>{grant.scope}</bdi></p>
            <p>{copy.revision}: {grant.revision}</p>
            <ul className="flex flex-wrap gap-2">{grant.permissions.map(permission => <li key={permission}><bdi>{permission}</bdi></li>)}</ul>
            <p>{copy.bindings}:</p>
            {grant.bindings.length ? <ul>{grant.bindings.map((binding, position) => <li key={position}><bdi>{binding.tool}</bdi> · <bdi>{binding.destination}</bdi></li>)}</ul> : <p>{copy.none}</p>}
          </li>)}
        </ul>
        {catalog.grants.length === 0 && <p className="mt-2 text-sm text-muted-foreground">{copy.noGrants}</p>}
      </details>
    </>}
    <CredentialHandoffPanel credentials={credentials} />
    {target?.kind === 'cancelPending' && <ConfirmDialog
      confirmLabel={copy.cancelPending} description={<><bdi>{target.operationId}</bdi><br />{copy.cancelPendingNote}</>}
      onClose={close} onConfirm={async () => {
        if (!await client.cancelPending(target.operationId!)) {
          throw new Error(copy.uncertain)
        }
      }} open title={copy.cancelPending} />}
    {target?.kind === 'createGrant' && catalog && <CredentialCreationGrantEditor busy={blocked} catalog={catalog} client={client} onClose={close} />}
    {target?.kind === 'grant' && catalog && <CredentialGrantEditor busy={blocked} catalog={catalog} client={client} onClose={close} />}
    {target?.kind === 'metadata' && <CredentialMetadataEditor busy={blocked} client={client} entry={target.entry} onClose={close} />}
    {target?.kind === 'value' && target.entry && canSubmit && !blocked && <CredentialValueEditor client={client} entry={target.entry} onClose={close} />}
    {(target?.kind === 'revoke' || target?.kind === 'remove') && target.entry && <ConfirmDialog
      confirmLabel={copy[target.kind]} description={<><bdi>{target.entry.name}</bdi><br />{copy[target.kind === 'revoke' ? 'revokeNote' : 'removeNote']}</>}
      destructive onClose={close} onConfirm={async () => {
        const saved = await client.mutate(target.kind as 'revoke' | 'remove', target.entry!.id, target.entry!.revision)

        if (!saved) {
          throw new Error(copy[client.getSnapshot().error === 'unavailable' ? 'unavailableError' : client.getSnapshot().error ?? 'invalid'])
        }
      }} open title={copy[target.kind]} />}
  </>
}

export function OrganizationCredentialsSettings() {
  const credentials = useOrganizationCredentials()
  const { locale } = useI18n()
  const copy = organizationCredentialsCopy[locale]
  const eligible = credentials.scope.eligible && credentials.metadata.descriptor?.state === 'ready'

  return <section aria-label={copy.title} className="grid min-w-0 gap-4">
    <h3 className="text-base font-medium">{copy.title}</h3>
    <p className="text-sm text-muted-foreground">{copy.note}</p>
    <p className="text-sm text-muted-foreground">{copy.boundary}</p>
    {eligible ? <CredentialCatalogPanel credentials={credentials} key={credentials.identity} /> : <>
      <p role="status">{copy.unavailableError}</p>
      <Button disabled={credentials.metadata.busy} onClick={() => void credentials.refresh()} variant="ghost">{copy.refresh}</Button>
    </>}
  </section>
}
