import { expect, it, vi } from 'vitest'

import {
  createOrganizationMetadataClient,
  type MetadataDescriptor,
  type MetadataScope
} from './organization-metadata-client'

const value = (revision = 1, displayName = 'Organization', ownerName = 'Alex'): MetadataDescriptor => ({
  capabilityVersion: 1,
  state: 'ready',
  metadata: {
    organizationId: '11111111-1111-4111-8111-111111111111',
    displayName,
    owner: { ownerId: '22222222-2222-4222-8222-222222222222', displayName: ownerName },
    revision,
    createdAt: '2026-10-11T00:00:00Z',
    updatedAt: '2026-10-11T00:00:00Z'
  },
  scope: { kind: 'served-profile', profile: 'default' }
})

function setup() {
  const request = vi.fn().mockResolvedValue(value())
  const key = vi.fn(() => 'intent-one')
  let scope: MetadataScope = { key: 'socket-a', ownerKey: 'route-a', profile: 'default', eligible: true, request }

  return {
    request,
    key,
    client: createOrganizationMetadataClient(() => scope, key),
    switch: (next: Partial<MetadataScope>) => {
      scope = { ...scope, ...next }
    }
  }
}

it('does not read on subscribe or ineligible routes and only calls descriptor/save', async () => {
  const h = setup()
  h.client.subscribe(vi.fn())
  expect(h.request).not.toHaveBeenCalled()
  h.switch({ eligible: false })
  await h.client.read()
  await h.client.save('Name', 'Owner')
  expect(h.request).not.toHaveBeenCalled()
  h.switch({ eligible: true })
  await h.client.read()
  h.request.mockResolvedValueOnce(value(2, 'Name', 'Owner'))
  await h.client.save('Name', 'Owner')
  expect(h.request.mock.calls.map(call => call[0])).toEqual(['organization.descriptor', 'organization.saveMetadata'])
  expect(h.request.mock.calls[1][1]).toMatchObject({
    expectedOrganizationId: '11111111-1111-4111-8111-111111111111',
    expectedRevision: 1,
    idempotencyKey: 'intent-one'
  })
})
it('retains uncertain Save and reuses original intent without regressing newer descriptor', async () => {
  const h = setup()
  await h.client.read()
  h.request.mockRejectedValueOnce(new Error('timeout'))
  await h.client.save('Original', 'Alex')
  const intent = h.client.getSnapshot().intent
  expect(h.request).toHaveBeenCalledTimes(2)
  h.request.mockResolvedValueOnce(value(3))
  await h.client.read()
  h.request.mockResolvedValueOnce(value(2, 'Original'))
  await h.client.save('Other', 'Other')
  expect(h.request.mock.calls[3][1]).toEqual(intent)
  expect(h.key).toHaveBeenCalledTimes(1)
  expect(h.client.getSnapshot().intent).toBeNull()
  expect(h.client.getSnapshot().descriptor?.metadata?.revision).toBe(3)
})
it('ignores late source results and retains original intent when returning', async () => {
  const h = setup()
  await h.client.read()
  let resolve!: (value: MetadataDescriptor) => void
  h.request.mockReturnValueOnce(
    new Promise<MetadataDescriptor>(yes => {
      resolve = yes
    })
  )
  const pending = h.client.save('Original', 'Alex')
  const other = vi.fn().mockResolvedValue(value())
  h.switch({ key: 'socket-b', ownerKey: 'route-b', request: other })
  await h.client.read()
  resolve(value(2))
  await pending
  expect(h.client.getSnapshot().descriptor?.metadata?.revision).toBe(1)
  expect(other).toHaveBeenCalledTimes(1)
  h.switch({ key: 'socket-a', ownerKey: 'route-a', request: h.request })
  await h.client.read()
  expect(h.client.getSnapshot().intent?.displayName).toBe('Original')
  expect(h.client.getSnapshot().error).toBe('uncertain')
})
it('does not replay uncertain initialization into an unconfigured ledger', async () => {
  const h = setup()
  h.request.mockResolvedValue({ ...value(), state: 'unconfigured', metadata: null })
  await h.client.read()
  h.request.mockRejectedValueOnce(new Error('timeout'))
  await h.client.save('Initial', 'Alex')
  await h.client.read()
  await h.client.save('Initial', 'Alex')
  expect(h.request.mock.calls.filter(call => call[0] === 'organization.saveMetadata')).toHaveLength(1)
  expect(h.client.getSnapshot().intent).not.toBeNull()
})
it('surfaces rejected writes and unsupported capability without retry', async () => {
  const h = setup()
  await h.client.read()
  h.request.mockRejectedValueOnce({ code: 5072, data: { reason: 'revision_conflict' } })
  await h.client.save('Conflict', 'Alex')
  expect(h.client.getSnapshot().error).toBe('conflict')
  expect(h.client.getSnapshot().intent).toBeNull()
  await h.client.save('Conflict', 'Alex')
  expect(h.request).toHaveBeenCalledTimes(2)
  h.request.mockRejectedValueOnce({ code: -32601 })
  await h.client.read()
  expect(h.client.getSnapshot().error).toBe('unsupported')
})
it.each(['name', 'owner', 'revision', 'identity'])(
  'retains the original intent for a malformed %s Save receipt',
  async kind => {
    const h = setup()
    await h.client.read()
    const receipt = value(2, 'Requested', 'Alex')

    if (kind === 'name') {
      receipt.metadata!.displayName = 'Different'
    }

    if (kind === 'owner') {
      receipt.metadata!.owner.ownerId = '33333333-3333-4333-8333-333333333333'
    }

    if (kind === 'revision') {
      receipt.metadata!.revision = 3
    }

    if (kind === 'identity') {
      receipt.metadata!.organizationId = 'not-a-uuid'
    }

    h.request.mockResolvedValueOnce(receipt)
    await h.client.save('Requested', 'Alex')
    expect(h.client.getSnapshot().error).toBe('uncertain')
    expect(h.client.getSnapshot().intent?.displayName).toBe('Requested')
    expect(h.client.getSnapshot().descriptor?.metadata?.revision).toBe(1)
    expect(h.request).toHaveBeenCalledTimes(2)
  }
)
it('restores an uncertain Save after window reload without automatically replaying it', async () => {
  const records = new Map<string, string>()

  const storage = {
    getItem: (key: string) => records.get(key) ?? null,
    setItem: (key: string, value: string) => {
      records.set(key, value)
    },
    removeItem: (key: string) => {
      records.delete(key)
    }
  }

  const request = vi.fn().mockResolvedValue(value())
  const scope = () => ({ key: 'socket', ownerKey: 'original-route', profile: 'default', eligible: true, request })
  const first = createOrganizationMetadataClient(scope, () => 'original-key', storage)
  await first.read()
  request.mockRejectedValueOnce(new Error('timeout'))
  await first.save('Requested', 'Alex')
  const intent = first.getSnapshot().intent
  const nextKey = vi.fn(() => 'must-not-be-used')
  const reloaded = createOrganizationMetadataClient(scope, nextKey, storage)
  await reloaded.read()
  expect(reloaded.getSnapshot().intent).toEqual(intent)
  expect(request.mock.calls.filter(call => call[0] === 'organization.saveMetadata')).toHaveLength(1)
  request.mockResolvedValueOnce(value(2, 'Requested'))
  await reloaded.save('Different', 'Someone else')
  expect(request.mock.calls.at(-1)?.[1]).toEqual(intent)
  expect(nextKey).not.toHaveBeenCalled()
  expect(records.size).toBe(0)
})

it('does not send a Save if its original request cannot be retained', async () => {
  const request = vi.fn().mockResolvedValue(value())

  const client = createOrganizationMetadataClient(
    () => ({ key: 'socket', ownerKey: 'route', profile: 'default', eligible: true, request }),
    () => 'key',
    {
      getItem: () => null,
      setItem: () => {
        throw new Error('Storage denied')
      },
      removeItem: vi.fn()
    }
  )

  await client.read()
  await client.save('Requested', 'Alex')
  expect(request).toHaveBeenCalledTimes(1)
  expect(client.getSnapshot().error).toBe('storage')
  expect(client.getSnapshot().intent).toBeNull()
})
it('keeps a corrupt retained request blocked across repeated Refresh instead of replacing it', async () => {
  const request = vi.fn().mockResolvedValue(value())
  const setItem = vi.fn()
  const removeItem = vi.fn()

  const client = createOrganizationMetadataClient(
    () => ({ key: 'socket', ownerKey: 'route', profile: 'default', eligible: true, request }),
    () => 'new-key',
    {
      getItem: () => '{damaged',
      setItem,
      removeItem
    }
  )

  await client.read()
  await client.read()
  await client.save('New name', 'Alex')
  expect(client.getSnapshot().error).toBe('storage')
  expect(request).not.toHaveBeenCalled()
  expect(setItem).not.toHaveBeenCalled()
  expect(removeItem).not.toHaveBeenCalled()
})

it('accepts the authoritative lower revision after restoring the same organization backup', async () => {
  const h = setup()
  h.request.mockResolvedValueOnce(value(4, 'Latest'))
  await h.client.read()
  h.request.mockResolvedValueOnce(value(1, 'Restored'))
  await h.client.read()
  expect(h.client.getSnapshot().descriptor?.metadata).toMatchObject({ revision: 1, displayName: 'Restored' })
  h.request.mockResolvedValueOnce(value(2, 'New after restore'))
  await h.client.save('New after restore', 'Alex')
  expect(h.request.mock.calls.at(-1)?.[1]).toMatchObject({ expectedRevision: 1 })
  expect(h.client.getSnapshot().error).toBeNull()
})

it.each([4001, -32601, -32602, 5072])(
  'retains the persisted original after retry rejection %s, including reload',
  async code => {
    const records = new Map<string, string>()

    const storage = {
      getItem: (key: string) => records.get(key) ?? null,
      setItem: (key: string, value: string) => {
        records.set(key, value)
      },
      removeItem: (key: string) => {
        records.delete(key)
      }
    }

    const request = vi.fn().mockResolvedValue(value())
    const scope = () => ({ key: 'socket', ownerKey: 'route', profile: 'default', eligible: true, request })
    const newKey = vi.fn(() => 'original-key')
    const client = createOrganizationMetadataClient(scope, newKey, storage)
    await client.read()
    request.mockRejectedValueOnce(new Error('Reply lost'))
    await client.save('Committed original', 'Alex')
    const intent = client.getSnapshot().intent
    request.mockRejectedValueOnce({ code, data: { reason: 'unsupported_schema' } })
    await client.save('Different', 'Other')
    expect(client.getSnapshot().intent).toEqual(intent)
    expect(client.getSnapshot().descriptor).toBeNull()
    expect(records.size).toBe(1)
    const reloaded = createOrganizationMetadataClient(scope, newKey, storage)
    request.mockResolvedValueOnce(value(2, 'Committed original'))
    await reloaded.read()
    expect(reloaded.getSnapshot().intent).toEqual(intent)
    request.mockResolvedValueOnce(value(2, 'Committed original'))
    await reloaded.save('Different', 'Other')
    expect(request.mock.calls.filter(call => call[0] === 'organization.saveMetadata').map(call => call[1])).toEqual([
      intent,
      intent,
      intent
    ])
    expect(newKey).toHaveBeenCalledTimes(1)
    expect(records.size).toBe(0)
  }
)

it('does not republish a cached identity or permit uncertain retry before the new generation verifies it', async () => {
  const h = setup()
  await h.client.read()
  h.request.mockRejectedValueOnce(new Error('Reply lost'))
  await h.client.save('Original', 'Alex')
  let fail!: (error: Error) => void
  h.switch({ key: 'new-socket' })
  h.request.mockReturnValueOnce(
    new Promise((_resolve, reject) => {
      fail = reject
    })
  )
  const pending = h.client.read()
  expect(h.client.getSnapshot().descriptor).toBeNull()
  expect(h.client.getSnapshot().intent?.displayName).toBe('Original')
  fail(new Error('Descriptor unavailable'))
  await pending
  expect(h.client.getSnapshot().descriptor).toBeNull()
  await h.client.save('Original', 'Alex')
  expect(h.request.mock.calls.filter(call => call[0] === 'organization.saveMetadata')).toHaveLength(1)
})
