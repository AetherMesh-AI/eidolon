import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import type { OrganizationSnapshot } from '@/app/eidolon/types'
import { registry } from '@/contrib/registry'
import { $organizationWork, clearOrganizationWork } from '@/store/organization-work'

import type * as RoutesModule from '../routes'
import { SIDEBAR_NAV_AREA } from '../routes'

import { OrganizationActiveWorkBridge, OrganizationWorkIndicator, PluginNavigation } from './organization-shell'

const data = vi.hoisted(() => ({ snapshot: null as OrganizationSnapshot | null }))
vi.mock('../eidolon/runtime-provider', () => ({
  useRuntimeOrganization: () => (data.snapshot ? { snapshot: data.snapshot } : null)
}))
vi.mock('../routes', async original => ({
  ...(await original<typeof RoutesModule>()),
  navigateToWorkspacePage: (navigate: (path: string) => void, path: string) => navigate(path)
}))

const Location = () => <output data-testid="location">{useLocation().pathname}</output>
beforeEach(clearOrganizationWork)
afterEach(() => {
  cleanup()
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
    connection: { scope: 'socket', ownerScope: 'owner', state: 'ready' }
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
