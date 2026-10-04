import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { RuntimeAcceptance } from './runtime-acceptance'
import type { Objective } from './types'

it('separates accepted result, aggregate validation and bounded replan state with provenance-linked reads', () => {
  const objective: Objective = {
    id: 'objective', title: 'Update project', description: 'Reviewed project changes', ownerId: 'executive', status: 'active', source: 'runtime', createdAt: '2026-10-04T00:00:00Z',
    acceptance: { status: 'replanning', criteria: ['Preserve behavior', 'Deliver reviewed bytes'], round: 2, maxReplans: 2, summary: 'Correct the missing setting.', deliverableId: 'integrated-draft' },
    requiredChecks: ['managed_validation'],
    projectValidation: { id: 'final-validation', status: 'passed', scope: 'latest_managed_project_heads', resultSha256: 'a'.repeat(64), filesCount: 2, checksCount: 4, notExecuted: ['functional_tests'] },
    usage: { stages: 8, stageLimit: 16, inputTokens: 100, outputTokens: 200, usageComplete: false, perCallOutputLimit: 2000 }
  }

  const open = vi.fn()
  const view = render(<RuntimeAcceptance objective={objective} onOpenEvidence={open} />)
  expect(screen.getByText('Replanning')).toBeTruthy()
  expect(screen.getByText('No accepted final result yet.')).toBeTruthy()
  expect(screen.getByText(/Usage is incomplete/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Final managed validation' }))
  expect(open).toHaveBeenLastCalledWith('final-validation')
  view.rerender(<RuntimeAcceptance objective={{ ...objective, status: 'completed', result: 'The accepted final deliverable.', acceptance: { ...objective.acceptance!, status: 'accepted', deliverableId: 'final-result' } }} onOpenEvidence={open} />)
  expect(screen.getByText('Accepted')).toBeTruthy()
  expect(screen.getByText('The accepted final deliverable.')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Read final deliverable' }))
  expect(open).toHaveBeenLastCalledWith('final-result')
  expect(screen.queryByRole('button', { name: /mark|approve|complete/i })).toBeNull()
})
