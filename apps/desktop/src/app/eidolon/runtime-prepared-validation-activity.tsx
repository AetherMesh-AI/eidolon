import { useI18n } from '@/i18n/context'

import { preparedValidationActivityCopy } from './runtime-prepared-validation-activity-copy'
import type { PreparedValidationSetStatus, PreparedValidationStepStatus } from './runtime-prepared-validation-status-types'
import type { Objective } from './types'

type ActivityCopy = typeof preparedValidationActivityCopy.en

function RecordedStep({ step, copy }: { step: PreparedValidationStepStatus; copy: ActivityCopy }) {
  return <li>
    <h6>{copy[step.recipe]}</h6>
    <dl>
      <dt>{copy.run}</dt><dd className="eid-result-text">{step.runId}</dd>
      <dt>{copy.step}</dt><dd className="eid-result-text">{step.stepId}</dd>
      <dt>{copy.reservedUnits}</dt><dd>{step.reservedUnits}</dd>
      <dt>{copy.result}</dt><dd>{step.resultClaimStatus === null ? copy.noResult : copy[step.resultClaimStatus]}</dd>
      <dt>{copy.cleanupSequence}</dt><dd>{step.latestCleanupSequence ?? copy.notRecorded}</dd>
      <dt>{copy.eventSequence}</dt><dd>{step.latestRuntimeEventSequence ?? copy.notRecorded}</dd>
      <dt>{copy.eventKind}</dt><dd>{step.latestRuntimeEventKind === null ? copy.notRecorded : copy[step.latestRuntimeEventKind]}</dd>
    </dl>
    <p>{step.dispatched ? copy.dispatched : copy.noDispatch}</p>
    <p>{step.resourceClaimed ? copy.resourceClaim : copy.noResourceClaim}</p>
    <p>{step.cleanupClaimSettled ? copy.cleanupSettled : copy.noCleanupSettled}</p>
    <ul aria-label={copy.blockers}>{step.blockerReasons.map(reason => <li key={reason}>{copy[reason]}</li>)}</ul>
  </li>
}

function RecordedSet({ set, copy }: { set: PreparedValidationSetStatus; copy: ActivityCopy }) {
  return <li>
    <details>
      <summary>{set.projectId} · {copy.generation}: {set.generation} · {copy[set.state]}</summary>
      <h5>{copy.set}: {set.setId}</h5>
      <dl>
        <dt>{copy.request}</dt><dd className="eid-result-text">{set.requestId}</dd>
        <dt>{copy.round}</dt><dd>{set.round}</dd>
        <dt>{copy.generation}</dt><dd>{set.generation}</dd>
        <dt>{copy.reservedUnits}</dt><dd>{set.reservedUnits}</dd>
        <dt>{copy.snapshot}</dt><dd className="eid-result-text">{set.snapshotSha256}</dd>
      </dl>
      <p>{set.aggregateClaimPresent ? copy.aggregateClaim : copy.noAggregateClaim}</p>
      <p>{set.reviewClaimPresent ? copy.reviewClaim : copy.noReviewClaim}</p>
      <p>{set.pairReviewRecorded ? copy.pairReview : copy.noPairReview}</p>
      <ol aria-label={copy.steps}>{set.steps.map(step => <RecordedStep copy={copy} key={step.runId} step={step} />)}</ol>
    </details>
  </li>
}

interface RuntimePreparedValidationActivityProps {
  objective: Objective
}

/** Scalar records are display-only; evidence and authorization remain outside this view. */
export function RuntimePreparedValidationActivity({ objective }: RuntimePreparedValidationActivityProps) {
  const { locale } = useI18n()
  const copy = preparedValidationActivityCopy[locale]
  const references = objective.preparedValidations ?? []
  const status = objective.preparedValidation

  if (!references.length && !status) { return null }

  return <section aria-label={copy.title}>
    <h3>{copy.title}</h3>
    <p className="eid-note">{copy.note}</p>
    <h4>{copy.references}</h4>
    <p className="eid-note">{copy.referenceNote}</p>
    {references.length ? <ul aria-label={copy.references}>
      {references.map(reference => <li key={reference.projectId}>
        <dl>
          <dt>{copy.project}</dt><dd>{reference.projectId}</dd>
          <dt>{copy.revision}</dt><dd>{reference.revision}</dd>
          <dt>{copy.registrationHash}</dt><dd className="eid-result-text">{reference.registrationSha256}</dd>
        </dl>
      </li>)}
    </ul> : <p>{copy.noReferences}</p>}
    {!status ? <p>{copy.unavailable}</p> : <>
      <h4>{copy.counts}</h4>
      <dl aria-label={copy.counts}>
        <dt>{copy.storage}</dt><dd>{copy[status.storageState]}</dd>
        <dt>{copy.legacyUnits}</dt><dd>{status.legacyUnits}</dd>
        <dt>{copy.preparedUnits}</dt><dd>{status.preparedUnits}</dd>
        <dt>{copy.totalUnits}</dt><dd>{status.totalUnits}</dd>
        <dt>{copy.totalSets}</dt><dd>{status.totalSets}</dd>
        <dt>{copy.totalHistory}</dt><dd>{status.totalHistory}</dd>
      </dl>
      <p className="eid-note">{copy.twoUnits}</p>
      <h4>{copy.current}</h4>
      {Object.keys(status.current).length ? <ul aria-label={copy.current}>
        {Object.values(status.current).map(set => <RecordedSet copy={copy} key={set.setId} set={set} />)}
      </ul> : <p>{copy.noCurrent}</p>}
      <details>
        <summary>{copy.history} ({status.history.length} / {status.totalHistory})</summary>
        <p className="eid-note">{copy.historyNote}</p>
        {status.historyTruncated && <p>{copy.historyTruncated}</p>}
        {status.history.length ? <ol aria-label={copy.history}>
          {status.history.map(set => <RecordedSet copy={copy} key={set.setId} set={set} />)}
        </ol> : <p>{copy.noHistory}</p>}
      </details>
      <details>
        <summary>{copy.blockers}</summary>
        <p>{copy.blockerCount}: {status.blockers.totalBlockers}</p>
        <p className="eid-note">{copy.blockersNote}</p>
        {status.blockers.truncated && <p>{copy.blockersTruncated}</p>}
        {status.blockers.entries.length ? <ul aria-label={copy.blockers}>
          {status.blockers.entries.map(entry => <li key={`${entry.setId}:${entry.runId}`}>
            <p>{copy.project}: {entry.projectId} · {copy.generation}: {entry.generation}</p>
            <p className="eid-result-text">{copy.set}: {entry.setId} · {copy.run}: {entry.runId}</p>
            <ul>{entry.reasons.map(reason => <li key={reason}>{copy[reason]}</li>)}</ul>
          </li>)}
        </ul> : <p>{copy.noBlockers}</p>}
      </details>
    </>}
  </section>
}
