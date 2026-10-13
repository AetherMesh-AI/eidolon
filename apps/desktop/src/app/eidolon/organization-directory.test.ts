import { expect, it, vi } from 'vitest'

import { deferred } from '../../test/deferred'

import {
  createOrganizationDirectory,
  type DirectorySource,
  searchOrganizationDirectory
} from './organization-directory'

function descriptor(name = 'Same name', revision = 4, profile = 'default') {
  return {
    capabilityVersion: 1,
    state: 'ready',
    scope: { kind: 'served-profile', profile },
    metadata: {
      organizationId: '11111111-1111-4111-8111-111111111111',
      displayName: name,
      owner: { ownerId: '22222222-2222-4222-8222-222222222222', displayName: 'Private owner' },
      revision,
      createdAt: '2026-10-12T00:00:00Z',
      updatedAt: '2026-10-12T00:00:00Z'
    }
  }
}

function harness(limit = 100) {
  const request = vi.fn().mockResolvedValue(descriptor())

  let changed = () => {}
  const unsubscribe = vi.fn()
  const connection = (id: string) => ({ id, label: `Host ${id}`, endpoint: `https://${id}.example/rpc` })
  const source: DirectorySource = { connections: ['a', 'b', 'c'].map(connection), current: null }

  const select = (id: string, generation = 1, eligible = true) => {
    source.current = {
      connectionId: id,
      endpoint: connection(id).endpoint,
      scope: { key: `${id}:${generation}`, ownerKey: `owner:${id}`, profile: 'default', eligible, request }
    }
    changed()
  }

  const directory = createOrganizationDirectory(
    () => source,
    listener => {
      changed = listener

      return unsubscribe
    },
    { limit, now: () => 1234 }
  )

  select('a')

  return { directory, source, request, select, changed: () => changed(), unsubscribe }
}

it('subscribing, snapshots and searching perform no discovery or enumeration', () => {
  const h = harness()
  h.directory.subscribe(vi.fn())
  expect(searchOrganizationDirectory(h.directory.getSnapshot().entries, 'anything')).toEqual([])
  expect(h.request).not.toHaveBeenCalled()
})

it('projects only display fields and removes endpoint credentials from serialized snapshots', async () => {
  const h = harness()
  const endpoint = 'https://user:secret@a.example/rpc?token=private#fragment'
  h.source.connections = [{ id: 'a', label: 'Host A', endpoint }]
  h.source.current!.endpoint = endpoint
  h.changed()
  expect(await h.directory.refresh()).toBe('verified')
  const view = h.directory.getSnapshot()
  expect(view.entries[0]).toMatchObject({
    endpointIdentity: 'https://a.example/rpc',
    name: 'Same name',
    freshness: 'fresh'
  })
  expect(JSON.stringify(view)).not.toMatch(/secret|private|fragment|Private owner|ownerId|ownerKey|request|token/)
  expect(h.request.mock.calls).toEqual([['organization.descriptor', {}]])
})

it('retains duplicate organization IDs and names as separate routed destinations', async () => {
  const h = harness()
  await h.directory.refresh()
  h.select('b')
  await h.directory.refresh()
  expect(h.directory.getSnapshot().entries.map(e => [e.connectionId, e.freshness])).toEqual([
    ['a', 'stale'],
    ['b', 'fresh']
  ])
  expect(searchOrganizationDirectory(h.directory.getSnapshot().entries, 'same')).toHaveLength(2)
  expect(searchOrganizationDirectory(h.directory.getSnapshot().entries, 'Host b')).toMatchObject([
    { connectionId: 'b' }
  ])
  expect(new Set(h.directory.getSnapshot().entries.map(e => e.key)).size).toBe(2)
})

it.each(['generation', 'disconnect', 'profile', 'owner'] as const)(
  'revokes freshness immediately on %s changes',
  async change => {
    const h = harness()
    await h.directory.refresh()
    const listener = vi.fn()
    h.directory.subscribe(listener)
    const scope = h.source.current!.scope

    if (change === 'generation') {
      scope.key += ':new'
    }

    if (change === 'disconnect') {
      scope.eligible = false
    }

    if (change === 'profile') {
      scope.profile = 'other'
    }

    if (change === 'owner') {
      scope.ownerKey = 'new-owner'
    }

    h.changed()
    expect(h.directory.getSnapshot().entries[0].freshness).toBe('stale')
    expect(listener).toHaveBeenCalled()
    expect(h.request).toHaveBeenCalledTimes(1)
  }
)

it('does not revive freshness on A → B → A without a new descriptor', async () => {
  const h = harness()
  await h.directory.refresh()
  h.select('b')
  h.select('a')
  expect(h.directory.getSnapshot().entries[0].freshness).toBe('stale')
  await h.directory.refresh()
  expect(h.directory.getSnapshot().entries[0].freshness).toBe('fresh')
})

it.each(['remove', 'endpoint'] as const)(
  'evicts a %s route and does not resurrect its cached metadata',
  async change => {
    const h = harness()
    await h.directory.refresh()
    const previous = h.source.connections
    h.source.connections =
      change === 'remove' ? [] : [{ id: 'a', label: 'Changed', endpoint: 'https://other.example/rpc' }]
    h.changed()
    expect(h.directory.getSnapshot().entries).toEqual([])
    h.source.connections = previous
    h.changed()
    expect(h.directory.getSnapshot().entries).toEqual([])
  }
)

it.each(['switch', 'ABA', 'remove-readd', 'dispose'] as const)('discards a late descriptor after %s', async change => {
  const h = harness()
  const pending = deferred<unknown>()
  h.request.mockReturnValueOnce(pending.promise)
  const reading = h.directory.refresh()

  if (change === 'switch' || change === 'ABA') {
    h.select('b')
  }

  if (change === 'ABA') {
    h.select('a')
  }

  if (change === 'remove-readd') {
    const previous = h.source.connections
    h.source.connections = []
    h.changed()
    h.source.connections = previous
    h.changed()
  }

  if (change === 'dispose') {
    h.directory.dispose()
  }

  pending.resolve(descriptor('Late'))
  expect(await reading).toBe('superseded')
  expect(h.directory.getSnapshot().entries).toEqual([])
})

it('lets only the newest overlapping refresh publish', async () => {
  const h = harness()
  const older = deferred<unknown>()
  h.request.mockReturnValueOnce(older.promise)
  const pending = h.directory.refresh()
  h.request.mockResolvedValueOnce(descriptor('Newest'))
  await h.directory.refresh()
  older.resolve(descriptor('Old'))
  expect(await pending).toBe('superseded')
  expect(h.directory.getSnapshot().entries[0].name).toBe('Newest')
})

it('accepts a restored lower revision and replaces changed identity rather than merging it', async () => {
  const h = harness()
  await h.directory.refresh()
  const restored = descriptor('Restored', 1)
  restored.metadata.organizationId = '33333333-3333-4333-8333-333333333333'
  h.request.mockResolvedValueOnce(restored)
  await h.directory.refresh()
  expect(h.directory.getSnapshot().entries[0]).toMatchObject({
    name: 'Restored',
    revision: 1,
    organizationId: restored.metadata.organizationId
  })
})

it('evicts least recently verified entries, not most recently searched entries', async () => {
  const h = harness(2)
  await h.directory.refresh()
  h.select('b')
  await h.directory.refresh()
  searchOrganizationDirectory(h.directory.getSnapshot().entries, 'Host a')
  h.select('c')
  await h.directory.refresh()
  expect(h.directory.getSnapshot().entries.map(e => e.connectionId)).toEqual(['b', 'c'])
})

it.each(['ineligible', 'foreign', 'failure', 'endpoint'] as const)(
  'does not turn %s discovery into an unconfigured organization',
  async failure => {
    const h = harness()

    if (failure === 'ineligible') {
      h.select('a', 1, false)
    }

    if (failure === 'foreign') {
      h.request.mockResolvedValueOnce(descriptor('Foreign', 1, 'other'))
    }

    if (failure === 'failure') {
      h.request.mockRejectedValueOnce(new Error('auth or transport failure'))
    }

    if (failure === 'endpoint') {
      h.source.current!.endpoint = 'https://foreign.example/rpc'
    }

    expect(await h.directory.refresh()).toBe('unavailable')
    expect(h.directory.getSnapshot().entries).toEqual([])

    if (failure === 'ineligible' || failure === 'endpoint') {
      expect(h.request).not.toHaveBeenCalled()
    }
  }
)

it('retains stale display data after a failed explicit refresh without claiming freshness', async () => {
  const h = harness()
  await h.directory.refresh()
  h.request.mockRejectedValueOnce(new Error('unavailable'))
  await h.directory.refresh()
  expect(h.directory.getSnapshot()).toMatchObject({
    error: 'storage',
    entries: [{ name: 'Same name', state: 'ready', freshness: 'stale' }]
  })
})

it.each(['unconfigured', 'unsupported'] as const)(
  'retains successful scoped %s separately from transport failure',
  async state => {
    const h = harness()
    h.request.mockResolvedValueOnce({ ...descriptor(), state, metadata: null })
    await h.directory.refresh()
    expect(h.directory.getSnapshot()).toMatchObject({
      error: null,
      entries: [{ state, name: null, freshness: 'fresh' }]
    })
  }
)

it('dispose unsubscribes, removes cached records and forbids future requests', async () => {
  const h = harness()
  await h.directory.refresh()
  h.directory.dispose()
  expect(h.unsubscribe).toHaveBeenCalledOnce()
  expect(await h.directory.refresh()).toBe('unavailable')
  expect(h.request).toHaveBeenCalledTimes(1)
  expect(h.directory.getSnapshot().entries).toEqual([])
})
