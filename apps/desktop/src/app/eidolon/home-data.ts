import type { OrganizationSnapshot } from './types'

/** Overview projections are read-only. A completed task is not an accepted outcome. */
export function homeData(snapshot: OrganizationSnapshot) {
  return {
    objectives: snapshot.objectives.filter(item => !['completed', 'cancelled', 'archived'].includes(item.status) && !item.history?.archived),
    requests: snapshot.requests?.filter(item => item.status === 'pending_intervention') ?? [],
    deliverables: snapshot.outcomes?.items.filter(item => item.status === 'accepted' && item.deliverableId) ?? [],
    activity: [...snapshot.activity].sort((a, b) => (Date.parse(b.timestamp) || 0) - (Date.parse(a.timestamp) || 0)).slice(0, 4)
  }
}
