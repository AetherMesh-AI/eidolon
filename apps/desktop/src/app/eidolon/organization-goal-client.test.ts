import { expect, it, vi } from 'vitest'

import { createGoalClient, type GoalTemplate } from './organization-goal-client'
import type { OwnerPolicyScope } from './organization-owner-policy-types'
const descriptor = () => ({ version: 1, organizationId: 'org', ownerId: 'owner', profile: 'default', goals: [], plans: [], objectives: [], leaders: [], projectIds: [] })

function fixture() {
  const request = vi.fn().mockResolvedValue(descriptor())
  const scope: OwnerPolicyScope = { key: 'socket', ownerKey: 'owner-socket', profile: 'default', eligible: true, organizationId: 'org', ownerId: 'owner', request }

  return { request, scope, client: createGoalClient(() => scope, () => 'key') }
}

const action = () => ({ goalId: 'goal', objectiveIds: ['one'], limits: {}, paused: false, expectedRevision: 0 })
const receipt = () => ({ ...descriptor(), receipt: { goalId: 'goal', revision: 1, sha256: 'a'.repeat(64) } })
it('requires explicit review and captures scope by value', async () => {
  const { client, request, scope } = fixture(); await client.read()
  await expect(client.write('organization.configureGoal', action(), false)).rejects.toThrow('confirm')
  expect(request).toHaveBeenCalledTimes(1)
  scope.ownerKey = 'changed'
  await expect(client.write('organization.configureGoal', action(), true)).rejects.toThrow('changed')
})
it('retains exact uncertain mutation and reuses its key', async () => {
  const { client, request } = fixture(); await client.read()
  request.mockRejectedValueOnce(new Error('timeout'))
  await expect(client.write('organization.configureGoal', action(), true)).rejects.toThrow('timeout')
  const sent = structuredClone(request.mock.calls[1][1])
  await expect(client.write('organization.configureGoal', { ...action(), goalId: 'other' }, true)).rejects.toThrow('exact')
  await expect(client.read()).rejects.toThrow('pending')
  request.mockResolvedValue(receipt())
  await client.write('organization.configureGoal', action(), true)
  expect(request.mock.calls[2][1]).toEqual(sent)
  expect(client.pending()).toBe(false)
})
it('reconciles only the same stable owner after connection epoch changes', async () => {
  const { client, request, scope } = fixture(); await client.read()
  request.mockRejectedValueOnce(new Error('timeout'))
  await expect(client.write('organization.configureGoal', action(), true)).rejects.toThrow()
  scope.key = 'new-epoch'; request.mockResolvedValue(receipt())
  expect(await client.reconcile()).toBe(true)
  expect(client.pending()).toBe(false)
  expect(request.mock.calls[2][0]).toBe('organization.goalReceipt')
})
it('rejects another owner and mismatched receipt without losing pending intent', async () => {
  const { client, request, scope } = fixture(); await client.read()
  request.mockRejectedValueOnce(new Error('timeout'))
  await expect(client.write('organization.configureGoal', action(), true)).rejects.toThrow()
  scope.ownerId = 'other'; await expect(client.reconcile()).rejects.toThrow('same authenticated')
  scope.ownerId = 'owner'; request.mockResolvedValue({ ...receipt(), receipt: { goalId: 'wrong', revision: 1, sha256: 'a'.repeat(64) } })
  await expect(client.reconcile()).rejects.toThrow('invalid')
  expect(client.pending()).toBe(true)
})
it('approval requires an exact still-current preview and never enables implicitly', async () => {
  const { client, request } = fixture(); await client.read()
  const draft = { goalId: 'goal', templates: [] as GoalTemplate[], deadlineTimestamp: 123 }
  request.mockResolvedValue({ ...descriptor(), preview: { plan: { goalId: 'goal' }, sha256: 'b'.repeat(64) } })
  await client.preview(draft)
  await expect(client.write('organization.approveGoalContinuation', { ...draft, deadlineTimestamp: 456, previewSha256: 'b'.repeat(64) }, true)).rejects.toThrow('fresh matching')
  request.mockResolvedValue({ ...descriptor(), receipt: { goalId: 'goal', revision: 1, enabled: false, sha256: 'b'.repeat(64) } })
  await client.write('organization.approveGoalContinuation', { ...draft, previewSha256: 'b'.repeat(64) }, true)
  expect(request.mock.calls[2][0]).toBe('organization.approveGoalContinuation')
  expect(request.mock.calls.some(c => c[0] === 'organization.setGoalContinuationEnabled')).toBe(false)
})

it('rejects malformed nested blockers and out-of-range deadlines before rendering', async () => {
  const { client, request } = fixture()
  request.mockResolvedValue({ ...descriptor(), plans: [{ goalId: 'goal', enabled: false, canEnable: true, controlRevision: 1, waitingReason: null, templates: [], launches: [], templateStates: [{ id: 'one', launched: false, blockers: null }] }] })
  await expect(client.read()).rejects.toThrow('fields')
  const limits = { modelCalls: 2, tokens: 10, stages: 2, projectRuns: 0, costUsd: null, deadlineTimestamp: Number.MAX_SAFE_INTEGER, completionReserve: { modelCalls: 0, tokens: 0, costUsd: null } }
  request.mockResolvedValue({ ...descriptor(), goals: [{ id: 'goal', revision: 1, paused: false, objectiveIds: [], limits, reserved: {} }] })
  await expect(client.read()).rejects.toThrow('fields')
  limits.deadlineTimestamp = 1800000000
  limits.completionReserve.tokens = -1
  await expect(client.read()).rejects.toThrow('fields')
})

it('accepts valid allowance values and rejects unsafe reserved children or manager identities', async () => {
  const { client, request } = fixture()
  const goal = { id: 'goal', revision: 1, paused: false, objectiveIds: [], limits: { modelCalls: 2, tokens: 10, stages: 2, projectRuns: 0, costUsd: null, deadlineTimestamp: 1800000000, completionReserve: { modelCalls: 0, tokens: 0, costUsd: null } }, reserved: { modelCalls: 0, tokens: 0, stages: 0, projectRuns: 0, costUsd: null } }
  request.mockResolvedValue({ ...descriptor(), goals: [goal] })
  expect((await client.read()).goals[0].reserved.costUsd).toBeNull()
  request.mockResolvedValue({ ...descriptor(), goals: [{ ...goal, reserved: { ...goal.reserved, modelCalls: {} } }] })
  await expect(client.read()).rejects.toThrow('fields')
  request.mockResolvedValue({ ...descriptor(), leaders: [{ id: 'manager', name: 'Manager', role: 'Manager', manager_id: {} }] })
  await expect(client.read()).rejects.toThrow('fields')
})
