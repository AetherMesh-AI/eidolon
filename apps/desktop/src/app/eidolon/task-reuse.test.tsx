import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { selectiveRepairCopy } from '@/i18n/organization-selective-repair'

import { TaskReuse } from './task-reuse'
import { validTaskReuse } from './task-reuse-contract'
import type { OrganizationTask } from './types'

const task = { id: 'a', status: 'completed', currentRound: true, historical: false, originalRound: 0,
  reuse: { disposition: 'reused', round: 1, evidenceId: 'e', reviewRequestId: 'r', evidenceSha256: 'a'.repeat(64), inputSha256: 'b'.repeat(64) }
} as OrganizationTask

describe('retained evidence labeling', () => {
  it('clearly identifies reuse rather than a fresh execution', () => {
    render(<I18nProvider><TaskReuse task={task} /></I18nProvider>)
    expect(screen.getByText('Reused reviewed output')).toBeTruthy()
    expect(screen.getByText(/No new execution/)).toBeTruthy()
  })
  it('rejects stale round, noncompleted task and malformed proof hashes', () => {
    expect(validTaskReuse(task)).toBe(true)
    expect(validTaskReuse({ ...task, currentRound: false })).toBe(false)
    expect(validTaskReuse({ ...task, originalRound: 1 })).toBe(false)
    expect(validTaskReuse({ ...task, status: 'working' })).toBe(false)
    expect(validTaskReuse({ ...task, reuse: { ...task.reuse!, inputSha256: 'forged' } })).toBe(false)
  })
  it('has complete copy in every supported language', () => {
    expect(Object.keys(selectiveRepairCopy)).toHaveLength(6)

    for (const copy of Object.values(selectiveRepairCopy)) {
      expect(copy.reused).toBeTruthy()
      expect(copy.detail).toBeTruthy()
      expect(copy.invalid).toBeTruthy()
    }
  })
})
