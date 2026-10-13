import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import type { HermesConnection } from '@/global'

import { deferred } from '../test/deferred'

vi.mock('@/eidolon', () => ({
  HermesGateway: class {}, getProfiles: vi.fn(async () => ({ profiles: [] })),
  setApiRequestProfile: vi.fn(), setApiRequestConnection: vi.fn(), resetSidebarBatchCapability: vi.fn()
}))
vi.mock('@/lib/query-client', () => ({ invalidateProfileScopedQueries: vi.fn() }))
vi.mock('@/app/contrib/hooks/use-background-sync', () => ({ resetLiveRuntimeTracking: vi.fn() }))
vi.mock('@/store/starmap', () => ({ resetStarmapGraph: vi.fn() }))
vi.mock('@/store/notify-baseline', () => ({ markNativeNotifyBaseline: vi.fn() }))

const { $connection } = await import('./session')
const profileStore = await import('./profile')
const { $activeGatewayProfile } = profileStore
const { configureGatewayRegistry, setPrimaryGateway, setPrimaryGatewayConnection, closeSecondaryGateways } = await import('./gateway')
const { selectConnectionWithOutcome, setConnectionsRegistry, _resetConnectionsForTests } = await import('./connections')

beforeEach(() => {
  vi.useFakeTimers()
  _resetConnectionsForTests()
  configureGatewayRegistry({ onEvent: vi.fn() })
  setPrimaryGateway({ connectionState: 'open' } as never, 'default')
  setPrimaryGatewayConnection({ connectionId: 'homelab' })
  $activeGatewayProfile.set('default')
  $connection.set({ connectionId: 'local', profile: 'default', mode: 'local', baseUrl: '' } as HermesConnection)
  setConnectionsRegistry({ connections: [{ id: 'homelab', kind: 'remote', label: 'Homelab', tokenPreview: null, tokenSet: true }], primary: 'homelab', secureTokenStorage: true, version: 2 })
})
afterEach(() => { closeSecondaryGateways(); vi.restoreAllMocks(); vi.useRealTimers(); vi.unstubAllGlobals() })

it.each(['default', 'research'])('does not count shared socket readiness as route publication while descriptor stalls (%s)', async profile => {
  const descriptor = deferred<HermesConnection>()
  const setLastUsed = vi.fn()
  vi.stubGlobal('window', { localStorage: window.localStorage, hermesDesktop: {
    connections: { setLastUsed },
    // Gateway probes have a priority; the independently awaited publication descriptor does not.
    getConnectionFor: vi.fn(({ priority }: { priority?: string }) => priority
      ? Promise.resolve({ connectionId: 'homelab', profile, sharedRemote: true }) : descriptor.promise)
  } })
  const publications: number[] = []
  const ensure = profileStore.ensureGatewayAgent
  vi.spyOn(profileStore, 'ensureGatewayAgent').mockImplementation((connection, target, options) => ensure(connection, target, {
    ...options, onPublished: () => { publications.push(Date.now()); options?.onPublished?.() }
  }))
  const pending = selectConnectionWithOutcome('homelab', { profile })

  try {
    await vi.advanceTimersByTimeAsync(19_999)
    expect($connection.get()?.connectionId).toBe('local')
    expect(setLastUsed).not.toHaveBeenCalled()
    expect(publications).toEqual([])
    await vi.advanceTimersByTimeAsync(1)
    const outcome = await pending
    // The descriptor's own timeout resolves null first. Existing profile behavior
    // then publishes the logical owner/profile batch; only that is publication.
    expect(outcome).toMatchObject({ status: 'committed', published: true, resetStarted: true })
    expect(publications).toHaveLength(1)
    expect($activeGatewayProfile.get()).toBe(profile)
    expect($connection.get()?.connectionId).toBe('local')
    descriptor.resolve({ connectionId: 'homelab', profile, mode: 'remote', baseUrl: 'https://example.invalid' } as HermesConnection)
    await vi.advanceTimersByTimeAsync(0)
    expect($connection.get()?.connectionId).toBe('local')
    expect(publications).toHaveLength(1)
  } finally {
    descriptor.resolve({ connectionId: 'homelab', profile, mode: 'remote', baseUrl: 'https://example.invalid' } as HermesConnection)
    await vi.advanceTimersByTimeAsync(20_000)
    await pending
  }
})
