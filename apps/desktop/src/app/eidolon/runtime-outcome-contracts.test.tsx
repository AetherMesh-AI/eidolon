import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { outcomeContractCopy } from '@/i18n/organization-outcome-contracts'

import { outcomeCriteria, OutcomeCriteriaEditor } from './outcome-criteria-editor'
import { RuntimeAcceptance } from './runtime-acceptance'
import { validOutcomeContract } from './runtime-outcome-contract-types'
import { OutcomeJudgmentPanel } from './runtime-outcome-judgment'
import { validateSnapshot } from './runtime-snapshot-contract'
import type { Objective, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

afterEach(cleanup)

function fixture() {
  const objective: Objective = { id: 'obj-test', title: 'Review report', description: 'Report', ownerId: 'executive', status: 'active', source: 'runtime', createdAt: '2026-10-15T00:00:00Z',
    acceptance: { status: 'reviewing', criteria: ['Owner approves'], round: 1, maxReplans: 2, summary: null, deliverableId: 'final', deliverableSha256: 'b'.repeat(64),
      contract: { schemaVersion: 1, objectiveId: 'obj-test', revision: 2, sha256: 'a'.repeat(64), criteria: [{ id: 'approve', text: 'Owner approves', kind: 'human_judgment', revision: 1, legacy: false }] } } }

  const snapshot = { objectives: [objective], connection: { state: 'ready', scope: 'socket1', ownerScope: 'owner1' } } as OrganizationSnapshot
  const recordOutcomeJudgment = vi.fn().mockResolvedValue(undefined)
  const adapter = { getSnapshot: () => snapshot, recordOutcomeJudgment } as unknown as RuntimeOrganizationAdapter

  return { objective, snapshot, recordOutcomeJudgment, adapter }
}

it('keeps legacy intake untouched and explicit kinds stable while text changes', () => {
  expect(outcomeCriteria(['A', 'B'])).toEqual(['A', 'B'])
  expect(outcomeCriteria(['Changed'], [{ id: 'approve', text: 'Old', kind: 'human_judgment' }])).toEqual([{ id: 'approve', text: 'Old', kind: 'human_judgment' }])
  const change = vi.fn()
  render(<OutcomeCriteriaEditor disabled={false} onChange={change} text="Owner approves" />)
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'human_judgment' } })
  expect(change).toHaveBeenCalledWith([{ id: 'criterion-1', text: 'Owner approves', kind: 'human_judgment' }])
})
it('requires a reason and explicit owner confirmation and sends exact fingerprints', async () => {
  const f = fixture()
  render(<OutcomeJudgmentPanel adapter={f.adapter} expectedOrganizationId="org1" objective={f.objective} />)
  expect((screen.getByRole('button', { name: 'Approve this requirement' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'I inspected the report' } })
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: 'Approve this requirement' }))
  await waitFor(() => expect(f.recordOutcomeJudgment).toHaveBeenCalledTimes(1))
  expect(f.recordOutcomeJudgment.mock.calls[0][0]).toMatchObject({ expectedOrganizationId: 'org1', objectiveId: 'obj-test', criterionId: 'approve', approved: true, confirmed: true,
    contractSha256: 'a'.repeat(64), deliverableSha256: 'b'.repeat(64), reason: 'I inspected the report' })
  expect((await screen.findByRole('status')).textContent).toContain('Final acceptance still requires all checks')
})
it('retains exact idempotent intent after an uncertain error', async () => {
  const f = fixture(); f.recordOutcomeJudgment.mockRejectedValueOnce(new Error('Connection lost'))
  render(<OutcomeJudgmentPanel adapter={f.adapter} expectedOrganizationId="org1" objective={f.objective} />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Incomplete result' } }); fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: 'Reject this requirement' }))
  await screen.findByRole('alert'); const first = f.recordOutcomeJudgment.mock.calls[0][0]
  fireEvent.click(screen.getByRole('button', { name: 'Retry exact review' }))
  await waitFor(() => expect(f.recordOutcomeJudgment).toHaveBeenCalledTimes(2))
  expect(f.recordOutcomeJudgment.mock.calls[1][0]).toEqual(first)
})
it('blocks scope changes and stale deliverable bytes before sending', async () => {
  const f = fixture(); const view = render(<OutcomeJudgmentPanel adapter={f.adapter} expectedOrganizationId="org1" objective={f.objective} />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Looks ready' } }); fireEvent.click(screen.getByRole('checkbox'))
  f.snapshot.connection!.scope = 'socket2'
  fireEvent.click(screen.getByRole('button', { name: 'Approve this requirement' }))
  expect(f.recordOutcomeJudgment).not.toHaveBeenCalled()
  view.rerender(<OutcomeJudgmentPanel adapter={f.adapter} expectedOrganizationId="org1" objective={{ ...f.objective }} />)
  expect(screen.queryByRole('button', { name: 'Approve this requirement' })).toBeNull()
})
it('renders typed revisions and handles legacy or malformed contracts safely', () => {
  const f = fixture()
  render(<RuntimeAcceptance objective={f.objective} onOpenEvidence={vi.fn()} />)
  expect(screen.getByText(/My explicit review/).textContent).toContain('Revision 1')
  expect(validOutcomeContract({ ...f.objective.acceptance!.contract, criteria: [] })).toBe(false)
  expect(validOutcomeContract(null)).toBe(false)
})


it('cannot remap typed obligations through reordered or deleted bulk lines', () => {
  const definitions = [{ id: 'report', text: 'Report exists', kind: 'deliverable' as const }, { id: 'owner', text: 'Owner approves', kind: 'human_judgment' as const }]
  expect(outcomeCriteria(['Owner approves'], definitions)).toEqual(definitions)
  expect(outcomeCriteria(['Owner approves', 'Report exists'], definitions)).toEqual(definitions)
})
it('keeps scoped project identity when selecting a compatible check', () => {
  const change = vi.fn()
  render(<OutcomeCriteriaEditor definitions={[{ id: 'test', text: 'Tests pass', kind: 'authorized_check', checkKind: 'project_tests', projectId: 'project-a' }]} disabled={false} onChange={change} text="Tests pass" />)
  fireEvent.change(screen.getByLabelText('Choose a check'), { target: { value: 'project_tests' } })
  expect(change.mock.calls[0][0][0].projectId).toBe('project-a')
})
it('retains but disables uncertain review when owner identity becomes unavailable', async () => {
  const f = fixture(); f.recordOutcomeJudgment.mockRejectedValue(new Error('Connection lost'))
  const view = render(<OutcomeJudgmentPanel adapter={f.adapter} expectedOrganizationId="org1" objective={f.objective} />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'My decision' } }); fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: 'Approve this requirement' })); await screen.findByRole('alert')
  view.rerender(<OutcomeJudgmentPanel adapter={f.adapter} objective={f.objective} />)
  const button = screen.getByRole('button', { name: 'Retry exact review' }) as HTMLButtonElement
  expect(button.disabled).toBe(true); fireEvent.click(button)
  expect(f.recordOutcomeJudgment).toHaveBeenCalledTimes(1)
})


it('rejects malformed or mismatched outcome snapshots and keeps legacy optional', () => {
  const f = fixture()
  const snapshot = { ...f.snapshot, source: 'runtime', agents: [], tasks: [], activity: [], knowledge: [], requests: [], runtime: { profile: 'p', state: 'ready', capabilities: [], maxWorkers: 1, scope: 'local' } } as OrganizationSnapshot
  expect(() => validateSnapshot(snapshot)).not.toThrow()
  const bad = structuredClone(snapshot)
  bad.objectives[0].acceptance!.contract!.objectiveId = 'other'
  expect(() => validateSnapshot(bad)).toThrow()
  bad.objectives[0].acceptance!.contract = null
  expect(() => validateSnapshot(bad)).not.toThrow()
  expect(validOutcomeContract({ ...f.objective.acceptance!.contract, criteria: [null] })).toBe(false)
})
it('has complete translated copy for all six supported locales', () => {
  expect(Object.keys(outcomeContractCopy)).toHaveLength(6)

  for (const copy of Object.values(outcomeContractCopy)) {
    expect(Object.keys(copy)).toEqual(Object.keys(outcomeContractCopy.en))
    expect(Object.values(copy).every(text => text.trim().length > 0)).toBe(true)
  }
})
