import { describe, expect, it } from 'vitest'

import { validObjectivePreparedValidation, validPreparedValidationStatus } from './runtime-prepared-validation-status-contract'
import { preparedStatusObjective, preparedStatusSnapshot } from './runtime-prepared-validation-status.test-fixtures'
import { validateSnapshot } from './runtime-snapshot-contract'
import type { Objective } from './types'

const valid = (objective: Objective) => validPreparedValidationStatus(objective.preparedValidation, objective.id, objective.preparedValidations!)

describe('scalar prepared snapshot contract', () => {
  it('preserves legacy omission and keeps missing selected status distinct from recorded zero', () => {
    const objective = preparedStatusObjective(0)
    expect(valid(objective)).toBe(true)
    expect(validateSnapshot(preparedStatusSnapshot(objective)).objectives[0]).toBe(objective)
    delete objective.preparedValidation
    expect(validObjectivePreparedValidation(objective)).toBe(true)
    expect(validateSnapshot(preparedStatusSnapshot(objective)).objectives[0].preparedValidation).toBeUndefined()
    delete objective.preparedValidations
    expect(validateSnapshot(preparedStatusSnapshot(objective)).objectives[0]).toBe(objective)
  })
  it('accepts eight current projects and twenty history rows while totals retain older reservations', () => {
    const objective = preparedStatusObjective(35, 8)
    expect(valid(objective)).toBe(true)
    expect(Object.keys(objective.preparedValidation!.current)).toHaveLength(8)
    expect(objective.preparedValidation!.history).toHaveLength(20)
    expect(objective.preparedValidation!.totalUnits).toBe(73)
    expect(objective.preparedValidation!.historyTruncated).toBe(true)
    expect(objective.preparedValidation!.blockers.entries).toHaveLength(20)
    expect(valid(preparedStatusObjective(10000, 8))).toBe(true)
  })
  it('requires an own current row even when a valid project ID resembles an inherited object property', () => {
    const objective = preparedStatusObjective()
    objective.projects![0].id = 'constructor'; objective.preparedValidations![0].projectId = 'constructor'
    const status = objective.preparedValidation!
    status.history[0].projectId = 'constructor'; status.blockers.entries.forEach(entry => { entry.projectId = 'constructor' }); status.current = {}
    expect(valid(objective)).toBe(false)
  })
  it('never turns all recorded passed/cleanup/review claims into verified authority', () => {
    const objective = preparedStatusObjective()
    const status = objective.preparedValidation!
    const set = status.current.project0
    set.state = 'runtime_unverified'; set.aggregateClaimPresent = true; set.reviewClaimPresent = true; set.pairReviewRecorded = true

    for (const step of set.steps) {
      step.dispatched = true; step.resourceClaimed = true; step.resultClaimStatus = 'passed'; step.cleanupClaimSettled = true
      step.latestCleanupSequence = 1; step.latestRuntimeEventSequence = 7; step.latestRuntimeEventKind = 'receipt'; step.blockerReasons = ['runtime_receipt_unverified']
    }

    status.blockers.entries = set.steps.map(step => ({ setId: set.setId, projectId: set.projectId, generation: set.generation, runId: step.runId, reasons: step.blockerReasons }))
    expect(valid(objective)).toBe(true)
    expect(validateSnapshot(preparedStatusSnapshot(objective)).objectives[0].preparedValidation).toMatchObject({
      accountingVerified: false, runtimeReceiptsVerified: false, acceptanceAuthorized: false, executionAuthorized: false,
      blockers: { blocked: true, totalBlockers: 2 } })
  })
  it.each(['accountingVerified', 'sourceBytesVerified', 'runtimeReceiptsVerified', 'executionAuthorized', 'acceptanceAuthorized', 'sourceDeliveryAuthorized'])('rejects elevated %s rather than accepting a stronger claim', flag => {
    const objective = preparedStatusObjective()
    Object.assign(objective.preparedValidation!, { [flag]: true })
    expect(valid(objective)).toBe(false)
    expect(() => validateSnapshot(preparedStatusSnapshot(objective))).toThrow('prepared')
  })
  it.each(['unknown_state', 'unknown_recipe', 'unknown_result', 'unknown_event', 'unknown_blocker', 'mixed_event', 'mixed_cleanup', 'undispatched_claim',
    'wrong_objective', 'wrong_project', 'wrong_current_key', 'wrong_total', 'wrong_history_total', 'wrong_units', 'wrong_truncation', 'wrong_blocker',
    'too_many_sets', 'too_many_legacy', 'too_many_history', 'too_many_current', 'too_many_blockers', 'huge_cleanup_sequence', 'huge_runtime_sequence',
    'duplicate_set', 'duplicate_run', 'duplicate_step', 'old_current', 'altered_history', 'missing_flag', 'extra_payload', 'absent_with_sets'])('rejects malformed %s', mutation => {
    const objective = preparedStatusObjective(2)
    const status = objective.preparedValidation!, set = status.current.project0, step = set.steps[0]

    if (mutation === 'unknown_state') { Object.assign(set, { state: 'verified_passed' }) }

    if (mutation === 'unknown_recipe') { Object.assign(step, { recipe: 'shell' }) }

    if (mutation === 'unknown_result') { Object.assign(step, { resultClaimStatus: 'success' }) }

    if (mutation === 'unknown_event') { Object.assign(step, { latestRuntimeEventSequence: 1, latestRuntimeEventKind: 'verified' }) }

    if (mutation === 'unknown_blocker') { Object.assign(step, { blockerReasons: ['safe'] }) }

    if (mutation === 'mixed_event') { step.latestRuntimeEventSequence = 1 }

    if (mutation === 'mixed_cleanup') { step.cleanupClaimSettled = true }

    if (mutation === 'undispatched_claim') { step.resourceClaimed = true }

    if (mutation === 'wrong_objective') { status.objectiveId = 'other' }

    if (mutation === 'wrong_project') { set.projectId = 'other' }

    if (mutation === 'wrong_current_key') { status.current = { other: set } }

    if (mutation === 'wrong_total') { status.totalUnits-- }

    if (mutation === 'wrong_history_total') { status.totalHistory-- }

    if (mutation === 'wrong_units') { Object.assign(step, { reservedUnits: 0 }) }

    if (mutation === 'wrong_truncation') { status.historyTruncated = true }

    if (mutation === 'wrong_blocker') { status.blockers.blocked = false }

    if (mutation === 'too_many_sets') { status.totalSets = 10001 }

    if (mutation === 'too_many_legacy') { status.legacyUnits = 10001 }

    if (mutation === 'too_many_history') { status.history = Array.from({ length: 21 }, () => set) }

    if (mutation === 'too_many_current') { status.current = Object.fromEntries(Array.from({ length: 9 }, (_, index) => [`project${index}`, set])) }

    if (mutation === 'too_many_blockers') { status.blockers.entries = Array.from({ length: 21 }, () => status.blockers.entries[0]) }

    if (mutation === 'huge_cleanup_sequence') { step.latestCleanupSequence = 10001 }

    if (mutation === 'huge_runtime_sequence') { step.latestRuntimeEventSequence = 129 }

    if (mutation === 'duplicate_set') { status.history[1] = set }

    if (mutation === 'duplicate_run') { status.history[1].steps[0].runId = step.runId }

    if (mutation === 'duplicate_step') { step.stepId = set.steps[1].stepId }

    if (mutation === 'old_current') { status.current.project0 = status.history[1] }

    if (mutation === 'altered_history') { status.history[0] = { ...set, snapshotSha256: 'c'.repeat(64) } }

    if (mutation === 'missing_flag') { Object.assign(status, { scalarMetadataOnly: undefined }) }

    if (mutation === 'extra_payload') { Object.assign(step, { receipt: { output: 'unbounded' } }) }

    if (mutation === 'absent_with_sets') { status.storageState = 'absent' }
    expect(valid(objective), mutation).toBe(false)
  })
  it.each(['no_refs', 'unknown_ref_field', 'duplicate_ref', 'missing_project', 'malformed_ref', 'too_many_refs', 'null_refs', 'input_ids_without_bindings'])('rejects inconsistent immutable selection %s', mutation => {
    const objective = preparedStatusObjective()

    if (mutation === 'no_refs') { delete objective.preparedValidations }

    if (mutation === 'unknown_ref_field') { Object.assign(objective.preparedValidations![0], { enabled: true }) }

    if (mutation === 'duplicate_ref') { objective.preparedValidations!.push(objective.preparedValidations![0]) }

    if (mutation === 'missing_project') { objective.projects = [] }

    if (mutation === 'malformed_ref') { objective.preparedValidations![0].registrationSha256 = 'invalid' }

    if (mutation === 'too_many_refs') { objective.preparedValidations = Array.from({ length: 9 }, () => objective.preparedValidations![0]) }

    if (mutation === 'input_ids_without_bindings') { objective.projectIds = ['project0']; delete objective.projects }

    if (mutation === 'null_refs') { Object.assign(objective, { preparedValidations: null }) }
    expect(validObjectivePreparedValidation(objective)).toBe(false)
    expect(() => validateSnapshot(preparedStatusSnapshot(objective))).toThrow('prepared')
  })
})

it('validates mixed historical v1/v2 accounting and requires every v2 step without promoting claims', () => {
  const objective = preparedStatusObjective(3, 1, [1, 2, 2])
  const status = objective.preparedValidation!
  expect(valid(objective)).toBe(true)
  expect(status).toMatchObject({ contractVersion: 2, setCounts: { v1: 1, v2: 2 }, preparedUnits: 8, totalUnits: 11,
    blockers: { totalBlockers: 8 }, acceptanceAuthorized: false, runtimeReceiptsVerified: false })
  expect(status.current.project0.steps.map(step => step.recipe)).toEqual(['workspace_typescript', 'workspace_vitest_ui', 'workspace_vite_build'])

  for (const mutation of ['missing_build', 'wrong_order', 'duplicate_run', 'duplicate_step', 'v2_without_top', 'unknown_top', 'unknown_profile',
    'missing_counts', 'unknown_count', 'wrong_counts', 'v1_claimed_counts', 'wrong_units', 'wrong_blockers', 'manifest_payload'] as const) {
    const invalid = structuredClone(objective)
    const value = invalid.preparedValidation!, set = value.current.project0

    if (mutation === 'missing_build') { set.steps.pop() }

    if (mutation === 'wrong_order') { set.steps.reverse() }

    if (mutation === 'duplicate_run') { set.steps[2]!.runId = set.steps[0].runId }

    if (mutation === 'duplicate_step') { set.steps[2]!.stepId = set.steps[0].stepId }

    if (mutation === 'v2_without_top') { delete value.contractVersion; delete value.setCounts }

    if (mutation === 'unknown_top') { Object.assign(value, { contractVersion: 3 }) }

    if (mutation === 'unknown_profile') { Object.assign(set, { profileVersion: 3 }) }

    if (mutation === 'missing_counts') { delete value.setCounts }

    if (mutation === 'unknown_count') { Object.assign(value.setCounts!, { v3: 1 }) }

    if (mutation === 'wrong_counts') { value.setCounts!.v2++ }

    if (mutation === 'v1_claimed_counts') { value.setCounts = { v1: 2, v2: 1 }; value.preparedUnits = 7; value.totalUnits = 10; value.blockers.totalBlockers = 7 }

    if (mutation === 'wrong_units') { value.preparedUnits-- }

    if (mutation === 'wrong_blockers') { value.blockers.totalBlockers-- }

    if (mutation === 'manifest_payload') { Object.assign(set.steps[2]!, { outputManifest: [] }) }
    expect(valid(invalid), mutation).toBe(false)
  }
})
