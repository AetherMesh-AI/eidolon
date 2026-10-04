import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { LogView } from '@/components/ui/log-view'
import { useI18n } from '@/i18n/context'

import type { OrganizationEditProposal } from './types'

/** Downloads are derived copies of the retained proposal, never a read of the
 * current source path or an instruction to write through the native bridge. */
export function RuntimeEditProposal({ proposal }: { proposal: OrganizationEditProposal }) {
  const { t } = useI18n()
  const copy = t.organizationRuntime.edits
  const urls = useRef(new Set<string>())
  const [downloadError, setDownloadError] = useState(false)
  const applied = proposal.status === 'applied'
  const reviewed = proposal.reviewStatus === 'approved'
  const stale = !applied && proposal.currentRevision !== proposal.baseRevision
  const advanced = applied && proposal.currentRevision !== proposal.appliedRevision

  const name =
    proposal.sourcePath
      .split(/[\\/]/)
      .pop()
      ?.replace(/[<>:"|?*]/g, '_')
      .split('')
      .map(character => (character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127 ? '_' : character))
      .join('')
      .replace(/^\.+$/, 'workspace-file') || 'workspace-file'

  useEffect(() => {
    const retained = urls.current

    return () => {
      retained.forEach(url => URL.revokeObjectURL(url))
      retained.clear()
    }
  }, [])

  const download = (content: string, filename: string) => {
    setDownloadError(false)
    let url: string | undefined

    try {
      // Blob preserves UTF-8 BOM and line-ending bytes in the reviewed string.
      url = URL.createObjectURL(new Blob([content], { type: 'application/octet-stream' }))
      urls.current.add(url)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.appendChild(link)

      try {
        link.click()
      } finally {
        link.remove()
      }
    } catch {
      if (url) {
        URL.revokeObjectURL(url)
        urls.current.delete(url)
      }

      setDownloadError(true)
    }
  }

  return (
    <section aria-label={copy.heading}>
      <h3>{copy.heading}</h3>
      <p className="eid-note">
        {applied
          ? copy.appliedNote
          : proposal.reviewStatus === 'rejected'
            ? copy.rejectedNote
            : reviewed
              ? copy.approvedNote
              : copy.proposedNote}
      </p>
      <dl className="eid-runtime-facts">
        <dt>{copy.proposalId}</dt>
        <dd>{proposal.id}</dd>
        <dt>{copy.workspaceId}</dt>
        <dd>{proposal.workspaceId}</dd>
        <dt>{copy.sourcePath}</dt>
        <dd>{proposal.sourcePath}</dd>
        <dt>{copy.baseRevision}</dt>
        <dd>{proposal.baseRevision}</dd>
        <dt>{copy.currentRevision}</dt>
        <dd>{proposal.currentRevision}</dd>
        <dt>{copy.appliedRevision}</dt>
        <dd>{applied ? proposal.appliedRevision : copy.notApplied}</dd>
        <dt>{copy.baseDigest}</dt>
        <dd>{proposal.baseSha256}</dd>
        <dt>{copy.newDigest}</dt>
        <dd>{proposal.newSha256}</dd>
        <dt>{copy.proposalDigest}</dt>
        <dd>{proposal.proposalSha256}</dd>
        <dt>{copy.review}</dt>
        <dd>{copy.reviewLabels[proposal.reviewStatus]}</dd>
        <dt>{copy.application}</dt>
        <dd>{applied ? copy.applied : copy.notApplied}</dd>
        {proposal.reviewReason && (
          <>
            <dt>{copy.reviewReason}</dt>
            <dd>{proposal.reviewReason}</dd>
          </>
        )}
        {proposal.applicationReason && (
          <>
            <dt>{copy.applicationReason}</dt>
            <dd>{proposal.applicationReason}</dd>
          </>
        )}
        {applied && proposal.appliedAt && (
          <>
            <dt>{copy.appliedAt}</dt>
            <dd>
              <time dateTime={proposal.appliedAt}>{new Date(proposal.appliedAt).toLocaleString()}</time>
            </dd>
          </>
        )}
      </dl>
      {stale && <p className="eid-note">{copy.staleNote}</p>}
      {advanced && <p className="eid-note">{copy.advancedNote}</p>}
      <h4>{copy.diff}</h4>
      <p className="eid-note">{copy.diffNote}</p>
      <LogView aria-label={copy.diff} className="max-h-96">
        {proposal.diff}
      </LogView>
      <div className="eid-inline">
        {reviewed && (
          <Button onClick={() => download(proposal.newContent, name)} size="sm" variant="secondary">
            {copy.downloadFile}
          </Button>
        )}
        <Button onClick={() => download(proposal.diff, `${name}.patch`)} size="sm" variant="secondary">
          {copy.downloadPatch}
        </Button>
      </div>
      {downloadError && <p role="alert">{copy.downloadFailed}</p>}
      <p className="eid-note">{copy.downloadNote}</p>
      <p className="eid-note">{copy.mergeNote}</p>
    </section>
  )
}
