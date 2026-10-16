import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { RuntimeAcceptance } from './runtime-acceptance'
import { preparedObjectiveCopy } from './runtime-prepared-objective-copy'
import { ScopeAmendmentFields } from './runtime-scope-amendment'
import type { Objective } from './types'

describe('prepared typecheck read-only labels', () => {
  it('shows the returned mandatory check without claiming it passed', () => {
    const objective: Objective = { id: 'objective', title: 'Prepared objective', description: 'Explicit selection', ownerId: 'owner', status: 'active', source: 'runtime',
      createdAt: '2026-10-16T00:00:00Z', requiredChecks: ['project_typecheck'] }

    render(<RuntimeAcceptance objective={objective} onOpenEvidence={() => {}} />)
    expect(screen.getByText(preparedObjectiveCopy.en.projectTypecheck)).toBeTruthy()
    expect(screen.queryByText('Passed')).toBeNull()
  })
  it('labels the current check without offering a new profile or typecheck amendment choice', () => {
    render(<ScopeAmendmentFields currentChecks={['project_typecheck']} disabled={false}
      draft={{ criteria: '', requiredChecks: ['project_typecheck'] }} onChange={() => {}} />)
    expect(screen.getByText(preparedObjectiveCopy.en.projectTypecheck)).toBeTruthy()
    expect(screen.queryByRole('checkbox', { name: preparedObjectiveCopy.en.projectTypecheck })).toBeNull()
    expect(within(screen.getByRole('group')).getAllByRole('checkbox')).toHaveLength(3)
  })
})
