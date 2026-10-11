import { act, cleanup, render, screen } from '@testing-library/react'
import { atom } from 'nanostores'
import type { ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { HermesGateway } from '@/eidolon'
import { $gateway } from '@/store/gateway'
import { $activeGatewayProfile } from '@/store/profile'

import { ChatRoutesSurface, SidebarSurface } from './surfaces'
import type { SidebarActions, WiringActions } from './types'

const routeState = vi.hoisted(() => ({ kanbanEnabled: false }))

vi.mock('@/contrib/react/use-contributions', () => ({ useContributions: vi.fn() }))
vi.mock('@/store/connections', () => ({ $activeConnectionId: atom('local') }))
vi.mock('@/store/gateway', () => ({ $gateway: atom<unknown>(null) }))
vi.mock('@/store/profile', () => ({ $activeGatewayProfile: atom('default') }))
vi.mock('@/store/session', () => ({
  $freshDraftReady: atom(false),
  $gatewayState: atom('open')
}))
vi.mock('../chat', () => ({
  ChatView: ({ gateway }: { gateway: { id?: string } | null }) => <div data-testid="gateway">{gateway?.id}</div>
}))
vi.mock('../chat/sidebar', () => ({
  ChatSidebar: ({ historyOnly }: { historyOnly: boolean }) => (
    <div data-testid="ordinary-history">{String(historyOnly)}</div>
  )
}))
vi.mock('@/plugins/eidolon-bots/roster-pane', () => ({
  BotsPane: () => {
    throw new Error('The shell must never mount a second plugin roster')
  }
}))
vi.mock('../eidolon/rail', () => ({ OrganizationRail: ({ sessions }: { sessions: ReactNode }) => <>{sessions}</> }))
vi.mock('../eidolon/workspace', () => ({ OrganizationWorkspace: () => <div>Organization workspace</div> }))
vi.mock('../eidolon/runtime-provider', () => ({ useRuntimeOrganization: () => null }))
vi.mock('../eidolon/legacy-history', () => ({ LegacyOrganizationHistory: () => <div>Read-only legacy history</div> }))
vi.mock('../artifacts/workspace', () => ({ ArtifactWorkspace: () => <div>Unified artifacts</div> }))
vi.mock('./organization-shell', () => ({
  LegacyKanbanUnavailable: () => <div>Disabled legacy Kanban</div>,
  OrganizationWorkIndicator: () => null,
  PluginNavigation: () => null
}))
vi.mock('../right-sidebar/terminal/chrome', () => ({ TerminalPaneChrome: () => null }))
vi.mock('../shell/hooks/use-status-snapshot', () => ({ useStatusSnapshot: () => ({}) }))
vi.mock('../shell/hooks/use-statusbar-items', () => ({
  useStatusbarItems: () => ({ leftStatusbarItems: [], statusbarItems: [] })
}))
vi.mock('../eidolon/system-health-bar', () => ({ SystemHealthBar: () => null }))
vi.mock('../shell/statusbar-controls', () => ({ StatusbarControls: () => null }))
vi.mock('../routes', () => ({
  contributedRoutes: () =>
    routeState.kanbanEnabled
      ? [{ key: 'kanban:page', path: '/kanban', render: () => <div>Existing Kanban board</div> }]
      : [],
  NEW_CHAT_ROUTE: '/new',
  LEGACY_KANBAN_ROUTE: '/kanban',
  ROUTES_AREA: 'routes',
  sessionRoute: (id: string) => `/${id}`
}))
vi.mock('./latest-actions', () => ({ latestChatActions: () => ({}), latestSidebarActions: () => ({}) }))
vi.mock('./panes', () => ({ setStatusbarItemGroup: vi.fn(), useStatusbarContributions: () => [] }))
vi.mock('../shell/model-menu-panel', () => ({ ModelMenuPanel: () => null }))

afterEach(() => {
  cleanup()
  routeState.kanbanEnabled = false
  $gateway.set(null)
  $activeGatewayProfile.set('default')
})

describe('ChatRoutesSurface', () => {
  it('passes the live gateway after an open-to-open profile switch', () => {
    const gatewayA = { id: 'a' } as unknown as HermesGateway
    const gatewayB = { id: 'b' } as unknown as HermesGateway

    $gateway.set(gatewayA)
    const actions = { getGateway: () => $gateway.get() } as unknown as WiringActions

    render(
      <MemoryRouter>
        <ChatRoutesSurface actions={actions} />
      </MemoryRouter>
    )

    expect(screen.getByTestId('gateway').textContent).toBe('a')

    act(() => {
      $gateway.set(gatewayB)
      $activeGatewayProfile.set('other')
    })

    expect(screen.getByTestId('gateway').textContent).toBe('b')
  })
})

const Location = () => {
  const location = useLocation()

  return (
    <output data-testid="location">
      {location.pathname}
      {location.search}
      {location.hash}
    </output>
  )
}

it('mounts ordinary history without a duplicate plugin roster', () => {
  render(
    <MemoryRouter>
      <SidebarSurface actions={{} as SidebarActions} currentView="organization" />
    </MemoryRouter>
  )
  expect(screen.getAllByTestId('ordinary-history')).toHaveLength(1)
  expect(screen.getByTestId('ordinary-history').textContent).toBe('true')
})

it.each([
  ['/knowledge?tab=file#retained', '/artifacts?tab=file&source=organization#retained', 'Unified artifacts'],
  ['/organization-preview', '/legacy-organization', 'Read-only legacy history'],
  ['/requests', '/requests', 'Organization workspace'],
  ['/kanban?board=work', '/kanban?board=work', 'Disabled legacy Kanban']
])('keeps the %s legacy or queue route out of chat', async (from, to, text) => {
  render(
    <MemoryRouter initialEntries={[from]}>
      <ChatRoutesSurface actions={{} as WiringActions} />
      <Location />
    </MemoryRouter>
  )
  expect(await screen.findByText(text)).toBeTruthy()
  expect(screen.getByTestId('location').textContent).toBe(to)
  expect(screen.queryByTestId('gateway')).toBeNull()
})

it('preserves a legacy session deeplink and canonical stored identity', () => {
  render(
    <MemoryRouter initialEntries={['/sessions/canonical-bot-chat']}>
      <ChatRoutesSurface actions={{} as WiringActions} />
      <Location />
    </MemoryRouter>
  )
  expect(screen.getByTestId('location').textContent).toBe('/canonical-bot-chat')
  expect(screen.getByTestId('gateway')).toBeTruthy()
})

it('restores the actual legacy board when enabled and safe recovery when disabled again', () => {
  const tree = () => (
    <MemoryRouter initialEntries={['/kanban?board=retained']}>
      <ChatRoutesSurface actions={{} as WiringActions} />
      <Location />
    </MemoryRouter>
  )

  const view = render(tree())
  expect(screen.getByText('Disabled legacy Kanban')).toBeTruthy()
  routeState.kanbanEnabled = true
  view.rerender(tree())
  expect(screen.getByText('Existing Kanban board')).toBeTruthy()
  expect(screen.queryByText('Disabled legacy Kanban')).toBeNull()
  routeState.kanbanEnabled = false
  view.rerender(tree())
  expect(screen.getByText('Disabled legacy Kanban')).toBeTruthy()
  expect(screen.getByTestId('location').textContent).toBe('/kanban?board=retained')
  expect(screen.queryByTestId('gateway')).toBeNull()
})
