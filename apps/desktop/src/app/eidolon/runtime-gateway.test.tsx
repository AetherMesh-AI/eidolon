import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'

const routing = vi.hoisted(() => ({ connectionId: 'a', epoch: 1 }))
vi.mock('@/store/gateway', async () => {
  const { atom } = await import('nanostores')

  return {
    $gateway: atom(null), $activeGatewayRoute: atom('default'),
    activeGatewayConnectionId: () => routing.connectionId, gatewayActivationEpoch: () => routing.epoch,
    ensureActiveGatewayOpen: vi.fn(), isActivePrimary: () => true
  }
})
vi.mock('@/store/session', async () => {
  const { atom } = await import('nanostores')

  const $connection = atom<unknown>(null)

  return { $connection, $gatewayState: atom('open'), setConnection: vi.fn(value => $connection.set(value)) }
})
vi.mock('@/store/profile', async () => {
  const { atom } = await import('nanostores')

  return { $activeGatewayProfile: atom('default'), $gatewaySwapTarget: atom(null) }
})

import { $activeGatewayRoute, $gateway } from '@/store/gateway'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection, $gatewayState } from '@/store/session'

import { createPrototypeAdapter } from './adapter'
import type { OrganizationSnapshot } from './types'
import { OrganizationWorkspace } from './workspace'

function snapshot(title: string): OrganizationSnapshot {
  return {
    source: 'runtime', objectives: [{ id: title, title, description: '', ownerId: 'manager', createdAt: '2026-10-04T00:00:00Z', status: 'planning', source: 'runtime' }],
    agents: [], tasks: [], requests: [], activity: [], knowledge: [],
    runtime: { state: 'ready', maxWorkers: 2, capabilities: ['work.draft'], scope: 'Submitted context only' }
  }
}

const connection = (sharedPrimary = false) => ({ connectionId: routing.connectionId, mode: 'remote', baseUrl: `https://${routing.connectionId}.example`, profile: sharedPrimary ? 'research' : 'remote-writing', sharedPrimary })
beforeEach(() => {
  routing.connectionId = 'a'
  routing.epoch++
  $gateway.set(null)
  $activeGatewayRoute.set('default')
  $activeGatewayProfile.set('default')
  $gatewaySwapTarget.set(null)
  $connection.set(connection() as never)
  $gatewayState.set('open')
})

it('defaults to the actual gateway requester and fences old same-named connections before rendering', async () => {
  let resolveOld!: (snapshot: OrganizationSnapshot) => void
  const oldRequest = vi.fn().mockImplementation(() => new Promise<OrganizationSnapshot>(resolve => {resolveOld = resolve}))
  $gateway.set({ request: oldRequest, connectionState: 'open' } as never)
  const view = render(<MemoryRouter initialEntries={['/objectives']}><OrganizationWorkspace /></MemoryRouter>)
  await waitFor(() => expect(oldRequest).toHaveBeenCalledTimes(1))
  const oldSignal = oldRequest.mock.calls[0][3] as AbortSignal
  const newRequest = vi.fn().mockResolvedValue(snapshot('Only B'))
  act(() => {
    routing.connectionId = 'b'
    routing.epoch++
    $gateway.set({ request: newRequest, connectionState: 'open' } as never)
    $connection.set(connection() as never)
  })
  expect(oldSignal.aborted).toBe(true)
  expect(await screen.findByRole('link', { name: /Only B/ })).toBeTruthy()
  await act(async () => {resolveOld(snapshot('Secret A')); await Promise.resolve()})
  expect(screen.queryByRole('link', { name: /Secret A/ })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Load example' })).toBeNull()
  view.unmount()
})

it('sends named profile parameters only for shared sockets and honors dedicated remote aliases', async () => {
  const request = vi.fn().mockResolvedValue(snapshot('Shared profile'))
  $activeGatewayRoute.set('default')
  $activeGatewayProfile.set('research')
  $connection.set(connection(true) as never)
  $gateway.set({ request, connectionState: 'open' } as never)
  const view = render(<MemoryRouter initialEntries={['/objectives']}><OrganizationWorkspace /></MemoryRouter>)
  await waitFor(() => expect(request).toHaveBeenCalledTimes(1))
  expect(request.mock.calls[0][1]).toEqual({ profile: 'research' })
  act(() => {
    routing.epoch++
    $activeGatewayRoute.set('local-display-alias')
    $activeGatewayProfile.set('local-display-alias')
    $connection.set(connection(false) as never)
  })
  await waitFor(() => expect(request.mock.calls.length).toBeGreaterThan(1))
  expect(request.mock.calls.at(-1)?.[1]).toEqual({})
  view.unmount()
})

it('keeps the explicit prototype entirely outside the gateway lifecycle', async () => {
  const request = vi.fn().mockResolvedValue(snapshot('Forbidden live data'))
  $gateway.set({ request, connectionState: 'open' } as never)
  const view = render(<MemoryRouter initialEntries={['/home']}><OrganizationWorkspace adapter={createPrototypeAdapter()} /></MemoryRouter>)
  expect(screen.getByRole('button', { name: 'Load example' })).toBeTruthy()
  await act(async () => {await Promise.resolve()})
  expect(request).not.toHaveBeenCalled()
  view.unmount()
})


it('pauses work during a profile swap and recovers after a failed same-socket activation', async () => {
  const request = vi.fn().mockResolvedValue(snapshot('Original scope'))
  $gateway.set({ request, connectionState: 'open' } as never)
  const view = render(<MemoryRouter initialEntries={['/home']}><OrganizationWorkspace /></MemoryRouter>)
  await screen.findByRole('link', { name: /Original scope/ })
  fireEvent.change(screen.getByRole('textbox', { name: 'Objective' }), { target: { value: 'Draft a memo' } })
  fireEvent.change(screen.getByRole('textbox', { name: 'Submitted context (optional)' }), { target: { value: 'Keep these facts' } })
  act(() => { $gatewaySwapTarget.set('missing-profile'); routing.epoch++ })
  expect(screen.getByRole('button', { name: 'Create objective' })).toHaveProperty('disabled', true)
  await act(async () => {await Promise.resolve()})
  expect(request).toHaveBeenCalledTimes(1)
  act(() => {$gatewaySwapTarget.set(null)})
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2))
  expect(await screen.findByRole('link', { name: /Original scope/ })).toBeTruthy()
  expect(screen.getByRole('textbox', { name: 'Objective' })).toHaveProperty('value', 'Draft a memo')
  expect(screen.getByRole('textbox', { name: 'Submitted context (optional)' })).toHaveProperty('value', 'Keep these facts')
  act(() => {$gateway.set({ request, connectionState: 'open' } as never)})
  await waitFor(() => expect(request).toHaveBeenCalledTimes(3))
  expect(screen.getByRole('textbox', { name: 'Objective' })).toHaveProperty('value', 'Draft a memo')
  expect(screen.getByRole('textbox', { name: 'Submitted context (optional)' })).toHaveProperty('value', 'Keep these facts')
  view.unmount()
})

it.each(['/organization', '/activity'])('clears same-ID inspectors when %s switches source', async path => {
  const forSource = (name: string) => ({ ...snapshot(name),
    agents: [{ id: 'manager', name: `${name} Manager`, role: 'Manager', responsibilities: [], capabilities: [], status: 'idle' as const, summary: '' }],
    activity: [{ id: 'event_1', agentId: 'manager', kind: 'planning' as const, text: `${name} update`, timestamp: '2026-10-04T00:00:00Z', source: 'runtime' as const }]
  })

  const request = vi.fn().mockResolvedValue(forSource('A'))
  $gateway.set({ request, connectionState: 'open' } as never)
  const view = render(<MemoryRouter initialEntries={[path]}><OrganizationWorkspace /></MemoryRouter>)
  fireEvent.click(await screen.findByRole('button', { name: path === '/organization' ? 'Inspect A Manager' : 'Inspect event: A update' }))
  expect(screen.getByRole('complementary')).toBeTruthy()
  act(() => {
    routing.connectionId = 'b'
    routing.epoch++
    $gateway.set({ request: vi.fn().mockResolvedValue(forSource('B')), connectionState: 'open' } as never)
    $connection.set(connection() as never)
  })
  await screen.findByRole('button', { name: path === '/organization' ? 'Inspect B Manager' : 'Inspect event: B update' })
  expect(screen.queryByRole('complementary')).toBeNull()
  view.unmount()
})


it('blocks ambiguous shared-profile routing when a failed descriptor lookup leaves a stale dedicated descriptor', async () => {
  const request = vi.fn().mockResolvedValue(snapshot('Default profile'))
  $gateway.set({ request, connectionState: 'open' } as never)
  const view = render(<MemoryRouter initialEntries={['/home']}><OrganizationWorkspace /></MemoryRouter>)
  await screen.findByRole('link', { name: /Default profile/ })
  act(() => { $gatewaySwapTarget.set('research'); routing.epoch++ })
  // The shared-primary socket activation succeeds but its parallel descriptor
  // lookup fails. profile.ts retains the old descriptor and still publishes research.
  act(() => { $activeGatewayProfile.set('research'); $gatewaySwapTarget.set(null) })
  await act(async () => {await Promise.resolve()})
  expect(request).toHaveBeenCalledTimes(1)
  expect(screen.getByText('Organization disconnected')).toBeTruthy()
  fireEvent.change(screen.getByRole('textbox', { name: 'Objective' }), { target: { value: 'Research-only context' } })
  expect(screen.getByRole('button', { name: 'Create objective' })).toHaveProperty('disabled', true)
  view.unmount()
})


it('waits for the matching descriptor after registry eviction changes the active source', async () => {
  const oldRequest = vi.fn().mockResolvedValue(snapshot('Source A'))
  $connection.set({ ...connection(true), profile: 'default' } as never)
  $gateway.set({ request: oldRequest, connectionState: 'open' } as never)
  const view = render(<MemoryRouter initialEntries={['/objectives']}><OrganizationWorkspace /></MemoryRouter>)
  await screen.findByRole('link', { name: /Source A/ })
  const newRequest = vi.fn().mockResolvedValue(snapshot('Source B'))
  act(() => {
    routing.connectionId = 'b'
    $gateway.set({ request: newRequest, connectionState: 'open' } as never)
  })
  await act(async () => {await Promise.resolve()})
  expect(newRequest).not.toHaveBeenCalled()
  expect(screen.queryByRole('link', { name: /Source A/ })).toBeNull()
  act(() => {$connection.set(connection(false) as never)})
  await screen.findByRole('link', { name: /Source B/ })
  expect(newRequest.mock.calls[0][1]).toEqual({})
  view.unmount()
})


it('preserves form content and admission receipt when the real requester clears a failed reconnect descriptor', async () => {
  let rejectCreate!: (reason: Error) => void
  const initial = snapshot('Existing work')

  const request = vi.fn().mockImplementation((method: string) => method === 'organization.create'
    ? new Promise((_resolve, reject) => {rejectCreate = reject})
    : Promise.resolve(initial))

  $gateway.set({ request, connectionState: 'open' } as never)
  const previousDesktop = window.hermesDesktop
  const getConnection = vi.fn().mockRejectedValue(new Error('Reconnect unavailable'))
  window.hermesDesktop = { getConnection } as never
  const view = render(<MemoryRouter initialEntries={['/home']}><OrganizationWorkspace /></MemoryRouter>)

  try {
    await screen.findByRole('link', { name: /Existing work/ })
    fireEvent.change(screen.getByRole('textbox', { name: 'Objective' }), { target: { value: 'Preserve this goal' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Submitted context (optional)' }), { target: { value: 'Preserve these facts' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create objective' }))
    await waitFor(() => expect(request.mock.calls.some(call => call[0] === 'organization.create')).toBe(true))
    const receipt = request.mock.calls.find(call => call[0] === 'organization.create')![1].idempotencyKey
    act(() => {$gatewayState.set('closed')})
    await act(async () => {rejectCreate(new Error('Connection closed')); await Promise.resolve()})
    await waitFor(() => expect($connection.get()).toBeNull())
    expect(getConnection).toHaveBeenCalled()
    expect(screen.getByRole('textbox', { name: 'Objective' })).toHaveProperty('value', 'Preserve this goal')
    expect(screen.getByRole('textbox', { name: 'Submitted context (optional)' })).toHaveProperty('value', 'Preserve these facts')
    expect(screen.getByRole('button', { name: 'Create objective' })).toHaveProperty('disabled', true)

    request.mockImplementation((method: string) => method === 'organization.create' ? Promise.reject(new Error('Still unavailable')) : Promise.resolve(initial))
    act(() => {$connection.set(connection() as never); $gatewayState.set('open')})
    await waitFor(() => expect(screen.getByRole('button', { name: 'Create objective' })).toHaveProperty('disabled', false))
    fireEvent.click(screen.getByRole('button', { name: 'Create objective' }))
    await waitFor(() => expect(request.mock.calls.filter(call => call[0] === 'organization.create')).toHaveLength(2))
    expect(request.mock.calls.filter(call => call[0] === 'organization.create')[1][1].idempotencyKey).toBe(receipt)
    await screen.findByText('Still unavailable')

    // A confirmed endpoint change is a new owner, unlike transient null.
    act(() => {$connection.set({ ...connection(), baseUrl: 'https://changed.example' } as never)})
    expect(screen.getByRole('textbox', { name: 'Objective' })).toHaveProperty('value', '')
    expect(screen.getByRole('textbox', { name: 'Submitted context (optional)' })).toHaveProperty('value', '')
  } finally {
    view.unmount()
    window.hermesDesktop = previousDesktop
  }
})
