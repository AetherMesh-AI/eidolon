import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n/context'
import { organizationCredentialHandoffCopy } from '@/i18n/organization-credential-handoff'
import { organizationCredentialsCopy } from '@/i18n/organization-credentials'

import { LocalizedTime } from './localized-time'
import { CredentialHandoffEditor } from './organization-credential-handoff-editor'
import { useCredentialHandoff } from './organization-credential-handoff-hook'
import type { useOrganizationCredentials } from './organization-credentials-hook'

export function CredentialHandoffPanel({ credentials }: { credentials: ReturnType<typeof useOrganizationCredentials> }) {
  const { locale } = useI18n()
  const copy = organizationCredentialHandoffCopy[locale]
  const common = organizationCredentialsCopy[locale]
  const { client, view } = useCredentialHandoff(credentials.identity)
  const [editor, setEditor] = useState(false)
  const [cancelOperationId, setCancelOperationId] = useState<string | null>(null)
  const catalog = credentials.view.catalog
  const status = view.status

  const canEdit = catalog?.secureInputAvailable && catalog.backend.state === 'available' && !credentials.view.busy && !credentials.view.intent &&
    !view.retained && view.error !== 'storage'

  const canPrepare = canEdit && !view.busy

  useEffect(() => {
    if (status?.state === 'committed') { void credentials.client.read() }
  }, [status, credentials.client])

  return <section aria-label={copy.title} className="grid gap-3">
    <h4 className="text-sm font-medium">{copy.title}</h4>
    <p className="text-sm text-muted-foreground">{copy.note}</p>
    <Button disabled={!canPrepare || !catalog?.entries.some(entry => entry.permissions.includes('replace_value') && !['revoked', 'expired'].includes(entry.status))}
      onClick={() => setEditor(true)} variant="secondary">{copy.open}</Button>
    {view.busy && <Loader aria-label={copy.title} className="size-6" />}
    {view.error && !editor && (view.retained || view.error === 'storage') && <div role="alert"><ErrorState title={common[view.error === 'unavailable' ? 'unavailableError' : view.error]} /></div>}
    {view.retained && <div className="grid gap-2 text-sm">
      <p className="break-words">{common.operation}: <bdi>{view.retained.intent.operationId}</bdi></p>
      <p className="break-words">{common.id}: <bdi>{view.retained.intent.entryId}</bdi> · {common.revision}: {view.retained.intent.expectedRevision}</p>
      <p className="break-words">{common.actor}: <bdi>{view.retained.intent.actorId}</bdi> · {copy.generation}: <bdi>{view.retained.intent.generation}</bdi></p>
      {view.retained.preparation && <p>{common.expiresAt}: <LocalizedTime value={view.retained.preparation.expiresAt * 1000} /></p>}
      <div className="flex flex-wrap gap-2">
        <Button disabled={view.busy} onClick={() => void client.reconcile()} variant="secondary">{copy.check}</Button>
        <Button disabled={view.busy} onClick={() => setCancelOperationId(view.retained!.intent.operationId)} variant="ghost">{copy.cancel}</Button>
      </div>
    </div>}
    {status && <div className="grid gap-1 text-sm" role="status">
      <p>{copy[status.state]}</p>
      {(status.state === 'committed' || status.state === 'cancelled') && <p className="break-words">{common.operation}: <bdi>{status.operationId}</bdi></p>}
      {status.receipt?.action === 'replace_value' && <p>{common.revision}: {status.receipt.revision} · {common.version}: {status.receipt.version}</p>}
    </div>}
    {editor && catalog && canEdit && <CredentialHandoffEditor catalog={catalog} client={client} onClose={() => setEditor(false)} view={view} />}
    {cancelOperationId && <ConfirmDialog confirmLabel={copy.cancel} description={<><bdi>{cancelOperationId}</bdi><br />{copy.cancelNote}</>}
      onClose={() => setCancelOperationId(null)} onConfirm={async () => {
        if (!await client.cancel(cancelOperationId)) { throw new Error(common.uncertain) }
      }} open title={copy.cancel} />}
  </section>
}
