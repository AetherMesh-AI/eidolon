import { atom } from 'nanostores'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { DesktopConnectionsRegistry, HermesConnection } from '@/global'

import { deferred } from '../test/deferred'

const ensureGatewayForAgent = vi.fn(async (_connectionId: null | string, _profile: string) => true)
const getProfiles = vi.fn(async () => ({ profiles: [] }))
const $gateway = atom<unknown>({ connectionState: 'open' })
vi.mock('@/store/gateway', () => ({
  $gateway,
  activeGatewayConnectionId: () => 'homelab',
  ensureGatewayForAgent,
  ensureGatewayForProfile: vi.fn(async () => undefined),
  openGatewayForAgent: vi.fn(async () => undefined),
  openGatewayForProfile: vi.fn(async () => undefined)
}))
vi.mock('@/eidolon', () => ({ getProfiles, setApiRequestProfile: vi.fn() }))
vi.mock('@/lib/query-client', () => ({ invalidateProfileScopedQueries: vi.fn() }))
vi.mock('@/store/starmap', () => ({ resetStarmapGraph: vi.fn() }))
vi.mock('@/store/gateway-switch', () => ({
  beginGatewaySwitch: vi.fn(() => 1),
  endGatewaySwitch: vi.fn(),
  recoverActiveSourceAfterFailedGatewaySwitch: vi.fn()
}))

const { $connection } = await import('./session')

const {
  $activeGatewayProfile, $newChatProfile, $showAllProfiles, $freshSessionRequest,
  selectProfile, newSessionInProfile, newSessionInAgent, setShowAllProfiles, ensureGatewayAgent
} = await import('./profile')

const { selectConnection, setConnectionsRegistry, $connectionsRegistry, _resetConnectionsForTests } = await import('./connections')

const registry: DesktopConnectionsRegistry = {
  connections: [{ id: 'homelab', kind: 'remote', label: 'Homelab', tokenPreview: null, tokenSet: true }],
  primary: 'homelab', secureTokenStorage: true, version: 2
}

const setLastUsed = vi.fn(async () => ({ ok: true, registry }))

beforeEach(() => {
  _resetConnectionsForTests()
  setConnectionsRegistry(registry)
  ensureGatewayForAgent.mockReset().mockResolvedValue(true)
  getProfiles.mockClear()
  setLastUsed.mockReset()
  $connection.set(null)
  $activeGatewayProfile.set('default')
  $newChatProfile.set(null)
  $showAllProfiles.set(false)
  vi.stubGlobal('window', { localStorage: window.localStorage, hermesDesktop: {
    connections: { setLastUsed },
    getConnectionFor: async ({ connectionId, profile }: { connectionId: string; profile: string }) =>
      ({ connectionId, profile, registryScoped: true, mode: 'remote', baseUrl: 'https://example.invalid' }) as HermesConnection
  } })
})
afterEach(() => vi.unstubAllGlobals())

describe('connection bookkeeping versus real profile intent doors', () => {
  it.each(['selectProfile', 'newSessionInProfile', 'newSessionInAgent'] as const)(
    'a pending %s owns the draft before its route publishes', async door => {
      const remembered = deferred<Awaited<ReturnType<typeof setLastUsed>>>()
      setLastUsed.mockReturnValueOnce(remembered.promise)
      const first = selectConnection('homelab', { profile: 'research' })
      await vi.waitFor(() => expect(setLastUsed).toHaveBeenCalledOnce())
      const dial = deferred<boolean>()
      ensureGatewayForAgent.mockReturnValueOnce(dial.promise)

      if (door === 'selectProfile') {selectProfile('delivery')}
      else if (door === 'newSessionInProfile') {newSessionInProfile('delivery')}
      else {newSessionInAgent({ connectionId: 'homelab', profile: 'delivery' })}

      expect($newChatProfile.get()).toBe('delivery')
      expect($connection.get()?.profile).toBe('research')
      const freshRequests = $freshSessionRequest.get()
      $showAllProfiles.set(true)
      remembered.resolve({ ok: true, registry: { ...registry, lastUsed: 'homelab' } })

      try {
        await first
        expect($newChatProfile.get()).toBe('delivery')
        expect($showAllProfiles.get()).toBe(true)
        expect($freshSessionRequest.get()).toBe(freshRequests)
        expect($connectionsRegistry.get()).toBe(registry)
        expect(getProfiles).not.toHaveBeenCalled()
      } finally {
        dial.resolve(true)
        await vi.waitFor(() => expect($activeGatewayProfile.get()).toBe('delivery'))
      }
    }
  )

  it.each(['activation', 'profile selection'] as const)('A→B→A through %s cannot revive an older continuation', async door => {
    const remembered = deferred<Awaited<ReturnType<typeof setLastUsed>>>()
    setLastUsed.mockReturnValueOnce(remembered.promise)
    const first = selectConnection('homelab', { profile: 'research' })
    await vi.waitFor(() => expect(setLastUsed).toHaveBeenCalledOnce())

    if (door === 'activation') {
      await ensureGatewayAgent('homelab', 'delivery')
      await ensureGatewayAgent('homelab', 'research')
    } else {
      selectProfile('delivery')
      await vi.waitFor(() => expect($activeGatewayProfile.get()).toBe('delivery'))
      selectProfile('research')
      await vi.waitFor(() => expect($activeGatewayProfile.get()).toBe('research'))
    }

    expect($connection.get()?.profile).toBe('research')
    const freshRequests = $freshSessionRequest.get()
    $showAllProfiles.set(true)
    remembered.resolve({ ok: true, registry: { ...registry, lastUsed: 'homelab' } })
    await first
    expect($connectionsRegistry.get()).toBe(registry)
    expect($showAllProfiles.get()).toBe(true)
    expect($freshSessionRequest.get()).toBe(freshRequests)
    expect(getProfiles).not.toHaveBeenCalled()
  })

  it('an explicit browse-mode pick also invalidates old finalization', async () => {
    const remembered = deferred<Awaited<ReturnType<typeof setLastUsed>>>()
    setLastUsed.mockReturnValueOnce(remembered.promise)
    const first = selectConnection('homelab', { profile: 'research' })
    await vi.waitFor(() => expect(setLastUsed).toHaveBeenCalledOnce())
    setShowAllProfiles(true)
    remembered.resolve({ ok: true, registry: { ...registry, lastUsed: 'homelab' } })
    await first
    expect($showAllProfiles.get()).toBe(true)
    expect($connectionsRegistry.get()).toBe(registry)
    expect(getProfiles).not.toHaveBeenCalled()
  })
})
