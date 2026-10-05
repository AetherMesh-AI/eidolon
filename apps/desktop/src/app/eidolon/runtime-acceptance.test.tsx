import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { RuntimeAcceptance } from './runtime-acceptance'
import type { Objective } from './types'

it('separates accepted result, aggregate validation and bounded replan state with provenance-linked reads', () => {
  const objective: Objective = {
    id: 'objective',
    title: 'Update project',
    description: 'Reviewed project changes',
    ownerId: 'executive',
    status: 'active',
    source: 'runtime',
    createdAt: '2026-10-04T00:00:00Z',
    acceptance: {
      status: 'replanning',
      criteria: ['Preserve behavior', 'Deliver reviewed bytes'],
      round: 2,
      maxReplans: 2,
      summary: 'Correct the missing setting.',
      deliverableId: 'integrated-draft'
    },
    requiredChecks: ['managed_validation'],
    projectValidation: {
      id: 'final-validation',
      status: 'passed',
      scope: 'latest_managed_project_heads',
      resultSha256: 'a'.repeat(64),
      filesCount: 2,
      checksCount: 4,
      notExecuted: ['functional_tests']
    },
    usage: {
      stages: 8,
      stageLimit: 16,
      inputTokens: 100,
      outputTokens: 200,
      usageComplete: false,
      perCallOutputLimit: 2000
    }
  }

  const open = vi.fn()
  const view = render(<RuntimeAcceptance objective={objective} onOpenEvidence={open} />)
  expect(screen.getByText('Replanning')).toBeTruthy()
  expect(screen.getByText('No accepted final result yet.')).toBeTruthy()
  expect(screen.getByText(/Usage is incomplete/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Final managed validation' }))
  expect(open).toHaveBeenLastCalledWith('final-validation')
  view.rerender(
    <RuntimeAcceptance
      objective={{
        ...objective,
        status: 'completed',
        result: 'The accepted final deliverable.',
        acceptance: { ...objective.acceptance!, status: 'accepted', deliverableId: 'final-result' }
      }}
      onOpenEvidence={open}
    />
  )
  expect(screen.getByText('Accepted')).toBeTruthy()
  expect(screen.getByText('The accepted final deliverable.')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Read final deliverable' }))
  expect(open).toHaveBeenLastCalledWith('final-result')
  expect(screen.queryByRole('button', { name: /mark|approve|complete/i })).toBeNull()
})

it('shows durable reservation ceilings and unknown cost without inventing billing or older-runtime limits', () => {
  const objective: Objective = {
    id: 'objective',
    title: 'Budgeted work',
    description: 'Bounded execution',
    ownerId: 'executive',
    status: 'needs_input',
    source: 'runtime',
    createdAt: '2026-10-04T00:00:00Z',
    usage: {
      stages: 8,
      stageLimit: 16,
      inputTokens: 100,
      outputTokens: 200,
      usageComplete: false,
      perCallOutputLimit: 2000,
      modelCalls: 9,
      modelCallLimit: 12,
      reservedTokens: 9000,
      tokenLimit: 15000,
      deadlineAt: '2026-10-06T08:00:00Z',
      configuredCostReservedUsd: null,
      configuredCostLimitUsd: '2.50',
      legacyUsageUnknown: true,
      budgetScope: 'Durable reservation policy'
    }
  }

  const view = render(<RuntimeAcceptance objective={objective} onOpenEvidence={vi.fn()} />)
  expect(screen.getByText('9 / 12')).toBeTruthy()
  expect(screen.getByText('9000 / 15000')).toBeTruthy()
  expect(screen.getByText('Unknown / 2.50')).toBeTruthy()
  expect(
    screen.getByText('Objective deadline (includes owner wait)').nextElementSibling?.querySelector('time')
  ).toHaveProperty('dateTime', objective.usage!.deadlineAt)
  expect(screen.getByText(/Durable reservations include interrupted or unreported/)).toBeTruthy()
  expect(screen.getByText(/not current provider prices or a bill/)).toBeTruthy()
  expect(screen.getByText(/Earlier model usage is unknown/)).toBeTruthy()
  view.rerender(
    <RuntimeAcceptance
      objective={{
        ...objective,
        usage: { ...objective.usage!, configuredCostReservedUsd: '0.25', configuredCostLimitUsd: null }
      }}
      onOpenEvidence={vi.fn()}
    />
  )
  expect(screen.queryByText('Configured cost reservations / limit (USD)')).toBeNull()
  view.rerender(
    <RuntimeAcceptance
      objective={{
        ...objective,
        usage: {
          stages: 8,
          stageLimit: 16,
          inputTokens: 100,
          outputTokens: 200,
          usageComplete: false,
          perCallOutputLimit: 2000
        }
      }}
      onOpenEvidence={vi.fn()}
    />
  )
  expect(screen.queryByText('Model calls reserved / limit')).toBeNull()
  expect(screen.queryByText('Token reservations / limit')).toBeNull()
  expect(screen.queryByText('Objective deadline (includes owner wait)')).toBeNull()
  expect(screen.getByText('8 / 16')).toBeTruthy()
})
