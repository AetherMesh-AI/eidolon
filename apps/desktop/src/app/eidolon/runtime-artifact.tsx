import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n/context'

import { Inspector, type InspectorOrigin } from './inspector'
import { LocalizedTime } from './localized-time'
import { RuntimeDeliverableHandoff, verifyDeliverable } from './runtime-deliverable-handoff'
import { RuntimeEditProposal } from './runtime-edit-proposal'
import { RuntimeToolReceipts } from './runtime-tool-receipts'
import type { OrganizationArtifact, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

interface RuntimeArtifactProps {
  adapter: RuntimeOrganizationAdapter
  snapshot: OrganizationSnapshot
  evidenceId: string
  title: string
  origin?: InspectorOrigin | null
  onClose(): void
}

export function RuntimeArtifact({ adapter, snapshot, evidenceId, title, onClose, origin }: RuntimeArtifactProps) {
  const { t } = useI18n()
  const copy = t.organizationRuntime
  const [artifact, setArtifact] = useState<OrganizationArtifact | null>(null)
  const [loadedVersion, setLoadedVersion] = useState('')
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [pending, setPending] = useState(true)

  // Full evidence is read on demand. Refresh while open when authoritative work
  // state changes so approved, applied and stale revisions are never inferred
  // from an old proposal. Quiet polls preserve this value and do not reread it.
  const evidenceVersion = JSON.stringify([
    snapshot.connection?.scope,
    snapshot.connection?.ownerScope,
    snapshot.runtime?.profile,
    evidenceId,
    snapshot.connection?.state,
    snapshot.runtime?.workspaceApplyEnabled,
    snapshot.requests?.map(request => [request.id, request.status, request.attempts, request.reason]),
    snapshot.tasks.map(task => [task.id, task.status, task.review, task.evidence?.map(evidence => evidence.id)])
  ])

  useEffect(() => {
    let current = true
    setPending(true)
    setArtifact(null)
    setError('')
    void adapter
      .getEvidence(evidenceId)
      .then(async value => {
        if (value.kind === 'integrated_deliverable') {await verifyDeliverable(value)}

        if (current) {
          setArtifact(value)
          setLoadedVersion(evidenceVersion)
          setPending(false)
        }
      })
      .catch(reason => {
        if (current) {
          setError(reason instanceof Error ? reason.message : copy.artifactError)
          setLoadedVersion(evidenceVersion)
          setPending(false)
        }
      })

    return () => {
      current = false
    }
  }, [adapter, evidenceId, evidenceVersion, attempt, copy.artifactError])

  const busy = pending || loadedVersion !== evidenceVersion

  return (
    <Inspector kind="artifact" onClose={onClose} origin={origin} title={title}>
      <Button aria-busy={busy} aria-disabled={busy} onClick={() => {
        if (busy) { return }
        setPending(true)
        setAttempt(value => value + 1)
      }} size="sm" variant="secondary">
        {busy ? copy.artifactLoading : error ? copy.artifactRetry : copy.artifactRefresh}
      </Button>
      {busy && <Loader label={copy.artifactLoading} />}
      {!busy && error && <p className="eid-artifact-error" role="alert">{error}</p>}
      {!busy && artifact && loadedVersion === evidenceVersion && (
        <>
          {artifact.kind === 'project_execution' && <h3>{t.organizationWork.projectExecutionEvidence}</h3>}
          {artifact.kind === 'source_integration' && <h3>{t.organizationWork.sourceIntegrationEvidence}</h3>}
          {artifact.editProposal && (
            <RuntimeEditProposal key={artifact.editProposal.id} proposal={artifact.editProposal} />
          )}
          {artifact.kind === 'integrated_deliverable' && (
            <RuntimeDeliverableHandoff adapter={adapter} artifact={artifact} key={`${evidenceVersion}:${artifact.id}:${artifact.sha256}`} snapshot={snapshot} />
          )}
          <p className="eid-result-text">{artifact.content}</p>
          <dl>
            <dt>{copy.artifactDigest}</dt>
            <dd className="eid-result-text">{artifact.sha256}</dd>
            <dt>{copy.artifactRecorded}</dt>
            <dd><LocalizedTime value={artifact.createdAt} /></dd>
            <dt>{copy.artifactTask}</dt>
            <dd>{artifact.taskId ?? t.organizationWork.notRecorded}</dd>
          </dl>
          <p className="eid-note">{copy.artifactLedgerNote}</p>
          <RuntimeToolReceipts artifact full receipts={artifact.toolReceipts} />
        </>
      )}
    </Inspector>
  )
}
