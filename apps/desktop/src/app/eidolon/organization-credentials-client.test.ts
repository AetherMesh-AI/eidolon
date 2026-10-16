import { expect, it } from 'vitest'

import { credentialMetadata, credentialName, validateCredentialCatalog, validCredentialMetadata } from './organization-credentials-contract'
import { catalogFixture, credentialsHarness, deferred, entryFixture } from './organization-credentials-test-support'

it('validates exact naming/metadata and strips unexpected secret-shaped response fields', async () => {
  const h = await credentialsHarness()
  const metadata = credentialMetadata(entryFixture())
  expect(credentialName(metadata)).toBe('github_eidolonReleases_token')

  for (const part of ['lowerCase9', 'a', 'x'.repeat(48)]) {
    expect(validCredentialMetadata({ ...metadata, label: part })).toBe(true)
  }

  for (const part of ['Uppercase', 'has_underscore', 'has-dash', '9number', 'x'.repeat(49)]) {
    expect(validCredentialMetadata({ ...metadata, label: part })).toBe(false)
  }

  expect(validCredentialMetadata({ ...metadata, description: '' })).toBe(false)
  expect(validCredentialMetadata({ ...metadata, description: 'a\nsecret' })).toBe(false)
  expect(validCredentialMetadata({ ...metadata, expiresAt: '2026-12-01T00:00:00Z' })).toBe(false)
  expect(validCredentialMetadata({ ...metadata, expiresAt: 1800000000 })).toBe(true)
  const data = catalogFixture()
  Object.assign(data.entries[0], { value: 'SYNTHETIC-SECRET', prefix: 'SYN', hash: 'hash' })
  const sanitized = validateCredentialCatalog(data, h.scope)
  expect(JSON.stringify(sanitized)).not.toContain('SYNTHETIC-SECRET')
  expect(sanitized.entries[0]).not.toHaveProperty('prefix')
  expect(sanitized.entries[0]).not.toHaveProperty('hash')
  expect(() => validateCredentialCatalog({ ...data, organizationId: 'other' }, h.scope)).toThrow()
})

it('retains only identity, binds secure input to the replacement operation, and never replays an uncertain value', async () => {
  const h = await credentialsHarness()
  h.request.mockImplementationOnce(async () => ({ inputHandle: 'SYNTHETIC-HANDLE', expiresIn: 90 }))
  h.request.mockRejectedValueOnce(new Error('SYNTHETIC-VALUE was echoed by an untrusted error'))
  expect(await h.client.mutate('replace_value', entryFixture().id, 3, null, 'SYNTHETIC-VALUE')).toBe(false)
  const calls = h.request.mock.calls
  expect(calls[1][0]).toBe('organization.credentials.secureInput')
  expect(calls[2][0]).toBe('organization.credentials.mutate')
  expect(calls[1][1].operationId).toBe(calls[2][1].operationId)
  expect(calls[2][1]).not.toHaveProperty('value')
  expect(calls[2][1].inputHandle).toBe('SYNTHETIC-HANDLE')
  expect(JSON.stringify(h.client.getSnapshot())).not.toMatch(/SYNTHETIC-(VALUE|HANDLE)/)
  expect([...h.values.values()].join()).not.toMatch(/SYNTHETIC-(VALUE|HANDLE)/)
  const retained = h.client.getSnapshot().intent
  expect(retained?.action).toBe('replace_value')
  expect(await h.client.mutate('replace_value', entryFixture().id, 3, null, 'another')).toBe(false)
  const remounted = h.makeClient()
  await remounted.read()
  expect(remounted.getSnapshot().intent).toEqual(retained)
  await remounted.reconcile()
  expect(remounted.getSnapshot().intent).toEqual(retained)
  expect(h.request.mock.calls.filter(call => call[0] === 'organization.credentials.mutate')).toHaveLength(1)
  h.receipts.set(retained!.operationId, { entryId: entryFixture().id, operationId: retained!.operationId, action: 'replace_value', revision: 4, version: 2 })
  expect(await remounted.reconcile()).toBe(true)
  expect(remounted.getSnapshot().intent).toBeNull()
  expect(h.values.size).toBe(0)
  expect(h.request.mock.calls.filter(call => call[0] === 'organization.credentials.secureInput')).toHaveLength(1)
})

it('fences late catalogs and secure input across disconnect, owner, profile, and socket changes', async () => {
  for (const change of [
    { eligible: false }, { ownerId: 'new-owner' }, { organizationId: 'new-org' }, { profile: 'different' }, { key: 'new-socket' }
  ]) {
    const h = await credentialsHarness()
    const pending = deferred<{ inputHandle: string; expiresIn: number }>()
    h.request.mockImplementationOnce(() => pending.promise)
    const writing = h.client.mutate('replace_value', entryFixture().id, 3, null, 'synthetic')
    Object.assign(h.scope, change)
    h.client.getSnapshot()
    pending.resolve({ inputHandle: 'synthetic-handle', expiresIn: 90 })
    expect(await writing).toBe(false)
    expect(h.request.mock.calls.filter(call => call[0] === 'organization.credentials.mutate')).toHaveLength(0)
    expect(h.client.getSnapshot().catalog).toBeNull()
    expect(h.values.size).toBe(0)
  }

  const h = await credentialsHarness()
  const pending = deferred<unknown>()
  h.request.mockImplementationOnce(() => pending.promise)
  const reading = h.client.read()
  h.scope.key = 'new-socket'
  h.client.getSnapshot()
  pending.resolve(catalogFixture())
  await reading
  expect(h.client.getSnapshot().catalog).toBeNull()
})

it('guards duplicate writes, stale revisions, independent permissions, locks, and revoke-before-remove', async () => {
  const h = await credentialsHarness()
  expect(await h.client.mutate('remove', entryFixture().id, 3)).toBe(false)
  expect(await h.client.mutate('edit_metadata', entryFixture().id, 2, credentialMetadata(entryFixture()))).toBe(false)
  h.data.entries[0].permissions = ['discover']
  await h.client.read()
  expect(await h.client.mutate('replace_value', entryFixture().id, 3, null, 'synthetic')).toBe(false)
  h.data.entries[0].permissions = entryFixture().permissions
  h.data.backend.state = 'locked'
  await h.client.read()
  expect(await h.client.mutate('replace_value', entryFixture().id, 3, null, 'synthetic')).toBe(false)
  const pending = deferred<unknown>()
  h.request.mockImplementationOnce(() => pending.promise)
  const revoking = h.client.mutate('revoke', entryFixture().id, 3)
  expect(await h.client.mutate('revoke', entryFixture().id, 3)).toBe(false)
  pending.resolve({ entryId: entryFixture().id, action: 'revoke', operationId: h.client.getSnapshot().intent!.operationId,
    revision: 4, version: 1, inFlight: 2, queuedCancelled: 1, providerRevoked: false, storedValueDeleted: false })
  expect(await revoking).toBe(true)
  expect(h.client.getSnapshot().receipt?.inFlight).toBe(2)
  expect(h.request.mock.calls.filter(call => call[0] === 'organization.credentials.mutate')).toHaveLength(1)
})

it('rejects wildcard, use and mixed create grants while fencing exact actor, scope and grant revisions', async () => {
  const h = await credentialsHarness()
  expect(await h.client.configureGrant('identity-1', 'create:github:*:token:secret', 0, ['create'])).toBe(false)
  expect(await h.client.configureGrant('identity-1', 'create:github:eidolonReleases:token:secret', 0, ['create', 'discover'])).toBe(false)
  expect(await h.client.configureGrant('identity-1', entryFixture().id, 2, ['use'])).toBe(false)
  expect(await h.client.configureGrant('unknown-agent', entryFixture().id, 0, ['discover'])).toBe(false)
  expect(await h.client.configureGrant('identity-1', entryFixture().id, 1, ['discover'])).toBe(false)
  expect(h.request.mock.calls).toHaveLength(1)
  expect(await h.client.configureGrant('identity-1', entryFixture().id, 2, [])).toBe(true)
  expect(h.client.getSnapshot().catalog?.grants[0].permissions).toEqual([])
  expect(await h.client.configureGrant('identity-1', 'create:github:eidolonReleases:token:secret', 0, ['create'])).toBe(true)
  expect(h.client.getSnapshot().receipt).toMatchObject({ action: 'configure_grant', actorId: 'identity-1', scope: 'create:github:eidolonReleases:token:secret', revision: 1 })
})

it('preserves a newer uncertain intent when old mutation or receipt replies arrive after returning to the same owner', async () => {
  for (const delayedReply of ['mutation', 'receipt']) {
    const h = await credentialsHarness()
    const handle = h.request.getMockImplementation()!
    const pending = deferred<unknown>()
    let oldReceipt: unknown

    h.request.mockImplementationOnce(async (method, params) => {
      oldReceipt = await handle(method, params)

      if (delayedReply === 'mutation') {
        return pending.promise
      }

      throw new Error('Synthetic lost mutation acknowledgement')
    })

    const writing = h.client.mutate('edit_metadata', entryFixture().id, 3, credentialMetadata(entryFixture()))
    let lateReply = writing

    if (delayedReply === 'receipt') {
      expect(await writing).toBe(false)
      h.request.mockImplementationOnce(() => pending.promise)
      lateReply = h.client.reconcile()
    } else {
      // Let the synthetic server commit without delivering its reply.
      await Promise.resolve()
    }

    h.scope.eligible = false
    h.client.getSnapshot()
    h.scope.eligible = true
    await h.client.read()
    expect(await h.client.reconcile()).toBe(true)
    h.request.mockRejectedValueOnce(new Error('Synthetic second uncertain operation'))
    expect(await h.client.mutate('edit_metadata', entryFixture().id, 4,
      { ...credentialMetadata(entryFixture()), description: 'Newer owner intent' })).toBe(false)
    const newer = h.client.getSnapshot().intent
    const retained = [...h.values.entries()]
    expect(newer?.operationId).toBe('operation-2')
    pending.resolve(delayedReply === 'mutation' ? oldReceipt : { receipt: oldReceipt })
    expect(await lateReply).toBe(true)
    expect([...h.values.entries()]).toEqual(retained)
    expect(h.client.getSnapshot()).toMatchObject({ intent: newer, error: 'uncertain' })
    const remounted = h.makeClient()
    await remounted.read()
    expect(remounted.getSnapshot().intent).toEqual(newer)
    expect(await remounted.mutate('edit_metadata', entryFixture().id, 4, credentialMetadata(entryFixture()))).toBe(false)
  }
})

it('compare-and-clears the full stored operation identity before discarding stale secure input', async () => {
  const h = await credentialsHarness()
  const pending = deferred<{ inputHandle: string; expiresIn: number }>()
  h.request.mockImplementationOnce(() => pending.promise)
  const writing = h.client.mutate('replace_value', entryFixture().id, 3, null, 'SYNTHETIC_old_value')
  const [key, raw] = [...h.values.entries()][0]
  const next = { ...JSON.parse(raw), expectedRevision: 4 }
  // Another mounted owner client may retain a different transition, even under a reused operation ID.
  h.values.set(key, JSON.stringify(next))
  h.scope.eligible = false
  h.client.getSnapshot()
  h.scope.eligible = true
  await h.client.read()
  pending.resolve({ inputHandle: 'SYNTHETIC_old_handle', expiresIn: 90 })
  expect(await writing).toBe(false)
  expect(JSON.parse(h.values.get(key)!)).toEqual(next)
  expect(h.client.getSnapshot().intent).toEqual(next)
  expect(h.request.mock.calls.filter(call => call[0] === 'organization.credentials.mutate')).toHaveLength(0)
})

it('keeps malformed transition revisions uncertain for mutation, grant, and receipt reconciliation', async () => {
  for (const action of ['create', 'edit_metadata', 'configure_grant'] as const) {
    for (const offset of [-1, 1]) {
      const h = await credentialsHarness()
      const handle = h.request.getMockImplementation()!
      h.request.mockImplementationOnce(async (method, params) => {
        const receipt = await handle(method, params) as { revision: number }
        receipt.revision += offset

        return receipt
      })

      const result = action === 'configure_grant'
        ? await h.client.configureGrant('identity-1', entryFixture().id, 2, ['discover'])
        : await h.client.mutate(action, action === 'create' ? null : entryFixture().id,
          action === 'create' ? null : 3, credentialMetadata(entryFixture()))

      expect(result).toBe(false)
      const intent = h.client.getSnapshot().intent
      expect(intent).not.toBeNull()
      expect(h.client.getSnapshot().error).toBe('uncertain')
      expect(h.values.size).toBe(1)
      expect(await h.client.reconcile()).toBe(false)
      expect(h.client.getSnapshot().intent).toEqual(intent)
      expect(h.values.size).toBe(1)
    }
  }
})

it('rejects inconsistent value versions when the original entry version is available', async () => {
  for (const action of ['create', 'edit_metadata', 'replace_value'] as const) {
    const h = await credentialsHarness()
    const handle = h.request.getMockImplementation()!

    if (action === 'replace_value') {
      h.request.mockImplementationOnce(handle)
    }

    h.request.mockImplementationOnce(async (method, params) => {
      const receipt = await handle(method, params) as { version: number }

      return { ...receipt, version: receipt.version + 1 }
    })

    expect(await h.client.mutate(action, action === 'create' ? null : entryFixture().id, action === 'create' ? null : 3,
      action === 'replace_value' ? null : credentialMetadata(entryFixture()), 'SYNTHETIC_value')).toBe(false)
    expect(h.client.getSnapshot().error).toBe('uncertain')
    expect(h.client.getSnapshot().intent).not.toBeNull()
    expect(h.values.size).toBe(1)
  }
})

it('requires a matching fenced cancellation result before releasing uncertainty and does not undo a committed operation', async () => {
  for (const committed of [false, true]) {
    const h = await credentialsHarness()
    const handle = h.request.getMockImplementation()!
    h.request.mockImplementationOnce(async (method, params) => {
      if (committed) { await handle(method, params) }
      throw new Error('Synthetic lost acknowledgement')
    })
    expect(await h.client.mutate('edit_metadata', entryFixture().id, 3, credentialMetadata(entryFixture()))).toBe(false)
    const intent = h.client.getSnapshot().intent!

    if (!committed) {
      expect(await h.client.reconcile()).toBe(false)
    }

    expect(await h.client.cancelPending('different-operation')).toBe(false)
    expect(await h.client.cancelPending(intent.operationId)).toBe(true)
    expect(h.client.getSnapshot().intent).toBeNull()
    expect(h.values.size).toBe(0)
    expect(h.client.getSnapshot().receipt?.operationId ?? null).toBe(committed ? intent.operationId : null)
    expect(h.client.getSnapshot().cancelledOperationId).toBe(committed ? null : intent.operationId)
    const calls = h.request.mock.calls.filter(call => call[0] === 'organization.credentials.cancelOperation')
    expect(calls).toHaveLength(1)
    expect(calls[0][1]).toEqual({ expectedOrganizationId: h.scope.organizationId, expectedOwnerId: h.scope.ownerId,
      operationId: intent.operationId, confirmed: true })
    expect(h.request.mock.calls.filter(call => call[0] === 'organization.credentials.mutate')).toHaveLength(1)
  }
})

it('retains uncertainty after malformed or unacknowledged cancellation without replaying input', async () => {
  for (const result of [
    { state: 'cancelled', receipt: null, operationId: 'different-operation' },
    { state: 'cancelled', receipt: {} },
    { state: 'committed', receipt: null },
    { state: 'unknown', receipt: null },
    { state: ['cancelled'], receipt: null },
    null
  ]) {
    const h = await credentialsHarness()
    h.request.mockRejectedValueOnce(new Error('Synthetic uncertain mutation'))
    await h.client.mutate('edit_metadata', entryFixture().id, 3, credentialMetadata(entryFixture()))
    const intent = h.client.getSnapshot().intent!
    h.request.mockImplementationOnce(async () => {
      if (result === null) { throw new Error('Synthetic lost cancellation acknowledgement') }

      return { operationId: intent.operationId, ...result }
    })
    expect(await h.client.cancelPending(intent.operationId)).toBe(false)
    expect(h.client.getSnapshot()).toMatchObject({ intent, error: 'uncertain' })
    expect(h.values.size).toBe(1)
    expect(await h.client.mutate('edit_metadata', entryFixture().id, 3, credentialMetadata(entryFixture()))).toBe(false)
  }
})

it('preserves newer uncertainty after a late cancellation reply and stale pre-mutation secure input', async () => {
  for (const delayed of ['cancel', 'secureInput']) {
    const h = await credentialsHarness()
    const pending = deferred<unknown>()
    h.request.mockImplementationOnce(delayed === 'secureInput' ? () => pending.promise : async () => { throw new Error('Synthetic uncertain operation') })

    const writing = h.client.mutate(delayed === 'secureInput' ? 'replace_value' : 'edit_metadata', entryFixture().id, 3,
      delayed === 'secureInput' ? null : credentialMetadata(entryFixture()), 'SYNTHETIC_old_value')

    const oldIntent = h.client.getSnapshot().intent!
    let late = writing

    if (delayed === 'cancel') {
      expect(await writing).toBe(false)
      h.request.mockImplementationOnce(() => pending.promise)
      late = h.client.cancelPending(oldIntent.operationId)
    }

    h.scope.eligible = false
    h.client.getSnapshot()
    h.scope.eligible = true
    await h.client.read()
    expect(await h.client.cancelPending(oldIntent.operationId)).toBe(true)
    h.request.mockRejectedValueOnce(new Error('Synthetic new uncertain operation'))
    await h.client.mutate('edit_metadata', entryFixture().id, 3, credentialMetadata(entryFixture()))
    const newer = h.client.getSnapshot().intent!
    const retained = [...h.values.entries()]
    pending.resolve(delayed === 'cancel'
      ? { operationId: oldIntent.operationId, state: 'cancelled', receipt: null }
      : { inputHandle: 'SYNTHETIC_old_handle', expiresIn: 90 })
    expect(await late).toBe(delayed === 'cancel')
    expect([...h.values.entries()]).toEqual(retained)
    expect(h.client.getSnapshot()).toMatchObject({ intent: newer, error: 'uncertain' })
    expect(h.request.mock.calls.filter(call => call[0] === 'organization.credentials.mutate' && call[1].action === 'replace_value')).toHaveLength(0)
  }
})
