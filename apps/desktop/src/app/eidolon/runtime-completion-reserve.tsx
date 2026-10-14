import { Fragment } from 'react'

import { useI18n } from '@/i18n/context'
import { completionReserveCopy } from '@/i18n/organization-completion-reserve'

import { completionReserveShortfall } from './runtime-completion-reserve-contract'
import type {
  OrganizationBudgetAmounts,
  OrganizationCompletionReserve,
  OrganizationCompletionReservePolicy
} from './types'

export function RuntimeBudgetAmounts({
  value,
  costEnabled,
  captured = false
}: {
  value: OrganizationBudgetAmounts
  costEnabled: boolean
  captured?: boolean
}) {
  const { locale } = useI18n()
  const copy = completionReserveCopy[locale]
  const number = new Intl.NumberFormat(locale)

  return (
    <>
      {(['modelCalls', 'tokens', 'configuredCostUsd'] as const).map((key, index) => (
        <span key={key}>
          {index > 0 && ' · '}
          {copy[key]}:{' '}
          <bdi>
            {value[key] === null
              ? key === 'configuredCostUsd' && (!costEnabled || captured)
                ? copy.notConfigured
                : copy.unknown
              : typeof value[key] === 'number'
                ? number.format(value[key])
                : value[key]}
          </bdi>
        </span>
      ))}
    </>
  )
}

export function RuntimeCompletionReserve({
  reserve,
  costEnabled
}: {
  reserve: OrganizationCompletionReserve
  costEnabled: boolean
}) {
  const { locale } = useI18n()
  const copy = completionReserveCopy[locale]

  return (
    <section aria-label={copy.heading}>
      <h3>{copy.heading}</h3>
      <dl>
        {(['totalRemaining', 'ordinaryRemaining', 'protectedRemaining', 'completionReserved', 'captured'] as const).map(
          key => (
            <Fragment key={key}>
              <dt>{copy[key]}</dt>
              <dd>
                <RuntimeBudgetAmounts captured={key === 'captured'} costEnabled={costEnabled} value={reserve[key]} />
              </dd>
            </Fragment>
          )
        )}
      </dl>
      {completionReserveShortfall(reserve) && <p className="eid-note">{copy.shortfall}</p>}
      {(!reserve.admissionUsageKnown || reserve.unavailableReason !== null) && (
        <p className="eid-note">{copy.unavailable}</p>
      )}
      <p className="eid-note">{copy.note}</p>
      <p className="eid-note">{copy.boundary}</p>
    </section>
  )
}

export function RuntimeCompletionReserveAllowance({
  reserve,
  costEnabled
}: {
  reserve: OrganizationCompletionReservePolicy
  costEnabled: boolean
}) {
  const { locale } = useI18n()
  const copy = completionReserveCopy[locale]

  return (
    <>
      <dt>{copy.captured}</dt>
      <dd>
        <RuntimeBudgetAmounts captured costEnabled={costEnabled} value={reserve.captured} />
      </dd>
    </>
  )
}
