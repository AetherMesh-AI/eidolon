import { atom } from 'nanostores'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const dial = vi.hoisted(() => ({ gate: null as Promise<void> | null, fail: false }))

vi.mock('@/store/settings-scope', () => ({ setSettingsScope: vi.fn() }))
vi.mock('@/store/notify-baseline', () => ({ markNativeNotifyBaseline: vi.fn() }))
vi.mock('@/eidolon', () => ({
  HermesGateway: class {
    connectionState = 'closed'
    async connect() {
      if (dial.gate) {await dial.gate}

      if (dial.fail) {throw new Error('Dial failed')}
      this.connectionState = 'open'
    }
    close() { this.connectionState = 'closed' }
    onEvent() { return () => {} }
    onState() { return () => {} }
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
import { $organizationObservationSwitching } from '@/store/organization-observation-mode'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection, $gatewayState } from '@/store/session'

import { createOrganizationScopeReader } from './runtime-provider'

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
