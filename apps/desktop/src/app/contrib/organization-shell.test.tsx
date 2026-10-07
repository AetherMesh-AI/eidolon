import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { Link, MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import type { OrganizationSnapshot } from '@/app/eidolon/types'
import { registry } from '@/contrib/registry'
import { $connectionsRegistry } from '@/store/connection-registry-state'
import { $activeConnectionId } from '@/store/connections'
import type * as ConnectionsModule from '@/store/connections'
import { $organizationWork, clearOrganizationWork, publishOrganizationWork } from '@/store/organization-work'
import { $activeGatewayProfile } from '@/store/profile'
import { $connection } from '@/store/session'
import { stubResizeObserver } from '@/test/jsdom'

import { organizationOwnerKey } from '../eidolon/organization-owner'
import type * as RoutesModule from '../routes'
import { SIDEBAR_NAV_AREA } from '../routes'

import { OrganizationActiveWorkBridge, OrganizationWorkIndicator, PluginNavigation } from './organization-shell'

const data = vi.hoisted(() => ({ snapshot: null as OrganizationSnapshot | null, selectConnection: vi.fn(), readScope: vi.fn() }))
vi.mock('@/store/connections', async original => ({ ...(await original<typeof ConnectionsModule>()), selectConnection: data.selectConnection }))
vi.mock('../eidolon/runtime-provider', () => ({
  createOrganizationScopeReader: () => data.readScope,
  useRuntimeOrganization: () => (data.snapshot ? { snapshot: data.snapshot } : null)
}))
vi.mock('../routes', async original => ({
  ...(await original<typeof RoutesModule>()),
  navigateToWorkspacePage: (navigate: (path: string) => void, path: string) => navigate(path)
}))

const Location = () => <output data-testid="location">{useLocation().pathname}</output>
beforeEach(() => {
  clearOrganizationWork()
  stubResizeObserver()
  data.selectConnection.mockReset()
  data.readScope.mockImplementation(() => ({ connected: true, ownerKey: organizationOwnerKey($activeConnectionId.get(), $activeGatewayProfile.get(), $connection.get()) }))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  $connectionsRegistry.set(null)
  $connection.set(null)
  $activeGatewayProfile.set('default')
  data.snapshot = null
  clearOrganizationWork()
})

it('keeps plugin navigation reactive across enable, disable and re-enable', () => {
  const view = render(
    <MemoryRouter>
      <PluginNavigation />
      <Location />
    </MemoryRouter>
  )

  let dispose = () => {}

  const enable = () =>
    registry.register({
      id: 'test-kanban',
      area: SIDEBAR_NAV_AREA,
      data: { label: 'Legacy Kanban', codicon: 'project', path: '/kanban' }
    })

  expect(screen.queryByRole('link', { name: 'Legacy Kanban' })).toBeNull()
  act(() => {
    dispose = enable()
  })
  fireEvent.click(screen.getByRole('link', { name: 'Legacy Kanban' }))
  expect(screen.getByTestId('location').textContent).toBe('/kanban')
  act(() => dispose())
  expect(screen.queryByRole('link', { name: 'Legacy Kanban' })).toBeNull()
  act(() => {
    dispose = enable()
  })
  expect(screen.getAllByRole('link', { name: 'Legacy Kanban' })).toHaveLength(1)
  view.unmount()
  dispose()
})

it('rejects external-protocol navigation contributions', () => {
  const dispose = registry.register({
    id: 'external',
    area: SIDEBAR_NAV_AREA,
    data: { label: 'Unsafe', path: '//external.example' }
  })

  render(
    <MemoryRouter>
      <PluginNavigation />
    </MemoryRouter>
  )
  expect(screen.queryByRole('link')).toBeNull()
  dispose()
})

it('publishes work and attention while on an ordinary chat without navigating automatically', () => {
  data.snapshot = {
    source: 'runtime',
    objectives: [],
    tasks: [],
    agents: [],
    activity: [],
    knowledge: [],
    requests: (['running', 'queued', 'pending_intervention'] as const).map(status => ({
      id: status,
      status,
      objectiveId: 'objective',
      type: 'work',
      team: 'general',
      priority: 1,
      attempts: 1,
      createdAt: ''
    })),
    connection: { scope: 'socket', ownerScope: organizationOwnerKey(null, 'default', null), state: 'ready' }
  }
  render(
    <MemoryRouter initialEntries={['/ordinary-chat']}>
      <OrganizationActiveWorkBridge />
      <OrganizationWorkIndicator />
      <Location />
    </MemoryRouter>
  )
  expect($organizationWork.get()).toMatchObject({ count: 2, needsYou: 1 })
  expect(screen.getByTestId('location').textContent).toBe('/ordinary-chat')
  fireEvent.click(screen.getByRole('button', { name: /Organization work: 1 running, 1 queued, 1 Needs You/ }))
  expect(screen.getByTestId('location').textContent).toBe('/requests')
  act(() => $organizationWork.set({ ...$organizationWork.get(), stale: true }))
  expect(screen.getByRole('button').textContent).toContain('Last known')
})


it('shows exact background owners and opens only the chosen connection/profile after activation', async () => {
  $connectionsRegistry.set({ version: 1, primary: 'local', secureTokenStorage: true, connections: [
    { id: 'remote-a', label: 'Remote A', kind: 'remote', tokenSet: false, tokenPreview: null },
    { id: 'local', label: 'Local', kind: 'local', tokenSet: false, tokenPreview: null }
  ] })
  const base: OrganizationSnapshot = { source: 'runtime', objectives: [], tasks: [], agents: [], activity: [], knowledge: [], requests: [] }
  publishOrganizationWork({ ...base, connection: { scope: 'a', ownerScope: organizationOwnerKey('remote-a', 'research', { mode: 'remote', baseUrl: 'https://old.example' }), state: 'ready', ownerRoute: { connectionId: 'remote-a', profile: 'research' } }, runtime: { profile: 'research', state: 'ready', capabilities: [], maxWorkers: 1, scope: '' }, requests: [{ id: 'question', status: 'pending_intervention', objectiveId: 'goal', type: 'request.question', team: 'research', priority: 3, attempts: 1, createdAt: '' }] } as OrganizationSnapshot)
  publishOrganizationWork({ ...base, connection: { scope: 'b', ownerScope: 'owner-b', state: 'ready', ownerRoute: { connectionId: 'local', profile: 'research' } } } as OrganizationSnapshot)
  $connection.set({ connectionId: 'local' } as never)
  $activeGatewayProfile.set('research')
  let endpoint = 'https://old.example'
  let finish!: () => void
  data.selectConnection.mockImplementation(() => new Promise<void>(resolve => { finish = () => { $connection.set({ connectionId: 'remote-a', mode: 'remote', baseUrl: endpoint } as never); resolve() } }))
  const view = render(<MemoryRouter initialEntries={['/ordinary-chat']}><OrganizationWorkIndicator /><Link to="/newer-route">Other page</Link><Location /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: /Organization work:/ }))
  expect(screen.getByTestId('location').textContent).toBe('/ordinary-chat')
  data.selectConnection.mockRejectedValueOnce(new Error('Connection unavailable'))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Open requests: Remote A / research' })) })
  expect(screen.getByRole('alert')).toBeTruthy()
  expect(screen.getByTestId('location').textContent).toBe('/ordinary-chat')
  fireEvent.click(screen.getByRole('button', { name: 'Open requests: Remote A / research' }))
  expect(data.selectConnection).toHaveBeenCalledWith('remote-a', { profile: 'research', preserveRoute: true })
  expect(screen.getByTestId('location').textContent).toBe('/ordinary-chat')
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  await act(async () => { finish() })
  expect(screen.getByTestId('location').textContent).toBe('/ordinary-chat')
  act(() => { $connection.set({ connectionId: 'local' } as never) })
  fireEvent.click(screen.getByRole('button', { name: /Organization work:/ }))
  endpoint = 'https://changed.example'
  fireEvent.click(screen.getByRole('button', { name: 'Open requests: Remote A / research' }))
  await act(async () => { finish() })
  expect(screen.getByRole('alert')).toBeTruthy()
  expect(screen.getByTestId('location').textContent).toBe('/ordinary-chat')
  endpoint = 'https://old.example'
  fireEvent.click(screen.getByRole('button', { name: 'Open requests: Remote A / research' }))
  fireEvent.click(screen.getByRole('link', { name: 'Other page' }))
  await act(async () => { finish() })
  expect(screen.getByTestId('location').textContent).toBe('/newer-route')
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  fireEvent.click(screen.getByRole('button', { name: /Organization work:/ }))
  expect(screen.getByTestId('location').textContent).toBe('/requests')
  view.unmount()
  $connectionsRegistry.set(null)
  $connection.set(null)
})


it('never routes an unavailable background owner to the current profile', () => {
  const base: OrganizationSnapshot = { source: 'runtime', objectives: [], tasks: [], agents: [], activity: [], knowledge: [], requests: [] }
  publishOrganizationWork({ ...base, connection: { scope: 'old', ownerScope: 'old-owner', state: 'ready' }, runtime: { profile: 'research', state: 'ready', capabilities: [], maxWorkers: 1, scope: '' }, requests: [{ id: 'question', status: 'pending_intervention', objectiveId: 'goal', type: 'request.question', team: 'research', priority: 3, attempts: 1, createdAt: '' }] })
  $connection.set({ connectionId: 'new-source' } as never)
  render(<MemoryRouter initialEntries={['/ordinary-chat']}><OrganizationWorkIndicator /><Location /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: /Organization work:/ }))
  const unavailable = screen.getByRole('button', { name: 'Open requests: Unrecorded connection / research' })
  expect(unavailable).toHaveProperty('disabled', true)
  fireEvent.click(unavailable)
  expect(data.selectConnection).not.toHaveBeenCalled()
  expect(screen.getByTestId('location').textContent).toBe('/ordinary-chat')
})
