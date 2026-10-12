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
vi.mock('@/eidolon', () => ({ getProfiles, setApiRequestProfile: vi.fn(), resetSidebarBatchCapability: vi.fn() }))
vi.mock('@/lib/query-client', () => ({ invalidateProfileScopedQueries: vi.fn() }))
vi.mock('@/app/contrib/hooks/use-background-sync', () => ({ resetLiveRuntimeTracking: vi.fn() }))
vi.mock('@/store/starmap', () => ({ resetStarmapGraph: vi.fn() }))
const { $connection, $sessions, $sessionsLoading, setSessions, setSessionsLoading } = await import('./session')
const { $gatewaySwitching, beginGatewaySwitch, endGatewaySwitch, registerGatewaySwitchLifecycle } = await import('./gateway-switch')

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
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('connection bookkeeping versus real profile intent doors', () => {
  it.each([
    ['failure', false], ['nonlanding', false], ['timeout', false],
    ['failure', true], ['nonlanding', true], ['timeout', true]
  ] as const)(
    'reset recovery after %s follows the reset owner (newer reset=%s)', async (outcome, newerReset) => {
      vi.useFakeTimers()
      $connection.set({ connectionId: 'local', profile: 'default', mode: 'local', baseUrl: '' } as HermesConnection)
      $showAllProfiles.set(true)
      setSessions([{ id: 'original-session', profile: 'default' } as never])
      setSessionsLoading(false)

      const refresh = vi.fn(async (shouldPublish?: () => boolean) => {
        expect($connection.get()?.connectionId).toBe('local')

        if (shouldPublish?.()) { setSessions([{ id: 'recovered-session', profile: 'default' } as never]) }
      })

      const off = registerGatewaySwitchLifecycle({ beforeConnectionSwitch: vi.fn(), refreshSessions: refresh })
      const activation = deferred<boolean>()
      ensureGatewayForAgent.mockReturnValueOnce(activation.promise)
      const freshRequests = $freshSessionRequest.get()
      const switching = selectConnection('homelab', { profile: 'research' })
      let newerToken: number | undefined

      try {
        await vi.advanceTimersByTimeAsync(0)
        expect($gatewaySwitching.get()).toBe(true)
        expect($sessions.get()).toEqual([])
        expect($sessionsLoading.get()).toBe(true)
        // Same value and route: only foreground intent changes, not reset ownership.
        setShowAllProfiles(true)

        if (newerReset) { newerToken = beginGatewaySwitch() }

        if (outcome === 'failure') { activation.reject(new Error('Activation failed')) }
        else if (outcome === 'nonlanding') { activation.resolve(false) }
        else { await vi.advanceTimersByTimeAsync(20_000) }

        await expect(switching).resolves.toBeUndefined()
        await vi.advanceTimersByTimeAsync(0)
        expect(refresh).toHaveBeenCalledTimes(newerReset ? 0 : 1)
        expect($sessions.get().map(session => session.id)).toEqual(newerReset ? [] : ['recovered-session'])
        expect($sessionsLoading.get()).toBe(newerReset)
        expect($gatewaySwitching.get()).toBe(newerReset)
        expect($showAllProfiles.get()).toBe(true)
        expect($freshSessionRequest.get()).toBe(freshRequests)
        expect($newChatProfile.get()).toBeNull()
        expect(setLastUsed).not.toHaveBeenCalled()
      } finally {
        activation.resolve(false)
        await vi.advanceTimersByTimeAsync(0)

        if (newerToken !== undefined) { endGatewaySwitch(newerToken) }
        setSessionsLoading(false)
        off()
      }
    }
  )

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

  it('accepts a fresh connection pick back to A while its invalidated bookkeeping is pending', async () => {
    const remembered = deferred<Awaited<ReturnType<typeof setLastUsed>>>()
    setLastUsed.mockResolvedValue({ ok: true, registry })
    setLastUsed.mockReturnValueOnce(remembered.promise)
    const first = selectConnection('homelab', { profile: 'research' })
    await vi.waitFor(() => expect(setLastUsed).toHaveBeenCalledOnce())
    const dial = deferred<boolean>()
    ensureGatewayForAgent.mockReturnValueOnce(dial.promise)
    selectProfile('delivery')
    const latest = selectConnection('homelab', { profile: 'research' })
    remembered.resolve({ ok: true, registry: { ...registry, lastUsed: 'homelab' } })
    await first
    dial.resolve(true)
    await latest
    await vi.waitFor(() => expect(setLastUsed).toHaveBeenCalledTimes(2))
    expect($connection.get()?.profile).toBe('research')
    expect($newChatProfile.get()).toBe('research')
    expect(getProfiles).toHaveBeenCalledOnce()
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
