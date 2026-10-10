import { useI18n } from '@/i18n/context'

import { LocalizedTime } from './localized-time'
import type { OrganizationEditProposal, OrganizationValidationResult } from './types'

function ValidationChecks({ result }: { result: OrganizationValidationResult }) {
  const { t } = useI18n()
  const copy = t.organizationWork

  return (
    <>
      <p>{copy[result.status]}</p>
      <ol>
        {result.checks.map((check, index) => (
          <li key={`${check.path}:${check.kind}:${index}`}>
            <strong>{check.path}</strong> · {check.kind} · {check.passed ? copy.passed : copy.failed}
            {check.reason && <p>{check.reason}</p>}
          </li>
        ))}
      </ol>
      <p>
        {copy.notExecuted}: {result.notExecuted.join(', ')}
      </p>
      <p className="eid-note">{result.limitations}</p>
    </>
  )
}

export function RuntimeProjectReceipts({ proposal }: { proposal: OrganizationEditProposal }) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const validation = proposal.validationReceipt
  const source = proposal.sourceVerificationReceipt

  return (
    <>
      <section aria-label={copy.validation}>
        <h4>{copy.validation}</h4>
        <p className="eid-note">{copy.validationNote}</p>
        {validation ? (
          <>
            <ValidationChecks result={validation} />
            <p>
              {copy.requests}: {validation.requestId}
            </p>
            <p className="eid-result-text">
              {copy.receiptDigest}: {validation.resultSha256}
            </p>
            <time dateTime={new Date(validation.created * 1000).toISOString()}>
              <LocalizedTime value={validation.created * 1000} />
            </time>
          </>
        ) : (
          <p>{copy.notRecorded}</p>
        )}
      </section>
      <section aria-label={copy.sourceVerification}>
        <h4>{copy.sourceVerification}</h4>
        <p className="eid-note">{copy.sourceObservationNote}</p>
        {source ? (
          <>
            <p>{copy[source.status]}</p>
            <p className="eid-note">{source.limitations}</p>
            <p>
              {copy.requests}: {source.requestId}
            </p>
            <p className="eid-result-text">
              {copy.receiptDigest}: {source.resultSha256}
            </p>
            <time dateTime={new Date(source.created * 1000).toISOString()}>
              <LocalizedTime value={source.created * 1000} />
            </time>
            <h5>{copy.observedFiles}</h5>
            <ul>
              {source.files.map(file => (
                <li className="eid-result-text" key={file.path}>
                  {file.path} · {file.revision} · {file.sha256} · {file.proposalId}
                </li>
              ))}
            </ul>
            <details>
              <summary>{copy.validation}</summary>
              <ValidationChecks result={source.manifestValidation} />
              <ul>
                {source.validations.map(item => (
                  <li className="eid-result-text" key={item.proposalId}>
                    {item.proposalId} · {item.status === 'unknown' ? copy.notRecorded : copy[item.status]} ·{' '}
                    {item.validationSha256 ?? copy.notRecorded}
                  </li>
                ))}
              </ul>
            </details>
          </>
        ) : (
          <p>{copy.notRecorded}</p>
        )}
      </section>
    </>
  )
}
