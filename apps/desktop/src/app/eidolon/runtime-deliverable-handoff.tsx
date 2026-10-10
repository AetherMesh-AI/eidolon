import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { writeClipboardText } from '@/components/ui/copy-button'
import { translateNow } from '@/i18n'
import { useI18n } from '@/i18n/context'

import type { OrganizationArtifact, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

export async function verifyDeliverable(artifact: OrganizationArtifact) {
  const valid = /^outcome_[a-f0-9]{32}$/.test(artifact.id) && /^obj_[a-f0-9]{32}$/.test(artifact.objectiveId)
    && /^[a-f0-9]{64}$/.test(artifact.sha256) && typeof artifact.content === 'string'
    && (artifact.round === undefined || (Number.isSafeInteger(artifact.round) && artifact.round >= 0))

  if (!valid) {throw new Error(translateNow('organizationWork.handoffInvalid'))}
  const bytes = new TextEncoder().encode(artifact.content)
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('')

  if (digest !== artifact.sha256) {throw new Error(translateNow('organizationWork.handoffInvalid'))}
}

export function RuntimeDeliverableHandoff({ artifact, adapter, snapshot }: {
  artifact: OrganizationArtifact
  adapter: RuntimeOrganizationAdapter
  snapshot: OrganizationSnapshot
}) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const [status, setStatus] = useState('')
  const busy = useRef(false)
  const active = useRef(true)
  const urls = useRef(new Set<string>())

  const current = () => {
    const latest = adapter.getSnapshot()

    return active.current && !!snapshot.runtime?.profile && latest.connection?.state === 'ready'
      && latest.connection.scope === snapshot.connection?.scope
      && latest.connection.ownerScope === snapshot.connection?.ownerScope
      && latest.runtime?.profile === snapshot.runtime?.profile
  }

  // eslint-disable-next-line no-restricted-syntax -- revoke derived URLs and fence detached clipboard callbacks
  useEffect(() => {
    active.current = true
    const retained = urls.current

    return () => {
      active.current = false
      retained.forEach(url => URL.revokeObjectURL(url))
      retained.clear()
    }
  }, [])

  const copyText = async () => {
    if (!current() || busy.current) {return}
    busy.current = true
    setStatus('')

    try {
      await writeClipboardText(artifact.content)

      if (current()) {setStatus(copy.handoffCopied)}
    } catch {
      if (current()) {setStatus(copy.handoffFailed)}
    } finally {busy.current = false}
  }

  const download = () => {
    if (!current()) {return}
    let url: string | undefined
    setStatus('')

    try {
      url = URL.createObjectURL(new Blob([artifact.content], { type: 'application/octet-stream' }))
      urls.current.add(url)
      const link = document.createElement('a')
      link.href = url
      // Only the validated immutable ID determines the filename; model text cannot supply a path or executable extension.
      link.download = `eidolon-${artifact.id}.txt`
      document.body.appendChild(link)

      try {link.click()} finally {link.remove()}
      setStatus(copy.handoffRequested)
    } catch {
      if (url) {URL.revokeObjectURL(url); urls.current.delete(url)}
      setStatus(copy.handoffFailed)
    }
  }

  return (
    <section aria-label={copy.handoff}>
      <p>{copy.handoffProfile}: {snapshot.runtime?.profile ?? copy.notRecorded}</p>
      <p>{copy.handoffObjective}: {artifact.objectiveId} · {copy.round}: {artifact.round ?? copy.notRecorded}</p>
      <p>{copy.handoffRevision}: {artifact.id}</p>
      <div className="eid-inline">
        <Button disabled={!current()} onClick={() => void copyText()} size="sm" variant="secondary">{copy.handoffCopy}</Button>
        <Button disabled={!current()} onClick={download} size="sm" variant="secondary">{copy.handoffDownload}</Button>
      </div>
      <p className="eid-note">{copy.handoffNote}</p>
      {status && <p role="status">{status}</p>}
    </section>
  )
}
