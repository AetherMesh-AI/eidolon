import type { PreparedValidationSetStatus, PreparedValidationStatus } from './runtime-prepared-validation-status-types'
import type { Objective, OrganizationSnapshot } from './types'

export function preparedStatusFixture(totalSets = 1, projectCount = 1): PreparedValidationStatus {
  const sets: PreparedValidationSetStatus[] = Array.from({ length: totalSets }, (_, index) => ({
    setId: `set-${index}`, requestId: `request-${index}`, projectId: `project${index % projectCount}`, round: 0,
    generation: Math.floor(index / projectCount) + 1, reservedUnits: 2, snapshotSha256: 'b'.repeat(64),
    aggregateClaimPresent: false, reviewClaimPresent: false, pairReviewRecorded: false, state: 'reserved_undispatched',
    steps: ['workspace_typescript', 'workspace_vitest_ui'].map((recipe, step) => ({ runId: `run-${index}-${step}`, stepId: `step-${step}`,
      recipe, reservedUnits: 1, dispatched: false, resourceClaimed: false, resultClaimStatus: null, cleanupClaimSettled: false,
      latestCleanupSequence: null, latestRuntimeEventSequence: null, latestRuntimeEventKind: null,
      blockerReasons: ['reserved_undispatched'] })) as PreparedValidationSetStatus['steps']
  }))

  const current = Object.fromEntries(sets.map(set => [set.projectId, set]))
  const history = [...sets].reverse().slice(0, 20)

  const entries = history.flatMap(set => set.steps.map(step => ({ setId: set.setId, projectId: set.projectId,
    generation: set.generation, runId: step.runId, reasons: step.blockerReasons }))).slice(0, 20)

  return { objectiveId: 'objective', storageState: 'present', legacyUnits: 3, preparedUnits: 2 * totalSets, totalUnits: 3 + 2 * totalSets,
    totalSets, totalHistory: totalSets, current, history, historyTruncated: totalSets > history.length,
    blockers: { blocked: totalSets > 0, totalBlockers: 2 * totalSets, entries, truncated: 2 * totalSets > entries.length },
    scalarMetadataOnly: true, accountingVerified: false, sourceBytesVerified: false, runtimeReceiptsVerified: false,
    executionAuthorized: false, acceptanceAuthorized: false, sourceDeliveryAuthorized: false }
}

export function preparedStatusObjective(totalSets = 1, projectCount = 1): Objective {
  return { id: 'objective', title: 'Prepared checks', description: 'Explicitly selected prepared registrations',
    createdAt: '2026-10-16T00:00:00Z', status: 'active', ownerId: 'owner', source: 'runtime', deliveryMode: 'managed_artifact',
    requiredChecks: ['project_typecheck', 'project_tests'], projects: Array.from({ length: projectCount }, (_, index) => ({ id: `project${index}`, root: `root${index}`, recipe: null, team: 'Prepared team' })),
    preparedValidations: Array.from({ length: projectCount }, (_, index) => ({ projectId: `project${index}`, revision: index + 1, registrationSha256: 'a'.repeat(64) })),
    preparedValidation: preparedStatusFixture(totalSets, projectCount) }
}

export function preparedStatusSnapshot(objective: Objective = preparedStatusObjective()): OrganizationSnapshot {
  return { source: 'runtime', objectives: [objective], agents: [], tasks: [], activity: [], knowledge: [], requests: [],
    runtime: { state: 'ready', capabilities: [], scope: 'Prepared status', maxWorkers: 1 } }
}
