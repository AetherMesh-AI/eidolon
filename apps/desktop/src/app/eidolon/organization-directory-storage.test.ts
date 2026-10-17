import { expect, it } from 'vitest'

import { createOrganizationDirectory, type DirectorySource } from './organization-directory'

it('restores only descriptor-verified routes as stale display records and prunes removed sources', async () => {
  const values = new Map<string, string>()

  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value)
    }
  }

  const endpoint = 'https://user:private@lab.example/rpc?token=secret#fragment'

  const source: DirectorySource = {
    connections: [{ id: 'lab', label: 'Lab', endpoint }],
    current: null
  }

  let changed = () => {}

  const open = () =>
    createOrganizationDirectory(
      () => source,
      callback => {
        changed = callback

        return () => {}
      },
      { storage }
    )

  const directory = open()

  for (const [index, profile] of ['org-a', 'org-b'].entries()) {
    source.current = {
      connectionId: 'lab',
      endpoint,
      scope: {
        key: profile,
        ownerKey: profile,
        profile,
        eligible: true,
        request: async <T>() =>
          ({
            capabilityVersion: 1,
            state: 'ready',
            scope: { kind: 'served-profile', profile },
            metadata: {
              organizationId: `${index + 1}1111111-1111-4111-8111-111111111111`,
              displayName: `Organization ${profile}`,
              owner: { ownerId: '33333333-3333-4333-8333-333333333333', displayName: 'Private owner' },
              revision: 1,
              createdAt: '2026-10-17T00:00:00Z',
              updatedAt: '2026-10-17T00:00:00Z'
            }
          }) as T
      }
    }
    changed()
    expect(await directory.refresh()).toBe('verified')
  }

  directory.dispose()
  const restored = open()
  expect(restored.getSnapshot().entries.map(entry => [entry.profile, entry.freshness])).toEqual([
    ['org-a', 'stale'],
    ['org-b', 'stale']
  ])
  expect([...values.values()].join('')).not.toMatch(/private|secret|fragment|Private owner|ownerId|ownerKey|request/i)
  source.ready = false
  source.connections = []
  changed()
  expect(restored.getSnapshot().entries).toHaveLength(2)
  expect(restored.getSnapshot().entries.every(entry => entry.freshness === 'stale')).toBe(true)
  source.ready = true
  source.connections = []
  changed()
  expect(restored.getSnapshot().entries).toEqual([])
  restored.dispose()
  source.connections = [{ id: 'lab', label: 'Lab', endpoint }]
  const reopened = open()
  expect(reopened.getSnapshot().entries).toEqual([])
  reopened.dispose()
})

it('rejects malformed remembered records and keeps a storage failure non-authoritative', () => {
  const source: DirectorySource = { connections: [{ id: 'a', label: 'Lab', endpoint: null }], current: null }

  const open = (getItem: () => string | null) =>
    createOrganizationDirectory(
      () => source,
      () => () => {},
      { storage: { getItem, setItem: () => {} } }
    )

  const invalid = open(() =>
    JSON.stringify([{ connectionId: 'a', profile: '../outside', name: 'Unverified', freshness: 'fresh' }])
  )

  expect(invalid.getSnapshot().entries).toEqual([])
  invalid.dispose()

  const blocked = open(() => {
    throw new Error('Storage unavailable')
  })

  expect(blocked.getSnapshot().entries).toEqual([])
  expect(blocked.getSnapshot().storageError).toBe(true)
  blocked.dispose()
  const oversized = open(() => `[${' '.repeat(600_000)}]`)
  expect(oversized.getSnapshot().entries).toEqual([])
  expect(oversized.getSnapshot().storageError).toBe(true)
  oversized.dispose()
})
