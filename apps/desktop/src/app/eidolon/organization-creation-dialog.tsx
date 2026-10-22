import './organization-dialog.css'

import { useEffect, useId, useState, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { ErrorState } from '@/components/ui/error-state'
import { Input } from '@/components/ui/input'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n/context'
import { organizationCreationCopy } from '@/i18n/organization-creation'

import { sourceCapabilityText } from './organization-source-capability'
import type { OrganizationCreationClient } from './organization-creation-client'
import { validWorkspaceModel, validWorkspaceName } from './organization-creation-contract'

export interface OrganizationCreationDialogProps {
  /** The owning controller disposes this client on dismissal or source replacement. */
  client: OrganizationCreationClient
  onClose: () => void
  onSetup: (profile: string) => void
}

/** Creating, selecting, and opening setup are three separate user actions. */
export function OrganizationCreationDialog({ client, onClose, onSetup }: OrganizationCreationDialogProps) {
  const { locale } = useI18n()
  const copy = organizationCreationCopy[locale]
  const view = useSyncExternalStore(client.subscribe, client.getSnapshot)
  const [editingModel, setEditingModel] = useState(false)
  const [model, setModel] = useState('')
  const [modelStatus, setModelStatus] = useState<'idle' | 'saving' | 'saved' | 'failed' | 'superseded'>('idle')
  const [displayName, setDisplayName] = useState('')
  const [ownerDisplayName, setOwnerDisplayName] = useState('')
  const id = useId()

  const names = {
    displayName: view.intent?.displayName ?? displayName,
    ownerDisplayName: view.intent?.ownerDisplayName ?? ownerDisplayName
  }

  const busy = ['loading', 'creating', 'checking', 'switching'].includes(view.stage) || modelStatus === 'saving'
  const showNames = !view.result && (view.stage === 'ready' || Boolean(view.intent || displayName || ownerDisplayName))
  const valid = validWorkspaceName(names.displayName) && validWorkspaceName(names.ownerDisplayName)
  const editable = view.stage === 'ready' && !view.intent

  const setupKeys = () => {
    if (view.result && client.canSetup()) {
      onSetup(view.result.profile)
    }
  }

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
        aria-busy={busy}
        bodyClassName="eid-organization-dialog-body"
        className="eid-organization-dialog"
        onEscapeKeyDown={event => event.stopPropagation()}
      >
        <DialogTitle>{copy.title}</DialogTitle>
        <DialogDescription>{copy.description}</DialogDescription>
        <div className="eid-organization-dialog-scroll">
          <div className="grid gap-2 text-sm text-muted-foreground">
            <p>{copy.credentials}</p>
            <p>{copy.limits}</p>
            <p role="status">{sourceCapabilityText(view.runtimeCapability, copy.sourceCapability)}</p>
          </div>
          {showNames && (
            <form
              className="grid gap-3"
              onSubmit={event => {
                event.preventDefault()

                if (editable && valid) {
                  void client.create(names.displayName, names.ownerDisplayName)
                }
              }}
            >
              <label className="grid gap-1">
                {copy.organizationName}
                <Input
                  aria-describedby={`${id}-names`}
                  aria-invalid={Boolean(names.displayName) && !validWorkspaceName(names.displayName)}
                  dir="auto"
                  disabled={!editable}
                  maxLength={160}
                  onChange={event => setDisplayName(event.target.value)}
                  value={names.displayName}
                />
              </label>
              <label className="grid gap-1">
                {copy.ownerName}
                <Input
                  aria-describedby={`${id}-names`}
                  aria-invalid={Boolean(names.ownerDisplayName) && !validWorkspaceName(names.ownerDisplayName)}
                  dir="auto"
                  disabled={!editable}
                  maxLength={160}
                  onChange={event => setOwnerDisplayName(event.target.value)}
                  value={names.ownerDisplayName}
                />
              </label>
              <p className="text-sm text-muted-foreground" id={`${id}-names`}>
                {copy.namesHint}
              </p>
              {view.stage === 'ready' && (
                <Button disabled={!editable || !valid} type="submit">
                  {copy.create}
                </Button>
              )}
            </form>
          )}
          {['loading', 'creating', 'checking', 'switching'].includes(view.stage) && (
            <div className="flex items-center gap-2" role="status">
              <Loader
                aria-hidden="true"
                label={copy.progress[view.stage as keyof typeof copy.progress]}
                role="presentation"
              />
              <p>{copy.progress[view.stage as keyof typeof copy.progress]}</p>
            </div>
          )}
          {view.error && (
            <div role="alert">
              <ErrorState description={copy.errors[view.error]} title={copy.errorTitle} />
            </div>
          )}
          {view.intent && view.stage !== 'selected' && <p className="text-sm text-muted-foreground">{copy.recovery}</p>}
          {view.stage === 'uncertain' && (
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => void client.check()} variant="secondary">
                {copy.check}
              </Button>
              {view.canRetry && <Button onClick={() => void client.retry()}>{copy.retry}</Button>}
            </div>
          )}
          {view.result && (
            <section aria-label={copy.receipt} className="grid gap-2">
              <p aria-live="polite">{view.stage === 'selected' ? copy.selected : copy.created}</p>
              <dl className="grid gap-1 text-sm">
                <dt className="text-muted-foreground">{copy.organizationName}</dt>
                <dd className="break-words" dir="auto">
                  {view.result.descriptor.displayName}
                </dd>
                <dt className="text-muted-foreground">{copy.ownerName}</dt>
                <dd className="break-words" dir="auto">
                  {view.result.descriptor.owner.displayName}
                </dd>
              </dl>
              {view.stage === 'created' && (
                <Button disabled={!client.isCurrent()} onClick={() => void client.select()}>
                  {copy.switch}
                </Button>
              )}
              {view.stage === 'selected' && client.canSetup() && (
                <>
                  <p className="text-sm text-muted-foreground">{copy.setupNote}</p>
                  <div className="flex flex-wrap gap-2">
                    <Button disabled={modelStatus === 'saving'} onClick={setupKeys}>
                      {copy.setupKeys}
                    </Button>
                    <Button
                      disabled={modelStatus === 'saving'}
                      onClick={() => setEditingModel(true)}
                      variant="secondary"
                    >
                      {copy.setupModel}
                    </Button>
                  </div>
                  {editingModel && (
                    <form
                      className="grid gap-2"
                      onSubmit={async event => {
                        event.preventDefault()

                        if (modelStatus === 'saving' || !client.canSetup() || !validWorkspaceModel(model)) {
                          return
                        }

                        setModelStatus('saving')
                        setModelStatus(await client.saveModel(model))
                      }}
                    >
                      <label className="grid gap-1">
                        {copy.modelId}
                        <Input
                          disabled={modelStatus === 'saving'}
                          maxLength={200}
                          onChange={event => {
                            setModel(event.target.value)
                            setModelStatus('idle')
                          }}
                          value={model}
                        />
                      </label>
                      <p className="text-sm text-muted-foreground">{copy.modelHint}</p>
                      <Button disabled={modelStatus === 'saving' || !validWorkspaceModel(model)} type="submit">
                        {copy.saveModel}
                      </Button>
                      {modelStatus !== 'idle' && (
                        <p role={modelStatus === 'failed' || modelStatus === 'superseded' ? 'alert' : 'status'}>
                          {copy.modelStatus[modelStatus]}
                        </p>
                      )}
                    </form>
                  )}
                </>
              )}
            </section>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
