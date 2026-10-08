import { expect, it } from 'vitest'

import { organizationPackageProposal } from './organization-package-proposal'

it('covers selected criteria and repository identities within the root allocation', () => {
  const context = {
    maxTasks: 3,
    objective: {
      title: 'Goal',
      acceptanceCriteria: ['Keep behavior', 'Update UI'],
      projects: [{ id: 'api' }, { id: 'frontend' }]
    }
  }

  const proposal = organizationPackageProposal(context, 2)
  expect(proposal.workPackages[0].criterionIndexes.map(index => context.objective.acceptanceCriteria[index])).toEqual(
    context.objective.acceptanceCriteria
  )
  expect(proposal.workPackages[0].projectIds).toEqual(context.objective.projects.map(project => project.id))
  expect(proposal.workPackages[0].maxTasks).toBeLessThanOrEqual(context.maxTasks)
  expect(() => organizationPackageProposal(context, context.maxTasks + 1)).toThrow('root task budget')
})
