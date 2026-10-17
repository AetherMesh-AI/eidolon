import { describe, expect, it, vi } from 'vitest'

import { createFolderAccessClient, type FolderAccess, FolderAccessError } from './organization-folder-access-client'
import type { OwnerPolicyScope } from './organization-owner-policy-types'

function harness() {
  const access: FolderAccess = {
    capabilityVersion: 1, organizationId: 'org', ownerId: 'owner', policyGeneration: 3,
    grants: [{ alias: 'root0', path: '/missing/notes', sourcePrefix: [], device: 1, inode: 2, grantHash: 'a'.repeat(64), revoked: false, reviewed: false }],
    paused: false, reviewVersion: 'b'.repeat(64), executionStillStopping: false, uncertainToolReceipts: [], uncertainSourceRequests: [], unsettledProjectRuns: [],
    scope: { kind: 'served-profile', profile: 'default' }
  }

  const request = vi.fn(async (method: string, params: Record<string, unknown>) => {
    if (method === 'organization.folderAccess') { return structuredClone(access) }

    if (method === 'organization.reviewFolderRevocation') {
      return { capabilityVersion: 1, alias: params.alias, accessRevoked: true, reviewed: true, scope: access.scope }
    }

    return { capabilityVersion: 1, scope: access.scope, receipt: { idempotencyKey: params.idempotencyKey,
      organizationId: 'org', ownerId: 'owner', alias: params.alias, grantHash: params.grantHash,
      policyGeneration: Number(params.expectedGeneration) + 1, accessRevoked: true, dispatchPaused: true } }
  })

  const scope: OwnerPolicyScope = { key: 'socket1', ownerKey: 'owner-route', eligible: true, profile: 'default', organizationId: 'org', ownerId: 'owner', request: request as OwnerPolicyScope['request'] }
  const key = vi.fn(() => 'same-key')
  const client = createFolderAccessClient(() => scope, key)

  return { access, request, scope, key, client, mutations: () => request.mock.calls.filter(([method]) => method !== 'organization.folderAccess') }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(yes => { resolve = yes })

  return { promise, resolve }
}

describe('owner folder revocation client', () => {
  it('reads without touching setup and only revokes the exact recorded grant after confirmation', async () => {
    const h = harness()
    const read = await h.client.read()
    expect(h.request.mock.calls).toEqual([['organization.folderAccess', { expectedOrganizationId: 'org' }]])
    read.grants[0].path = '/different'
    await expect(h.client.revoke('root0', false)).rejects.toEqual(new FolderAccessError('confirm'))
    await expect(h.client.revoke('root7', true)).rejects.toEqual(new FolderAccessError('confirm'))
    expect(h.mutations()).toEqual([])
    await expect(h.client.revoke('root0', true)).resolves.toBe('revoke')
    expect(h.mutations()).toEqual([['organization.revokeProjectFolder', {
      expectedOrganizationId: 'org', expectedGeneration: 3, alias: 'root0', grantHash: h.access.grants[0].grantHash,
      confirmed: true, idempotencyKey: 'same-key'
    }]])
    await expect(h.client.revoke('root0', true)).rejects.toEqual(new FolderAccessError('confirm'))
  })

  it.each(['revoke', 'review'] as const)('retains immutable %s intent through uncertain delivery and newer status reads', async action => {
    const h = harness()
    h.access.grants[0].revoked = action === 'review'
    h.access.uncertainToolReceipts = [{ id: 'receipt', request_id: 'request', status: 'unknown' }]
    await h.client.read()
    h.request.mockRejectedValueOnce(new Error('Lost response'))
    await expect(action === 'revoke' ? h.client.revoke('root0', true) : h.client.review('root0', true, true)).rejects.toThrow('Lost response')
    const retained = h.client.pending()!
    retained.params.grantHash = 'c'.repeat(64)
    retained.grant.path = '/changed'
    expect(h.client.pending()?.grant.path).toBe('/missing/notes')
    await expect(h.client.revoke('root0', true)).rejects.toEqual(new FolderAccessError('pending'))
    h.access.policyGeneration = 8; h.access.reviewVersion = 'd'.repeat(64); h.access.grants[0].revoked = true
    await h.client.read()
    await expect(h.client.retry()).resolves.toBe(action)
    const writes = h.mutations()
    expect(writes).toHaveLength(2)
    expect(writes[1]).toEqual(writes[0])
    expect(writes[1][1].expectedGeneration).toBe(3)

    if (action === 'review') {
      expect(writes[1][1]).toEqual({ expectedOrganizationId: 'org', expectedGeneration: 3, alias: 'root0', grantHash: 'a'.repeat(64),
        confirmed: true, reviewVersion: 'b'.repeat(64), acknowledgeUncertain: true })
      expect(h.key).not.toHaveBeenCalled()
    } else { expect(h.key).toHaveBeenCalledOnce() }

    expect(h.client.pending()).toBeNull()
    expect(h.access.uncertainToolReceipts[0].status).toBe('unknown')
  })

  it.each([
    ['revoke', 'generation_conflict'], ['review', 'review_conflict']
  ] as const)('requires a fresh read and confirmation after a definite %s rejection', async (action, reason) => {
    const h = harness()
    h.access.grants[0].revoked = action === 'review'
    h.key.mockReturnValueOnce('first-key').mockReturnValueOnce('fresh-key')
    await h.client.read()
    h.request.mockRejectedValueOnce(Object.assign(new Error(reason), { code: 5074, data: { reason } }))
    const perform = (confirmed: boolean) => action === 'revoke' ? h.client.revoke('root0', confirmed) : h.client.review('root0', confirmed, false)
    await expect(perform(true)).rejects.toEqual(new FolderAccessError('conflict'))
    expect(h.client.pending()).toBeNull()
    await expect(h.client.retry()).rejects.toEqual(new FolderAccessError('pending'))
    await expect(perform(true)).rejects.toEqual(new FolderAccessError('confirm'))
    h.access.policyGeneration = 4; h.access.reviewVersion = 'c'.repeat(64)
    await h.client.read()
    await expect(perform(false)).rejects.toEqual(new FolderAccessError('confirm'))
    expect(h.mutations()).toHaveLength(1)
    await expect(perform(true)).resolves.toBe(action)
    const writes = h.mutations()
    expect(writes[0][1].expectedGeneration).toBe(3)
    expect(writes[1][1].expectedGeneration).toBe(4)

    if (action === 'review') {
      expect(writes[0][1].reviewVersion).toBe('b'.repeat(64))
      expect(writes[1][1].reviewVersion).toBe('c'.repeat(64))
    } else {
      expect(writes[0][1].idempotencyKey).toBe('first-key')
      expect(writes[1][1].idempotencyKey).toBe('fresh-key')
    }
  })

  it.each(['unreadable', 'corrupt', 'locked', 'unrecognized'])('retains the exact uncertain request for ambiguous %s errors', async reason => {
    const h = harness(); await h.client.read()
    h.request.mockRejectedValueOnce(Object.assign(new Error(reason), { code: 5074, data: { reason } }))
    await expect(h.client.revoke('root0', true)).rejects.toThrow(reason)
    expect(h.client.pending()?.params).toMatchObject({ idempotencyKey: 'same-key' })
    await h.client.retry()
    expect(h.mutations()[1]).toEqual(h.mutations()[0])
  })

  it('requires a fresh read and owner confirmation after a capacity refusal with unchanged policy and outcomes', async () => {
    const h = harness()
    h.access.grants[0].revoked = true; h.access.paused = true
    await h.client.read()
    h.request.mockRejectedValueOnce(Object.assign(new Error('execution_still_stopping'), { code: 5074, data: { reason: 'execution_still_stopping' } }))
    await expect(h.client.review('root0', true, false)).rejects.toEqual(new FolderAccessError('conflict'))
    expect(h.client.pending()).toBeNull()
    expect(h.access.grants[0]).toMatchObject({ revoked: true, reviewed: false })
    expect(h.access.paused).toBe(true)
    await expect(h.client.retry()).rejects.toEqual(new FolderAccessError('pending'))
    await expect(h.client.review('root0', true, false)).rejects.toEqual(new FolderAccessError('confirm'))
    await h.client.read()
    await expect(h.client.review('root0', false, false)).rejects.toEqual(new FolderAccessError('confirm'))
    expect(h.mutations()).toHaveLength(1)
    await expect(h.client.review('root0', true, false)).resolves.toBe('review')
    expect(h.mutations()).toHaveLength(2)
    expect(h.mutations()[1]).toEqual(h.mutations()[0])
    expect(h.mutations()[1][1]).toMatchObject({ expectedGeneration: 3, reviewVersion: 'b'.repeat(64) })
    expect(h.key).not.toHaveBeenCalled()
  })

  it.each([
    { code: 5074, data: { reason: 'unrecognized' } },
    { code: 5000, data: { reason: 'execution_still_stopping' } },
    {}
  ])('does not infer a definite review rejection from an error message: %j', async details => {
    const h = harness()
    h.access.grants[0].revoked = true; h.access.paused = true
    await h.client.read()
    h.request.mockRejectedValueOnce(Object.assign(new Error('execution_still_stopping'), details))
    await expect(h.client.review('root0', true, false)).rejects.toThrow('execution_still_stopping')
    expect(h.client.pending()?.action).toBe('review')
    await h.client.read()
    await expect(h.client.review('root0', true, false)).rejects.toEqual(new FolderAccessError('pending'))
    expect(h.mutations()).toHaveLength(1)
    expect(h.access.grants[0]).toMatchObject({ revoked: true, reviewed: false })
  })

  it.each(['key', 'ownerKey', 'profile', 'organizationId', 'ownerId', 'eligible'] as const)('fences a changed %s before writes and never rebinds', async field => {
    const h = harness(); await h.client.read()
    Object.assign(h.scope, { [field]: field === 'eligible' ? false : 'other' })
    await expect(h.client.revoke('root0', true)).rejects.toEqual(new FolderAccessError('changed'))
    await expect(h.client.read()).rejects.toEqual(new FolderAccessError('changed'))
    expect(h.mutations()).toEqual([])
  })

  it.each(['read', 'revoke', 'review'] as const)('rejects a late %s response after the authenticated owner changes', async action => {
    const h = harness()
    h.access.grants[0].revoked = action === 'review'

    if (action !== 'read') { await h.client.read() }
    const delayed = deferred<unknown>()
    h.request.mockImplementationOnce(() => delayed.promise as ReturnType<typeof h.request>)
    const promise = action === 'read' ? h.client.read() : action === 'revoke' ? h.client.revoke('root0', true) : h.client.review('root0', true, false)
    const assertion = expect(promise).rejects.toEqual(new FolderAccessError('changed'))
    h.scope.ownerId = 'new-owner'
    delayed.resolve(h.access)
    await assertion
    h.scope.ownerId = 'owner'
    expect(h.client.current()).toBe(false)
  })

  it('requires settled execution, reconciled project runs, and explicit unknown-outcome acknowledgment', async () => {
    const h = harness()
    h.access.grants[0].revoked = true
    h.access.executionStillStopping = true
    await h.client.read()
    await expect(h.client.review('root0', true, true)).rejects.toEqual(new FolderAccessError('blocked'))
    h.access.executionStillStopping = false
    h.access.unsettledProjectRuns = [{ id: 'run', request_id: 'request' }]
    await h.client.read()
    await expect(h.client.review('root0', true, true)).rejects.toEqual(new FolderAccessError('blocked'))
    h.access.unsettledProjectRuns = []
    h.access.uncertainToolReceipts = [{ id: 'receipt', request_id: 'request', status: 'running' }]
    await h.client.read()
    await expect(h.client.review('root0', true, false)).rejects.toEqual(new FolderAccessError('blocked'))
    await expect(h.client.review('root0', false, true)).rejects.toEqual(new FolderAccessError('confirm'))
    expect(h.mutations()).toEqual([])
    h.access.uncertainToolReceipts = []
    h.access.uncertainSourceRequests = [{ id: 'source', request_id: 'request' }]
    await h.client.read()
    await expect(h.client.review('root0', true, false)).rejects.toEqual(new FolderAccessError('blocked'))
    await h.client.review('root0', true, true)
    expect(h.mutations()).toHaveLength(1)
    expect(h.access.grants[0].revoked).toBe(true)
    expect(h.access.uncertainSourceRequests).toEqual([{ id: 'source', request_id: 'request' }])
  })

  it('rejects unverified descriptors and receipts without clearing uncertain intent', async () => {
    const h = harness()
    h.request.mockResolvedValueOnce({ ...h.access, ownerId: 'someone-else' })
    await expect(h.client.read()).rejects.toEqual(new FolderAccessError('invalid'))
    await h.client.read()
    h.request.mockResolvedValueOnce({ capabilityVersion: 1, scope: h.access.scope, receipt: { alias: 'root7', accessRevoked: true } } as never)
    await expect(h.client.revoke('root0', true)).rejects.toEqual(new FolderAccessError('uncertain'))
    expect(h.client.pending()?.params.alias).toBe('root0')
    await h.client.retry()
    expect(h.mutations()[1]).toEqual(h.mutations()[0])
  })

  it('fails closed for unsupported access reads without falling back or mutating', async () => {
    const h = harness()
    h.request.mockRejectedValueOnce(Object.assign(new Error('Method not found'), { code: -32601 }))
    await expect(h.client.read()).rejects.toThrow('Method not found')
    await expect(h.client.revoke('root0', true)).rejects.toEqual(new FolderAccessError('confirm'))
    expect(h.request).toHaveBeenCalledOnce()
  })
})
