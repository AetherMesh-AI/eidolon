import { describe, expect, it, vi } from 'vitest'

import { createIntakeInspectionClient, type IntakeInspectionDescriptor } from './organization-intake-inspection-client'
import type { MetadataScope } from './organization-metadata-client'

function descriptor(): IntakeInspectionDescriptor {
  return {
    profile: 'default',
    organizationId: 'org',
    revision: 0,
    enabled: false,
    workerId: null,
    projectIds: [],
    paths: [],
    workers: [{ id: 'worker', name: 'Research worker', team: 'research' }],
    projects: [
      { id: 'docs', root: 'root0', team: 'research' },
      { id: 'private', root: 'root1', team: 'other' }
    ]
  }
}

function setup() {
  const request = vi.fn().mockResolvedValue(descriptor())
  let scope: MetadataScope = { key: 'socket', ownerKey: 'owner', profile: 'default', eligible: true, request }
  const key = vi.fn(() => 'immutable-key')
  const client = createIntakeInspectionClient(() => scope, key)

  return {
    client,
    request,
    key,
    rotate: (patch: Partial<MetadataScope>) => {
      scope = { ...scope, ...patch }
    }
  }
}

function edit(client: ReturnType<typeof createIntakeInspectionClient>) {
  client.edit({ enabled: true, workerId: 'worker' })
  client.edit({ projectIds: ['docs'], pathsText: 'root0/README.md\nroot0/context.md' })
}

function deferred<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(yes => {
    resolve = yes
  })

  return { promise, resolve }
}

const mutations = (request: ReturnType<typeof vi.fn>) =>
  request.mock.calls.filter(([method]) => method.endsWith('.configure'))

describe('inspection delegation authority', () => {
  it('requires a reviewed explicit selection, cancels without writing, and disables without hidden delegation', async () => {
    const { client, request, key } = setup()
    await client.read()
    edit(client)
    await client.confirm()
    expect(mutations(request)).toEqual([])
    client.review()
    client.cancel()
    await client.confirm()
    expect(mutations(request)).toEqual([])
    expect(key).not.toHaveBeenCalled()
    client.review()
    const preview = client.getSnapshot().preview!
    request.mockResolvedValueOnce({ ...descriptor(), ...preview, revision: 1 })
    await client.confirm()
    expect(mutations(request)[0][1]).toEqual({
      enabled: true,
      workerId: 'worker',
      projectIds: ['docs'],
      paths: ['root0/README.md', 'root0/context.md'],
      expectedOrganizationId: 'org',
      expectedRevision: 0,
      confirmed: true,
      idempotencyKey: 'immutable-key'
    })
    expect(client.getSnapshot().saved).toBe(true)
    client.edit({ enabled: false })
    client.review()
    expect(client.getSnapshot().preview).toMatchObject({ enabled: false, workerId: null, projectIds: [], paths: [] })
    request.mockResolvedValueOnce({ ...descriptor(), revision: 2 })
    await client.confirm()
    expect(mutations(request)[1][1]).toMatchObject({
      expectedRevision: 1,
      enabled: false,
      workerId: null,
      projectIds: [],
      paths: []
    })
    expect(client.getSnapshot().descriptor?.enabled).toBe(false)
  })

  it('rejects cross-team, secret, traversal, unselected roots and oversized selections before confirmation', async () => {
    const { client, request } = setup()
    await client.read()
    edit(client)

    const invalid = [
      { workerId: 'missing' },
      { projectIds: ['private'], pathsText: 'root1/README.md' },
      { projectIds: ['docs', 'docs'] },
      { pathsText: 'root0/.env' },
      { pathsText: 'root0/../README.md' },
      { pathsText: 'root0/sub/README.md' },
      { pathsText: 'root1/README.md' },
      { pathsText: 'root0/README.md\nroot0/README.md' },
      { pathsText: 'root0/README.md\nroot0/README.rst\nroot0/package.json\nroot0/context.md\nroot0/go.mod' },
      { pathsText: '' }
    ]

    for (const patch of invalid) {
      edit(client)
      client.edit(patch)
      client.review()
      expect(client.getSnapshot().preview).toBeNull()
      expect(client.getSnapshot().error).toBe('invalid')
      await client.confirm()
    }

    expect(mutations(request)).toEqual([])
  })

  it('retains one immutable payload and key after uncertain transport, rejecting edits and mismatched acknowledgments', async () => {
    const { client, request, key } = setup()
    await client.read()
    edit(client)
    client.review()
    request.mockImplementationOnce(async (_method, payload) => {
      payload.paths.push('root0/go.mod')
      throw new Error('Reply lost')
    })
    await client.confirm()
    const intent = structuredClone(client.getSnapshot().intent)
    expect(intent?.paths).toEqual(['root0/README.md', 'root0/context.md'])
    client.edit({ enabled: false, workerId: 'another' })
    client.cancel()
    client.review()
    await client.read()
    expect(client.getSnapshot().intent).toEqual(intent)
    request.mockResolvedValueOnce({ ...descriptor(), revision: 1 })
    await client.retry()
    expect(client.getSnapshot().error).toBe('uncertain')
    expect(client.getSnapshot().intent).toEqual(intent)
    request.mockResolvedValueOnce({ ...descriptor(), ...intent, revision: 1 })
    await client.retry()
    expect(
      mutations(request)
        .slice(1)
        .map(([, payload]) => payload)
    ).toEqual([intent, intent])
    expect(key).toHaveBeenCalledOnce()
    expect(client.getSnapshot().intent).toBeNull()
    expect(client.getSnapshot().saved).toBe(true)
  })

  it('clears drafts and ignores late reads and writes across owner, profile, socket and eligibility rotations', async () => {
    for (const patch of [
      { ownerKey: 'other-owner' },
      { profile: 'other-profile' },
      { key: 'other-socket' },
      { eligible: false }
    ]) {
      const { client, request, rotate } = setup()
      const lateRead = deferred<IntakeInspectionDescriptor>()
      request.mockReturnValueOnce(lateRead.promise)
      const reading = client.read()
      rotate(patch)
      expect(client.getSnapshot().busy).toBe(false)
      lateRead.resolve(descriptor())
      await reading
      expect(client.getSnapshot().descriptor).toBeNull()
    }

    const { client, request, rotate } = setup()
    await client.read()
    edit(client)
    client.review()
    rotate({ key: 'new-socket' })
    await client.confirm()
    expect(client.getSnapshot().draft).toBeNull()
    expect(mutations(request)).toEqual([])
    await client.read()
    edit(client)
    client.review()
    const lateWrite = deferred<IntakeInspectionDescriptor>()
    request.mockReturnValueOnce(lateWrite.promise)
    const saving = client.confirm()
    rotate({ ownerKey: 'other-owner' })
    expect(client.getSnapshot().intent).toBeNull()
    lateWrite.resolve({
      ...descriptor(),
      enabled: true,
      workerId: 'worker',
      projectIds: ['docs'],
      paths: ['root0/README.md', 'root0/context.md'],
      revision: 1
    })
    await saving
    expect(client.getSnapshot().descriptor).toBeNull()
    expect(client.getSnapshot().saved).toBe(false)
    await client.retry()
    expect(mutations(request)).toHaveLength(1)
  })
})
