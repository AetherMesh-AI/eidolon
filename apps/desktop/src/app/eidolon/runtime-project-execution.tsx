import { Button } from '@/components/ui/button'
import { LogView } from '@/components/ui/log-view'
import { useI18n } from '@/i18n/context'

import type { OrganizationProjectExecution } from './types'

interface RuntimeProjectExecutionProps {
  projectId?: string
  execution?: OrganizationProjectExecution | null
  onOpenEvidence(id: string): void
}

export function RuntimeProjectExecution({ execution, projectId, onOpenEvidence }: RuntimeProjectExecutionProps) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const receipt = execution?.receipt
  const integration = execution?.sourceIntegration

  const statuses = {
    passed: copy.passed,
    failed: copy.failed,
    blocked: copy.blocked,
    unsupported: copy.projectUnsupported,
    cancelled: copy.cancelled,
    timed_out: copy.projectTimedOut,
    unknown: copy.unknownUsage
  }

  return (
    <section aria-label={projectId ? `${copy.projectExecution}: ${projectId}` : copy.projectExecution}>
      <h3>{copy.projectExecution}</h3>
      {projectId && <p>{copy.projectBinding}: {projectId}</p>}
      <p className="eid-note">{copy.projectExecutionNote}</p>
      {!execution || !receipt ? (
        <p>{copy.noProjectExecution}</p>
      ) : (
        <>
          <dl>
            <dt>{copy.status}</dt>
            <dd>{statuses[execution.status] ?? copy.unknownUsage}</dd>
            <dt>{copy.executionId}</dt>
            <dd className="eid-result-text">{execution.id}</dd>
            <dt>{copy.requests}</dt>
            <dd className="eid-result-text">{execution.requestId}</dd>
            <dt>{copy.round}</dt>
            <dd>{execution.round}</dd>
            <dt>{copy.snapshotDigest}</dt>
            <dd className="eid-result-text">{execution.snapshotSha256}</dd>
            <dt>{copy.projectTestCount}</dt>
            <dd>{receipt.testCount ?? copy.notRecorded}</dd>
            <dt>{copy.projectExitCode}</dt>
            <dd>{receipt.exitCode ?? copy.notRecorded}</dd>
            <dt>{copy.projectDuration}</dt>
            <dd>{receipt.durationSeconds ?? copy.notRecorded}</dd>
            <dt>{copy.projectCommand}</dt>
            <dd>{receipt.command?.length ? <LogView>{JSON.stringify(receipt.command)}</LogView> : copy.notRecorded}</dd>
            <dt>{copy.projectIsolation}</dt>
            <dd>
              {receipt.isolation
                ? `${receipt.isolation.backend} · ${receipt.isolation.established ? copy.isolationEstablished : copy.isolationNotEstablished}`
                : copy.notRecorded}
            </dd>
          </dl>
          {receipt.reason && <p className="eid-result-text">{receipt.reason}</p>}
          {execution.status === 'unknown' && <p className="eid-note">{copy.projectUnknownNote}</p>}
          {receipt.isolation && (
            <details>
              <summary>{copy.isolationDetails}</summary>
              <LogView>{JSON.stringify(receipt.isolation, null, 2)}</LogView>
            </details>
          )}
          <details>
            <summary>
              {copy.testedFiles} ({execution.files.length})
            </summary>
            <ul>
              {execution.files.map(file => (
                <li key={file.path}>
                  <p className="eid-result-text">{file.path}</p>
                  <p>
                    {copy.projectRevision}: {file.revision}
                  </p>
                  <p className="eid-result-text">{file.sha256}</p>
                </li>
              ))}
            </ul>
          </details>
          <section aria-label={copy.projectReview}>
            <h4>{copy.projectReview}</h4>
            {execution.review ? (
              <>
                <p>{execution.review.approved ? copy.approved : copy.denied}</p>
                <dl>
                  <dt>{copy.projectReviewer}</dt>
                  <dd>{execution.review.reviewerId}</dd>
                  <dt>{copy.requests}</dt>
                  <dd>{execution.review.requestId}</dd>
                </dl>
                <p className="eid-result-text">{execution.review.summary}</p>
              </>
            ) : (
              <p>{copy.notRecorded}</p>
            )}
          </section>
          <section aria-label={copy.projectSourceIntegration}>
            <h4>{copy.projectSourceIntegration}</h4>
            {integration ? (
              <>
                <p>{copy.sourceBranchCreated}</p>
                <dl>
                  <dt>{copy.sourceBranch}</dt>
                  <dd className="eid-result-text">{integration.ref}</dd>
                  <dt>{copy.sourceBaseCommit}</dt>
                  <dd className="eid-result-text">{integration.sourceBaseCommit}</dd>
                  <dt>{copy.sourceCommit}</dt>
                  <dd className="eid-result-text">{integration.commit}</dd>
                  <dt>{copy.sourceTree}</dt>
                  <dd className="eid-result-text">{integration.tree}</dd>
                  <dt>{copy.sourceManifestDigest}</dt>
                  <dd className="eid-result-text">{integration.manifestSha256}</dd>
                </dl>
                <p className="eid-note">{copy.sourceBranchNote}</p>
                <Button onClick={() => onOpenEvidence(integration.evidenceId)} size="sm" variant="secondary">
                  {copy.openSourceIntegration}
                </Button>
              </>
            ) : (
              <p>{copy.noSourceIntegration}</p>
            )}
          </section>
          {execution.status !== 'unknown' && (
            <Button onClick={() => onOpenEvidence(execution.id)} size="sm" variant="secondary">
              {copy.openProjectExecution}
            </Button>
          )}
        </>
      )}
    </section>
  )
}
