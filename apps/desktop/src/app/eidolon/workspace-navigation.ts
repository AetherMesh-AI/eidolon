import { createContext, type RefObject, useState } from 'react'
import { useSearchParams } from 'react-router'

/** Presentation-only routes. Never accept an arbitrary URL as a return target. */
const returnPaths = new Set(['/home', '/work', '/objectives', '/messages', '/requests', '/artifacts', '/activity', '/organization'])

export function workspaceReturnTarget(value: string | null): string | null {
  if (!value || value.length > 4096 || !value.startsWith('/') || value.startsWith('//') || [...value].some(character => character === '\\' || character.charCodeAt(0) < 32)) {return null}

  const url = new URL(value, 'https://workspace.invalid')

  return url.origin === 'https://workspace.invalid' && returnPaths.has(url.pathname) && !url.hash
    ? `${url.pathname}${url.search}`
    : null
}

export function objectiveHref(objectiveId: string, origin: string, ownerScope?: string, requestId?: string): string {
  const params = new URLSearchParams()
  const from = workspaceReturnTarget(origin)

  if (from && ownerScope) {params.set('from', from); params.set('originOwner', ownerScope)}

  if (requestId && ownerScope) {params.set('request', requestId); params.set('requestOwner', ownerScope)}

  const query = params.toString()

  return `/objectives/${encodeURIComponent(objectiveId)}${query ? `?${query}` : ''}`
}

export function objectiveReturnTarget(params: URLSearchParams, ownerScope?: string): string {
  return ownerScope && params.get('originOwner') === ownerScope
    ? workspaceReturnTarget(params.get('from')) ?? '/objectives'
    : '/objectives'
}

export interface WorkspaceScrollPosition {
  top: number
  left: number
  contentHeight: number
  panels: { key: string; top: number; left: number }[]
  focusHref: string | null
}

/** Window-local presentation memory. Owner changes discard positions, never transfer them. */
export function createWorkspaceScrollMemory() {
  let owner: string | undefined
  const positions = new Map<string, WorkspaceScrollPosition>()

  const selectOwner = (next: string | undefined) => {
    if (owner !== next) { positions.clear(); owner = next }
  }

  return {
    save(nextOwner: string | undefined, route: string, position: WorkspaceScrollPosition) {
      selectOwner(nextOwner)

      if (nextOwner && workspaceReturnTarget(route)) {
        positions.set(route, position)

        if (positions.size > 50) { positions.delete(positions.keys().next().value!) }
      }
    },
    read(nextOwner: string | undefined, route: string) {
      selectOwner(nextOwner)

      return nextOwner ? positions.get(route) : undefined
    },
    reset(nextOwner: string | undefined) { selectOwner(nextOwner) }
  }
}

/** Presentation-only, owner-scoped route state. Never restores mutations or drafts. */
export function useWorkspaceRouteState(namespace: string, owner: string | undefined) {
  const [params, setParams] = useSearchParams()
  const [local, setLocal] = useState<Record<string, string>>({})
  const ownerKey = `${namespace}Owner`

  const read = (key: string) => owner
    ? params.get(ownerKey) === owner ? params.get(`${namespace}${key}`) ?? '' : ''
    : local[key] ?? ''

  const write = (values: Record<string, string | null>) => {
    if (!owner) {
      setLocal(current => Object.fromEntries(Object.entries({ ...current, ...values }).filter((entry): entry is [string, string] => entry[1] !== null)))

      return
    }

    setParams(current => {
      const next = new URLSearchParams(current)

      if (next.get(ownerKey) !== owner) {
        for (const key of [...next.keys()]) { if (key.startsWith(namespace)) { next.delete(key) } }
      }

      next.set(ownerKey, owner)

      for (const [key, value] of Object.entries(values)) {
        if (value) { next.set(`${namespace}${key}`, value) } else { next.delete(`${namespace}${key}`) }
      }

      return next
    }, { replace: true })
  }

  return { read, write }
}

/** A link from an objective's own activity must not discard its original Back context. */
export function contextualObjectiveHref(objectiveId: string, pathname: string, search: string, owner?: string) {
  return pathname === `/objectives/${encodeURIComponent(objectiveId)}`
    ? pathname + search
    : objectiveHref(objectiveId, pathname + search, owner)
}

/** Read-only focus arbitration; populated only by validated window-local restoration. */
export interface WorkspaceFocusRestore { owner: string; route: string }
export interface WorkspaceFocusRestoration { pending: WorkspaceFocusRestore | null; origin: WorkspaceFocusRestore }
export const WorkspaceFocusRestoreContext = createContext<RefObject<WorkspaceFocusRestoration | null> | null>(null)
