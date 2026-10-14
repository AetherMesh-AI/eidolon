import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway, type OrganizationScope } from './runtime-adapter'
import { validAuditModelRouting, validModelAssignment, validModelDispatch, validModelRouting } from './runtime-model-routing-contract'
import { autoAssignment, modelDispatch, modelRouting, modelRoutingAudit, modelRoutingSnapshot } from './runtime-model-routing.test-support'
import { validateSnapshot } from './runtime-snapshot-contract'
import type { OrganizationExecutionAudit, OrganizationSnapshot } from './types'

it('accepts absent legacy extensions and immutable old dispatches while rejecting malformed or contradictory routing data', () => {
  const older = modelRoutingSnapshot()
  delete older.runtime!.modelRouting
  older.agents.forEach(agent => {delete agent.modelAssignment})
  older.requests!.forEach(request => {delete request.routeDispatch})
  expect(validateSnapshot(older)).toMatchObject(older)
  const fresh = modelRoutingSnapshot()
  expect(validateSnapshot(fresh).requests![0].routeDispatch).toEqual(modelDispatch)
  expect(modelDispatch.policyGeneration).toBeLessThan(modelRouting.policyGeneration)
  expect(modelRouting.routes.some(route => route.id === modelDispatch.routeId)).toBe(false)
  expect(validModelAssignment({ mode: 'legacy', label: 'Legacy', routeIds: [], minimumEvidence: null, maxFallbacks: 0 })).toBe(true)

  for (const invalid of [null, { ...modelRouting, policyGeneration: -1 }, { ...modelRouting, routes: [modelRouting.routes[0], modelRouting.routes[0]] }, { ...modelRouting, transport: 'all-providers' }, { ...modelRouting, routes: [{ ...modelRouting.routes[0], maxOutputTokens: 31000 }] }, { ...modelRouting, routes: [{ ...modelRouting.routes[0], reasoning: 'automatic' }] }]) {
    expect(validModelRouting(invalid)).toBe(false)
    expect(() => validateSnapshot({ ...fresh, runtime: { ...fresh.runtime!, modelRouting: invalid } } as OrganizationSnapshot)).toThrow('invalid model-routing')
  }

  for (const invalid of [null, { ...autoAssignment, routeIds: [] }, { ...autoAssignment, maxFallbacks: 3 }, { ...autoAssignment, minimumEvidence: null }, { ...autoAssignment, routeIds: ['economy', 'economy'] }, { ...autoAssignment, mode: 'legacy' }]) {
    expect(validModelAssignment(invalid)).toBe(false)
    expect(() => validateSnapshot({ ...fresh, agents: [{ ...fresh.agents[1], modelAssignment: invalid }] } as OrganizationSnapshot)).toThrow('invalid model-routing')
  }

  expect(() => validateSnapshot({ ...fresh, agents: [{ ...fresh.agents[0], modelAssignment: autoAssignment }] })).toThrow('invalid model-routing')
  expect(() => validateSnapshot({ ...fresh, agents: [{ ...fresh.agents[1], modelAssignment: { ...autoAssignment, routeIds: ['missing-route'] } }] })).toThrow('invalid model-routing')
  expect(() => validateSnapshot({ ...fresh, runtime: { ...fresh.runtime!, modelRouting: { ...modelRouting, enabled: false } } })).toThrow('invalid model-routing')

  for (const invalid of [null, { ...modelDispatch, requestId: 'another-request' }, { ...modelDispatch, mode: 'legacy' }, { ...modelDispatch, createdAt: 'not-a-date' }, { ...modelDispatch, pricing: { inputUsdPerMillion: null, outputUsdPerMillion: '1' } }, { ...modelDispatch, pricing: { inputUsdPerMillion: null, outputUsdPerMillion: null } }, { ...modelDispatch, inputBound: NaN }, { ...modelDispatch, evidenceLevel: 1 }, { ...modelDispatch, pricing: { inputUsdPerMillion: '-1', outputUsdPerMillion: '1' } }]) {
    expect(validModelDispatch(invalid, 'draft')).toBe(false)

    if (invalid !== null) {
      expect(() => validateSnapshot({ ...fresh, requests: [{ ...fresh.requests![0], routeDispatch: invalid }] } as OrganizationSnapshot)).toThrow('invalid model-routing')
    }
  }

  expect(validateSnapshot({ ...fresh, requests: [{ ...fresh.requests![0], routeDispatch: null }] }).requests![0].routeDispatch).toBeNull()
  expect(validModelDispatch({ ...modelDispatch, mode: 'fixed', pricing: { inputUsdPerMillion: null, outputUsdPerMillion: null } }, 'draft')).toBe(true)
  // Decimal pricing can legitimately retain signed zero; it is still exact zero, never a negative allowance.
  const signedZero = modelRoutingAudit()
  const prices = { inputUsdPerMillion: '-0', outputUsdPerMillion: '-0.000000000' }
  signedZero.modelDispatches![0].pricing = prices
  signedZero.modelCalls[0].pricing = { policyGeneration: modelDispatch.policyGeneration, ...prices }
  signedZero.modelCalls[0].reserved_cost_usd = '-0.000000000'
  expect(validAuditModelRouting(signedZero)).toBe(true)
  expect(validModelDispatch({ ...modelDispatch, pricing: { ...prices, inputUsdPerMillion: '-0.000000001' } }, 'draft')).toBe(false)
})

it('fences routing reads by profile, retains last good policy on invalid polls, and validates exact dispatch-reservation ownership', async () => {
  let scope: OrganizationScope = { key: 'owner-socket', ownerKey: 'owner-profile', connected: true }
  const listeners = new Set<() => void>()
  const read = vi.fn().mockResolvedValue(modelRoutingSnapshot())

  const adapter = createRuntimeAdapter({
    request: read as OrganizationGateway['request'], getScope: () => scope,
    subscribeScope: listener => {listeners.add(listener);

 return () => {listeners.delete(listener)}}
  })

  await adapter.refresh()
  const savedAgent = adapter.getSnapshot().agents[1]
  const invalid = modelRoutingSnapshot()
  invalid.agents[1].modelAssignment = { ...autoAssignment, routeIds: ['missing'] }
  read.mockResolvedValueOnce(invalid)
  await adapter.refresh()
  expect(adapter.getSnapshot().connection?.state).toBe('error')
  expect(adapter.getSnapshot().agents[1]).toBe(savedAgent)
  expect(savedAgent.identityId).toBe('persistent-worker')

  let finish!: (snapshot: OrganizationSnapshot) => void
  read.mockReturnValueOnce(new Promise<OrganizationSnapshot>(resolve => {finish = resolve}))
  const pending = adapter.refresh()
  await Promise.resolve()
  scope = { key: 'other-socket', ownerKey: 'other-profile', connected: true }
  listeners.forEach(listener => listener())
  finish(modelRoutingSnapshot())
  await pending
  expect(adapter.getSnapshot().agents).toEqual([])
  expect(adapter.getSnapshot().runtime?.modelRouting).toBeUndefined()

  const audit = modelRoutingAudit()
  read.mockResolvedValueOnce(audit)
  await expect(adapter.getExecutionAudit!('draft')).resolves.toEqual(audit)
  const oldAudit = { ...audit, modelDispatches: undefined, modelCalls: audit.modelCalls.map(call => ({ ...call, dispatchId: undefined })) }
  expect(validAuditModelRouting(oldAudit)).toBe(true)

  for (const bad of [
    { ...audit, modelDispatches: null },
    { ...audit, modelDispatches: [{ ...modelDispatch, requestId: 'other-request' }] },
    { ...audit, modelDispatches: [modelDispatch, modelDispatch] },
    { ...audit, modelCalls: [] },
    { ...audit, modelDispatches: [modelDispatch, { ...modelDispatch, id: 'another-dispatch' }], modelCalls: [audit.modelCalls[0], { ...audit.modelCalls[0], id: 'another-call', dispatchId: 'another-dispatch' }] },
    { ...audit, modelCalls: [{ ...audit.modelCalls[0], dispatchId: 'missing' }] },
    { ...audit, modelCalls: [{ ...audit.modelCalls[0], provider: 'different-provider' }] },
    { ...audit, modelCalls: [{ ...audit.modelCalls[0], model: 'different-model' }] },
    { ...audit, modelCalls: [{ ...audit.modelCalls[0], token: 'different-attempt' }] },
    { ...audit, modelCalls: [{ ...audit.modelCalls[0], output_limit: 1 }] },
    { ...audit, modelCalls: [{ ...audit.modelCalls[0], input_limit: 12000 }] },
    { ...audit, modelCalls: [{ ...audit.modelCalls[0], reserved_cost_usd: '0.0025' }] },
    { ...audit, modelCalls: [{ ...audit.modelCalls[0], pricing: undefined }] },
    { ...audit, modelCalls: [{ ...audit.modelCalls[0], pricing: { ...audit.modelCalls[0].pricing!, policyGeneration: 7 } }] },
    { ...audit, modelCalls: [{ ...audit.modelCalls[0], pricing: { ...audit.modelCalls[0].pricing!, inputUsdPerMillion: '0' } }] }
  ]) {
    expect(validAuditModelRouting(bad as OrganizationExecutionAudit)).toBe(false)
    read.mockResolvedValueOnce(bad)
    await expect(adapter.getExecutionAudit!('draft')).rejects.toThrow('execution audit')
  }

  let finishAudit!: (audit: OrganizationExecutionAudit) => void
  read.mockReturnValueOnce(new Promise<OrganizationExecutionAudit>(resolve => {finishAudit = resolve}))
  const pendingAudit = adapter.getExecutionAudit!('draft')
  const rejected = expect(pendingAudit).rejects.toThrow(/connection or profile changed/)
  await Promise.resolve()
  scope = { key: 'third-socket', ownerKey: 'third-profile', connected: true }
  listeners.forEach(listener => listener())
  finishAudit(audit)
  await rejected
})
