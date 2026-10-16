import { useEffect, useId, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { controlVariants } from '@/components/ui/control'
import { CopyButton } from '@/components/ui/copy-button'
import { ErrorState } from '@/components/ui/error-state'
import { Input } from '@/components/ui/input'
import { Loader } from '@/components/ui/loader'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n/context'

import { createPreparedValidationClient } from './runtime-prepared-validation-client'
import { preparedRecord } from './runtime-prepared-validation-contract'
import { preparedValidationCopy } from './runtime-prepared-validation-copy'
import { PreparedValidationReview } from './runtime-prepared-validation-review'
import { readPreparedValidationScope } from './runtime-prepared-validation-scope'
import { type PreparedValidationDescriptor, PreparedValidationError, type PreparedValidationLimits, type PreparedValidationPreview } from './runtime-prepared-validation-types'
import { useOrganizationMetadata } from './use-organization-metadata'

const initialLimits: PreparedValidationLimits = { max_files: 10000, max_file_bytes: 8388608, max_total_bytes: 67108864, max_path_bytes: 512, max_depth: 32 }

const profileExample = JSON.stringify({ version: 2, id: 'renderer', workspace: 'apps/desktop', bundleSha256: '<prepared bundle SHA-256>',
  steps: [{ id: 'types', recipe: 'workspace_typescript', checkKind: 'project_typecheck', config: 'apps/desktop/tsconfig.json', testFiles: [], sourceDirectories: ['apps/desktop/src'] },
    { id: 'ui', recipe: 'workspace_vitest_ui', checkKind: 'project_tests', config: 'apps/desktop/vitest.config.ts', testFiles: ['apps/desktop/src/example.test.tsx'], sourceDirectories: [] },
    { id: 'build', recipe: 'workspace_vite_build', checkKind: 'project_build', config: 'apps/desktop/vite.config.ts', testFiles: [], sourceDirectories: [] }],
  limits: { wallSeconds: 300, memoryMiB: 2048, cpus: 1, pids: 64, scratchMiB: 256, logBytes: 65536 } }, null, 2)

/** Setup only: the backend owns byte verification and every later execution decision. */
interface RuntimePreparedValidationSetupProps {
  onSaved?(): void
  onConfirmationChange?: (cancel: (() => void) | null) => void
}

export function RuntimePreparedValidationSetup({ onSaved, onConfirmationChange }: RuntimePreparedValidationSetupProps) {
  const { locale } = useI18n()
  const copy = preparedValidationCopy[locale]
  const metadata = useOrganizationMetadata()
  const client = useRef(createPreparedValidationClient(readPreparedValidationScope)).current
  const reportConfirmation = useRef(onConfirmationChange).current
  const active = useRef(true)
  const pending = useRef(false)
  const started = useRef(false)
  const minimumSidecarRevision = useRef(0)
  const enableId = useId()
  const revokeId = useId()
  const [descriptor, setDescriptor] = useState<PreparedValidationDescriptor | null>(null)
  const [preview, setPreview] = useState<PreparedValidationPreview | null>(null)
  const [projectId, setProjectId] = useState('')
  const [files, setFiles] = useState('')
  const [limits, setLimits] = useState(initialLimits)
  const [exceptions, setExceptions] = useState('{}')
  const [directory, setDirectory] = useState('')
  const [manifest, setManifest] = useState('')
  const [profile, setProfile] = useState('')
  const [enabled, setEnabled] = useState(false)
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [confirming, setConfirming] = useState<'save' | 'revoke' | null>(null)
  const [retainedAction, setRetainedAction] = useState<'save' | 'revoke' | null>(null)
  const [revokeAcknowledged, setRevokeAcknowledged] = useState(false)
  const [saved, setSaved] = useState<'save' | 'revoke' | null>(null)
  const [error, setError] = useState('')
  const eligible = metadata.scope.eligible && !!metadata.view.descriptor?.metadata
  const current = !started.current || client.current()
  const disabled = !eligible || !current || uncertain || !!saved || (busy && client.hasRetainedIntent())
  const project = descriptor?.projects.find(value => value.id === projectId)
  const consequences = preview?.candidate.contract.version === 2 ? copy.threeConsequences : copy.consequences

  const message = (reason: unknown) => reason instanceof PreparedValidationError
    ? reason.code === 'confirm' ? copy.confirmRequired : copy[reason.code]
    : reason instanceof Error ? reason.message : copy.failure

  // eslint-disable-next-line no-restricted-syntax -- mount lifetime fences late owner/socket results
  useEffect(() => {
    active.current = true

    return () => { active.current = false; reportConfirmation?.(null) }
  }, [reportConfirmation])

  const run = async (operation: () => Promise<void>) => {
    if (pending.current || !active.current) { return }
    pending.current = true; setBusy(true); setError('')

    try { await operation() }
    catch (reason) { if (active.current) { setError(message(reason)) } }
    finally {
      pending.current = false

      if (active.current) { setBusy(false) }
    }
  }

  const closeConfirmation = () => { setConfirming(null); reportConfirmation?.(null) }

  const openConfirmation = (action: 'save' | 'revoke') => {
    if (pending.current || !client.current()) { return }
    setConfirming(action); reportConfirmation?.(closeConfirmation)
  }

  const edit = (change: () => void) => {
    if (disabled || client.hasRetainedIntent()) { return }
    client.invalidatePreview(); setPreview(null); closeConfirmation(); setError(''); change()
  }

  const read = () => void run(async () => {
    if (!eligible || uncertain || !current) { return }
    started.current = true
    setDescriptor(null); setPreview(null); setEnabled(false); setSaved(null); setRevokeAcknowledged(false); setRetainedAction(null)
    const value = await client.read()

    if (value.sidecarRevision < minimumSidecarRevision.current) { throw new PreparedValidationError('invalid') }

    if (active.current && client.current()) { minimumSidecarRevision.current = value.sidecarRevision; setDescriptor(value) }
  })

  const parse = (input: string) => {
    let value: unknown

    try { value = JSON.parse(input) } catch { throw new Error(copy.json) }

    if (!preparedRecord(value)) { throw new Error(copy.json) }

    return value
  }

  const review = () => void run(async () => {
    if (disabled || !enabled) { return }
    setPreview(null)

    const value = await client.preview({ projectId, enabled,
      source: { files: files.split(/\r?\n/).filter(path => path !== ''), limits: { ...limits }, executionOnlyHashes: parse(exceptions) as Record<string, string> },
      dependencies: { directory, manifest: parse(manifest) }, profile: parse(profile) })

    if (active.current && client.current()) { setPreview(value) }
  })

  const save = async (action: 'save' | 'revoke') => {
    if (pending.current || !active.current || !client.current()) { return }

    if (action === 'revoke' && !revokeAcknowledged && !uncertain) { return }
    pending.current = true; setBusy(true); setError('')

    try {
      const receipt = action === 'revoke' ? await client.revoke(projectId, true) : await client.save(true)

      if (active.current && client.current()) {
        minimumSidecarRevision.current = Math.max(minimumSidecarRevision.current, receipt.sidecarRevision)
        setUncertain(false); setRetainedAction(null); setSaved(action); setDescriptor(null); closeConfirmation(); onSaved?.()

        try {
          // A confirmed receipt is historical evidence, not proof the reference remains enabled.
          const refreshed = await client.read()

          if (refreshed.sidecarRevision < minimumSidecarRevision.current) { throw new PreparedValidationError('invalid') }

          if (active.current && client.current()) { minimumSidecarRevision.current = refreshed.sidecarRevision; setDescriptor(refreshed) }
        } catch {
          if (active.current && client.current()) { setError(copy.referencesUnavailable) }
        }
      }
    } catch (reason) {
      if (active.current) { setUncertain(client.hasRetainedIntent()); setRetainedAction(client.hasRetainedIntent() ? action : null); setError(message(reason)) }
      throw new Error(message(reason))
    } finally {
      pending.current = false

      if (active.current) { setBusy(false) }
    }
  }

  return <section aria-label={copy.title} className="eid-resolution-form">
    <h3>{copy.title}</h3>
    <p className="eid-note">{copy.note}</p>
    <p className="eid-note">{copy.platform}</p>
    <Button disabled={!eligible || busy || uncertain || !current} onClick={read} type="button" variant="secondary">{descriptor ? copy.refresh : copy.read}</Button>
    {busy && <Loader label={copy.loading} />}
    {!eligible && <p role="alert">{copy.unavailable}</p>}
    {!current && <p role="alert">{copy.changed}</p>}
    {error && current && <div role="alert"><ErrorState description={error} title={copy.failure} /></div>}
    {uncertain && <p role="status">{copy.uncertain}</p>}
    {descriptor && current && <>
      {!busy && !uncertain && <section aria-label={copy.references} className="eid-resolution-form">
        <h4>{copy.references}</h4>
        <p className="eid-note">{copy.referencesNote}</p>
        {!descriptor.registrationRefs.length && <p>{copy.referencesEmpty}</p>}
        {descriptor.registrationRefs.map(reference => <div key={reference.projectId}>
          <pre className="whitespace-pre-wrap break-all text-xs" dir="ltr">{JSON.stringify(reference, null, 2)}</pre>
          <CopyButton label={`${copy.copyReference}: ${reference.projectId}`} text={() => {
            if (!active.current || !client.current() || pending.current || client.hasRetainedIntent()) { throw new PreparedValidationError('changed') }

            return JSON.stringify(reference, null, 2)
          }} />
        </div>)}
      </section>}
      {!descriptor.platform.supported && <p role="alert">{copy.unsupported} ({descriptor.platform.system}, {descriptor.platform.architecture})</p>}
      {descriptor.blockers.length > 0 && <div role="alert"><p>{copy.blocked}</p><ul>{descriptor.blockers.map((blocker, index) => <li key={`${index}:${blocker}`}>{blocker}</li>)}</ul></div>}
      {!descriptor.projects.length && <p>{copy.empty}</p>}
      {descriptor.projects.length > 0 && !saved && <div className="eid-management-fields">
          <label>{copy.project}<select className={controlVariants()} disabled={disabled || busy} onChange={event => edit(() => { setProjectId(event.target.value); setEnabled(false); setRevokeAcknowledged(false) })} required value={projectId}>
            <option value="">{copy.selectProject}</option>
            {descriptor.projects.map(value => <option key={value.id} value={value.id}>{value.id} · {value.root} · {value.team}</option>)}
          </select></label>
          {project?.rootRevoked && <p role="alert">{copy.rootRevoked}</p>}
          {project && <p className="eid-note break-all">{copy.projectDetails}: {project.id} · {project.root} · {project.team} · recipe: {project.recipe ?? 'null'} · {copy.registrationRevision}: {project.registrationRevision}</p>}
        {project?.enabled && <>
          <p className="eid-note">{copy.revokeNote}</p>
          <div className="flex items-start gap-2"><Checkbox checked={revokeAcknowledged} disabled={disabled || busy} id={revokeId} onCheckedChange={value => setRevokeAcknowledged(value === true)} /><label htmlFor={revokeId}>{copy.revokeChoice}</label></div>
          <Button disabled={busy || !eligible || !current || (uncertain ? retainedAction !== 'revoke' : !revokeAcknowledged)} onClick={() => {
            if (uncertain) { void save('revoke').catch(() => {}) } else { openConfirmation('revoke') }
          }} type="button" variant="destructive">{retainedAction === 'revoke' ? copy.revokeRetry : copy.revoke}</Button>
        </>}
      </div>}
      {descriptor.platform.supported && descriptor.blockers.length === 0 && descriptor.projects.length > 0 && !saved && <>
        <form className="eid-management-fields" onSubmit={event => { event.preventDefault(); review() }}>
          <label>{copy.files}<Textarea disabled={disabled} onChange={event => edit(() => setFiles(event.target.value))} required rows={6} value={files} /></label>
          <p className="eid-note">{copy.filesNote}</p>
          <details><summary>{copy.limits}</summary><div className="eid-management-fields">
            {(Object.keys(initialLimits) as (keyof PreparedValidationLimits)[]).map(key => <label key={key}>{copy[key]}<Input disabled={disabled} min={1} onChange={event => edit(() => setLimits(value => ({ ...value, [key]: event.target.value === '' ? 0 : Number(event.target.value) })))} required step={1} type="number" value={limits[key] || ''} /></label>)}
          </div></details>
          <label>{copy.exceptions}<Textarea disabled={disabled} onChange={event => edit(() => setExceptions(event.target.value))} required value={exceptions} /></label>
          <p className="eid-note">{copy.exceptionsNote}</p>
          <label>{copy.dependencies}<Input disabled={disabled} onChange={event => edit(() => setDirectory(event.target.value))} required value={directory} /></label>
          <p className="eid-note">{copy.dependenciesNote}</p>
          <label>{copy.manifest}<Textarea disabled={disabled} onChange={event => edit(() => setManifest(event.target.value))} required rows={8} value={manifest} /></label>
          <label>{copy.profile}<Textarea disabled={disabled} onChange={event => edit(() => setProfile(event.target.value))} placeholder={profileExample} required rows={12} value={profile} /></label>
          <p className="eid-note">{copy.profileNote}</p>
          <div className="flex items-start gap-2"><Checkbox checked={enabled} disabled={disabled || busy} id={enableId} onCheckedChange={value => edit(() => setEnabled(value === true))} /><label htmlFor={enableId}>{copy.enable}</label></div>
          <p className="eid-note">{copy.enableNote}</p>
          <Button disabled={disabled || busy || !enabled || !projectId || project?.rootRevoked} type="submit">{copy.preview}</Button>
        </form>
        {preview && <section aria-label={copy.review} className="eid-resolution-form">
          <h4>{copy.review}</h4>
          <p className="eid-note">{copy.verified}</p>
          <p>{copy.sourceCount}: {preview.candidate.verification.sourceFiles} / {preview.candidate.verification.sourceBytes}</p>
          <p>{copy.dependencyCount}: {preview.candidate.verification.dependencyFiles} / {preview.candidate.verification.dependencyBytes}</p>
          <p className="eid-note break-all">{copy.snapshot}: {preview.candidate.verification.snapshotSha256}</p>
          <p className="eid-note break-all">{copy.candidateHash}: {preview.candidateHash}</p>
          <PreparedValidationReview candidate={preview.candidate} />
          <p className="eid-note">{consequences}</p>
          {preview.candidate.contract.version === 2 && <p className="eid-note">{copy.buildBoundary}</p>}
          <p className="eid-note">{copy.boundaries}</p>
          <Button disabled={busy || !eligible || !current || (uncertain && retainedAction !== 'save')} onClick={() => { if (uncertain) { void save('save').catch(() => {}) } else { openConfirmation('save') } }} type="button">{uncertain ? copy.retry : copy.save}</Button>
        </section>}
      </>}
    </>}
    {saved && current && <p role="status">{saved === 'revoke' ? copy.revoked : copy.saved}</p>}
    <ConfirmDialog confirmLabel={confirming === 'revoke' ? copy.revokeConfirm : copy.confirm}
      description={confirming === 'revoke' ? `${projectId}: ${copy.revokeNote}` : `${projectId}: ${consequences} ${preview?.candidate.contract.version === 2 ? copy.buildBoundary : ''} ${copy.boundaries}`}
      destructive={confirming === 'revoke'} onClose={closeConfirmation} onConfirm={() => { if (confirming) { return save(confirming) } }}
      open={!!confirming && eligible && current && (confirming === 'revoke' || !!preview) && !saved}
      title={confirming === 'revoke' ? copy.revokeTitle : copy.confirmTitle} />
  </section>
}
