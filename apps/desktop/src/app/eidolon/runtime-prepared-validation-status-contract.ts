import { policyEqual } from './organization-owner-policy-contract'
import { preparedHash, preparedInteger, preparedRecord } from './runtime-prepared-validation-contract'
import type { PreparedValidationSetStatus, PreparedValidationStatus, PreparedValidationStepStatus } from './runtime-prepared-validation-status-types'
import type { Objective, PreparedValidationReference } from './types'

const exact = (value: Record<string, unknown>, keys: string[]) => policyEqual(Object.keys(value).sort(), [...keys].sort())
const identifier = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value)
const count = (value: unknown, max: number, min = 0): value is number => preparedInteger(value, min) && value <= max
const boolean = (value: unknown): value is boolean => typeof value === 'boolean'
const eventKinds = ['dispatch_planned', 'staging_allocated', 'container_create_planned', 'container_observed', 'container_start_planned', 'cleanup_observed', 'receipt']
const blockerKinds = ['reserved_undispatched', 'runtime_receipt_unverified', 'execution_outcome_unknown', 'validation_failed', 'resource_cleanup_unconfirmed']
const validReasons = (value: unknown) => Array.isArray(value) && value.length <= 5 && value.every(reason => typeof reason === 'string' && blockerKinds.includes(reason))
const resultKinds = ['passed', 'failed', 'interrupted', 'cleanup_failed', 'unknown']
const authorityFlags = ['accountingVerified', 'sourceBytesVerified', 'runtimeReceiptsVerified', 'executionAuthorized', 'acceptanceAuthorized', 'sourceDeliveryAuthorized']

function validReferences(value: unknown): value is PreparedValidationReference[] {
  return Array.isArray(value) && value.length <= 8 && value.every(reference => preparedRecord(reference) &&
    exact(reference, ['projectId', 'revision', 'registrationSha256']) && typeof reference.projectId === 'string' &&
    /^[a-z][a-z0-9_-]{0,63}$/.test(reference.projectId) && preparedInteger(reference.revision, 1) && preparedHash(reference.registrationSha256)) &&
    new Set(value.map(reference => reference.projectId)).size === value.length
}

function validStep(value: unknown): value is PreparedValidationStepStatus {
  if (!preparedRecord(value) || !exact(value, ['runId', 'stepId', 'recipe', 'reservedUnits', 'dispatched', 'resourceClaimed', 'resultClaimStatus',
    'cleanupClaimSettled', 'latestCleanupSequence', 'latestRuntimeEventSequence', 'latestRuntimeEventKind', 'blockerReasons']) ||
    !identifier(value.runId) || !identifier(value.stepId) || !['workspace_typescript', 'workspace_vitest_ui'].includes(value.recipe as string) ||
    value.reservedUnits !== 1 || !boolean(value.dispatched) || !boolean(value.resourceClaimed) || !boolean(value.cleanupClaimSettled) ||
    value.resultClaimStatus !== null && !resultKinds.includes(value.resultClaimStatus as string) ||
    value.latestCleanupSequence !== null && !count(value.latestCleanupSequence, 10000, 1) ||
    value.latestRuntimeEventSequence !== null && !count(value.latestRuntimeEventSequence, 128, 1) ||
    value.latestRuntimeEventKind !== null && !eventKinds.includes(value.latestRuntimeEventKind as string) ||
    (value.latestRuntimeEventKind === null) !== (value.latestRuntimeEventSequence === null) || !validReasons(value.blockerReasons)) { return false }

  if (!value.dispatched && (value.resourceClaimed || value.resultClaimStatus !== null || value.cleanupClaimSettled ||
    value.latestCleanupSequence !== null || value.latestRuntimeEventSequence !== null)) { return false }

  if (value.cleanupClaimSettled && (!value.resourceClaimed || value.latestCleanupSequence === null)) { return false }
  const reasons = [value.dispatched ? 'runtime_receipt_unverified' : 'reserved_undispatched']

  if (value.dispatched && (value.resultClaimStatus === null || ['unknown', 'interrupted'].includes(value.resultClaimStatus as string))) {
    reasons.push('execution_outcome_unknown')
  } else if (value.resultClaimStatus !== null && value.resultClaimStatus !== 'passed') { reasons.push('validation_failed') }

  if (value.dispatched && !value.cleanupClaimSettled) { reasons.push('resource_cleanup_unconfirmed') }

  return policyEqual(value.blockerReasons, reasons)
}

function validSet(value: unknown, projects: Set<string>): value is PreparedValidationSetStatus {
  return preparedRecord(value) && exact(value, ['setId', 'requestId', 'projectId', 'round', 'generation', 'reservedUnits', 'snapshotSha256',
    'steps', 'aggregateClaimPresent', 'reviewClaimPresent', 'pairReviewRecorded', 'state']) &&
    identifier(value.setId) && identifier(value.requestId) && typeof value.projectId === 'string' && projects.has(value.projectId) &&
    preparedInteger(value.round) && preparedInteger(value.generation, 1) && value.reservedUnits === 2 && preparedHash(value.snapshotSha256) &&
    boolean(value.aggregateClaimPresent) && boolean(value.reviewClaimPresent) && boolean(value.pairReviewRecorded) && Array.isArray(value.steps) && value.steps.length === 2 &&
    value.steps.every(validStep) && value.steps[0].recipe === 'workspace_typescript' && value.steps[1].recipe === 'workspace_vitest_ui' &&
    new Set([value.setId, ...value.steps.map(step => step.runId)]).size === 3 &&
    value.steps[0].stepId !== value.steps[1].stepId &&
    value.state === (value.steps.some(step => step.dispatched) ? 'runtime_unverified' : 'reserved_undispatched')
}

/** Validate only the bounded display contract, never authenticate the recorded claims. */
export function validPreparedValidationStatus(value: unknown, objectiveId: string, references: PreparedValidationReference[]): value is PreparedValidationStatus {
  if (!preparedRecord(value) || !exact(value, ['objectiveId', 'storageState', 'legacyUnits', 'preparedUnits', 'totalUnits', 'totalSets', 'totalHistory',
    'current', 'history', 'historyTruncated', 'blockers', 'scalarMetadataOnly', ...authorityFlags]) ||
    value.objectiveId !== objectiveId || !identifier(value.objectiveId) || !['absent', 'present'].includes(value.storageState as string) ||
    value.scalarMetadataOnly !== true || authorityFlags.some(key => value[key] !== false) ||
    !count(value.totalSets, 10000) || !count(value.legacyUnits, 10000) || value.totalHistory !== value.totalSets ||
    value.preparedUnits !== value.totalSets * 2 || value.totalUnits !== value.legacyUnits + value.totalSets * 2 ||
    !validReferences(references) || references.length === 0 || !preparedRecord(value.current) || Object.keys(value.current).length > 8 ||
    !Array.isArray(value.history) || value.history.length !== Math.min(value.totalSets, 20) || value.historyTruncated !== (value.totalSets > value.history.length) ||
    !preparedRecord(value.blockers)) { return false }

  const projects = new Set(references.map(reference => reference.projectId))
  const currentByProject = value.current
  const current = Object.values(currentByProject)
  const history = value.history

  if (value.storageState === 'absent' && value.totalSets !== 0 || !current.every(item => validSet(item, projects)) ||
    !history.every(item => validSet(item, projects)) || current.length > value.totalSets ||
    Object.entries(value.current).some(([projectId, item]) => (item as PreparedValidationSetStatus).projectId !== projectId) ||
    new Set(history.map(item => item.setId)).size !== history.length ||
    history.some(item => !Object.hasOwn(currentByProject, item.projectId) || (currentByProject[item.projectId] as PreparedValidationSetStatus).generation < item.generation)) { return false }

  const sets = new Map<string, PreparedValidationSetStatus>()
  const runs = new Set<string>()
  const generations = new Set<string>()

  for (const item of [...current, ...history] as PreparedValidationSetStatus[]) {
    if (sets.has(item.setId)) {
      if (!policyEqual(sets.get(item.setId), item)) { return false }

      continue
    }

    const generation = JSON.stringify([item.projectId, item.generation])

    if (generations.has(generation) || item.steps.some(step => runs.has(step.runId))) { return false }
    generations.add(generation); item.steps.forEach(step => runs.add(step.runId)); sets.set(item.setId, item)
  }

  if (sets.size > value.totalSets) { return false }

  const expectedEntries = history.flatMap(item => item.steps.map(step => ({ setId: item.setId, projectId: item.projectId,
    generation: item.generation, runId: step.runId, reasons: step.blockerReasons }))).slice(0, 20)

  const blockers = value.blockers

  return exact(blockers, ['blocked', 'totalBlockers', 'entries', 'truncated']) && blockers.blocked === (value.totalSets > 0) &&
    blockers.totalBlockers === value.totalSets * 2 && Array.isArray(blockers.entries) && blockers.entries.length <= 20 &&
    blockers.entries.every(entry => preparedRecord(entry) && exact(entry, ['setId', 'projectId', 'generation', 'runId', 'reasons']) &&
      identifier(entry.setId) && identifier(entry.projectId) && identifier(entry.runId) && preparedInteger(entry.generation, 1) && validReasons(entry.reasons)) &&
    policyEqual(blockers.entries, expectedEntries) &&
    blockers.truncated === (value.totalSets * 2 > expectedEntries.length)
}

export function validObjectivePreparedValidation(objective: Objective): boolean {
  const references = objective.preparedValidations

  if (references === undefined) { return objective.preparedValidation == null }

  if (!validReferences(references)) { return false }

  if (!references.length) { return objective.preparedValidation == null }
  const bindings = objective.projects

  if (!Array.isArray(bindings) || bindings.length !== references.length ||
    bindings.some(binding => !preparedRecord(binding) || typeof binding.id !== 'string')) { return false }

  const projects = bindings.map(binding => binding.id)

  if (new Set(projects).size !== projects.length || references.some(reference => !projects.includes(reference.projectId))) { return false }

  // Missing status is rendered as unavailable, never inferred as zero or success.
  return objective.preparedValidation == null || validPreparedValidationStatus(objective.preparedValidation, objective.id, references)
}
