import { useCallback, useId, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n/context'
import { organizationCredentialsCopy } from '@/i18n/organization-credentials'

import type { OrganizationCredentialsClient } from './organization-credentials-client'
import { credentialMetadata, credentialName, validCredentialMetadata } from './organization-credentials-contract'
import type { CredentialEntry, CredentialMetadata } from './organization-credentials-types'

interface MetadataEditorProps {
  entry: CredentialEntry | null
  client: OrganizationCredentialsClient
  busy: boolean
  onClose: () => void
}

function localTime(value: number | null) {
  if (!value) {
    return ''
  }

  const date = new Date(value * 1000)

  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}

export function CredentialMetadataEditor({ entry, client, busy, onClose }: MetadataEditorProps) {
  const { locale } = useI18n()
  const copy = organizationCredentialsCopy[locale]
  const id = useId()

  const [draft, setDraft] = useState<CredentialMetadata>(() => entry ? credentialMetadata(entry) : {
    service: '', label: '', type: '', description: '', kind: 'secret', expiresAt: null
  })

  const [expiry, setExpiry] = useState(() => localTime(entry?.expiresAt ?? null))
  const expiryDate = expiry ? new Date(expiry) : null
  const expiryValid = !expiryDate || Number.isFinite(expiryDate.getTime())

  const candidate: CredentialMetadata = { ...draft, service: draft.service.trim(), label: draft.label.trim(), type: draft.type.trim(),
    expiresAt: entry ? entry.expiresAt : expiryDate && expiryValid ? expiryDate.getTime() / 1000 : null }

  const valid = expiryValid && validCredentialMetadata(candidate)

  return <Dialog onOpenChange={open => !open && !busy && onClose()} open>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{entry ? copy.edit : copy.create}</DialogTitle>
        <DialogDescription>{copy.naming}</DialogDescription>
      </DialogHeader>
      <form className="grid gap-4" onSubmit={event => {
        event.preventDefault()

        if (valid && !busy) {
          void client.mutate(entry ? 'edit_metadata' : 'create', entry?.id ?? null, entry?.revision ?? null, candidate)
            .then(saved => {
              // Retain uncertain intent in the client, but expose its recovery controls outside this modal.
              if (saved || client.getSnapshot().intent) { onClose() }
            })
        }
      }}>
        <div className="grid gap-3 sm:grid-cols-3">
          {(['service', 'label', 'type'] as const).map(field => <label className="grid gap-1 text-sm" htmlFor={`${id}-${field}`} key={field}>
            {copy[field]}
            <Input autoCapitalize="none" autoComplete="off" dir="ltr" disabled={busy} id={`${id}-${field}`} maxLength={48}
              onChange={event => setDraft(current => ({ ...current, [field]: event.target.value }))} required spellCheck={false} value={draft[field]} />
          </label>)}
        </div>
        <p className="break-words text-sm">{copy.name}: <bdi>{credentialName(candidate)}</bdi></p>
        <label className="grid gap-1 text-sm" htmlFor={`${id}-description`}>{copy.description}
          <Textarea dir="auto" disabled={busy} id={`${id}-description`} maxLength={1000} onChange={event => setDraft(current => ({ ...current, description: event.target.value }))} required value={draft.description} />
        </label>
        <fieldset className="grid gap-2" disabled={busy || Boolean(entry)}>
          <legend className="mb-2 text-sm">{copy.kind}</legend>
          <SegmentedControl disabled={busy || Boolean(entry)} onChange={kind => setDraft(current => ({ ...current, kind }))}
            options={[{ id: 'secret', label: copy.secret }, { id: 'config', label: copy.config }]} value={draft.kind} />
        </fieldset>
        <label className="grid gap-1 text-sm" htmlFor={`${id}-expiry`}>{copy.expiresAt}
          <Input disabled={busy || Boolean(entry)} id={`${id}-expiry`} onChange={event => setExpiry(event.target.value)} type="datetime-local" value={expiry} />
        </label>
        <DialogFooter>
          <Button disabled={busy} onClick={onClose} type="button" variant="ghost">{copy.cancel}</Button>
          <Button disabled={!valid || busy} type="submit">{entry ? copy.save : copy.create}</Button>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>
}

interface ValueEditorProps {
  entry: CredentialEntry
  client: OrganizationCredentialsClient
  onClose: () => void
}

export function CredentialValueEditor({ entry, client, onClose }: ValueEditorProps) {
  const { locale } = useI18n()
  const copy = organizationCredentialsCopy[locale]
  const id = useId()
  const input = useRef<HTMLInputElement>(null)
  const [hasValue, setHasValue] = useState(false)

  const bindInput = useCallback((field: HTMLInputElement | null) => {
    if (input.current && input.current !== field) {
      input.current.value = ''
    }

    input.current = field
  }, [])

  const close = () => {
    if (input.current) {
      input.current.value = ''
    }

    onClose()
  }

  return <Dialog onOpenChange={open => !open && close()} open>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{entry.version > 0 ? copy.replaceValue : copy.submitValue}</DialogTitle>
        <DialogDescription>{copy.secureNote}</DialogDescription>
      </DialogHeader>
      <p className="break-words text-sm"><bdi>{entry.name}</bdi></p>
      <p className="break-words text-xs text-muted-foreground">{copy.id}: <bdi>{entry.id}</bdi> · {copy.revision}: {entry.revision}</p>
      <form autoComplete="off" className="grid gap-4" onSubmit={event => {
        event.preventDefault()
        let value = input.current?.value ?? ''

        if (!value) {
          return
        }

        if (input.current) {
          input.current.value = ''
        }

        // The field is deliberately uncontrolled: secret bytes never enter React state.
        void client.mutate('replace_value', entry.id, entry.revision, null, value)
        value = ''
        close()
      }}>
        <label className="grid gap-1 text-sm" htmlFor={`${id}-value`}>{copy.value}
          <Input autoCapitalize="none" autoComplete="new-password" dir="ltr" id={`${id}-value`} maxLength={65536}
            onChange={event => setHasValue(Boolean(event.target.value))} ref={bindInput} required spellCheck={false} type="password" />
        </label>
        <DialogFooter>
          <Button onClick={close} type="button" variant="ghost">{copy.cancel}</Button>
          <Button disabled={!hasValue} type="submit">{entry.version > 0 ? copy.replaceValue : copy.submitValue}</Button>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>
}
