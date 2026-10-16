import { describe, expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway } from './runtime-adapter'
import type { ObjectiveMetadata, OrganizationSnapshot } from './types'

const metadata = (): ObjectiveMetadata => ({ projectIds: ['desktop'], deliveryMode: 'managed_artifact',
  requiredChecks: ['project_typecheck', 'project_tests'],
  preparedValidations: [{ projectId: 'desktop', revision: 1, registrationSha256: 'a'.repeat(64) }] })

function harness() {
  const request = vi.fn().mockRejectedValue(new Error('Uncertain response'))

  const adapter = createRuntimeAdapter({ request: request as OrganizationGateway['request'],
    getScope: () => ({ key: 'socket', ownerKey: 'owner', connected: true }), subscribeScope: () => () => {} })

  return { request, adapter }
}

describe('prepared objective create serialization', () => {
  it('omits preparedValidations entirely for legacy create requests', async () => {
    const h = harness()
    await expect(h.adapter.createObjective('Legacy objective', { projectIds: ['desktop'], deliveryMode: 'source_project' }, 'legacy')).rejects.toThrow('Uncertain response')
    expect(h.request.mock.calls[0][0]).toBe('organization.create')
    expect(h.request.mock.calls[0][1]).not.toHaveProperty('preparedValidations')
    expect(h.request.mock.calls[0][1]).toEqual({ title: 'Legacy objective', projectIds: ['desktop'], executiveId: undefined, managerId: undefined,
      description: undefined, priority: undefined, acceptanceCriteria: undefined, deliveryMode: 'source_project', requiredChecks: undefined, idempotencyKey: 'legacy' })
  })
  it('captures exact refs/project/check arrays before a request can yield, and retains their idempotency identity', async () => {
    const h = harness()
    const input = metadata()
    const first = h.adapter.createObjective('Prepared objective', input)
    input.preparedValidations![0].revision = 2; input.projectIds!.push('other'); input.requiredChecks!.push('source_integration')
    await expect(first).rejects.toThrow('Uncertain response')
    const sent = h.request.mock.calls[0][1]
    expect(sent).toMatchObject(metadata())
    await expect(h.adapter.createObjective('Prepared objective', metadata())).rejects.toThrow('Uncertain response')
    expect(h.request.mock.calls[1][1]).toEqual(sent)
    const changed = metadata(); changed.preparedValidations![0].revision = 2
    await expect(h.adapter.createObjective('Prepared objective', changed)).rejects.toThrow('Uncertain response')
    expect(h.request.mock.calls[2][1].idempotencyKey).not.toBe(sent.idempotencyKey)
  })
  it('uses a caller-retained key unchanged even when a later explicit selection changes', async () => {
    const h = harness()
    await expect(h.adapter.createObjective('Prepared objective', metadata(), 'sticky-intake')).rejects.toThrow()
    const changed = metadata(); changed.preparedValidations![0].revision = 2
    await expect(h.adapter.createObjective('Prepared objective', changed, 'sticky-intake')).rejects.toThrow()
    expect(h.request.mock.calls.map(call => call[1].idempotencyKey)).toEqual(['sticky-intake', 'sticky-intake'])
  })
  it('rejects partial prepared selection before sending any create or snapshot request', async () => {
    const h = harness()
    const input = metadata(); input.projectIds!.push('other')
    await expect(h.adapter.createObjective('Partial objective', input)).rejects.toThrow('every selected project')
    expect(h.request).not.toHaveBeenCalled()
  })
  it('coalesces repeated explicit submission and returns the authoritative created objective', async () => {
    const h = harness()
    let resolve!: (value: unknown) => void
    h.request.mockReturnValueOnce(new Promise(done => { resolve = done }))
    const first = h.adapter.createObjective('Prepared objective', metadata(), 'same-create')
    const second = h.adapter.createObjective('Prepared objective', metadata(), 'same-create')
    await Promise.resolve()
    const objective = { id: 'created', title: 'Prepared objective', description: 'Explicit selection', createdAt: '2026-10-16T00:00:00Z', ownerId: 'owner', status: 'planning' as const, source: 'runtime' as const }

    const snapshot: OrganizationSnapshot = { source: 'runtime', objectives: [objective], agents: [], tasks: [], activity: [], knowledge: [], requests: [],
      runtime: { state: 'ready', capabilities: [], maxWorkers: 1, scope: 'Explicit prepared selection' } }

    resolve({ objective, snapshot })
    expect(await first).toEqual(objective)
    expect(await second).toEqual(objective)
    expect(h.request).toHaveBeenCalledOnce()
    expect(h.request.mock.calls[0][0]).toBe('organization.create')
  })
})
