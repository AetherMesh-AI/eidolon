import { fireEvent, render, screen, within } from '@testing-library/react'
import { expect, it } from 'vitest'

import { RuntimePreparedValidationActivity } from './runtime-prepared-validation-activity'
import { preparedValidationActivityCopy } from './runtime-prepared-validation-activity-copy'
import { validPreparedValidationStatus } from './runtime-prepared-validation-status-contract'
import type { PreparedValidationSetStatus, PreparedValidationStatus, PreparedValidationStepStatus } from './runtime-prepared-validation-status-types'
import { preparedStatusObjective } from './runtime-prepared-validation-status.test-fixtures'
import type { Objective, PreparedValidationReference } from './types'

const copy = preparedValidationActivityCopy.en
const reference = (projectId = 'desktop'): PreparedValidationReference => ({ projectId, revision: 2, registrationSha256: 'a'.repeat(64) })

function step(runId: string, recipe: PreparedValidationStepStatus['recipe'], result: PreparedValidationStepStatus['resultClaimStatus'] = null): PreparedValidationStepStatus {
  const dispatched = result !== null
  const settled = result === 'passed'
  const blockerReasons: PreparedValidationStepStatus['blockerReasons'] = [dispatched ? 'runtime_receipt_unverified' : 'reserved_undispatched']

  if (result === 'unknown' || result === 'interrupted') { blockerReasons.push('execution_outcome_unknown') }
  else if (result !== null && result !== 'passed') { blockerReasons.push('validation_failed') }

  if (dispatched && !settled) { blockerReasons.push('resource_cleanup_unconfirmed') }

  return { runId, stepId: recipe, recipe, reservedUnits: 1, dispatched, resourceClaimed: dispatched, resultClaimStatus: result,
    cleanupClaimSettled: settled, latestCleanupSequence: dispatched ? 1 : null, latestRuntimeEventSequence: dispatched ? 2 : null,
    latestRuntimeEventKind: dispatched ? 'receipt' : null, blockerReasons }
}

function recordedSet(projectId = 'desktop', generation = 1, result: PreparedValidationStepStatus['resultClaimStatus'] = null): PreparedValidationSetStatus {
  const setId = `${projectId}-set-${generation}`

  return { setId, requestId: `${projectId}-request-${generation}`, projectId, round: generation - 1, generation, reservedUnits: 2,
    snapshotSha256: 'b'.repeat(64), steps: [step(`${setId}-types`, 'workspace_typescript', result), step(`${setId}-tests`, 'workspace_vitest_ui', result)],
    aggregateClaimPresent: result === 'passed', pairReviewRecorded: result === 'passed', reviewClaimPresent: result === 'passed', state: result === null ? 'reserved_undispatched' : 'runtime_unverified' }
}

function objective(sets: PreparedValidationSetStatus[] = [], references = [reference()]): Objective {
  const history = [...sets].sort((left, right) => right.generation - left.generation || right.projectId.localeCompare(left.projectId)).slice(0, 20)
  const current: PreparedValidationStatus['current'] = {}

  for (const set of sets) {
    if (!current[set.projectId] || current[set.projectId].generation < set.generation) { current[set.projectId] = set }
  }

  const entries = history.flatMap(set => set.steps.map(step => ({ setId: set.setId, projectId: set.projectId, generation: set.generation, runId: step.runId, reasons: step.blockerReasons }))).slice(0, 20)

  const status: PreparedValidationStatus = { objectiveId: 'objective', storageState: 'present', legacyUnits: 3, preparedUnits: 2 * sets.length,
    totalUnits: 3 + 2 * sets.length, totalSets: sets.length, totalHistory: sets.length, current, history, historyTruncated: sets.length > history.length,
    blockers: { blocked: sets.length > 0, totalBlockers: 2 * sets.length, entries, truncated: 2 * sets.length > entries.length },
    scalarMetadataOnly: true, accountingVerified: false, sourceBytesVerified: false, runtimeReceiptsVerified: false,
    executionAuthorized: false, acceptanceAuthorized: false, sourceDeliveryAuthorized: false }

  expect(validPreparedValidationStatus(status, 'objective', references)).toBe(true)

  return { id: 'objective', title: 'Prepared objective', description: 'Explicit registration', createdAt: '2026-10-16T00:00:00Z', ownerId: 'owner',
    source: 'runtime', status: 'active', projects: references.map((ref, index) => ({ id: ref.projectId, root: `root${index}`, recipe: null, team: 'Prepared team' })), preparedValidations: references, preparedValidation: status }
}

function counts() { return screen.getByLabelText(copy.counts) }

function count(label: string) { return within(counts()).getByText(label).nextElementSibling?.textContent }

function currentList() { return screen.getByRole('list', { name: copy.current }) }

function openCurrentSet() { fireEvent.click(within(currentList()).getByText(/Recorded generation:/)) }

it('leaves the empty legacy view unchanged', () => {
  const legacy = objective()
  delete legacy.preparedValidations; delete legacy.preparedValidation
  const { container, rerender } = render(<RuntimePreparedValidationActivity objective={legacy} />)
  expect(container.textContent).toBe('')
  rerender(<RuntimePreparedValidationActivity objective={{ ...legacy, preparedValidations: [] }} />)
  expect(container.textContent).toBe('')
})

it.each([undefined, null])('shows exact immutable references and unavailable for missing status %s', missing => {
  const item = objective(); item.preparedValidation = missing
  render(<RuntimePreparedValidationActivity objective={item} />)
  const refs = screen.getByRole('list', { name: copy.references })
  expect(within(refs).getByText('desktop')).toBeTruthy()
  expect(within(refs).getByText('2')).toBeTruthy()
  expect(within(refs).getByText(reference().registrationSha256)).toBeTruthy()
  expect(screen.getByText(copy.unavailable)).toBeTruthy()
  expect(screen.queryByLabelText(copy.counts)).toBeNull()
  expect(screen.queryByText(copy.noCurrent)).toBeNull()
  expect(screen.queryByText(copy.noHistory)).toBeNull()
})

it('labels explicit zero reservations as recorded and unverified, never as successful', () => {
  const item = objective(); item.preparedValidation!.storageState = 'absent'
  expect(validPreparedValidationStatus(item.preparedValidation, item.id, item.preparedValidations!)).toBe(true)
  render(<RuntimePreparedValidationActivity objective={item} />)
  expect(count(copy.preparedUnits)).toBe('0')
  expect(count(copy.legacyUnits)).toBe('3')
  expect(count(copy.totalUnits)).toBe('3')
  expect(count(copy.totalSets)).toBe('0')
  expect(screen.getByText(copy.absent)).toBeTruthy()
  expect(screen.getByText(copy.noCurrent)).toBeTruthy()
  expect(screen.getByText(copy.noHistory)).toBeTruthy()
  expect(screen.getByText(copy.noBlockers)).toBeTruthy()
  expect(screen.getByText(copy.note)).toBeTruthy()
  expect(screen.queryByText('Passed')).toBeNull()
})

it('keeps reservations distinct from dispatch and does not invent runtime evidence', () => {
  render(<RuntimePreparedValidationActivity objective={objective([recordedSet()])} />)
  openCurrentSet()
  const current = within(currentList())
  expect(current.getByText(`${copy.set}: desktop-set-1`)).toBeTruthy()
  expect(current.getByText('desktop-request-1')).toBeTruthy()
  expect(current.getByText('b'.repeat(64))).toBeTruthy()
  expect(current.getAllByText(copy.noDispatch)).toHaveLength(2)
  expect(current.getAllByText(copy.noResult)).toHaveLength(2)
  expect(current.getAllByText(copy.noCleanupSettled)).toHaveLength(2)
  expect(current.getByText(copy.noAggregateClaim)).toBeTruthy()
  expect(current.getByText(copy.noReviewClaim)).toBeTruthy()
  expect(current.getByText(copy.noPairReview)).toBeTruthy()
})

it('shows passed, cleanup, aggregate and review only as unverified claims with no success styling', () => {
  const { container } = render(<RuntimePreparedValidationActivity objective={objective([recordedSet('desktop', 1, 'passed')])} />)
  openCurrentSet()
  const current = within(currentList())
  expect(current.getAllByText(copy.passed)).toHaveLength(2)
  expect(current.getAllByText(copy.cleanupSettled)).toHaveLength(2)
  expect(current.getByText(copy.aggregateClaim)).toBeTruthy()
  expect(current.getByText(copy.reviewClaim)).toBeTruthy()
  expect(current.getByText(copy.pairReview)).toBeTruthy()
  expect(current.getAllByText(copy.runtime_receipt_unverified)).toHaveLength(2)
  expect(screen.getByText(copy.note)).toBeTruthy()

  for (const word of ['Passed', 'Verified', 'Accepted', 'Approved', 'Completed']) { expect(screen.queryByText(word, { exact: true })).toBeNull() }

  for (const element of container.querySelectorAll('[class]')) { expect(element.className).not.toMatch(/success|positive|green|emerald/) }
})

it.each(['failed', 'interrupted', 'cleanup_failed', 'unknown'] as const)('labels the recorded %s outcome without promoting it to verified evidence', result => {
  render(<RuntimePreparedValidationActivity objective={objective([recordedSet('desktop', 1, result)])} />)
  openCurrentSet()
  const current = within(currentList())
  expect(current.getAllByText(copy[result])).toHaveLength(2)
  expect(current.getAllByText(copy.resource_cleanup_unconfirmed)).toHaveLength(2)
  expect(current.getAllByText(copy.runtime_receipt_unverified)).toHaveLength(2)
  expect(current.getAllByText(result === 'unknown' || result === 'interrupted' ? copy.execution_outcome_unknown : copy.validation_failed)).toHaveLength(2)
})

it('renders the valid eight-current/twenty-history bounds and labels omitted records without adding counts twice', () => {
  const refs = Array.from({ length: 8 }, (_, index) => reference(`project${index}`))
  const sets = refs.flatMap(ref => [1, 2, 3].map(generation => recordedSet(ref.projectId, generation)))
  render(<RuntimePreparedValidationActivity objective={objective(sets, refs)} />)
  expect(screen.getByRole('list', { name: copy.references }).children).toHaveLength(8)
  expect(currentList().children).toHaveLength(8)
  fireEvent.click(screen.getByText(`${copy.history} (20 / 24)`))
  expect(screen.getByRole('list', { name: copy.history }).children).toHaveLength(20)
  expect(screen.getByText(copy.historyTruncated)).toBeTruthy()
  expect(screen.getByText(copy.historyNote)).toBeTruthy()
  expect(count(copy.preparedUnits)).toBe('48')
  expect(count(copy.totalUnits)).toBe('51')
  expect(count(copy.totalSets)).toBe('24')
  fireEvent.click(screen.getByText(copy.blockers, { selector: 'summary' }))
  const blockerList = screen.getAllByRole('list', { name: copy.blockers }).at(-1)!
  expect(blockerList.children).toHaveLength(20)
  expect(screen.getByText(`${copy.blockerCount}: 48`)).toBeTruthy()
  expect(screen.getByText(copy.blockersTruncated)).toBeTruthy()
})

it('has no mutation controls or evidence links and never reads retained paths, manifests or output bodies', () => {
  const item = objective([recordedSet('desktop', 1, 'passed')])

  for (const target of [item, item.preparedValidation!, item.preparedValidation!.history[0], item.preparedValidation!.history[0].steps[0]]) {
    for (const key of ['path', 'files', 'manifest', 'receipt', 'output', 'envelope']) {
      Object.defineProperty(target, key, { get: () => { throw new Error('Non-scalar body must stay unread') } })
    }
  }

  const { container } = render(<RuntimePreparedValidationActivity objective={item} />)
  openCurrentSet()
  fireEvent.click(screen.getByText(`${copy.history} (1 / 1)`))
  fireEvent.click(screen.getByText(copy.blockers, { selector: 'summary' }))
  expect(container.querySelector('a,button,input,textarea,select')).toBeNull()
  expect(container.textContent).not.toContain('Non-scalar')
})

it('treats pair-review presence separately from older review claims and never infers approval', () => {
  const set = recordedSet()
  set.pairReviewRecorded = true
  render(<RuntimePreparedValidationActivity objective={objective([set])} />)
  openCurrentSet()
  const current = within(currentList())
  expect(current.getByText(copy.pairReview)).toBeTruthy()
  expect(current.getByText(copy.noReviewClaim)).toBeTruthy()
  expect(current.getAllByText(copy.noResult)).toHaveLength(2)
  expect(current.getAllByText(copy.noDispatch)).toHaveLength(2)
  expect(screen.queryByText('Approved', { exact: true })).toBeNull()
  expect(screen.queryByText(copy.passed)).toBeNull()
})


it('shows all three v2 steps and an interrupted build as unverified metadata without output bodies or artifact claims', () => {
  const item = preparedStatusObjective(1, 1, [2])
  const set = item.preparedValidation!.current.project0
  set.state = 'runtime_unverified'
  set.steps = [step('types', 'workspace_typescript', 'passed'), step('tests', 'workspace_vitest_ui', 'passed'), step('build', 'workspace_vite_build', 'interrupted')]
  item.preparedValidation!.blockers.entries = set.steps.map(value => ({ setId: set.setId, projectId: set.projectId,
    generation: set.generation, runId: value.runId, reasons: value.blockerReasons }))
  expect(validPreparedValidationStatus(item.preparedValidation, item.id, item.preparedValidations!)).toBe(true)
  const { container } = render(<RuntimePreparedValidationActivity objective={item} />)
  openCurrentSet()
  expect(within(currentList()).getByText(copy.workspace_vite_build)).toBeTruthy()
  expect(within(currentList()).getByText(copy.interrupted)).toBeTruthy()
  expect(within(currentList()).getByText(copy.noSetReview)).toBeTruthy()
  expect(count(copy.preparedUnits)).toBe('3')
  expect(screen.getByText(copy.versionedUnits)).toBeTruthy()
  expect(screen.getByText(copy.buildBoundary)).toBeTruthy()
  expect(screen.queryByText('Passed', { exact: true })).toBeNull()
  expect(container.querySelector('a,button,input,textarea,select')).toBeNull()
})
