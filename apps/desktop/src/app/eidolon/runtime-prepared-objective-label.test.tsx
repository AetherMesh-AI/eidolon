import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { RuntimeAcceptance } from './runtime-acceptance'
import { preparedObjectiveCopy } from './runtime-prepared-objective-copy'
import { ScopeAmendmentFields } from './runtime-scope-amendment'
import type { Objective } from './types'

describe('prepared typecheck read-only labels', () => {
  it.each(['project_typecheck', 'project_build'] as const)('shows the returned mandatory %s check without claiming it passed', check => {
    const objective: Objective = { id: 'objective', title: 'Prepared objective', description: 'Explicit selection', ownerId: 'owner', status: 'active', source: 'runtime',
      createdAt: '2026-10-16T00:00:00Z', requiredChecks: [check] }

    render(<RuntimeAcceptance objective={objective} onOpenEvidence={() => {}} />)
    expect(screen.getByText(check === 'project_build' ? preparedObjectiveCopy.en.projectBuild : preparedObjectiveCopy.en.projectTypecheck)).toBeTruthy()
    expect(screen.queryByText('Passed')).toBeNull()
  })
  it.each(['project_typecheck', 'project_build'] as const)('labels current %s without offering a new profile or check amendment choice', check => {
    render(<ScopeAmendmentFields currentChecks={[check]} disabled={false}
      draft={{ criteria: '', requiredChecks: [check] }} onChange={() => {}} />)
    const label = check === 'project_build' ? preparedObjectiveCopy.en.projectBuild : preparedObjectiveCopy.en.projectTypecheck
    expect(screen.getByText(label)).toBeTruthy()
    expect(screen.queryByRole('checkbox', { name: label })).toBeNull()
    expect(within(screen.getByRole('group')).getAllByRole('checkbox')).toHaveLength(3)
  })
})
