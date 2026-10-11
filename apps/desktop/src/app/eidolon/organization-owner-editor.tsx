import { useEffect, useRef, useState, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { useI18n } from '@/i18n/context'

import type { createOrganizationMetadataClient } from './organization-metadata-client'

interface IdentityDraft {
  displayName: string
  ownerDisplayName: string
}
// Names and uncertain intents are window-owned; no credentials or chat history.
const drafts = new Map<string, IdentityDraft>()
interface OrganizationOwnerEditorProps {
  client: ReturnType<typeof createOrganizationMetadataClient>
  draftKey: string
  onClose: () => void
  onReturnFocus: () => void
}

export function OrganizationOwnerEditor(props: OrganizationOwnerEditorProps) {
  return <ScopedOrganizationOwnerEditor key={props.draftKey} {...props} />
}

function ScopedOrganizationOwnerEditor({ client, draftKey, onClose, onReturnFocus }: OrganizationOwnerEditorProps) {
  const { t } = useI18n()
  const copy = t.organizationMetadata
  const view = useSyncExternalStore(client.subscribe, client.getSnapshot)
  const data = view.descriptor?.metadata
  const original = view.intent

  const [draft, setDraft] = useState<IdentityDraft>(() =>
    original
      ? { displayName: original.displayName, ownerDisplayName: original.ownerDisplayName }
      : (drafts.get(draftKey) ?? {
          displayName: data?.displayName ?? '',
          ownerDisplayName: data?.owner.displayName ?? ''
        })
  )

  useEffect(() => {
    if (!drafts.has(draftKey) && !original && data) {
      setDraft({ displayName: data.displayName, ownerDisplayName: data.owner.displayName })
    }
  }, [data, draftKey, original])
  const mounted = useRef({ active: true })
  useEffect(() => {
    const lifecycle = mounted.current
    lifecycle.active = true

    return () => {
      lifecycle.active = false
    }
  }, [])

  const update = (next: IdentityDraft) => {
    drafts.set(draftKey, next)
    setDraft(next)
  }

  const blockedRetry = Boolean(
    original &&
    (!data || (original.expectedOrganizationId !== null && data.organizationId !== original.expectedOrganizationId))
  )

  const disabled = view.busy || Boolean(original) || view.descriptor?.state === 'unsupported' || !view.descriptor

  const saveDisabled =
    view.busy ||
    blockedRetry ||
    (!original && (Boolean(view.error) || !draft.displayName.trim() || !draft.ownerDisplayName.trim())) ||
    !view.descriptor ||
    view.descriptor.state === 'unsupported'

  const submit = async () => {
    const saved = await client.save(draft.displayName, draft.ownerDisplayName)

    if (saved) {
      drafts.delete(draftKey)

      if (mounted.current.active) {
        onClose()
      }
    }
  }

  return (
    <Dialog
      onOpenChange={open => {
        if (!open) {
          onClose()
        }
      }}
      open
    >
      <DialogContent
        className="eidolon eid-owner-editor"
        onCloseAutoFocus={event => {
          event.preventDefault()
          onReturnFocus()
        }}
      >
        <DialogTitle>{copy.title}</DialogTitle>
        <DialogDescription>{copy.note}</DialogDescription>
        <form
          onSubmit={event => {
            event.preventDefault()

            if (!saveDisabled) {
              void submit()
            }
          }}
        >
          <label>
            {copy.organizationName}
            <Input
              disabled={disabled}
              maxLength={160}
              onChange={event => update({ ...draft, displayName: event.target.value })}
              value={draft.displayName}
            />
          </label>
          <label>
            {copy.ownerName}
            <Input
              disabled={disabled}
              maxLength={160}
              onChange={event => update({ ...draft, ownerDisplayName: event.target.value })}
              value={draft.ownerDisplayName}
            />
          </label>
          {data && (
            <p>
              {copy.revision(data.revision)} · {copy.savedNames(data.displayName, data.owner.displayName)}
            </p>
          )}
          {view.busy && <p role="status">{view.intent ? copy.saving : copy.loading}</p>}
          {view.error && <p role="alert">{copy[view.error]}</p>}
          {blockedRetry && <p role="status">{copy.blockedRetry}</p>}
          <div className="eid-actions">
            <Button disabled={view.busy} onClick={() => void client.read()} type="button" variant="ghost">
              {copy.refresh}
            </Button>
            <Button
              onClick={() => {
                if (!original) {
                  drafts.delete(draftKey)
                }

                onClose()
              }}
              type="button"
              variant="ghost"
            >
              {copy.cancel}
            </Button>
            <Button disabled={saveDisabled} type="submit">
              {original ? copy.retry : copy.save}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
