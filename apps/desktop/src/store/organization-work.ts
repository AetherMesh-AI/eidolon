import { atom } from 'nanostores'

import type { OrganizationSnapshot } from '@/app/eidolon/types'

/** Small runtime facts only. No adapter, transport descriptor, credential, or
 * snapshot payload crosses the quit-guard bridge or is persisted to disk. */
export interface OrganizationWork {
  count: number
  titles: string[]
  running: number
  queued: number
  needsYou: number
  stale: boolean
  queueAvailable: boolean
}

export const NO_ORGANIZATION_WORK: OrganizationWork = {
  count: 0,
  titles: [],
  running: 0,
  queued: 0,
  needsYou: 0,
  stale: false,
  queueAvailable: false
}
export const $organizationWork = atom<OrganizationWork>(NO_ORGANIZATION_WORK)

export function organizationWorkSummary(snapshot: OrganizationSnapshot): OrganizationWork {
  if (snapshot.source !== 'runtime') {
    return NO_ORGANIZATION_WORK
  }

  const requests = snapshot.requests ?? []
  const active = requests.filter(request => request.status === 'running' || request.status === 'queued')
  const objectives = new Map(snapshot.objectives.map(objective => [objective.id, objective.title.trim()]))

  return {
    count: active.length,
    titles: [...new Set(active.map(request => objectives.get(request.objectiveId) ?? '').filter(Boolean))],
    running: active.filter(request => request.status === 'running').length,
    queued: active.filter(request => request.status === 'queued').length,
    needsYou: requests.filter(request => request.status === 'pending_intervention').length,
    queueAvailable: snapshot.requests !== undefined && snapshot.connection?.state === 'ready',
    stale: snapshot.connection?.state !== 'ready'
  }
}

// An inactive profile may still be working on its own backend. Remember only
// this small summary until that owner is visited and reconciled again. Unknown
// completion is never reported as idle just because the foreground changed.
export interface OrganizationOwnerWork {
  ownerScope: string
  route?: { connectionId: string; profile: string }
  profile: string
  work: OrganizationWork
}
// Renderer-only routing facts; the quit-guard bridge receives only the summary above.
export const $organizationWorkOwners = atom<OrganizationOwnerWork[]>([])
const ownerContexts = new Map<string, Pick<OrganizationOwnerWork, 'route' | 'profile'>>()
const workByOwner = new Map<string, OrganizationWork>()

export function clearOrganizationWork() {
  workByOwner.clear()
  ownerContexts.clear()
  $organizationWorkOwners.set([])
  $organizationWork.set(NO_ORGANIZATION_WORK)
}

export function publishOrganizationWork(snapshot: OrganizationSnapshot) {
  if (snapshot.source !== 'runtime') {
    return
  }

  const owner = snapshot.connection?.ownerScope ?? snapshot.connection?.scope

  if (!owner) {
    return
  }

  const context = ownerContexts.get(owner)
  ownerContexts.set(owner, {
    route: snapshot.connection?.ownerRoute ?? context?.route,
    profile: snapshot.connection?.ownerRoute?.profile ?? snapshot.runtime?.profile ?? context?.profile ?? ''
  })
  const current = organizationWorkSummary(snapshot)
  const previous = workByOwner.get(owner)

  // Only a fresh authoritative snapshot may declare known work finished.
  // A reconnect starts empty and must not clear the quit warning.
  if ((snapshot.connection?.state === 'ready' && current.queueAvailable) || !previous) {
    workByOwner.set(owner, current)
  } else {
    workByOwner.set(owner, { ...previous, stale: true, queueAvailable: current.queueAvailable })
  }

  const next: OrganizationWork = { ...NO_ORGANIZATION_WORK, titles: [], queueAvailable: true }

  for (const [key, work] of workByOwner) {
    next.queueAvailable &&= work.queueAvailable
    next.count += work.count
    next.running += work.running
    next.queued += work.queued
    next.needsYou += work.needsYou
    next.titles.push(...work.titles.filter(title => !next.titles.includes(title)))
    next.stale ||= (work.count > 0 || work.needsYou > 0) && (key !== owner || work.stale)
  }

  const owners = [...workByOwner]
    .filter(([, work]) => work.count || work.needsYou)
    .map(([key, work]) => ({
      ownerScope: key,
      ...ownerContexts.get(key)!,
      work: { ...work, stale: key !== owner || work.stale }
    }))

  if (JSON.stringify(owners) !== JSON.stringify($organizationWorkOwners.get())) {
    $organizationWorkOwners.set(owners)
  }

  if (JSON.stringify(next) !== JSON.stringify($organizationWork.get())) {
    $organizationWork.set(next)
  }
}
