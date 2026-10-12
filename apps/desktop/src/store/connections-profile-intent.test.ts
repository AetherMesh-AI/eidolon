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

const { selectConnectionWithOutcome, selectConnection, setConnectionsRegistry, $connectionsRegistry, _resetConnectionsForTests } = await import('./connections')

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
    ['failure', false, false], ['nonlanding', false, false], ['timeout', false, false],
    ['failure', true, false], ['nonlanding', true, false], ['timeout', true, false],
    ['failure', false, true], ['nonlanding', false, true], ['timeout', false, true],
    ['failure', true, true], ['nonlanding', true, true], ['timeout', true, true]
  ] as const)(
    'reset recovery after %s follows the reset owner (newer reset=%s, result API=%s)', async (outcome, newerReset, resultApi) => {
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
      const switching = resultApi ? selectConnectionWithOutcome('homelab', { profile: 'research' }) : selectConnection('homelab', { profile: 'research' })
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

        if (resultApi) {await expect(switching).resolves.toMatchObject({ status: 'superseded', resetStarted: true, published: false })}
        else {await expect(switching).resolves.toBeUndefined()}

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
    const first = selectConnectionWithOutcome('homelab', { profile: 'research' })
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
    expect(await first).toMatchObject({ status: 'superseded', published: true, observedRoute: { connectionId: 'homelab', profile: 'research' } })
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

it('organization selection reports a real reset callback failure without leaving observation suspended', async () => {
  const { selectOrganizationConnection } = await import('./organization-switch')
  const { $organizationObservationEpoch, $organizationObservationSwitching } = await import('./organization-observation-mode')
  $connection.set({ connectionId: 'local', profile: 'default', mode: 'local', baseUrl: '' } as HermesConnection)
  const off = registerGatewaySwitchLifecycle({ beforeConnectionSwitch: () => {throw new Error('reset failed')}, refreshSessions: vi.fn() })

  try {
    const result = await selectOrganizationConnection('homelab', 'research')
    expect(result).toMatchObject({ status: 'unchanged', reason: 'failed', phase: 'activation', resetStarted: true, published: false, observedRoute: { connectionId: 'local', profile: 'default' } })
    expect($gatewaySwitching.get()).toBe(false)
    expect($organizationObservationSwitching.get()).toBe(false)
    expect($organizationObservationEpoch.get()).toBeGreaterThan(0)
  } finally {off()}
})

it('coalesces identical organization picks until a newer foreground intent takes ownership', async () => {
  const { selectOrganizationConnection } = await import('./organization-switch')
  const { $organizationObservationSwitching } = await import('./organization-observation-mode')
  const remembered = deferred<Awaited<ReturnType<typeof setLastUsed>>>()
  setLastUsed.mockReturnValueOnce(remembered.promise)
  const first = selectOrganizationConnection('homelab', 'research')
  expect(selectOrganizationConnection('homelab', 'research')).toBe(first)
  await vi.waitFor(() => expect(setLastUsed).toHaveBeenCalledOnce())
  expect($organizationObservationSwitching.get()).toBe(true)
  setShowAllProfiles(true)
  const latest = selectOrganizationConnection('homelab', 'research')
  expect(latest).not.toBe(first)
  remembered.resolve({ ok: true, registry })
  expect(await first).toMatchObject({ status: 'superseded' })
  expect(await latest).toMatchObject({ status: 'committed' })
  expect($organizationObservationSwitching.get()).toBe(false)
})

it('records successful publication after reset even when a newer browse intent supersedes it', async () => {
  $connection.set({ connectionId: 'local', profile: 'default', mode: 'local', baseUrl: '' } as HermesConnection)
  const activation = deferred<boolean>()
  ensureGatewayForAgent.mockReturnValueOnce(activation.promise)
  const pending = selectConnectionWithOutcome('homelab', { profile: 'research' })
  await vi.waitFor(() => expect($gatewaySwitching.get()).toBe(true))
  setShowAllProfiles(true)
  activation.resolve(true)
  expect(await pending).toMatchObject({ status: 'superseded', resetStarted: true, published: true, observedRoute: { connectionId: 'homelab', profile: 'research' } })
  expect(setLastUsed).not.toHaveBeenCalled()
  expect($gatewaySwitching.get()).toBe(false)
})
