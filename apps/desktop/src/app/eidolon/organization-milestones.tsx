import { useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useI18n } from '@/i18n/context'
import { milestoneCopy } from '@/i18n/organization-milestones'

import { createMilestoneClient, type MilestoneDraft, type MilestoneView } from './organization-milestone-client'
import { organizationMetadataClient, readOrganizationMetadataScope, useOrganizationMetadata } from './use-organization-metadata'

function source() {
  const scope = readOrganizationMetadataScope()
  const metadata = organizationMetadataClient.getSnapshot().descriptor?.metadata

  return { ...scope, organizationId: metadata?.organizationId ?? null, ownerId: metadata?.owner.ownerId ?? null }
}

const blank = (): MilestoneDraft => ({ milestoneId: '', title: '', objectiveIds: [], dependencyIds: [], expectedRevision: 0 })

export function OrganizationMilestones() {
  const { locale } = useI18n()
  const copy = milestoneCopy[locale]
  const metadata = useOrganizationMetadata()
  const client = useRef(createMilestoneClient(source)).current
  const [view, setView] = useState<MilestoneView | null>(null)
  const [draft, setDraft] = useState(blank)
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef(false)
  const eligible = metadata.scope.eligible && !!metadata.view.descriptor?.metadata
  const current = !view || client.current()
  const disabled = busy || uncertain || !eligible || !current

  const run = async (operation: () => Promise<void>) => {
    if (pending.current) { return }
    pending.current = true; setBusy(true); setError('')

    try { await operation() } catch (reason) { setError(reason instanceof Error ? reason.message : copy.needs_attention) }
    finally { pending.current = false; setBusy(false) }
  }

  const edit = (change: Partial<MilestoneDraft>) => { setDraft(value => ({ ...value, ...change })); setConfirmed(false) }
  const toggle = (field: 'objectiveIds' | 'dependencyIds', id: string) => edit({ [field]: draft[field].includes(id) ? draft[field].filter(value => value !== id) : [...draft[field], id] })

  return <details className="eid-roster-details">
    <summary>{copy.heading}</summary>
    <p className="eid-note">{copy.note}</p>
    <Button disabled={busy || uncertain || !eligible} onClick={() => void run(async () => { setView(await client.read()); setDraft(blank()); setConfirmed(false) })} variant="secondary">{copy.open}</Button>
    {!current && <p role="alert">{uncertain ? copy.returnOriginal : copy.changed}</p>}
    {error && <p role="alert">{error}</p>}
    {uncertain && <Button disabled={busy || !eligible} onClick={() => void run(async () => {
      if (await client.reconcile()) { setUncertain(false); setDraft(blank()); setConfirmed(false); setView(await client.read()) }
    })} variant="secondary">{copy.reconcile}</Button>}
    {view && <>
      <ul className="max-h-48 overflow-y-auto">{view.milestones.map(m => <li key={m.id}><Button disabled={disabled} onClick={() => { setDraft({ milestoneId: m.id, title: m.title, expectedRevision: m.revision, objectiveIds: m.objectives.map(o => o.id), dependencyIds: m.dependencies.map(d => d.id) }); setConfirmed(false) }} variant="secondary">{m.title} · {copy[m.status]}</Button></li>)}</ul>
      <Button disabled={disabled} onClick={() => { setDraft(blank()); setConfirmed(false) }} variant="secondary">{copy.new}</Button>
      <form onSubmit={event => { event.preventDefault();

 if (!confirmed || !current || !eligible || busy) { return }; void run(async () => {
        setUncertain(true)

        try { await client.save(draft, true) } catch (reason) { setUncertain(client.pending()); throw reason }
        setUncertain(false); setDraft(blank()); setConfirmed(false)
        setView(await client.read())
      }) }}>
        <label>{copy.id}<Input disabled={disabled || draft.expectedRevision > 0} maxLength={128} onChange={event => edit({ milestoneId: event.target.value })} required value={draft.milestoneId} /></label>
        <label>{copy.title}<Input disabled={disabled} maxLength={500} onChange={event => edit({ title: event.target.value })} required value={draft.title} /></label>
        <fieldset className="max-h-48 overflow-y-auto" disabled={disabled}><legend>{copy.objectives}</legend>{view.objectives.filter(o => o.projectIds.length).map(o => <label className="block" key={o.id}><input checked={draft.objectiveIds.includes(o.id)} onChange={() => toggle('objectiveIds', o.id)} type="checkbox" /> {o.title} · {o.projectIds.join(', ')}</label>)}</fieldset>
        <fieldset className="max-h-48 overflow-y-auto" disabled={disabled}><legend>{copy.dependencies}</legend>{view.milestones.filter(m => m.id !== draft.milestoneId).map(m => <label className="block" key={m.id}><input checked={draft.dependencyIds.includes(m.id)} onChange={() => toggle('dependencyIds', m.id)} type="checkbox" /> {m.title} · {copy[m.status]}</label>)}</fieldset>
        <label><input checked={confirmed} disabled={disabled} onChange={event => setConfirmed(event.target.checked)} type="checkbox" /> {copy.confirm}</label>
        <Button disabled={busy || !current || !eligible || !confirmed || !draft.objectiveIds.length} type="submit">{uncertain ? copy.retry : copy.save}</Button>
      </form>
    </>}
  </details>
}
