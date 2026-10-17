import './organization-dialog.css'

import { useEffect, useState, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { useI18n } from '@/i18n/context'
import { organizationCreationCopy } from '@/i18n/organization-creation'

import type { OrganizationModelSetupClient } from './organization-creation-client'
import { validWorkspaceModel } from './organization-creation-contract'

/** Controller owns disposal; every opening creates a freshly verified source-bound client. */
export function OrganizationModelSetup({
  client,
  onClose
}: {
  client: OrganizationModelSetupClient
  onClose: () => void
}) {
  const { locale } = useI18n()
  const copy = organizationCreationCopy[locale]
  const view = useSyncExternalStore(client.subscribe, client.getSnapshot)
  const [model, setModel] = useState('')
  useEffect(() => {
    void client.load()
  }, [client])

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
        aria-busy={view.busy}
        bodyClassName="eid-organization-dialog-body"
        className="eid-organization-dialog"
        onEscapeKeyDown={event => event.stopPropagation()}
      >
        <DialogTitle>{copy.setupModel}</DialogTitle>
        <DialogDescription>{copy.modelHint}</DialogDescription>
        <div className="eid-organization-dialog-scroll">
          <p className="text-sm text-muted-foreground">{copy.modelDraft}</p>
          {view.stage === 'loading' && <p role="status">{copy.progress.loading}</p>}
          {view.stage === 'unavailable' && (
            <p role="alert">{view.result === 'superseded' ? copy.modelStatus.superseded : copy.modelUnsupported}</p>
          )}
          <form
            className="grid gap-2"
            onSubmit={event => {
              event.preventDefault()

              if (client.isCurrent()) {
                void client.save(model)
              }
            }}
          >
            <label className="grid gap-1">
              {copy.modelId}
              <Input
                disabled={view.stage !== 'ready' || view.busy}
                maxLength={200}
                onChange={event => setModel(event.target.value)}
                value={model}
              />
            </label>
            <Button disabled={view.stage !== 'ready' || view.busy || !validWorkspaceModel(model)} type="submit">
              {copy.saveModel}
            </Button>
            {view.stage === 'ready' && view.busy && <p role="status">{copy.modelStatus.saving}</p>}
            {view.stage === 'ready' && view.result && (
              <p role={view.result === 'saved' ? 'status' : 'alert'}>{copy.modelStatus[view.result]}</p>
            )}
          </form>
        </div>
      </DialogContent>
    </Dialog>
  )
}
