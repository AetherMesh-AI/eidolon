import { act, cleanup, renderHook } from '@testing-library/react'
import { atom } from 'nanostores'
import { createElement, type ReactNode } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const dial = vi.hoisted(() => ({ gate: null as Promise<void> | null, fail: false, request: vi.fn() }))

vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({ useGatewayRequest: () => ({ requestGateway: dial.request }) }))
vi.mock('@/store/session-states', () => ({ resetTileRuntimeBindings: vi.fn(), reconcileBusyStatesOnReconnect: vi.fn() }))
vi.mock('@/store/connections', () => ({ $pendingConnectionId: atom(null), $connectionsRegistry: atom(null) }))
vi.mock('@/store/settings-scope', () => ({ setSettingsScope: vi.fn() }))
vi.mock('@/store/notify-baseline', () => ({ markNativeNotifyBaseline: vi.fn() }))
vi.mock('@/eidolon', () => ({
  HermesGateway: class {
    connectionState = 'closed'
    listeners = new Set<(state: string) => void>()
    emit(state: string) { this.connectionState = state; this.listeners.forEach(listener => listener(state)) }
    request = dial.request
    async connect() {
      this.emit('open')

      if (dial.gate) {await dial.gate}

      if (dial.fail) {throw new Error('Dial failed')}
      this.connectionState = 'open'
    }
    close() { this.emit('closed') }
    onEvent() { return () => {} }
    onState(listener: (state: string) => void) { this.listeners.add(listener);

 return () => this.listeners.delete(listener) }
  },
  setApiRequestConnection: vi.fn()
}))
vi.mock('@/store/session', () => {
  const $connection = atom(null)
  const $gatewayState = atom('open')

  return {
    $connection, $gatewayState,
    setConnection: (value: null) => $connection.set(value),
    setGatewayState: (value: string) => $gatewayState.set(value)
  }
})
vi.mock('@/store/profile', () => ({ $activeGatewayProfile: atom('default'), $gatewaySwapTarget: atom(null) }))

import type { HermesConnection } from '@/global'
import {
  $gateway, activeGatewayConnectionId, closeSecondaryGateways, configureGatewayRegistry,
  ensureActiveGatewayOpen, ensureGatewayForAgent, ensureGatewayForProfile, setPrimaryGateway
} from '@/store/gateway'
import { $organizationObservationEpoch, $organizationObservationSwitching } from '@/store/organization-observation-mode'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection, $gatewayState } from '@/store/session'

import { subscribeOrganizationDirectorySource } from './organization-directory-runtime'
import { createOrganizationScopeReader, OrganizationRuntimeProvider, useRuntimeOrganization } from './runtime-provider'
import { useOrganizationMetadata } from './use-organization-metadata'

const descriptor = (profile = 'a', token = 'first') => ({
  connectionId: 'local', mode: 'local', profile, baseUrl: 'http://127.0.0.1:4321',
  wsUrl: `ws://127.0.0.1:4321/ws?token=${token}`, token
}) as HermesConnection

let resolved: HermesConnection
const read = createOrganizationScopeReader()

beforeEach(() => {
  closeSecondaryGateways()
  dial.gate = null
  dial.fail = false
  dial.request.mockReset()
  $organizationObservationEpoch.set(0)
  configureGatewayRegistry({ onEvent: vi.fn() })
  setPrimaryGateway({ connectionState: 'open' } as never, 'default')
  resolved = descriptor()
  window.hermesDesktop = {
    getConnection: vi.fn(async () => ({ ...resolved })),
    getConnectionFor: vi.fn(async () => ({ ...resolved, registryScoped: true })),
    touchBackend: vi.fn(async () => undefined)
  } as never
  $connection.set(null)
  $activeGatewayProfile.set('default')
  $gatewaySwapTarget.set(null)
  $gatewayState.set('open')
  $organizationObservationSwitching.set(false)
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  closeSecondaryGateways()
  delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
})

async function activate(profile = 'a') {
  await ensureGatewayForProfile(profile)
  $activeGatewayProfile.set(profile)
  $connection.set({ ...resolved })
}

it('retains the actual local owner route without changing legacy session routing', async () => {
  await activate()
  expect(activeGatewayConnectionId()).toBeNull()
  const original = read()
  expect(original.ownerRoute).toEqual({ connectionId: 'local', profile: 'a' })
  await ensureGatewayForAgent('local', 'a')
  expect(read().ownerKey).toBe(original.ownerKey)
  expect(read().ownerRoute).toEqual(original.ownerRoute)
  await activate()
  expect(activeGatewayConnectionId()).toBeNull()
  expect(read().ownerKey).toBe(original.ownerKey)
  expect(read().ownerRoute).toEqual(original.ownerRoute)
})

it.each([
  ['profile', { profile: 'b' }], ['source', { connectionId: 'other' }],
  ['endpoint', { baseUrl: 'http://127.0.0.1:9999' }], ['auth generation', { token: 'rotated' }],
  ['shared primary', { sharedPrimary: true }], ['shared remote', { sharedRemote: true }],
  ['remote alias', { mode: 'remote', profile: 'server-alias' }]
])('rejects a mismatched published %s descriptor', async (_name, change) => {
  await activate()
  $connection.set({ ...resolved, ...change } as HermesConnection)
  expect(read().ownerRoute).toBeUndefined()
})

it('does not promote an unknown or remote legacy socket from a local-looking published descriptor', async () => {
  resolved = { ...descriptor(), connectionId: undefined }
  await activate()
  $connection.set(descriptor())
  expect(read().ownerRoute).toBeUndefined()
  closeSecondaryGateways()
  resolved = { ...descriptor(), mode: 'remote', profile: 'server-alias' }
  await activate()
  $connection.set(descriptor())
  expect(read().ownerRoute).toBeUndefined()
})

it('fences switches, closed sockets, and a failed-open stale profile descriptor', async () => {
  await activate()
  $gatewaySwapTarget.set('b')
  expect(read().ownerRoute).toBeUndefined()
  $gatewaySwapTarget.set(null)
  $organizationObservationSwitching.set(true)
  expect(read().ownerRoute).toBeUndefined()
  $organizationObservationSwitching.set(false)
  const old = resolved
  resolved = descriptor('b', 'second')
  await activate('b')
  $connection.set(old)
  expect(read().ownerRoute).toBeUndefined()
  $connection.set(resolved)
  $gateway.get()!.close()
  expect(read().ownerRoute).toBeUndefined()
})

it('rejects the old descriptor after an A-B-A socket generation change', async () => {
  await activate()
  const oldA = { ...resolved }
  resolved = descriptor('b', 'second')
  await activate('b')
  closeSecondaryGateways()
  resolved = descriptor('a', 'third')
  await activate('a')
  $connection.set(oldA)
  expect(read().ownerRoute).toBeUndefined()
  $connection.set({ ...resolved })
  expect(read().ownerRoute).toEqual({ connectionId: 'local', profile: 'a' })
})


it('revokes the old proof while a new descriptor generation is dialing and after failed authentication', async () => {
  await activate()
  const old = { ...resolved }
  $gateway.get()!.close()
  resolved = descriptor('a', 'replacement')
  let release!: () => void
  dial.gate = new Promise<void>(resolve => { release = resolve })
  const reconnect = ensureActiveGatewayOpen()
  await vi.waitFor(() => expect(window.hermesDesktop.getConnection).toHaveBeenCalledTimes(2))
  $connection.set(old)
  expect(read().ownerRoute).toBeUndefined()
  dial.fail = true
  release()
  await reconnect.catch(() => undefined)
  $connection.set(resolved)
  expect(read().ownerRoute).toBeUndefined()
})


it('keeps missing descriptors and a missing socket-bound profile unidentified', async () => {
  await activate()
  $connection.set(null)
  expect(read().ownerRoute).toBeUndefined()
  closeSecondaryGateways()
  resolved = { ...descriptor(), profile: undefined }
  await activate()
  $connection.set(descriptor())
  expect(read().ownerRoute).toBeUndefined()
})

it('recovers mounted observation, metadata and directory after open-before-connect settlement and keeps polling', async () => {
  vi.useFakeTimers()
  // Deliberately reuse the descriptor and socket: neither atom can announce settlement.
  window.hermesDesktop.getConnection = vi.fn(async () => resolved)
  await activate()
  $connection.set(resolved)
  $organizationObservationEpoch.set(1)

  const observation = (title: string) => ({
    capabilityVersion: 1, state: 'available', scope: { kind: 'served-profile', profile: 'a' },
    snapshot: {
      source: 'runtime', objectives: [{ id: title, title, description: '', ownerId: 'manager', createdAt: '2026-10-04T00:00:00Z', status: 'planning', source: 'runtime' }],
      agents: [], tasks: [], requests: [], activity: [], knowledge: [],
      runtime: { state: 'ready', maxWorkers: 2, capabilities: [], scope: 'Submitted context only' }
    }
  })

  let holdObservation = false
  let lateResponse!: (value: unknown) => void
  dial.request.mockImplementation(async (method: string) => {
    if (method === 'organization.descriptor') {
      return { capabilityVersion: 1, state: 'unconfigured', metadata: null, scope: { kind: 'served-profile', profile: 'a' } }
    }

    expect(method).toBe('organization.observe')

    if (holdObservation) {
      holdObservation = false

      return new Promise(resolve => { lateResponse = resolve })
    }

    return observation('Current generation')
  })

  const mounted = renderHook(() => ({ runtime: useRuntimeOrganization(), metadata: useOrganizationMetadata() }), {
    wrapper: ({ children }: { children: ReactNode }) => createElement(OrganizationRuntimeProvider, null, children)
  })

  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  expect(mounted.result.current.runtime?.snapshot.connection?.state).toBe('ready')
  expect(mounted.result.current.runtime?.snapshot.connection?.ownerRoute).toEqual({ connectionId: 'local', profile: 'a' })
  const directoryScopes: ReturnType<typeof read>[] = []
  const offDirectory = subscribeOrganizationDirectorySource(() => directoryScopes.push(read()))
  const originalSocket = $gateway.get()!
  let releaseConnect!: () => void
  dial.gate = new Promise(resolve => { releaseConnect = resolve })
  holdObservation = true
  await act(async () => {
    originalSocket.close()
    // The real registry backoff starts recovery; the mounted view never refreshes manually.
    await vi.advanceTimersByTimeAsync(300)
  })
  expect(originalSocket.connectionState).toBe('open')
  expect(read().ownerRoute).toBeUndefined()
  expect(lateResponse).toBeTypeOf('function')
  expect(directoryScopes.at(-1)?.ownerRoute).toBeUndefined()
  const descriptorsBeforeSettlement = dial.request.mock.calls.filter(([method]) => method === 'organization.descriptor').length
  await act(async () => { releaseConnect(); await vi.advanceTimersByTimeAsync(0) })
  expect($gateway.get()).toBe(originalSocket)
  expect($connection.get()).toBe(resolved)
  expect(mounted.result.current.runtime?.snapshot.connection?.state).toBe('ready')
  expect(mounted.result.current.runtime?.snapshot.connection?.ownerRoute).toEqual({ connectionId: 'local', profile: 'a' })
  expect(directoryScopes.at(-1)?.ownerRoute).toEqual({ connectionId: 'local', profile: 'a' })
  expect(dial.request.mock.calls.filter(([method]) => method === 'organization.descriptor').length).toBeGreaterThan(descriptorsBeforeSettlement)
  // A response started while proof was withheld cannot replace the recovered view.
  await act(async () => { lateResponse(observation('Stale generation')) })
  expect(mounted.result.current.runtime?.snapshot.objectives[0]?.title).toBe('Current generation')
  const observations = dial.request.mock.calls.filter(([method]) => method === 'organization.observe').length
  await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
  expect(dial.request.mock.calls.filter(([method]) => method === 'organization.observe')).toHaveLength(observations + 1)
  expect(mounted.result.current.runtime?.snapshot.connection?.state).toBe('ready')
  offDirectory()
})

it('does not announce a retired dial settling over a replacement of the same local route', async () => {
  vi.useFakeTimers()
  await activate()
  let release!: () => void
  dial.gate = new Promise(resolve => { release = resolve })
  $gateway.get()!.close()
  const retired = ensureActiveGatewayOpen()
  await vi.advanceTimersByTimeAsync(0)
  expect(read().ownerRoute).toBeUndefined()
  closeSecondaryGateways()
  dial.gate = null
  resolved = descriptor('a', 'replacement')
  await activate()
  const replacement = read()
  const changed = vi.fn()
  const stop = subscribeOrganizationDirectorySource(changed)
  release()
  await retired
  expect(read()).toEqual(replacement)
  expect(changed).not.toHaveBeenCalled()
  stop()
})
