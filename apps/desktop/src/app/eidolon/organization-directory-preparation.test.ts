import { expect, it, vi } from 'vitest'

import { createOrganizationMetadataClient, type MetadataDescriptor, type MetadataScope } from './organization-metadata-client'
import { organizationOwnerKey } from './organization-owner'

const descriptor = (name: string): MetadataDescriptor => ({
  capabilityVersion: 1, state: 'ready', scope: { kind: 'served-profile', profile: 'default' },
  metadata: { organizationId: '11111111-1111-4111-8111-111111111111', displayName: name,
    owner: { ownerId: '22222222-2222-4222-8222-222222222222', displayName: 'Owner' },
    revision: 1, createdAt: '2026-10-11T00:00:00Z', updatedAt: '2026-10-11T00:00:00Z' }
})

it('keeps copied-ledger display authority and uncertain intent scoped to their routed destination', async () => {
  const a = vi.fn().mockResolvedValue(descriptor('A'))
  const b = vi.fn().mockResolvedValue(descriptor('B'))
  const ownerA = organizationOwnerKey('a', 'default', { mode: 'remote', baseUrl: 'https://user:secret@a.example/rpc?token=private#fragment' })
  const ownerB = organizationOwnerKey('b', 'default', { mode: 'remote', baseUrl: 'https://b.example/rpc' })
  expect(ownerA).not.toMatch(/secret|private|user:|fragment/)
  let scope: MetadataScope = { key: 'a-1', ownerKey: ownerA, profile: 'default', eligible: true, request: a }
  const client = createOrganizationMetadataClient(() => scope, () => 'original-intent')
  await client.read()
  a.mockRejectedValueOnce(new Error('reply lost'))
  await client.save('A pending', 'Owner')
  const intent = client.getSnapshot().intent
  scope = { ...scope, key: 'b-1', ownerKey: ownerB, request: b }
  expect(client.getSnapshot().descriptor).toBeNull()
  await client.read()
  expect(client.getSnapshot().descriptor?.metadata?.displayName).toBe('B')
  expect(client.getSnapshot().intent).toBeNull()
  expect(b.mock.calls).toEqual([['organization.descriptor', {}]])
  scope = { ...scope, key: 'a-2', ownerKey: ownerA, request: a }
  expect(client.getSnapshot().descriptor).toBeNull()
  await client.read()
  expect(client.getSnapshot().intent).toEqual(intent)
  expect(client.getSnapshot().descriptor?.metadata?.displayName).toBe('A')
  expect(a.mock.calls.filter(call => call[0] === 'organization.saveMetadata')).toHaveLength(1)
})

it('requires successful served-profile verification rather than turning unavailable discovery into setup', async () => {
  const request = vi.fn().mockResolvedValue(descriptor('Previously verified'))
  let scope: MetadataScope = { key: 'socket-1', ownerKey: 'route', profile: 'default', eligible: true, request }
  const client = createOrganizationMetadataClient(() => scope)
  await client.read()
  scope = { ...scope, key: 'socket-2', eligible: false }
  await client.read()
  expect(client.getSnapshot().descriptor).toBeNull()
  expect(request).toHaveBeenCalledTimes(1)
  scope = { ...scope, eligible: true }
  request.mockResolvedValueOnce({ ...descriptor('Foreign'), scope: { kind: 'served-profile', profile: 'foreign' } })
  await client.read()
  expect(client.getSnapshot().descriptor).toBeNull()
  request.mockRejectedValueOnce(new Error('unreachable'))
  await client.read()
  expect(client.getSnapshot().descriptor).toBeNull()
  expect(client.getSnapshot().error).toBe('storage')
  expect(request.mock.calls.every(call => call[0] === 'organization.descriptor' && Object.keys(call[1]).length === 0)).toBe(true)
})
