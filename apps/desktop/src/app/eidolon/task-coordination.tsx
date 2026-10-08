import { useI18n } from '@/i18n/context'
import type { OrganizationWorkCopy } from '@/i18n/organization-work'

import type { OrganizationTask } from './types'

export function taskCoordinationLabel(task: OrganizationTask, copy: OrganizationWorkCopy): string | null {
  if (!task.coordination) {
    return null
  }

  return {
    unscoped: copy.coordinationUnscoped,
    ready: copy.coordinationReady,
    waiting: copy.coordinationWaiting,
    reserved: copy.coordinationReserved,
    released: copy.coordinationReleased
  }[task.coordination.state]
}

interface TaskCoordinationProps {
  task: OrganizationTask
  tasks: OrganizationTask[]
}

/** Scheduling facts never replace a task's lifecycle or imply an execution. */
export function TaskCoordination({ task, tasks }: TaskCoordinationProps) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const label = taskCoordinationLabel(task, copy)

  if (!task.coordination && !task.writePaths?.length && !task.projectId) {
    return null
  }

  const blockers = task.coordination?.blockingTaskIds.map(id => {
    const blocker = tasks.find(item => item.id === id)

    return blocker ? `${blocker.title} (${id})` : id
  }) ?? []

  return <span className="flex flex-col gap-1" style={{ overflowWrap: 'anywhere' }}>
    {task.projectId && <small>{copy.projectBinding}: {task.projectId}</small>}
    {label && <small>{copy.taskCoordination}: {label}</small>}
    {task.coordination?.reason && <small>{copy.coordinationReason}: {task.coordination.reason}</small>}
    {blockers.length > 0 && <small>{copy.coordinationBlockers}: {blockers.join(', ')}</small>}
    <small>{copy.declaredWritePaths}: {task.writePaths?.join(', ') || copy.writePathsUnspecified}</small>
  </span>
}
