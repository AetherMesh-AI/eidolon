import type { PreparedValidationSetStatus, PreparedValidationStatus } from './runtime-prepared-validation-status-types'
import type { Objective, OrganizationSnapshot } from './types'

export function preparedStatusFixture(totalSets = 1, projectCount = 1, versions: (1 | 2)[] = []): PreparedValidationStatus {
  const sets: PreparedValidationSetStatus[] = Array.from({ length: totalSets }, (_, index) => ({
    setId: `set-${index}`, requestId: `request-${index}`, projectId: `project${index % projectCount}`, round: 0,
    generation: Math.floor(index / projectCount) + 1, reservedUnits: versions[index] === 2 ? 3 : 2,
    ...(versions[index] === 2 ? { profileVersion: 2 as const } : {}), snapshotSha256: 'b'.repeat(64),
    aggregateClaimPresent: false, reviewClaimPresent: false, pairReviewRecorded: false, state: 'reserved_undispatched',
    steps: ['workspace_typescript', 'workspace_vitest_ui', ...(versions[index] === 2 ? ['workspace_vite_build'] : [])].map((recipe, step) => ({ runId: `run-${index}-${step}`, stepId: `step-${step}`,
      recipe, reservedUnits: 1, dispatched: false, resourceClaimed: false, resultClaimStatus: null, cleanupClaimSettled: false,
      latestCleanupSequence: null, latestRuntimeEventSequence: null, latestRuntimeEventKind: null,
      blockerReasons: ['reserved_undispatched'] })) as PreparedValidationSetStatus['steps']
  }))

  const current = Object.fromEntries(sets.map(set => [set.projectId, set]))
  const history = [...sets].reverse().slice(0, 20)

  const entries = history.flatMap(set => set.steps.map(step => ({ setId: set.setId, projectId: set.projectId,
    generation: set.generation, runId: step.runId, reasons: step.blockerReasons }))).slice(0, 20)

  const v2 = sets.filter(set => set.profileVersion === 2).length
  const preparedUnits = totalSets * 2 + v2

  return { objectiveId: 'objective', storageState: 'present', legacyUnits: 3, preparedUnits, totalUnits: 3 + preparedUnits,
    ...(v2 ? { contractVersion: 2 as const, setCounts: { v1: totalSets - v2, v2 } } : {}),
    totalSets, totalHistory: totalSets, current, history, historyTruncated: totalSets > history.length,
    blockers: { blocked: totalSets > 0, totalBlockers: preparedUnits, entries, truncated: preparedUnits > entries.length },
    scalarMetadataOnly: true, accountingVerified: false, sourceBytesVerified: false, runtimeReceiptsVerified: false,
    executionAuthorized: false, acceptanceAuthorized: false, sourceDeliveryAuthorized: false }
}

export function preparedStatusObjective(totalSets = 1, projectCount = 1, versions: (1 | 2)[] = []): Objective {
  return { id: 'objective', title: 'Prepared checks', description: 'Explicitly selected prepared registrations',
    createdAt: '2026-10-16T00:00:00Z', status: 'active', ownerId: 'owner', source: 'runtime', deliveryMode: 'managed_artifact',
    requiredChecks: ['project_typecheck', 'project_tests'], projects: Array.from({ length: projectCount }, (_, index) => ({ id: `project${index}`, root: `root${index}`, recipe: null, team: 'Prepared team' })),
    preparedValidations: Array.from({ length: projectCount }, (_, index) => ({ projectId: `project${index}`, revision: index + 1, registrationSha256: 'a'.repeat(64) })),
    preparedValidation: preparedStatusFixture(totalSets, projectCount, versions) }
}

export function preparedStatusSnapshot(objective: Objective = preparedStatusObjective()): OrganizationSnapshot {
  return { source: 'runtime', objectives: [objective], agents: [], tasks: [], activity: [], knowledge: [], requests: [],
    runtime: { state: 'ready', capabilities: [], scope: 'Prepared status', maxWorkers: 1 } }
}
