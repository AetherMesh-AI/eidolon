import { useI18n } from '@/i18n/context'

import { LocalizedTime } from './localized-time'
import type { Objective } from './types'

export function MetadataSummary({ objective }: { objective: Objective }) {
  const { locale, t } = useI18n()
  const copy = t.organizationFoundation
  const runtime = objective.source === 'runtime'
  const progress = objective.progress === undefined ? copy.unknown : new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 2 }).format(objective.progress / 100)

  return <><dl>
    <dt>{copy.priority}</dt><dd>{objective.priority ? copy.priorities[objective.priority] : copy.notSet}</dd>
    <dt>{copy.progress}</dt><dd>{objective.progress === undefined ? progress : runtime ? copy.completedProgress(progress) : copy.estimatedProgress(progress)}</dd>
    <dt>{copy.currentPhase}</dt><dd>{objective.phase || copy.notRecorded}</dd>
    <dt>{copy.milestone}</dt><dd>{objective.milestone || copy.notRecorded}</dd>
    {objective.source === 'prototype' && <><dt>{copy.autonomyIntent}</dt><dd>{objective.autonomyIntent || copy.notSpecified}</dd></>}
    <dt>{runtime ? copy.created : copy.createdLocally}</dt><dd><LocalizedTime value={objective.createdAt} /></dd>
  </dl><p className="eid-note">{runtime ? copy.runtimeProgressNote : copy.localProgressNote}</p></>
}
