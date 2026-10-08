/** Synthetic executive output for real gateway/ledger scenarios. The backend
 * still assigns IDs, validates authority, and enforces the shared task budget. */
export interface PackageProposalContext {
  maxTasks?: number
  objective: {
    title: string
    acceptanceCriteria: string[]
    projects?: { id: string }[]
  }
}

export function organizationPackageProposal(context: PackageProposalContext, maxTasks: number) {
  if (context.maxTasks !== undefined && maxTasks > context.maxTasks) {
    throw new Error('Synthetic package exceeded the root task budget')
  }

  return {
    workPackages: [
      {
        title: `Manager package: ${context.objective.title}`,
        description:
          'Delegate the submitted objective to its responsible manager within the selected criteria and repositories.',
        managerId: 'manager',
        criterionIndexes: context.objective.acceptanceCriteria.map((_, index) => index),
        projectIds: context.objective.projects?.map(project => project.id) ?? [],
        dependsOn: [],
        maxTasks
      }
    ]
  }
}
