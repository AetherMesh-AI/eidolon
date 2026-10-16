import { useCallback, useId, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { ErrorState } from '@/components/ui/error-state'
import { Input } from '@/components/ui/input'
import { Loader } from '@/components/ui/loader'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useI18n } from '@/i18n/context'
import { organizationCredentialHandoffCopy } from '@/i18n/organization-credential-handoff'
import { organizationCredentialsCopy } from '@/i18n/organization-credentials'

import type { CredentialHandoffClient } from './organization-credential-handoff-client'
import type { HandoffView } from './organization-credential-handoff-types'
import type { CredentialCatalog } from './organization-credentials-types'

interface HandoffEditorProps {
  catalog: CredentialCatalog
  client: CredentialHandoffClient
  view: HandoffView
  onClose: () => void
}

export function CredentialHandoffEditor({ catalog, client, view, onClose }: HandoffEditorProps) {
  const { locale } = useI18n()
  const copy = organizationCredentialHandoffCopy[locale]
  const common = organizationCredentialsCopy[locale]
  const id = useId()
  const input = useRef<HTMLInputElement>(null)
  const [entryId, setEntryId] = useState('')
  const [targetKey, setTargetKey] = useState('')
  const [reviewed, setReviewed] = useState(false)
  const [hasValue, setHasValue] = useState(false)
  const entry = catalog.entries.find(item => item.id === entryId)
  const entryMatches = entry && view.entry?.id === entry.id && view.entry.revision === entry.revision && view.entry.version === entry.version
  const targets = entryMatches ? view.targets : null
  const target = targets?.find(item => JSON.stringify([item.actorId, item.requestId, item.generation]) === targetKey)

  const reset = () => {
    if (input.current) { input.current.value = '' }
    setHasValue(false)
    setReviewed(false)
  }

  const close = () => { reset(); onClose() }

  const bindInput = useCallback((field: HTMLInputElement | null) => {
    if (input.current && input.current !== field) { input.current.value = '' }
    input.current = field
  }, [])

  return <Dialog onOpenChange={open => !open && close()} open>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{copy.open}</DialogTitle>
        <DialogDescription>{copy.secureNote}</DialogDescription>
      </DialogHeader>
      {view.error && <div role="alert"><ErrorState title={common[view.error === 'unavailable' ? 'unavailableError' : view.error]} /></div>}
      <form autoComplete="off" className="grid gap-4" onSubmit={event => {
        event.preventDefault()
        let value = input.current?.value ?? ''

        if (!entry || !target || !reviewed || !value || view.busy) { value = '';

 return }

        reset()
        void client.prepare(entry, target, value)
        value = ''
        onClose()
      }}>
        <div className="grid gap-1 text-sm">
          <label htmlFor={`${id}-entry`}>{copy.entry}</label>
          <Select disabled={view.busy} onValueChange={value => {
            reset(); setEntryId(value); setTargetKey('')
            const selected = catalog.entries.find(item => item.id === value)

            if (selected) { void client.loadTargets(selected) }
          }} value={entryId}>
            <SelectTrigger id={`${id}-entry`}><SelectValue placeholder={copy.entry} /></SelectTrigger>
            <SelectContent>{catalog.entries.filter(item => item.status !== 'revoked' && item.status !== 'expired' && item.permissions.includes('replace_value')).map(item =>
              <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        {view.busy && <Loader aria-label={copy.worker} className="size-6" />}
        {entry && <Button disabled={view.busy} onClick={() => { reset(); setTargetKey(''); void client.loadTargets(entry) }} type="button" variant="ghost">{common.refresh}</Button>}
        {targets?.length === 0 && <p role="status">{copy.noTargets}</p>}
        {Boolean(targets?.length) && <div className="grid gap-1 text-sm">
          <label htmlFor={`${id}-worker`}>{copy.worker}</label>
          <Select onValueChange={value => { reset(); setTargetKey(value) }} value={targetKey}>
            <SelectTrigger id={`${id}-worker`}><SelectValue placeholder={copy.selectWorker} /></SelectTrigger>
            <SelectContent>{targets!.map(item => {
              const key = JSON.stringify([item.actorId, item.requestId, item.generation])
              const agent = catalog.agents.find(agent => agent.identityId === item.actorId)

              return <SelectItem key={key} value={key}>{agent?.name ?? item.actorId} · {item.requestId} · {item.generation}</SelectItem>
            })}</SelectContent>
          </Select>
        </div>}
        {entry && <p className="break-words text-xs">{common.id}: <bdi>{entry.id}</bdi> · {common.revision}: {entry.revision}</p>}
        {target && <>
          <dl className="grid gap-1 break-words text-xs">
            <div><dt className="inline">{common.actor}: </dt><dd className="inline"><bdi>{target.actorId}</bdi></dd></div>
            <div><dt className="inline">{copy.request}: </dt><dd className="inline"><bdi>{target.requestId}</bdi></dd></div>
            <div><dt className="inline">{copy.generation}: </dt><dd className="inline"><bdi>{target.generation}</bdi></dd></div>
            <div><dt className="inline">{copy.grantRevision}: </dt><dd className="inline">{target.grantRevision}</dd></div>
          </dl>
          <div className="grid gap-1 text-sm">
            <label htmlFor={`${id}-value`}>{common.value}</label>
            <Input autoCapitalize="none" autoComplete="new-password" dir="ltr" id={`${id}-value`} key={`${entry?.id}:${entry?.revision}:${targetKey}`}
              maxLength={65536} onChange={event => setHasValue(Boolean(event.target.value))} ref={bindInput} required spellCheck={false} type="password" />
          </div>
          <label className="flex items-start gap-2 text-sm">
            <input checked={reviewed} onChange={event => setReviewed(event.target.checked)} type="checkbox" />{copy.reviewed}
          </label>
        </>}
        <DialogFooter>
          <Button onClick={close} type="button" variant="ghost">{common.cancel}</Button>
          <Button disabled={!target || !reviewed || !hasValue || view.busy} type="submit">{copy.authorize}</Button>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>
}
