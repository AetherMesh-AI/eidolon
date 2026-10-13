/** Synthetic read-only organization records shared by native presentation scenarios. */
export const createdAt = '2026-10-08T09:00:00Z'

export const agents = [
  {
    id: 'executive',
    name: 'Avery',
    role: 'Executive',
    team: 'Leadership',
    status: 'reviewing',
    capabilities: ['request.accept', 'request.decompose'],
    responsibilities: ['Set direction and accept reviewed outcomes']
  },
  {
    id: 'manager',
    name: 'Morgan',
    role: 'Manager',
    team: 'Product',
    managerId: 'executive',
    status: 'working',
    capabilities: ['request.plan', 'request.integrate'],
    responsibilities: ['Plan delivery and coordinate specialists']
  },
  {
    id: 'writer',
    name: 'Robin',
    role: 'Worker',
    team: 'Product',
    managerId: 'manager',
    status: 'working',
    capabilities: ['work.draft'],
    responsibilities: ['Draft clear release documentation']
  },
  {
    id: 'reviewer',
    name: 'Sage',
    role: 'Worker',
    team: 'Quality',
    managerId: 'manager',
    status: 'reviewing',
    capabilities: ['request.review'],
    responsibilities: ['Review evidence and acceptance criteria']
  },
  {
    id: 'researcher',
    name: 'Rowan',
    role: 'Worker',
    team: 'Research',
    managerId: 'manager',
    status: 'idle',
    capabilities: ['work.analyze'],
    responsibilities: ['Verify source material and context']
  }
].map(agent => ({
  ...agent,
  identityId: `fixture-${agent.id}`,
  persistent: true,
  lifecycle: 'active',
  provider: null,
  model: null,
  tools: [],
  summary: 'Synthetic identity for native layout inspection. No provider work is running.'
}))

export const snapshot = {
  source: 'runtime',
  runtime: {
    state: 'idle',
    profile: 'default',
    capabilities: ['work.draft', 'work.analyze'],
    maxWorkers: 2,
    maxInflight: 2,
    rosterCount: agents.length,
    workingCount: 0,
    scope: 'Synthetic visual fixture. No work, external tools, or provider calls are executed.',
    setup: {
      version: 1,
      provider: { status: 'unchecked', blockers: [], inheritedMembers: agents.length, overriddenMembers: 0 },
      backgroundOptIn: false
    }
  },
  outcomes: { generation: 'fixture-1', total: 1, unread: 1, hasMore: false, nextCursor: null, items: [{ objectiveId: 'research', revision: 1, seen: false, status: 'accepted', title: 'Document provider boundaries', summary: 'Synthetic reviewed scope and provider boundary summary.', round: 1, deliverableId: 'fixture-deliverable', acceptanceRequestId: 'fixture-accept', evidenceIds: ['fixture-deliverable'], createdAt, updatedAt: createdAt, archived: false }] },
  objectives: [
    {
      id: 'release',
      title: 'Prepare the autumn release brief',
      description: 'Bring the launch narrative, verified changes, and review notes into one clear brief.',
      status: 'active',
      phaseCode: 'execution_review',
      phase: 'Execution and review',
      milestone: 'Release context verified',
      progress: 60,
      priority: 'P3',
      priorityRevision: 0,
      dispatchControl: { paused: false, revision: 0, runningCount: 1 },
      planningMode: 'executive_packages',
      acceptance: { status: 'blocked', round: 1, maxReplans: 2, criteria: ['Brief reflects verified changes'], summary: 'Conflicting source dates require resolution.', deliverableId: null, criteriaResults: [{ criterion: 'Brief reflects verified changes', satisfied: false, reason: 'Two source dates remain unresolved.', evidenceIds: ['fixture-source-a', 'fixture-source-b'] }], conflicts: ['Published launch dates disagree.'] },
      workPackages: [
        { id: 'release-package', objectiveId: 'release', round: 1, managerId: 'manager', title: 'Release documentation', description: 'Draft the narrative from verified changes.', criterionIndexes: [0], projectIds: [], dependencyIds: [], maxTasks: 3, planRequestId: 'fixture-plan', status: 'working', taskIds: ['draft'] },
        { id: 'evidence-package', objectiveId: 'release', round: 1, managerId: 'manager', title: 'Acceptance evidence', description: 'Review supporting evidence against the brief.', criterionIndexes: [0], projectIds: [], dependencyIds: ['release-package'], maxTasks: 3, planRequestId: 'fixture-evidence-plan', status: 'working', taskIds: ['review'] },
        { id: 'sources-package', objectiveId: 'release', round: 1, managerId: 'manager', title: 'Source verification', description: 'Check source material and retained context.', criterionIndexes: [0], projectIds: [], dependencyIds: [], maxTasks: 3, planRequestId: 'fixture-sources-plan', status: 'completed', taskIds: ['sources'] }
      ]
    },
    {
      id: 'navigation',
      title: 'Review the workspace navigation',
      description: 'Compare the owner journey across a standard desktop and a narrow window.',
      status: 'waiting',
      phase: 'Owner review',
      milestone: 'Navigation proposal ready',
      progress: 80
    },
    {
      id: 'research',
      title: 'Document provider boundaries',
      description: 'Record the supported runtime responsibilities and evidence requirements.',
      status: 'completed',
      phase: 'Complete',
      milestone: 'Reviewed summary retained',
      progress: 100,
      acceptance: { status: 'accepted', round: 1, maxReplans: 2, criteria: ['Boundaries documented'], summary: 'Synthetic reviewed scope and provider boundary summary.', deliverableId: 'fixture-deliverable' }
    }
  ].map(objective => ({ ...objective, createdAt, source: 'runtime', ownerId: 'executive', managerId: 'manager' })),
  agents,
  tasks: [
    {
      id: 'draft',
      objectiveId: 'release',
      title: 'Draft the release brief',
      ownerId: 'writer',
      managingAgentId: 'manager',
      assignedById: 'manager',
      workPackageId: 'release-package',
      currentRound: true,
      status: 'working',
      dependsOn: []
    },
    {
      id: 'review',
      objectiveId: 'release',
      title: 'Review the acceptance evidence',
      ownerId: 'reviewer',
      managingAgentId: 'manager',
      workPackageId: 'evidence-package',
      currentRound: true,
      status: 'review',
      dependsOn: ['draft']
    },
    { id: 'sources', objectiveId: 'release', title: 'Verify source material', ownerId: 'researcher', managingAgentId: 'manager', workPackageId: 'sources-package', currentRound: true, status: 'completed', dependsOn: [] }
  ],
  requests: [{ id: 'fixture-question', objectiveId: 'navigation', requesterId: 'manager', type: 'request.question', team: 'Product', priority: 2, status: 'pending_intervention', reason: 'Review the proposed owner journey before the next iteration.', attempts: 0, createdAt }],
  activity: ['executive', 'manager', 'writer', 'reviewer'].map((agentId, index) => ({ id: `fixture-event-${index}`, agentId, source: 'runtime', kind: 'system', text: ['Synthetic direction recorded for the release brief.', 'Synthetic manager assignment recorded.', 'Synthetic draft prepared for review.', 'Synthetic review evidence recorded.'][index], timestamp: createdAt })),
  knowledge: [],
  conversations: []
}

