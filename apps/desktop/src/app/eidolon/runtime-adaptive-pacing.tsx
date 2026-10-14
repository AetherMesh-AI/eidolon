import { useI18n } from '@/i18n/context'
import { adaptivePacingCopy } from '@/i18n/organization-adaptive-pacing'
import { completionReserveCopy } from '@/i18n/organization-completion-reserve'

import { LocalizedTime } from './localized-time'
import { RuntimeBudgetAmounts } from './runtime-completion-reserve'
import type { OrganizationAdaptivePacing } from './types'

export interface RuntimeAdaptivePacingProps {
  pacing: OrganizationAdaptivePacing
  costEnabled: boolean
}

export function RuntimeAdaptivePacing({ pacing, costEnabled }: RuntimeAdaptivePacingProps) {
  const { locale } = useI18n()
  const copy = adaptivePacingCopy[locale]
  const reserveCopy = completionReserveCopy[locale]
  const number = new Intl.NumberFormat(locale, { maximumFractionDigits: 6 })
  const observation = pacing.observation

  return (
    <section aria-label={copy.heading}>
      <h3>{copy.heading}</h3>
      <dl>
        <dt>{copy.state}</dt>
        <dd>{copy[pacing.mode]}</dd>
        <dt>{copy.reason}</dt>
        <dd>{copy[pacing.reason]}</dd>
        <dt>{copy.evaluatedAt}</dt>
        <dd>
          <LocalizedTime value={pacing.evaluatedAt} />
        </dd>
        <dt>{copy.deferUntil}</dt>
        <dd>{pacing.deferUntil === null ? copy.noDeferral : <LocalizedTime value={pacing.deferUntil} />}</dd>
        <dt>{copy.maxInflight}</dt>
        <dd>
          <bdi>{number.format(pacing.maxInflight)}</bdi>
        </dd>
        <dt>{copy.minIntervalSeconds}</dt>
        <dd>
          <bdi>{number.format(pacing.minIntervalSeconds)}</bdi>
        </dd>
        <dt>{copy.remainingPercent}</dt>
        <dd>
          <bdi>{pacing.remainingPercent === null ? copy.unknown : number.format(pacing.remainingPercent)}</bdi>
        </dd>
        <dt>{reserveCopy.ordinaryRemaining}</dt>
        <dd>
          <RuntimeBudgetAmounts costEnabled={costEnabled} value={pacing.remaining} />
        </dd>
        <dt>{reserveCopy.protectedRemaining}</dt>
        <dd>
          <RuntimeBudgetAmounts costEnabled={costEnabled} value={pacing.protectedRemaining} />
        </dd>
      </dl>
      <p className="eid-note">{copy.note}</p>
      <p className="eid-note">{copy.boundary}</p>
      {observation ? (
        <section aria-label={copy.observation}>
          <h4>{copy.observation}</h4>
          <dl>
            <dt>{copy.source}</dt>
            <dd>{copy.owner_manual}</dd>
            <dt>{copy.observationState}</dt>
            <dd>{copy[observation.status]}</dd>
            <dt>{copy.reportedPercent}</dt>
            <dd>
              <bdi>{number.format(observation.remainingPercent)}</bdi>
            </dd>
            <dt>{copy.asOf}</dt>
            <dd>
              <LocalizedTime value={observation.asOf} />
            </dd>
            <dt>{copy.expiresAt}</dt>
            <dd>
              <LocalizedTime value={observation.expiresAt} />
            </dd>
            <dt>{copy.resetAt}</dt>
            <dd>{observation.resetAt === null ? copy.noReset : <LocalizedTime value={observation.resetAt} />}</dd>
          </dl>
        </section>
      ) : (
        <p className="eid-note">{copy.noObservation}</p>
      )}
    </section>
  )
}
