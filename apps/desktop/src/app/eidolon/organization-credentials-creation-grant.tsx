import { useId, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useI18n } from '@/i18n/context'
import { organizationCredentialsCopy } from '@/i18n/organization-credentials'

import type { OrganizationCredentialsClient } from './organization-credentials-client'
import { validCredentialCreateScope } from './organization-credentials-contract'
import type { CredentialCatalog } from './organization-credentials-types'

interface CreationGrantProps {
  catalog: CredentialCatalog
  client: OrganizationCredentialsClient
  busy: boolean
  onClose: () => void
}

export function CredentialCreationGrantEditor({ catalog, client, busy, onClose }: CreationGrantProps) {
  const { locale } = useI18n()
  const copy = organizationCredentialsCopy[locale]
  const id = useId()
  const [actorId, setActorId] = useState('')
  const [fields, setFields] = useState({ service: '', label: '', type: '', kind: 'secret' as 'secret' | 'config' })
  const [allowCreate, setAllowCreate] = useState(false)
  const [review, setReview] = useState<{ scope: string; actorId: string; revision: number; allowCreate: boolean } | null>(null)
  const scope = `create:${fields.service.trim()}:${fields.label.trim()}:${fields.type.trim()}:${fields.kind}`
  const grant = catalog.grants.find(item => item.actor === actorId && item.scope === scope)
  const revision = grant?.revision ?? 0
  const valid = validCredentialCreateScope(scope) && catalog.agents.some(agent => agent.identityId === actorId)
  const reviewed = Boolean(review && review.scope === scope && review.actorId === actorId && review.revision === revision && review.allowCreate === allowCreate)

  return <Dialog onOpenChange={open => !open && !busy && onClose()} open>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{copy.manageCreation}</DialogTitle>
        <DialogDescription>{copy.creationNote}</DialogDescription>
      </DialogHeader>
      <form className="grid gap-4" onSubmit={event => {
        event.preventDefault()

        if (valid && reviewed && review && !busy) {
          void client.configureGrant(review.actorId, review.scope, review.revision, review.allowCreate ? ['create'] : [])
            .then(saved => {
              // Retain uncertain intent in the client, but expose its recovery controls outside this modal.
              if (saved || client.getSnapshot().intent) { onClose() }
            })
        }
      }}>
        <div className="grid gap-1 text-sm">
          <label htmlFor={`${id}-actor`}>{copy.actor}</label>
          <Select disabled={busy} onValueChange={value => { setActorId(value); setReview(null) }} value={actorId}>
            <SelectTrigger id={`${id}-actor`}><SelectValue placeholder={copy.selectAgent} /></SelectTrigger>
            <SelectContent>{catalog.agents.map(agent => <SelectItem key={agent.identityId} value={agent.identityId}><bdi>{agent.name}</bdi> · <bdi>{agent.identityId}</bdi></SelectItem>)}</SelectContent>
          </Select>
        </div>
        <p className="text-xs text-muted-foreground">{copy.naming}</p>
        <div className="grid gap-3 sm:grid-cols-3">{(['service', 'label', 'type'] as const).map(field => <label className="grid gap-1 text-sm" htmlFor={`${id}-${field}`} key={field}>
          {copy[field]}
          <Input autoCapitalize="none" autoComplete="off" dir="ltr" disabled={busy} id={`${id}-${field}`} maxLength={48}
            onChange={event => { setFields(current => ({ ...current, [field]: event.target.value })); setReview(null) }} required spellCheck={false} value={fields[field]} />
        </label>)}</div>
        <fieldset className="grid gap-2" disabled={busy}>
          <legend className="mb-2 text-sm">{copy.kind}</legend>
          <SegmentedControl disabled={busy} onChange={kind => { setFields(current => ({ ...current, kind })); setReview(null) }}
            options={[{ id: 'secret', label: copy.secret }, { id: 'config', label: copy.config }]} value={fields.kind} />
        </fieldset>
        <div className="grid gap-1 text-xs text-muted-foreground">
          <p className="break-words">{copy.scope}: <bdi>{scope}</bdi></p>
          <p>{copy.revision}: {revision}</p>
          <p>{copy.permissions}: <bdi>{grant?.permissions.join(', ') || copy.none}</bdi></p>
        </div>
        <label className="flex items-start gap-2 text-sm" htmlFor={`${id}-allow`}>
          <Checkbox checked={allowCreate} disabled={busy || !valid} id={`${id}-allow`} onCheckedChange={checked => { setAllowCreate(checked === true); setReview(null) }} />
          {copy.allowCreate}
        </label>
        {!allowCreate && <p className="text-sm">{copy.grantEmpty}</p>}
        <label className="flex items-start gap-2 text-sm" htmlFor={`${id}-review`}>
          <Checkbox checked={reviewed} disabled={busy || !valid} id={`${id}-review`} onCheckedChange={checked => setReview(checked === true ? { scope, actorId, revision, allowCreate } : null)} />
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
