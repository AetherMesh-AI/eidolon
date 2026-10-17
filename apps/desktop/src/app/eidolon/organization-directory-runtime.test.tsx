import { act, render as baseRender, fireEvent, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { type ReactNode, StrictMode } from 'react'
import { MemoryRouter } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { organizationDirectoryCopy } from '@/i18n/organization-directory'
import { $connectionsRegistry, $pendingConnectionId } from '@/store/connections'
import { $activeGatewayRoute, $gateway } from '@/store/gateway'
import { $organizationObservationEpoch, $organizationObservationSwitching } from '@/store/organization-observation-mode'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection, $gatewayState } from '@/store/session'

import { deferred } from '../../test/deferred'

import { organizationActivationVerifier } from './organization-creation-runtime'
import { OrganizationDirectoryControl } from './organization-directory-control'
import { createRuntimeOrganizationDirectory } from './organization-directory-runtime'

const render = (ui: ReactNode) => baseRender(ui, { wrapper: MemoryRouter })

vi.mock('@/store/connections', () => ({ $pendingConnectionId: atom(null), $connectionsRegistry: atom(null) }))
vi.mock('@/store/gateway', () => ({
  $gateway: atom(null), $localOwnerProofRevision: atom(0),
  $activeGatewayRoute: atom('default'),
  activeGatewayConnectionId: () => 'a',
  gatewayTransportGeneration: (gateway: { generation?: number }) => gateway.generation ?? 1
}))
vi.mock('@/store/profile', () => ({ $activeGatewayProfile: atom('default'), $gatewaySwapTarget: atom(null), normalizeProfileKey: (name: string | null | undefined) => name || 'default' }))
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
  $gateway.set({ request, connectionState: 'open' } as never)
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

it.each(['en', 'ja', 'zh', 'zh-hant', 'ar', 'ru'] as const)('exposes the authored organization and localized directory action in %s without changing eligibility', async locale => {
  const label = 'Aether Research · أبحاث'
  const action = organizationDirectoryCopy[locale].heading
  const ui = render(<I18nProvider configClient={null} initialLocale={locale}><OrganizationDirectoryControl label={label} organizationName={label} /></I18nProvider>)
  const trigger = screen.getByRole('button', { name: `${label} · ${action}` }) as HTMLButtonElement
  expect(trigger.textContent).toContain(label)
  expect(trigger.querySelector('.eid-workspace-organization-monogram')?.getAttribute('aria-hidden')).toBe('true')
  expect(trigger.querySelector('.eid-workspace-organization-monogram')?.textContent).toBe('AR')
  expect(trigger.disabled).toBe(false)
  fireEvent.click(trigger)
  expect(screen.getByRole('dialog', { name: action })).toBeTruthy()
  expect(request).not.toHaveBeenCalled()
  await act(async () => { $organizationObservationSwitching.set(true) })
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(trigger.disabled).toBe(true)
  expect(request).not.toHaveBeenCalled()
  ui.rerender(<I18nProvider configClient={null} initialLocale={locale}><OrganizationDirectoryControl /></I18nProvider>)
  expect(screen.getByRole('button', { name: action }).querySelector('.eid-workspace-organization-monogram')?.textContent).toBe('')
  expect(request).not.toHaveBeenCalled()
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


it('makes creation discoverable and fails closed on an unsupported runtime without changing the owner', async () => {
  $connection.set({ connectionId: 'a', profile: 'default', mode: 'local', baseUrl: 'http://127.0.0.1:6161' } as never)
  $connectionsRegistry.set({ ...$connectionsRegistry.get()!, connections: [{ id: 'a', kind: 'local', label: 'This device', tokenSet: false, tokenPreview: null }] })
  request.mockRejectedValue({ code: -32601 })
  const original = $connection.get()
  const ui = render(<StrictMode><OrganizationDirectoryControl /></StrictMode>)
  fireEvent.click(screen.getByRole('button', { name: 'Organizations' }))
  fireEvent.click(screen.getByRole('button', { name: 'Create organization' }))
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('not supported'))
  expect(request.mock.calls.map(call => call[0])).toEqual(['organization.workspaceCreation'])
  expect($connection.get()).toBe(original)
  expect(screen.queryByRole('button', { name: 'Switch to this organization' })).toBeNull()
  ui.unmount()
})

it('proves the passed socket and drops descriptor replies when the original owner transport or registry changes', async () => {
  for (const change of ['none', 'socket', 'generation', 'registry', 'connection', 'closed']) {
    const original = { request, generation: 1, connectionState: 'open' }
    $gateway.set(original as never)
    const proof = deferred<unknown>()
    const target = { request: vi.fn().mockReturnValue(proof.promise) }
    const verified = vi.fn()
    const verify = organizationActivationVerifier(verified)
    const signal = new AbortController().signal
    const checking = verify(target as never, {} as never, signal)

    if (change === 'socket') { $gateway.set({ request, connectionState: 'open' } as never) }

    if (change === 'generation') { original.generation++ }

    if (change === 'closed') { original.connectionState = 'closed' }

    if (change === 'registry') { $connectionsRegistry.set({ ...$connectionsRegistry.get()! }) }

    if (change === 'connection') { $connection.set({ ...$connection.get()! }) }
    proof.resolve(descriptor)

    if (change === 'none') { const finalGuard = await checking; expect(verified).toHaveBeenCalledWith(descriptor); expect(finalGuard && finalGuard()).toBe(true); original.connectionState = 'closed'; expect(finalGuard && finalGuard()).toBe(false) }
    else { await expect(checking).rejects.toThrow('source changed'); expect(verified).not.toHaveBeenCalled() }

    expect(target.request).toHaveBeenCalledExactlyOnceWith('organization.descriptor', {}, 15000, signal)
  }

  expect(request).not.toHaveBeenCalled()
})
