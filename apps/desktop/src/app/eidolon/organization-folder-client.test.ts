import { describe, expect, it, vi } from 'vitest'

import { createFolderClient } from './organization-folder-client'
import type { OwnerPolicyScope } from './organization-owner-policy-types'

function harness() {
  const folder = { path: '/selected/notes', projectId: 'notes', team: 'general' }
  const descriptor = { capabilityVersion: 1, organizationId: 'org', ownerId: 'owner', policyGeneration: 2, sourceRevision: 'a'.repeat(64), rootAliases: [], teams: ['general'], blockers: [], inspectionEnabled: false, scope: { kind: 'served-profile', profile: 'default' } }
  const candidate = { folder, project: { id: 'notes', root: 'root0', recipe: null, team: 'general' }, directoryIdentity: { device: 1, inode: 2 } }
  let fail = false

  const request = vi.fn(async (method: string, params: Record<string, unknown>) => {
    if (method === 'organization.folderSetup') { return descriptor }

    if (method === 'organization.previewProjectFolder') { return { ...descriptor, candidate, previewVersion: 'b'.repeat(64) } }

    if (fail) { throw new Error('Lost response') }

    return { capabilityVersion: 1, scope: descriptor.scope, receipt: { ...params, organizationId: 'org', ownerId: 'owner', policyGeneration: 3 } }
  })

  const scope: OwnerPolicyScope = { key: 'socket1', ownerKey: 'owner1', eligible: true, profile: 'default', organizationId: 'org', ownerId: 'owner', request: request as OwnerPolicyScope['request'] }
  const client = createFolderClient(() => scope, () => 'same-key')

  return { client, scope, request, folder, descriptor, candidate, fail: (value: boolean) => { fail = value } }
}

describe('explicit owner folder registration', () => {
  it('only registers after exact preview and explicit confirmation', async () => {
    const h = harness()
    await h.client.read()
    await expect(h.client.register(true)).rejects.toThrow('Review')
    await h.client.preview(h.folder)
    await expect(h.client.register(false)).rejects.toThrow('Review')
    expect(h.request.mock.calls.filter(call => call[0] === 'organization.registerProjectFolder')).toHaveLength(0)
    expect(await h.client.register(true)).toEqual(h.candidate.project)
  })
  it('retries the same immutable intent after uncertain delivery', async () => {
    const h = harness(); await h.client.read(); await h.client.preview(h.folder)
    h.fail(true)
    await expect(h.client.register(true)).rejects.toThrow('Lost response')
    await expect(h.client.preview(h.folder)).rejects.toThrow('another preview')
    h.fail(false)
    await h.client.register(true)
    const calls = h.request.mock.calls.filter(call => call[0] === 'organization.registerProjectFolder')
    expect(calls).toHaveLength(2)
    expect(calls[0][1]).toEqual(calls[1][1])
  })
  it('rejects connection and owner changes before any write', async () => {
    const h = harness(); await h.client.read(); await h.client.preview(h.folder)
    h.scope.key = 'socket2'
    await expect(h.client.register(true)).rejects.toThrow('connection changed')
    expect(h.request.mock.calls.filter(call => call[0] === 'organization.registerProjectFolder')).toHaveLength(0)
  })
  it('rejects a preview for a different folder or alias', async () => {
    const h = harness(); await h.client.read()
    h.candidate.project.root = 'root1'
    await expect(h.client.preview(h.folder)).rejects.toThrow('does not match')
  })
  it('does not fall back when the backend is unsupported', async () => {
    const h = harness(); h.request.mockRejectedValueOnce(new Error('Unsupported method'))
    await expect(h.client.read()).rejects.toThrow('Unsupported')
    expect(h.request.mock.calls).toHaveLength(1)
  })
})
