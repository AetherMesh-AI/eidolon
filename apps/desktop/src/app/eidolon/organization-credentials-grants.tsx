import { useId, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useI18n } from '@/i18n/context'
import { organizationCredentialsCopy } from '@/i18n/organization-credentials'

import type { OrganizationCredentialsClient } from './organization-credentials-client'
import { CREDENTIAL_GRANT_PERMISSIONS } from './organization-credentials-contract'
import type { CredentialCatalog } from './organization-credentials-types'

interface GrantEditorProps {
  catalog: CredentialCatalog
  client: OrganizationCredentialsClient
  busy: boolean
  onClose: () => void
}
interface GrantDraft {
  actorId: string
  entryId: string
  expectedRevision: number
  permissions: string[]
}

export function CredentialGrantEditor({ catalog, client, busy, onClose }: GrantEditorProps) {
  const { locale } = useI18n()
  const copy = organizationCredentialsCopy[locale]
  const id = useId()
  const [draft, setDraft] = useState<GrantDraft>({ actorId: '', entryId: '', expectedRevision: 0, permissions: [] })
  const [reviewed, setReviewed] = useState(false)
  const selectedGrant = catalog.grants.find(grant => grant.actor === draft.actorId && grant.scope === draft.entryId)
  const hasUnsupportedPermission = selectedGrant?.permissions.some(permission => !CREDENTIAL_GRANT_PERMISSIONS.includes(permission as typeof CREDENTIAL_GRANT_PERMISSIONS[number]))
  const revisionMatches = (selectedGrant?.revision ?? 0) === draft.expectedRevision

  const valid = draft.actorId && draft.entryId && revisionMatches && !hasUnsupportedPermission &&
    catalog.agents.some(agent => agent.identityId === draft.actorId) && catalog.entries.some(entry => entry.id === draft.entryId)

  const select = (actorId: string, entryId: string) => {
    const grant = catalog.grants.find(item => item.actor === actorId && item.scope === entryId)
    setDraft({ actorId, entryId, expectedRevision: grant?.revision ?? 0, permissions: [...(grant?.permissions ?? [])] })
    setReviewed(false)
  }

  return <Dialog onOpenChange={open => !open && !busy && onClose()} open>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{copy.manageGrants}</DialogTitle>
        <DialogDescription>{copy.grantNote}</DialogDescription>
      </DialogHeader>
      <form className="grid gap-4" onSubmit={event => {
        event.preventDefault()

        if (valid && reviewed && !busy) {
          void client.configureGrant(draft.actorId, draft.entryId, draft.expectedRevision, draft.permissions)
            .then(saved => {
              // Retain uncertain intent in the client, but expose its recovery controls outside this modal.
              if (saved || client.getSnapshot().intent) { onClose() }
            })
        }
      }}>
        <div className="grid gap-1 text-sm">
          <label htmlFor={`${id}-agent`}>{copy.actor}</label>
          <Select disabled={busy} onValueChange={actorId => select(actorId, draft.entryId)} value={draft.actorId}>
            <SelectTrigger id={`${id}-agent`}><SelectValue placeholder={copy.selectAgent} /></SelectTrigger>
            <SelectContent>{catalog.agents.map(agent => <SelectItem key={agent.identityId} value={agent.identityId}><bdi>{agent.name}</bdi> · <bdi>{agent.identityId}</bdi></SelectItem>)}</SelectContent>
          </Select>
        </div>
        <div className="grid gap-1 text-sm">
          <label htmlFor={`${id}-entry`}>{copy.scope}</label>
          <Select disabled={busy} onValueChange={entryId => select(draft.actorId, entryId)} value={draft.entryId}>
            <SelectTrigger id={`${id}-entry`}><SelectValue placeholder={copy.selectEntry} /></SelectTrigger>
            <SelectContent>{catalog.entries.map(entry => <SelectItem key={entry.id} value={entry.id}><bdi>{entry.name}</bdi> · <bdi>{entry.id}</bdi></SelectItem>)}</SelectContent>
          </Select>
        </div>
        <p className="text-xs text-muted-foreground">{copy.expectedRevision}: {draft.expectedRevision}</p>
        <p className="text-xs text-muted-foreground">{copy.grantUnavailable}</p>
        {!revisionMatches && <p role="alert">{copy.conflict}</p>}
        <fieldset className="grid gap-2" disabled={busy || !valid}>
          <legend className="mb-2 text-sm">{copy.permissions}</legend>
          {CREDENTIAL_GRANT_PERMISSIONS.map(permission => <label className="flex items-center gap-2 text-sm" htmlFor={`${id}-${permission}`} key={permission}>
            <Checkbox checked={draft.permissions.includes(permission)} disabled={busy || !valid} id={`${id}-${permission}`} onCheckedChange={checked => {
              setDraft(current => ({ ...current, permissions: checked === true ? [...current.permissions, permission] : current.permissions.filter(value => value !== permission) }))
              setReviewed(false)
            }} />
            <bdi>{permission}</bdi>
          </label>)}
        </fieldset>
        {draft.permissions.length === 0 && <p className="text-sm">{copy.grantEmpty}</p>}
        <label className="flex items-start gap-2 text-sm" htmlFor={`${id}-reviewed`}>
          <Checkbox checked={reviewed} disabled={busy || !valid} id={`${id}-reviewed`} onCheckedChange={checked => setReviewed(checked === true)} />
          {copy.grantReviewed}
        </label>
        <DialogFooter>
          <Button disabled={busy} onClick={onClose} type="button" variant="ghost">{copy.cancel}</Button>
          <Button disabled={busy || !valid || !reviewed} type="submit">{copy.grantSave}</Button>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>
}
