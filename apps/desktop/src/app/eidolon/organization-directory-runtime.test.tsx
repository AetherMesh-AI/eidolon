import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { StrictMode } from 'react'
import { beforeEach, expect, it, vi } from 'vitest'

import { $connectionsRegistry, $pendingConnectionId } from '@/store/connections'
import { $activeGatewayRoute, $gateway } from '@/store/gateway'
import { $organizationObservationEpoch, $organizationObservationSwitching } from '@/store/organization-observation-mode'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection, $gatewayState } from '@/store/session'

import { deferred } from '../../test/deferred'

import { OrganizationDirectoryControl } from './organization-directory-control'
import { createRuntimeOrganizationDirectory } from './organization-directory-runtime'

vi.mock('@/store/connections', () => ({ $pendingConnectionId: atom(null), $connectionsRegistry: atom(null) }))
vi.mock('@/store/gateway', () => ({
  $gateway: atom(null),
  $activeGatewayRoute: atom('default'),
  activeGatewayConnectionId: () => 'a'
}))
vi.mock('@/store/profile', () => ({ $activeGatewayProfile: atom('default'), $gatewaySwapTarget: atom(null) }))
vi.mock('@/store/session', () => ({ $connection: atom(null), $gatewayState: atom('closed') }))
vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({
  useGatewayRequest: () => {
    throw new Error('Must not reconnect')
  }
}))
vi.mock('./runtime-adapter', () => ({
  createRuntimeAdapter: () => {
    throw new Error('Must not start runtime observation')
  }
}))

const descriptor = {
  capabilityVersion: 1,
  state: 'unconfigured',
  metadata: null,
  scope: { kind: 'served-profile', profile: 'default' }
}

let request = vi.fn()
beforeEach(() => {
  request = vi.fn().mockResolvedValue(descriptor)
  $gateway.set({ request } as never)
  $gatewayState.set('open')
  $connection.set({ connectionId: 'a', profile: 'default', mode: 'remote', baseUrl: 'https://a.example/rpc' } as never)
  $connectionsRegistry.set({
    version: 2,
    primary: 'a',
    secureTokenStorage: true,
    connections: [
      { id: 'a', kind: 'remote', label: 'Lab', url: 'https://a.example/rpc', tokenSet: true, tokenPreview: null }
    ]
  })
  $activeGatewayRoute.set('default')
  $activeGatewayProfile.set('default')
  $gatewaySwapTarget.set(null)
  $pendingConnectionId.set(null)
  $organizationObservationEpoch.set(0)
  $organizationObservationSwitching.set(false)
})

it('observes registry and scope without starting any requests, then explicitly reads only the captured descriptor', async () => {
  const runtime = createRuntimeOrganizationDirectory()
  runtime.directory.getSnapshot()
  expect(request).not.toHaveBeenCalled()
  expect(await runtime.directory.refresh()).toBe('verified')
  expect(request.mock.calls).toEqual([['organization.descriptor', {}, 15000]])
  runtime.directory.dispose()
})

it.each(['query', 'credential', 'authMode'] as const)(
  'revokes same-redacted-endpoint authority and drops late responses after %s edits',
  async change => {
    const runtime = createRuntimeOrganizationDirectory()
    await runtime.directory.refresh()
    const before = runtime.readSource().current!.scope.key
    const old = deferred<unknown>()
    request.mockReturnValueOnce(old.promise)
    const pending = runtime.directory.refresh()
    const registry = $connectionsRegistry.get()!
    $connectionsRegistry.set({
      ...registry,
      connections: registry.connections.map(item => ({
        ...item,
        ...(change === 'query'
          ? { url: 'https://a.example/rpc?token=private-query' }
          : change === 'credential'
            ? { url: 'https://user:fake-secret@a.example/rpc' }
            : { authMode: 'oauth' as const })
      }))
    })
    expect(runtime.readSource().current!.scope.key).not.toBe(before)
    expect(runtime.directory.getSnapshot().entries[0].freshness).toBe('stale')
    old.resolve(descriptor)
    expect(await pending).toBe('superseded')
    expect(runtime.directory.getSnapshot().entries[0].freshness).toBe('stale')
    expect(JSON.stringify(runtime.directory.getSnapshot())).not.toMatch(
      /fake-secret|private-query|user:|tokenPreview|authMode/
    )
    expect(request).toHaveBeenCalledTimes(2)
    runtime.directory.dispose()
  }
)

it.each([
  'sharedPrimary',
  'sharedRemote',
  'profileMismatch',
  'pending',
  'observationSwitch',
  'endpoint',
  'removed'
] as const)('refuses current descriptor reads on %s', async reason => {
  const runtime = createRuntimeOrganizationDirectory()

  if (reason === 'sharedPrimary' || reason === 'sharedRemote') {
    $connection.set({ ...$connection.get()!, [reason]: true })
  }

  if (reason === 'profileMismatch') {
    $activeGatewayProfile.set('foreign')
  }

  if (reason === 'pending') {
    $pendingConnectionId.set('b')
  }

  if (reason === 'observationSwitch') {
    $organizationObservationSwitching.set(true)
  }

  if (reason === 'endpoint') {
    $connectionsRegistry.set({
      ...$connectionsRegistry.get()!,
      connections: [{ ...$connectionsRegistry.get()!.connections[0], url: 'https://changed.example/rpc' }]
    })
  }

  if (reason === 'removed') {
    $connectionsRegistry.set({ ...$connectionsRegistry.get()!, connections: [] })
  }

  expect(await runtime.directory.refresh()).toBe('unavailable')
  expect(request).not.toHaveBeenCalled()
  runtime.directory.dispose()
})


it('mounts inspection safely in StrictMode, refreshes explicitly and restores real trigger focus', async () => {
  const ui = render(<StrictMode><OrganizationDirectoryControl /></StrictMode>)
  const trigger = screen.getByRole('button', { name: 'Organizations' })
  fireEvent.click(trigger)
  expect(screen.getByRole('dialog', { name: 'Organizations' })).toBeTruthy()
  expect((screen.getByRole('button', { name: 'Use selected organization' }) as HTMLButtonElement).disabled).toBe(true)
  expect(request).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Refresh current organization' }))
  await waitFor(() => expect(screen.getByRole('option')).toBeTruthy())
  expect(request).toHaveBeenCalledExactlyOnceWith('organization.descriptor', {}, 15000)
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
  await waitFor(() => expect(globalThis.document.activeElement).toBe(trigger))
  fireEvent.click(trigger)
  expect(screen.getByRole('option')).toBeTruthy()
  expect(request).toHaveBeenCalledTimes(1)
  ui.unmount()
})

it('closes inspection and suppresses deferred trigger focus when a source switch begins', async () => {
  const ui = render(<OrganizationDirectoryControl />)
  const trigger = screen.getByRole('button', { name: 'Organizations' })
  fireEvent.click(trigger)
  vi.useFakeTimers()

  try {
    await act(async () => {
      $organizationObservationSwitching.set(true)
      await vi.runOnlyPendingTimersAsync()
    })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect((trigger as HTMLButtonElement).disabled).toBe(true)
    expect(globalThis.document.activeElement).not.toBe(trigger)
    expect(request).not.toHaveBeenCalled()
  } finally { vi.useRealTimers(); ui.unmount() }
})
