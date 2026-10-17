import type { OrganizationDirectoryEntry } from './organization-directory'
import { organizationEndpointIdentity } from './organization-owner'

export type DirectoryStorage = Pick<Storage, 'getItem' | 'setItem'>
const CACHE_KEY = 'eidolon.organization-directory.display.v1'
const MAX_CACHE_CHARACTERS = 512_000
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

const text = (value: unknown, max: number): value is string =>
  typeof value === 'string' && value.length > 0 && value.length <= max && !/\p{C}/u.test(value)

/** Remembered names are display hints, never proof of the current owner or access. */
export function readDirectoryRecords(storage: DirectoryStorage, limit: number): OrganizationDirectoryEntry[] {
  const raw = storage.getItem(CACHE_KEY)

  if (!raw) {
    return []
  }

  if (raw.length > MAX_CACHE_CHARACTERS) {
    throw new Error('Organization display cache is too large')
  }

  const records: unknown = JSON.parse(raw)

  if (!Array.isArray(records)) {
    return []
  }

  return records.slice(-limit).flatMap((value: unknown): OrganizationDirectoryEntry[] => {
    if (!value || typeof value !== 'object') {
      return []
    }

    const item = value as OrganizationDirectoryEntry

    if (
      !text(item.connectionId, 200) ||
      !text(item.connectionLabel, 300) ||
      typeof item.profile !== 'string' ||
      !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(item.profile) ||
      (item.endpointIdentity !== null &&
        (!text(item.endpointIdentity, 2048) ||
          organizationEndpointIdentity(item.endpointIdentity) !== item.endpointIdentity)) ||
      !Number.isFinite(item.verifiedAt) ||
      item.verifiedAt <= 0 ||
      !['ready', 'unconfigured', 'unsupported'].includes(item.state)
    ) {
      return []
    }

    if (
      item.state === 'ready'
        ? !text(item.name, 320) ||
          typeof item.organizationId !== 'string' ||
          !uuid.test(item.organizationId) ||
          !Number.isSafeInteger(item.revision) ||
          item.revision! < 1
        : item.organizationId !== null || item.name !== null || item.revision !== null
    ) {
      return []
    }

    return [
      {
        key: JSON.stringify([item.connectionId, item.profile]),
        connectionId: item.connectionId,
        connectionLabel: item.connectionLabel,
        profile: item.profile,
        endpointIdentity: item.endpointIdentity,
        organizationId: item.organizationId,
        name: item.name,
        revision: item.revision,
        state: item.state,
        verifiedAt: item.verifiedAt,
        freshness: 'stale'
      }
    ]
  })
}

export function writeDirectoryRecords(storage: DirectoryStorage, records: readonly OrganizationDirectoryEntry[]) {
  const value = JSON.stringify(
    records.map(item => ({
      connectionId: item.connectionId,
      connectionLabel: item.connectionLabel,
      profile: item.profile,
      endpointIdentity: item.endpointIdentity,
      organizationId: item.organizationId,
      name: item.name,
      revision: item.revision,
      state: item.state,
      verifiedAt: item.verifiedAt
    }))
  )

  if (value.length > MAX_CACHE_CHARACTERS) {
    throw new Error('Organization display cache is too large')
  }

  storage.setItem(CACHE_KEY, value)
}
