import type { OrganizationSnapshot } from './types'

/** Presentation only: an empty cache is not evidence of an empty organization. */
export function observationState(snapshot: OrganizationSnapshot, recordCount: number) {
  if (snapshot.source !== 'runtime' || snapshot.connection?.state === 'ready') { return 'current' }

  if (recordCount > 0 || snapshot.connection?.lastUpdatedAt) { return 'retained' }

  return snapshot.connection?.state === 'connecting' ? 'loading' : 'unavailable'
}
