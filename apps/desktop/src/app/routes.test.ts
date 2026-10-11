import { describe, expect, it } from 'vitest'

import { registry } from '@/contrib/registry'

import {
  appViewForPath,
  contributedRoutes,
  NEW_CHAT_ROUTE,
  primaryRouteSelectedSessionId,
  ROUTES_AREA,
  routeSessionId,
  sessionRoute,
  SETTINGS_ROUTE
} from './routes'

it('keeps organization navigation out of canonical session identity', () => {
  for (const path of [
    '/home',
    '/messages',
    '/work',
    '/objectives',
    '/objectives/example',
    '/activity',
    '/knowledge',
    '/organization',
    '/requests',
    '/legacy-organization',
    '/organization-preview'
  ]) {
    expect(routeSessionId(path)).toBeNull()
    expect(appViewForPath(path)).toBe('organization')
    expect(primaryRouteSelectedSessionId(path, 'canonical-session')).toBe('canonical-session')
  }
})

const SESS_A = 'sess-a'
const SESS_B = 'sess-b'

describe('primaryRouteSelectedSessionId', () => {
  it('prefers the routed session id over a stale/different store selection (#59305)', () => {
    // The route already committed to B while the store selection hasn't
    // caught up yet (still reads A) — the route wins.
    expect(primaryRouteSelectedSessionId(sessionRoute(SESS_B), SESS_A)).toBe(SESS_B)
  })

  it('returns null on the new-chat route even with a leftover selection from the previous chat', () => {
    expect(primaryRouteSelectedSessionId(NEW_CHAT_ROUTE, SESS_A)).toBeNull()
  })

  it('falls back to the store selection on a non-chat route (settings, overlays)', () => {
    expect(primaryRouteSelectedSessionId(SETTINGS_ROUTE, SESS_A)).toBe(SESS_A)
  })

  it('falls back to the store selection when the route matches the same session', () => {
    expect(primaryRouteSelectedSessionId(sessionRoute(SESS_A), SESS_A)).toBe(SESS_A)
  })

  it('returns null on a non-chat route with no store selection', () => {
    expect(primaryRouteSelectedSessionId(SETTINGS_ROUTE, null)).toBeNull()
  })
})

it('reserves organization routes while keeping installed plugin pages available', () => {
  const dispose = registry.registerMany([
    { id: 'override-requests', area: ROUTES_AREA, data: { path: '/requests' }, render: () => null },
    { id: 'legacy-kanban', area: ROUTES_AREA, data: { path: '/kanban' }, render: () => null }
  ])

  try {
    expect(contributedRoutes().some(route => route.path === '/requests')).toBe(false)
    expect(contributedRoutes().some(route => route.path === '/kanban')).toBe(true)
    expect(routeSessionId('/kanban')).toBeNull()
    expect(appViewForPath('/kanban')).toBe('extension')
  } finally {
    dispose()
  }
})

it('keeps a disabled legacy Kanban deeplink out of ordinary session history', () => {
  expect(routeSessionId('/kanban?board=work')).toBeNull()
  expect(appViewForPath('/kanban')).toBe('extension')
  expect(primaryRouteSelectedSessionId('/kanban', 'my-chat')).toBe('my-chat')
})
