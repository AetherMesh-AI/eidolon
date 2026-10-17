import { type DirectoryStorage, readDirectoryRecords, writeDirectoryRecords } from './organization-directory-storage'
import {
  createOrganizationMetadataClient,
  type MetadataDescriptor,
  type MetadataScope
} from './organization-metadata-client'
import { organizationEndpointIdentity } from './organization-owner'

export interface DirectoryConnection {
  id: string
  label: string
  endpoint: string | null
}
export interface DirectorySource {
  ready?: boolean
  connections: readonly DirectoryConnection[]
  current: { connectionId: string; endpoint: string | null; scope: MetadataScope } | null
}
export interface OrganizationDirectoryEntry {
  key: string
  connectionId: string
  connectionLabel: string
  profile: string
  endpointIdentity: string | null
  organizationId: string | null
  name: string | null
  revision: number | null
  state: MetadataDescriptor['state']
  verifiedAt: number
  freshness: 'fresh' | 'stale'
}
export interface OrganizationDirectoryView {
  entries: readonly OrganizationDirectoryEntry[]
  refreshing: boolean
  error: 'unavailable' | 'unsupported' | 'invalid' | 'storage' | null
  storageError?: boolean
}

const routeKey = (connectionId: string, profile: string) => JSON.stringify([connectionId, profile])
const endpoint = (value: string | null) => (value === null ? null : (organizationEndpointIdentity(value) ?? undefined))

/** Display-only cache of verified descriptors. It never enumerates profiles or grants access. */
export function createOrganizationDirectory(
  readSource: () => DirectorySource,
  subscribeSource: (changed: () => void) => () => void,
  options: { limit?: number; now?: () => number; storage?: DirectoryStorage } = {}
) {
  const limit = Math.max(1, Math.min(100, Math.floor(options.limit ?? 100) || 100))
  const now = options.now ?? Date.now
  const records = new Map<string, OrganizationDirectoryEntry>()
  const listeners = new Set<() => void>()
  let view: OrganizationDirectoryView = { entries: [], refreshing: false, error: null }
  let fingerprint = ''
  let epoch = 0
  let sequence = 0
  let disposed = false

  if (options.storage) {
    try {
      for (const record of readDirectoryRecords(options.storage, limit)) { records.set(record.key, record) }
    } catch { view = { ...view, storageError: true } }
  }

  const remember = () => {
    if (!options.storage) { return }

    try {
      writeDirectoryRecords(options.storage, [...records.values()])
      view = { ...view, storageError: false }
    } catch { view = { ...view, storageError: true } }
  }

  const publish = (notify = true) => {
    view = { ...view, entries: [...records.values()] }

    if (notify) {
      for (const listener of listeners) {
        listener()
      }
    }
  }

  const synchronize = (notify = true) => {
    const source = readSource()
    const current = source.current

    const next = JSON.stringify([
      current?.connectionId,
      endpoint(current?.endpoint ?? null),
      current?.scope.key,
      current?.scope.ownerKey,
      current?.scope.profile,
      current?.scope.eligible,
      source.ready,
      source.connections.map(item => [item.id, endpoint(item.endpoint)]).sort()
    ])

    let changed = next !== fingerprint

    if (changed) {
      fingerprint = next
      epoch++
      sequence++
      view = { ...view, refreshing: false, error: null }

      for (const [key, record] of records) {
        records.set(key, { ...record, freshness: 'stale' })
      }
    }

    let pruned = false

    for (const [key, record] of source.ready === false ? [] : records) {
      const connection = source.connections.find(item => item.id === record.connectionId)

      if (!connection || endpoint(connection.endpoint) !== record.endpointIdentity) {
        records.delete(key)
        changed = true
        pruned = true
      } else if (record.connectionLabel !== connection.label) {
        records.set(key, { ...record, connectionLabel: connection.label })
        changed = true
      }
    }

    if (pruned) { remember() }

    if (changed) {
      publish(notify)
    }

    return source
  }

  // Source subscriptions revoke freshness even through A → B → A and remove/re-add.
  const unsubscribe = subscribeSource(() => {
    if (!disposed) {
      synchronize()
    }
  })

  return {
    getSnapshot: () => {
      if (!disposed) {
        synchronize(false)
      }

      return view
    },
    subscribe: (listener: () => void) => {
      listeners.add(listener)

      return () => {
        listeners.delete(listener)
      }
    },
    async refresh(): Promise<'verified' | 'unavailable' | 'superseded'> {
      if (disposed) {
        return 'unavailable'
      }

      const source = synchronize()
      const current = source.current
      const connection = source.connections.find(item => item.id === current?.connectionId)
      const identity = endpoint(current?.endpoint ?? null)

      if (
        source.ready === false ||
        !current?.scope.eligible ||
        !connection ||
        identity === undefined ||
        endpoint(connection.endpoint) !== identity
      ) {
        view = { ...view, error: 'unavailable' }
        publish()

        return 'unavailable'
      }

      const capturedEpoch = epoch
      const capturedSequence = ++sequence
      const key = routeKey(connection.id, current.scope.profile)
      const scope = { ...current.scope }
      const client = createOrganizationMetadataClient(() => scope)
      const old = records.get(key)

      if (old) {
        records.set(key, { ...old, freshness: 'stale' })
      }

      view = { ...view, refreshing: true, error: null }
      publish()
      await client.read()
      const latest = synchronize()

      if (disposed || epoch !== capturedEpoch || sequence !== capturedSequence) {
        return 'superseded'
      }

      const liveConnection = latest.connections.find(item => item.id === connection.id)

      if (!liveConnection || endpoint(liveConnection.endpoint) !== identity) {
        view = { ...view, refreshing: false, error: 'unavailable' }
        publish()

        return 'superseded'
      }

      const result = client.getSnapshot()
      const descriptor = result.descriptor
      view = { ...view, refreshing: false, error: null }

      if (!descriptor) {
        view.error = result.error === 'unsupported' || result.error === 'invalid' ? result.error : 'storage'
        publish()

        return 'unavailable'
      }

      // Deliberately project only display fields: no owner names, drafts, tokens or request closures.
      records.delete(key)
      records.set(key, {
        key,
        connectionId: connection.id,
        connectionLabel: liveConnection.label,
        profile: scope.profile,
        endpointIdentity: identity,
        organizationId: descriptor.metadata?.organizationId ?? null,
        name: descriptor.metadata?.displayName ?? null,
        revision: descriptor.metadata?.revision ?? null,
        state: descriptor.state,
        verifiedAt: now(),
        freshness: 'fresh'
      })

      while (records.size > limit) {
        records.delete(records.keys().next().value!)
      }

      remember()
      publish()

      return 'verified'
    },
    dispose() {
      disposed = true
      sequence++
      unsubscribe()
      records.clear()
      view = { entries: [], refreshing: false, error: 'unavailable' }
      publish()
      listeners.clear()
    }
  }
}

export type OrganizationDirectory = ReturnType<typeof createOrganizationDirectory>

/** Search cannot read transport state or issue requests. Duplicate names stay distinct. */
export function searchOrganizationDirectory(entries: readonly OrganizationDirectoryEntry[], query: string) {
  const words = query.normalize('NFKC').toLocaleLowerCase().trim().split(/\s+/).filter(Boolean)

  return entries.filter(entry =>
    words.every(word =>
      `${entry.name ?? ''} ${entry.connectionLabel} ${entry.profile}`
        .normalize('NFKC')
        .toLocaleLowerCase()
        .includes(word)
    )
  )
}
