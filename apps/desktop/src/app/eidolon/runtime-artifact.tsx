import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n/context'

import { Inspector } from './inspector'
import { RuntimeEditProposal } from './runtime-edit-proposal'
import { RuntimeToolReceipts } from './runtime-tool-receipts'
import type { OrganizationArtifact, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

interface RuntimeArtifactProps {
  adapter: RuntimeOrganizationAdapter
  snapshot: OrganizationSnapshot
  evidenceId: string
  title: string
  onClose(): void
}

export function RuntimeArtifact({ adapter, snapshot, evidenceId, title, onClose }: RuntimeArtifactProps) {
  const { t } = useI18n()
  const copy = t.organizationRuntime
  const [artifact, setArtifact] = useState<OrganizationArtifact | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)

  // Full evidence is read on demand. Refresh while open when authoritative work
  // state changes so approved, applied and stale revisions are never inferred
  // from an old proposal. Quiet polls preserve this value and do not reread it.
  const evidenceVersion = JSON.stringify([
    snapshot.connection?.scope,
    snapshot.connection?.state,
    snapshot.runtime?.workspaceApplyEnabled,
    snapshot.requests?.map(request => [request.id, request.status, request.attempts, request.reason]),
    snapshot.tasks.map(task => [task.id, task.status, task.review, task.evidence?.map(evidence => evidence.id)])
  ])

  useEffect(() => {
    let current = true
    setArtifact(null)
    setError('')
    void adapter
      .getEvidence(evidenceId)
      .then(value => {
        if (current) {
          setArtifact(value)
        }
      })
      .catch(reason => {
        if (current) {
          setError(reason instanceof Error ? reason.message : copy.artifactError)
        }
      })

    return () => {
      current = false
    }
  }, [adapter, evidenceId, evidenceVersion, attempt, copy.artifactError])

  return (
    <Inspector kind="artifact" onClose={onClose} title={title}>
      {!artifact && !error && <Loader label={copy.artifactLoading} />}
      {error && (
        <>
          <p role="alert">{error}</p>
          <Button onClick={() => setAttempt(value => value + 1)} size="sm" variant="secondary">
            {copy.artifactRetry}
          </Button>
        </>
      )}
      {artifact && (
        <>
          <Button onClick={() => setAttempt(value => value + 1)} size="sm" variant="secondary">
            {copy.artifactRefresh}
          </Button>
          {artifact.kind === 'project_execution' && <h3>{t.organizationWork.projectExecutionEvidence}</h3>}
          {artifact.kind === 'source_integration' && <h3>{t.organizationWork.sourceIntegrationEvidence}</h3>}
          {artifact.editProposal && (
            <RuntimeEditProposal key={artifact.editProposal.id} proposal={artifact.editProposal} />
          )}
          <p className="eid-result-text">{artifact.content}</p>
          <dl>
            <dt>{copy.artifactDigest}</dt>
            <dd className="eid-result-text">{artifact.sha256}</dd>
            <dt>{copy.artifactRecorded}</dt>
            <dd>{new Date(artifact.createdAt).toLocaleString()}</dd>
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
