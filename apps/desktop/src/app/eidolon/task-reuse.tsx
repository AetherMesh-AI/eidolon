import { useI18n } from '@/i18n/context'
import { selectiveRepairCopy } from '@/i18n/organization-selective-repair'

import { validTaskReuse } from './task-reuse-contract'
import type { OrganizationTask } from './types'

export function TaskReuse({ task }: { task: OrganizationTask }) {
  const { locale } = useI18n()

  if (!task.reuse || !validTaskReuse(task)) { return null }
  const copy = selectiveRepairCopy[locale]

  return <span className="flex flex-col gap-1"><small>{copy.reused}</small><small>{copy.detail}</small></span>
}
