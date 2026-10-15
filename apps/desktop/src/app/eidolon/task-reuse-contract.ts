import type { OrganizationTask } from './types'

export function validTaskReuse(task: OrganizationTask): boolean {
  if (task.reuse == null) { return true }
  const record = task.reuse

  return record.disposition === 'reused' && task.status === 'completed' && task.currentRound === true && task.historical === false &&
    Number.isInteger(record.round) && record.round > 0 && Number.isInteger(task.originalRound) && task.originalRound! >= 0 && task.originalRound! < record.round &&
    ['evidenceId', 'reviewRequestId'].every(key => typeof record[key as keyof typeof record] === 'string' && Boolean(record[key as keyof typeof record])) &&
    ['evidenceSha256', 'inputSha256'].every(key => typeof record[key as keyof typeof record] === 'string' && /^[a-f0-9]{64}$/.test(String(record[key as keyof typeof record])))
}

