import { useI18n } from '@/i18n/context'

import type { Objective } from './types'

/** Read-only findings from the current round; never infer final acceptance. */
export function ObjectiveReviewFindings({ objective }: { objective: Objective }) {
  const { t, locale } = useI18n()
  const copy = t.organizationHome.detail.reviewFindings
  const findings = objective.acceptance?.criteriaResults ?? []
  const conflicts = objective.acceptance?.conflicts ?? []
  const unmet = findings.filter(finding => finding.satisfied === false).length

  if (objective.source !== 'runtime' || (!findings.length && !conflicts.length)) { return null }

  return <section aria-label={copy.heading} className="eid-home-panel eid-review-findings">
    <header><h2>{copy.heading}</h2><p>{copy.note}</p>{unmet > 0 && <span className="eid-review-unmet eid-status-blocked">{copy.unmetCriteria} · {new Intl.NumberFormat(locale).format(unmet)}</span>}</header>
    {!!conflicts.length && <section aria-label={copy.conflicts}>
      <h3>{copy.conflicts}</h3>
      <ul>{conflicts.map((conflict, index) => <li className="eid-result-text" dir="auto" key={index}>{conflict}</li>)}</ul>
    </section>}
    {!!findings.length && <details>
      <summary>{copy.criteria} · {new Intl.NumberFormat(locale).format(findings.length)}</summary>
      <ol aria-label={copy.criteria}>
        {findings.map((finding, index) => <li key={index}>
          <strong dir="auto">{finding.criterion}</strong>
          <span className={finding.satisfied ? 'eid-status-completed' : 'eid-status-blocked'}>{finding.satisfied ? copy.satisfied : copy.unsatisfied}</span>
          <p className="eid-result-text" dir="auto">{finding.reason}</p>
          {!!finding.evidenceIds.length && <small>{copy.evidence}: <bdi>{finding.evidenceIds.join(', ')}</bdi></small>}
        </li>)}
      </ol>
    </details>}
  </section>
}
