import { describe, expect, it, vi } from 'vitest'

import { createMilestoneClient } from './organization-milestone-client'
import type { OwnerPolicyScope } from './organization-owner-policy-types'
const descriptor = () => ({ version: 1, organizationId: 'org', ownerId: 'owner', profile: 'default', milestones: [], objectives: [{ id: 'objective', title: 'Work', projectIds: ['project'] }] })
const draft = () => ({ milestoneId: 'launch', title: 'Launch', expectedRevision: 0, objectiveIds: ['objective'], dependencyIds: [] })

function fixture() {
  const request = vi.fn().mockResolvedValue(descriptor())
  const scope: OwnerPolicyScope = { key: 'socket', ownerKey: 'owner-socket', profile: 'default', eligible: true, organizationId: 'org', ownerId: 'owner', request }

  return { request, scope, client: createMilestoneClient(() => scope, () => 'key') }
}

describe('milestone owner client', () => {
  it('reads without mutation and saves only confirmed exact intent', async () => {
    const { client, request } = fixture()
    const view = await client.read()
    view.objectives[0].projectIds.push('evil')
    await expect(client.save(draft(), false)).rejects.toThrow('confirm')
    expect(request).toHaveBeenCalledTimes(1)
    request.mockResolvedValue({ ...descriptor(), receipt: { milestoneId: 'launch', revision: 1, sha256: 'a'.repeat(64) } })
    await client.save(draft(), true)
    expect(request.mock.calls[1][1]).toMatchObject({ projectIds: ['project'], expectedOrganizationId: 'org', expectedOwnerId: 'owner', confirmed: true, idempotencyKey: 'key' })
    expect(client.pending()).toBe(false)
  })
  it('captures identity by value and rejects lost owner scope', async () => {
    const { client, scope, request } = fixture()
    await client.read(); scope.ownerKey = 'other'
    await expect(client.save(draft(), true)).rejects.toThrow('changed')
    expect(request).toHaveBeenCalledTimes(1)
  })
  it('retains exact uncertain intent and rejects edited retry', async () => {
    const { client, request } = fixture()
    await client.read()
    request.mockRejectedValueOnce(new Error('timeout'))
    await expect(client.save(draft(), true)).rejects.toThrow('timeout')
    const sent = structuredClone(request.mock.calls[1][1])
    await expect(client.read()).rejects.toThrow('pending')
    await expect(client.save({ ...draft(), title: 'Edited' }, true)).rejects.toThrow('exact')
    request.mockResolvedValue({ ...descriptor(), receipt: { milestoneId: 'launch', revision: 1, sha256: 'a'.repeat(64) } })
    await client.save(draft(), true)
    expect(request.mock.calls[2][1]).toEqual(sent)
  })
  it('rejects stale reads and malformed receipts without clearing pending intent', async () => {
    const { client, request, scope } = fixture()
    let resolve!: (value: unknown) => void
    request.mockImplementationOnce(() => new Promise(value => { resolve = value }))
    const reading = client.read(); scope.eligible = false; resolve(descriptor())
    await expect(reading).rejects.toThrow('changed')
    scope.eligible = true; await client.read()
    request.mockResolvedValue({ ...descriptor(), receipt: { milestoneId: 'wrong', revision: 1, sha256: 'a'.repeat(64) } })
    await expect(client.save(draft(), true)).rejects.toThrow('uncertain')
    expect(client.pending()).toBe(true)
  })
  it('rejects unbound objectives before producing a save intent', async () => {
    const { client, request } = fixture()
    request.mockResolvedValue({ ...descriptor(), objectives: [{ id: 'objective', title: 'Work', projectIds: [] }] })
    await client.read()
    await expect(client.save(draft(), true)).rejects.toThrow('workspace')
    expect(client.pending()).toBe(false)
  })
})

it('reconciles an uncertain receipt after a real socket epoch change without resending', async () => {
  const { client, request, scope } = fixture()
  await client.read(); request.mockRejectedValueOnce(new Error('timeout'))
  await expect(client.save(draft(), true)).rejects.toThrow('timeout')
  scope.key = 'new-socket-epoch'
  expect(client.current()).toBe(false)
  request.mockResolvedValue({ ...descriptor(), receipt: { milestoneId: 'launch', revision: 1, sha256: 'a'.repeat(64) } })
  expect(await client.reconcile()).toBe(true)
  expect(client.pending()).toBe(false)
  expect(request.mock.calls[2]).toEqual(['organization.milestoneReceipt', { expectedOrganizationId: 'org', expectedOwnerId: 'owner', idempotencyKey: 'key' }])
  expect(request.mock.calls.filter(call => call[0] === 'organization.reviseMilestone')).toHaveLength(1)
})

it('rebinds missing receipt retry only to the same stable owner identity', async () => {
  const { client, request, scope } = fixture()
  await client.read(); request.mockRejectedValueOnce(new Error('timeout'))
  await expect(client.save(draft(), true)).rejects.toThrow('timeout')
  scope.key = 'new-epoch'; scope.ownerKey = 'other-server'
  await expect(client.reconcile()).rejects.toThrow('same authenticated')
  scope.ownerKey = 'owner-socket'
  request.mockResolvedValue({ ...descriptor(), receipt: null })
  expect(await client.reconcile()).toBe(false)
  expect(client.current()).toBe(true)
  expect(client.pending()).toBe(true)
})
