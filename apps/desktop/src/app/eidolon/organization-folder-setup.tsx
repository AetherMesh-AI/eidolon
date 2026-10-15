import { useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { controlVariants } from '@/components/ui/control'
import { Input } from '@/components/ui/input'
import { useI18n } from '@/i18n/context'
import { pickProjectFolder } from '@/store/projects'

import { createFolderClient, type FolderDescriptor, type FolderPreview } from './organization-folder-client'
import { organizationMetadataClient, readOrganizationMetadataScope, useOrganizationMetadata } from './use-organization-metadata'

function source() {
  const scope = readOrganizationMetadataScope()
  const metadata = organizationMetadataClient.getSnapshot().descriptor?.metadata

  return { ...scope, organizationId: metadata?.organizationId ?? null, ownerId: metadata?.owner.ownerId ?? null }
}

export function OrganizationFolderSetup({ onSaved }: { onSaved(): void }) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const metadata = useOrganizationMetadata()
  const client = useRef(createFolderClient(source)).current
  const [descriptor, setDescriptor] = useState<FolderDescriptor | null>(null)
  const [preview, setPreview] = useState<FolderPreview | null>(null)
  const [path, setPath] = useState('')
  const [id, setId] = useState('')
  const [team, setTeam] = useState('')
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef(false)
  const eligible = metadata.scope.eligible && !!metadata.view.descriptor?.metadata
  const current = !descriptor || client.current()

  const run = async (operation: () => Promise<void>) => {
    if (pending.current) { return }
    pending.current = true; setBusy(true); setError('')

    try { await operation() } catch (reason) { setError(reason instanceof Error ? reason.message : copy.projectSetupError) }
    finally { pending.current = false; setBusy(false) }
  }

  return <section aria-label={copy.folderChoose} className="eid-resolution-form">
    <Button disabled={!eligible || busy || uncertain || !current} onClick={() => void run(async () => {
      const value = await client.read()
      setDescriptor(value); setPreview(null); setPath(''); setSaved(false)

      if (value.blockers.length) { throw new Error(copy.projectSetupBlocked) }
      const selected = await pickProjectFolder()

      if (!client.current()) { throw new Error(copy.projectSelectionChanged) }

      if (selected) { setPath(selected); setTeam(value.teams[0] ?? '') }
    })} type="button" variant="secondary">{copy.folderChoose}</Button>
    {!current && <p role="alert">{copy.projectSelectionChanged}</p>}
    {error && <p role="alert">{error}</p>}
    {path && descriptor && !saved && <>
      <p className="eid-note break-all">{path}</p>
      {!preview && <form className="eid-management-fields" onSubmit={event => { event.preventDefault(); void run(async () => { setPreview(await client.preview({ path, projectId: id.trim(), team })) }) }}>
        <label>{copy.projectSetupId}<Input disabled={busy || !current} onChange={event => setId(event.target.value)} required value={id} /></label>
        <label>{copy.projectSetupTeam}<select className={controlVariants()} disabled={busy || !current} onChange={event => setTeam(event.target.value)} value={team}>{descriptor.teams.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
        <Button disabled={busy || !current} type="submit">{copy.projectSetupAdd}</Button>
      </form>}
      {preview && <div>
        <p>{preview.candidate.project.id} · {preview.candidate.project.root} · {preview.candidate.project.team}</p>
        <p className="eid-note">{copy.folderAccess}</p>
        <p className="eid-note">{copy.projectPlanningNote}</p>
        <Button disabled={busy || !current} onClick={() => void run(async () => {
          setUncertain(true)
          await client.register(true)
          setUncertain(false); setSaved(true); onSaved()
        })} type="button">{uncertain ? copy.folderRetry : copy.folderApprove}</Button>
      </div>}
    </>}
    {saved && <p role="status">{copy.projectSaveSaved}</p>}
  </section>
}
