import type { GatewayWsUrlResult } from '@aethermesh/shared'
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const gatewayMocks = vi.hoisted(() => ({
  instances: [] as Array<{
    connect: ReturnType<typeof vi.fn>
    connectionState: string
    request: ReturnType<typeof vi.fn>
    wsUrl: string
  }>
}))

vi.mock('@/eidolon', async importOriginal => {
  const actual = await importOriginal<typeof HermesModule>()

  class FakeHermesGateway {
    connectionState = 'closed'
    wsUrl = ''
    request = vi.fn()
    connect = vi.fn(async (wsUrl: string) => {
      this.wsUrl = wsUrl
      this.connectionState = 'open'

      for (const handler of this.stateHandlers) {
        handler('open')
      }
    })
    close = vi.fn(() => {
      this.connectionState = 'closed'

      for (const handler of this.stateHandlers) {
        handler('closed')
      }
    })
    onEvent = vi.fn(() => () => undefined)
    onState = vi.fn((handler: (state: string) => void) => {
      this.stateHandlers.add(handler)
      handler(this.connectionState)

      return () => this.stateHandlers.delete(handler)
    })
    private stateHandlers = new Set<(state: string) => void>()

    constructor() {
      gatewayMocks.instances.push(this)
    }
  }

  return { ...actual, HermesGateway: FakeHermesGateway }
})

import type * as HermesModule from '@/eidolon'
import { HermesGateway } from '@/eidolon'
import {
  $gateway,
  activeGateway,
  activeGatewayProfileKey,
  closeSecondaryGateways,
  configureGatewayRegistry,
  connectPrimaryGateway,
  ensureGatewayForAgent,
  ensureGatewayForProfile,
  recordPrimaryRuntimeProducer,
  requestGatewayForAgent,
  retireOAuthSession,
  setPrimaryGateway
} from '@/store/gateway'
import { $activeGatewayProfile } from '@/store/profile'
import { $connection, $gatewayState } from '@/store/session'
import { beginSourceSwitchIntent } from '@/store/source-switch-intent'

import { useGatewayRequest } from './use-gateway-request'

interface TestGateway {
  close: () => void
  connect: ReturnType<typeof vi.fn>
  connectionState: string
  request: ReturnType<typeof vi.fn>
  wsUrl?: string
}

const fakeGateway = { connectionState: 'open' } as unknown as HermesGateway

const remoteConnection = {
  authMode: 'oauth' as const,
  baseUrl: 'https://ssh.example.test',
  connectionId: 'ssh-source',
  mode: 'remote' as const,
  profile: 'research',
  remoteIdentity: 'ssh.example.test',
  remoteKind: 'ssh' as const,
  token: 'remote-token',
  wsUrl: 'wss://ssh.example.test/api/ws?ticket=stale'
}

function installRemoteDesktop() {
  let mintCount = 0

  const getConnection = vi.fn(async (profile?: null | string) => ({
    authMode: 'token' as const,
    baseUrl: 'http://127.0.0.1:5151',
    mode: 'local' as const,
    profile: profile ?? 'default',
    token: 'local-token',
    wsUrl: 'ws://127.0.0.1:5151/api/ws?token=local'
  }))

  const getConnectionFor = vi.fn(async ({ connectionId, profile }: { connectionId: string; profile: string }) => ({
    ...remoteConnection,
    connectionId,
    profile
  }))

  const getGatewayWsUrl = vi.fn(async () => ({
    ok: true as const,
    wsUrl: 'ws://127.0.0.1:5151/api/ws?token=fresh-local'
  }))

  const getGatewayWsUrlFor = vi.fn(
    async ({ connectionId, profile }: { connectionId: string; profile: string }): Promise<GatewayWsUrlResult> => {
      mintCount += 1

      return {
        ok: true as const,
        wsUrl: `wss://${connectionId}.example.test/api/ws?profile=${profile}&ticket=fresh-${mintCount}`
      }
    }
  )

  Object.defineProperty(window, 'hermesDesktop', {
    configurable: true,
    value: { getConnection, getConnectionFor, getGatewayWsUrl, getGatewayWsUrlFor }
  })

  return { getConnection, getConnectionFor, getGatewayWsUrl, getGatewayWsUrlFor }
}

function installPrimaryDesktop(authMode: 'oauth' | 'token') {
  const getConnection = vi.fn(async (profile?: null | string) => ({
    authMode,
    isFullscreen: false,
    nativeOverlayWidth: 0,
    logs: [] as string[],
    windowButtonPosition: null,
    connectionId: 'local',
    baseUrl: authMode === 'oauth' ? 'https://gateway.example.test' : 'http://127.0.0.1:5151',
    mode: authMode === 'oauth' ? ('remote' as const) : ('local' as const),
    profile: profile ?? 'default',
    token: 'primary-token',
    wsUrl: authMode === 'oauth' ? 'wss://gateway.example.test/api/ws?ticket=stale' : 'ws://127.0.0.1:5151/api/ws'
  }))

  const getGatewayWsUrl = vi.fn(async (profile?: null | string) => ({
    ok: true as const,
    wsUrl:
      authMode === 'oauth'
        ? `wss://gateway.example.test/api/ws?profile=${profile ?? 'default'}&ticket=fresh`
        : 'ws://127.0.0.1:5151/api/ws?token=fresh'
  }))

  const getConnectionFor = vi.fn()
  const getGatewayWsUrlFor = vi.fn()

  Object.defineProperty(window, 'hermesDesktop', {
    configurable: true,
    value: { getConnection, getConnectionFor, getGatewayWsUrl, getGatewayWsUrlFor }
  })

  return { getConnection, getConnectionFor, getGatewayWsUrl, getGatewayWsUrlFor }
}

function makePrimaryGateway(): TestGateway {
  const gateway = new HermesGateway() as unknown as TestGateway
  gateway.connectionState = 'open'

  return gateway
}

async function activateRemoteGateway() {
  const desktop = installRemoteDesktop()
  const primary = makePrimaryGateway()

  setPrimaryGateway(primary as unknown as HermesGateway, 'default')
  $gateway.set(primary as unknown as HermesGateway)
  await ensureGatewayForAgent('ssh-source', 'research')

  const gateway = $gateway.get() as unknown as TestGateway

  expect(gateway).not.toBe(primary)
  expect($activeGatewayProfile.get()).toBe('research')

  return { desktop, gateway }
}

async function expectSecondaryRecoveryFailure(
  gateway: TestGateway,
  request: ReturnType<typeof useGatewayRequest>['requestGateway']
) {
  const transportError = new Error('connection closed')
  gateway.request.mockRejectedValueOnce(transportError)
  gateway.connectionState = 'closed'

  vi.useFakeTimers()

  const retry = request('session.resume').then(
    () => undefined,
    error => error
  )

  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
    await vi.advanceTimersByTimeAsync(8_000)
  })

  await expect(retry).resolves.toBe(transportError)
  expect(gateway.request).toHaveBeenCalledTimes(1)
  expect(gateway.connect).toHaveBeenCalledTimes(1)
}

beforeEach(async () => {
  gatewayMocks.instances.length = 0
  closeSecondaryGateways()
  setPrimaryGateway(null)
  await ensureGatewayForProfile('default')
  $gateway.set(null)
  $connection.set(null)
  $gatewayState.set('idle')
  $activeGatewayProfile.set('default')
  configureGatewayRegistry({
    onActiveRouteChanged: profile => $activeGatewayProfile.set(profile),
    onEvent: vi.fn()
  })
})

afterEach(() => {
  vi.useRealTimers()
  closeSecondaryGateways()
  setPrimaryGateway(null)
  $gateway.set(null)
  $connection.set(null)
  $gatewayState.set('idle')
  $activeGatewayProfile.set('default')
  Reflect.deleteProperty(window, 'hermesDesktop')
})

describe('useGatewayRequest', () => {
  it('exposes the live gateway on the first render, before effects run', () => {
    $gateway.set(fakeGateway)

    const { result } = renderHook(() => useGatewayRequest())

    expect(result.current.gateway).toBe(fakeGateway)
  })

  it('tracks the gateway when the active socket changes', () => {
    const { result } = renderHook(() => useGatewayRequest())

    expect(result.current.gateway).toBeNull()

    act(() => $gateway.set(fakeGateway))

    expect(result.current.gateway).toBe(fakeGateway)
  })

  it.each([
    { error: new Error('connection closed'), label: 'closed message' },
    { error: new Error('ECONNRESET'), label: 'reset message' },
    { error: Object.assign(new Error('socket failed'), { code: 'ECONNRESET' }), label: 'error code' },
    { error: Object.assign(new Error('socket failed'), { cause: { code: 'ECONNRESET' } }), label: 'cause code' }
  ])('recovers the registered remote source after a $label failure', async ({ error }) => {
    const { desktop, gateway } = await activateRemoteGateway()
    gateway.request.mockResolvedValueOnce({ turn: 1 }).mockRejectedValueOnce(error).mockResolvedValueOnce({ turn: 2 })

    const { result } = renderHook(() => useGatewayRequest())

    await act(async () => {
      await expect(result.current.requestGateway('prompt.submit', { text: 'first' })).resolves.toEqual({ turn: 1 })
    })
    gateway.connectionState = 'closed'
    await act(async () => {
      await expect(result.current.requestGateway('prompt.submit', { text: 'second' })).resolves.toEqual({ turn: 2 })
    })

    expect(desktop.getConnectionFor).toHaveBeenCalledTimes(2)
    expect(desktop.getConnectionFor).toHaveBeenCalledWith({ connectionId: 'ssh-source', profile: 'research' })
    expect(desktop.getGatewayWsUrlFor).toHaveBeenCalledTimes(2)
    expect(desktop.getGatewayWsUrlFor).toHaveBeenCalledWith({ connectionId: 'ssh-source', profile: 'research' })
    expect(desktop.getConnection).not.toHaveBeenCalled()
    expect(desktop.getGatewayWsUrl).not.toHaveBeenCalled()
    expect(gateway.connect).toHaveBeenLastCalledWith(expect.stringContaining('ticket=fresh-2'), null)
  })

  it('does not reconnect for a non-transport request failure', async () => {
    const { desktop, gateway } = await activateRemoteGateway()
    const failure = Object.assign(new Error('request rejected'), { code: 'EVALIDATION' })
    gateway.request.mockRejectedValueOnce(failure)

    const { result } = renderHook(() => useGatewayRequest())

    await expect(result.current.requestGateway('session.resume')).rejects.toBe(failure)
    expect(desktop.getConnectionFor).toHaveBeenCalledTimes(1)
    expect(desktop.getGatewayWsUrlFor).toHaveBeenCalledTimes(1)
    expect(gateway.connect).toHaveBeenCalledTimes(1)
  })

  it('surfaces a real secondary OAuth reauth rejection as the original transport failure', async () => {
    const { desktop, gateway } = await activateRemoteGateway()
    desktop.getGatewayWsUrlFor.mockImplementation(async () => ({
      error: '401 cookie expired',
      needsOauthLogin: true,
      ok: false as const
    }))

    const { result } = renderHook(() => useGatewayRequest())

    await expectSecondaryRecoveryFailure(gateway, result.current.requestGateway)

    expect(desktop.getConnectionFor).toHaveBeenCalledWith({ connectionId: 'ssh-source', profile: 'research' })
    expect(desktop.getGatewayWsUrlFor).toHaveBeenCalled()
    expect(desktop.getConnection).not.toHaveBeenCalled()
    expect(desktop.getGatewayWsUrl).not.toHaveBeenCalled()
  })

  it('surfaces a failed secondary OAuth ticket mint without using the stale ticket or local bridges', async () => {
    const { desktop, gateway } = await activateRemoteGateway()
    desktop.getGatewayWsUrlFor.mockRejectedValue(new Error('ticket mint failed'))

    const { result } = renderHook(() => useGatewayRequest())

    await expectSecondaryRecoveryFailure(gateway, result.current.requestGateway)

    expect(desktop.getConnectionFor).toHaveBeenCalledWith({ connectionId: 'ssh-source', profile: 'research' })
    expect(desktop.getConnection).not.toHaveBeenCalled()
    expect(desktop.getGatewayWsUrl).not.toHaveBeenCalled()
  })

  it('surfaces a missing optional scoped mint bridge without falling back to a stale ticket or local lookup', async () => {
    const { desktop, gateway } = await activateRemoteGateway()
    Reflect.deleteProperty(window.hermesDesktop, 'getGatewayWsUrlFor')

    const { result } = renderHook(() => useGatewayRequest())

    await expectSecondaryRecoveryFailure(gateway, result.current.requestGateway)

    expect(desktop.getConnectionFor).toHaveBeenCalledWith({ connectionId: 'ssh-source', profile: 'research' })
    expect(desktop.getConnection).not.toHaveBeenCalled()
    expect(desktop.getGatewayWsUrl).not.toHaveBeenCalled()
  })

  it.each([
    { authMode: 'oauth' as const, label: 'primary OAuth' },
    { authMode: 'token' as const, label: 'local primary' }
  ])('preserves $label recovery', async ({ authMode }) => {
    const desktop = installPrimaryDesktop(authMode)
    const primary = makePrimaryGateway()
    primary.request.mockRejectedValueOnce(new Error('connection closed')).mockResolvedValueOnce({ recovered: true })
    primary.connectionState = 'closed'

    setPrimaryGateway(primary as unknown as HermesGateway, 'default')
    $gateway.set(primary as unknown as HermesGateway)
    $gatewayState.set('closed')

    const { result } = renderHook(() => useGatewayRequest())

    await act(async () => {
      await expect(result.current.requestGateway('session.resume')).resolves.toEqual({ recovered: true })
    })

    expect(desktop.getConnection).toHaveBeenCalledWith()
    expect(desktop.getGatewayWsUrl).toHaveBeenCalledWith('default')
    expect(desktop.getConnectionFor).not.toHaveBeenCalled()
    expect(desktop.getGatewayWsUrlFor).not.toHaveBeenCalled()
  })

  it('rejects instead of hanging forever when the reconnect getConnection() wedges (#93454)', async () => {
    // Repro: a request lands on a dropped socket, the "not connected" catch
    // kicks off a reconnect, and the IPC round-trip into main
    // (desktop.getConnection) never settles — e.g. a wedged revalidation after
    // a liveness-probe trip. Without an internal timeout on that await,
    // reconnectingRef never clears and requestGateway hangs forever instead of
    // surfacing the original transport error.
    vi.useFakeTimers()

    const dropped = {
      connectionState: 'closed',
      request: vi.fn().mockRejectedValue(new Error('connection closed'))
    } as unknown as HermesGateway

    const getConnection = vi.fn(() => new Promise(() => undefined))

    ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = { getConnection }
    $gateway.set(dropped)

    const { result } = renderHook(() => useGatewayRequest())

    const pending = expect(result.current.requestGateway('some.method')).rejects.toThrow('connection closed')

    // Advance past the internal reconnect-attempt timeout (20s) — the stalled
    // getConnection() await must reject so the reconnect gives up and the
    // original transport error surfaces, instead of requestGateway() never
    // settling.
    await vi.advanceTimersByTimeAsync(20_000)
    await pending
  })
})


it.each(['session.resume', 'session.create'])('request-triggered reconnect retires old proof and binds a silent %s response', async method => {
  const desktop = installPrimaryDesktop('token')
  const primary = makePrimaryGateway()
  setPrimaryGateway(primary as unknown as HermesGateway, 'default', { connectionId: 'local' })
  $gateway.set(primary as unknown as HermesGateway)
  recordPrimaryRuntimeProducer({ type: 'approval.request', session_id: 'runtime-r' })
  primary.close()
  $gatewayState.set('closed')
  primary.request.mockRejectedValueOnce(new Error('connection closed')).mockResolvedValueOnce({ pong: true })
  const { result } = renderHook(() => useGatewayRequest())
  await act(async () => { await expect(result.current.requestGateway('ping')).resolves.toEqual({ pong: true }) })
  expect(desktop.getConnection).toHaveBeenCalledWith()
  await expect(requestGatewayForAgent('local', 'default', 'approval.respond', { session_id: 'runtime-r' }))
    .rejects.toThrow('runtime transport is unavailable')
  primary.request.mockResolvedValueOnce({ session_id: 'runtime-r', stored_session_id: 'stored-s' })
  await act(async () => {
    await expect(result.current.requestGateway(method, method === 'session.resume' ? { session_id: 'stored-s' } : { title: 'new session' }))
      .resolves.toEqual({ session_id: 'runtime-r', stored_session_id: 'stored-s' })
  })
  primary.request.mockResolvedValue({ status: 'complete' })
  await expect(requestGatewayForAgent('local', 'default', 'prompt.submit', { session_id: 'runtime-r' }))
    .resolves.toEqual({ status: 'complete' })
  await expect(requestGatewayForAgent('local', 'default', 'approval.respond', { session_id: 'runtime-r' }))
    .resolves.toEqual({ status: 'complete' })
})

it.each(['session.resume', 'session.create'])('a late %s response cannot restore proof after its transport is replaced', async method => {
  const desktop = installPrimaryDesktop('token')
  const primary = makePrimaryGateway()
  setPrimaryGateway(primary as unknown as HermesGateway, 'default', { connectionId: 'local' })
  $gateway.set(primary as unknown as HermesGateway)
  $gatewayState.set('open')
  recordPrimaryRuntimeProducer({ type: 'approval.request', session_id: 'runtime-r' })
  let release!: (value: { session_id: string }) => void
  primary.request.mockReturnValueOnce(new Promise(resolve => { release = resolve }))
  const { result } = renderHook(() => useGatewayRequest())

  const pending = result.current.requestGateway(method, method === 'session.resume' ? { session_id: 'stored-s' } : { title: 'new session' })
    .then(value => ({ value }), error => ({ error }))

  primary.close()
  const connection = await desktop.getConnection()
  await connectPrimaryGateway(primary as unknown as HermesGateway, connection, connection.wsUrl)
  release({ session_id: 'runtime-r' })
  await expect(pending).resolves.toMatchObject({ error: expect.objectContaining({ message: expect.stringContaining('response settled') }) })
  await expect(requestGatewayForAgent('local', 'default', 'approval.respond', { session_id: 'runtime-r' }))
    .rejects.toThrow('runtime transport is unavailable')
})


it.each([false, true])('reconciles a request-triggered changed-profile primary only while its intent is current (superseded: %s)', async superseded => {
  const desktop = installPrimaryDesktop('token')
  const connection = { ...await desktop.getConnection(), profile: 'research', baseUrl: 'http://127.0.0.1:6262', wsUrl: 'ws://127.0.0.1:6262/api/ws' }
  desktop.getConnection.mockResolvedValue(connection)
  desktop.getGatewayWsUrl.mockResolvedValue({ ok: true, wsUrl: connection.wsUrl })
  const primary = makePrimaryGateway()
  setPrimaryGateway(primary as unknown as HermesGateway, 'default', { connectionId: 'local' })
  $gateway.set(primary as unknown as HermesGateway)
  $gatewayState.set('open')
  primary.close()
  primary.request.mockRejectedValueOnce(new Error('connection closed'))
  const connect = primary.connect.getMockImplementation() as (wsUrl: string) => Promise<void>
  primary.connect.mockImplementation(async (wsUrl: string) => {
    if (superseded) {beginSourceSwitchIntent()}

    return connect(wsUrl)
  })
  const { result } = renderHook(() => useGatewayRequest())
  await act(async () => {
    await expect(result.current.requestGateway('session.create', { title: 'belongs to default' })).rejects.toThrow('connection closed')
  })
  expect(primary.connectionState).toBe('open')
  expect(primary.request).toHaveBeenCalledTimes(1)

  if (superseded) {
    expect(activeGateway()).toBeNull()
    expect($activeGatewayProfile.get()).toBe('default')
    expect($connection.get()).toBeNull()
    expect($gatewayState.get()).toBe('closed')
  } else {
    expect(activeGateway()).toBe(primary)
    expect(activeGatewayProfileKey()).toBe('research')
    expect($activeGatewayProfile.get()).toBe('research')
    expect($connection.get()).toMatchObject({ profile: 'research', baseUrl: connection.baseUrl })
    expect($gatewayState.get()).toBe('open')
    primary.request.mockResolvedValueOnce({ pong: true })
    await act(async () => { await expect(result.current.requestGateway('ping')).resolves.toEqual({ pong: true }) })
  }
})


it.each(['A-B', 'A-B-A', 'new-generation'])('does not replay a held original request after ownership changes (%s)', async change => {
  const desktop = installRemoteDesktop()
  const primary = makePrimaryGateway()
  setPrimaryGateway(primary as unknown as HermesGateway, 'default', { connectionId: 'local' })
  await ensureGatewayForProfile('default')
  let rejectOriginal!: (error: Error) => void
  primary.request.mockReturnValueOnce(new Promise((_resolve, reject) => { rejectOriginal = reject }))
  const { result } = renderHook(() => useGatewayRequest())
  const transportError = new Error('connection closed')

  const pending = result.current.requestGateway('session.create', { title: 'A-only draft' })
    .then(value => ({ value }), error => ({ error }))

  let secondary: TestGateway | undefined
  await act(async () => {
    if (change === 'new-generation') {
      primary.close()
      const connection = { ...await installPrimaryDesktop('token').getConnection(), profile: 'default' }
      await connectPrimaryGateway(primary as unknown as HermesGateway, connection, connection.wsUrl)
    } else {
      beginSourceSwitchIntent()
      await ensureGatewayForAgent('ssh-source', 'research')
      secondary = $gateway.get() as unknown as TestGateway

      if (change === 'A-B-A') {
        beginSourceSwitchIntent()
        await ensureGatewayForProfile('default')
      }

      primary.close()
    }
  })
  const descriptorCalls = desktop.getConnection.mock.calls.length
  await act(async () => {
    rejectOriginal(transportError)
    await expect(pending).resolves.toEqual({ error: transportError })
  })
  expect(primary.request).toHaveBeenCalledTimes(1)
  expect(secondary?.request.mock.calls ?? []).toEqual([])
  expect(desktop.getConnection).toHaveBeenCalledTimes(descriptorCalls)
})


it('retains logical member B when its shared primary reconnects on the same launch profile', async () => {
  const desktop = installPrimaryDesktop('oauth')
  const primaryConnection = { ...await desktop.getConnection(), authenticationIdentity: 'opened-authentication', connectionId: 'shared', profile: 'default' }
  const memberConnection = { ...primaryConnection, profile: 'member-b', sharedPrimary: true }
  desktop.getConnection.mockImplementation(async profile => profile === 'member-b' ? memberConnection : primaryConnection)
  const primary = makePrimaryGateway()
  setPrimaryGateway(primary as unknown as HermesGateway, 'default', primaryConnection)
  await ensureGatewayForProfile('default')
  $activeGatewayProfile.set('member-b')
  $connection.set(memberConnection)
  $gatewayState.set('open')
  primary.close()
  primary.request.mockRejectedValueOnce(new Error('connection closed')).mockResolvedValueOnce({ session_id: 'member-runtime' })
  const { result } = renderHook(() => useGatewayRequest())
  await act(async () => {
    await expect(result.current.requestGateway('session.resume', { session_id: 'member-stored', profile: 'wrong' })).resolves.toEqual({ session_id: 'member-runtime' })
  })
  expect($activeGatewayProfile.get()).toBe('member-b')
  expect(activeGatewayProfileKey()).toBe('default')
  expect($connection.get()).toMatchObject({ profile: 'member-b', sharedPrimary: true })
  expect(primary.request.mock.calls).toEqual([
    ['session.resume', { session_id: 'member-stored', profile: 'member-b' }],
    ['session.resume', { session_id: 'member-stored', profile: 'member-b' }]
  ])
  primary.request.mockResolvedValueOnce({ goal: 'B goal' })
  await requestGatewayForAgent('shared', 'member-b', 'session.control.read', { session_id: 'member-runtime', profile: 'wrong' })
  expect(primary.request).toHaveBeenLastCalledWith('session.control.read', { session_id: 'member-runtime', profile: 'member-b' })
})


it.each(['newer-intent', 'changed-endpoint', 'changed-authentication', 'changed-primary-authentication', 'changed-registry-authentication', 'missing-authentication'])('does not republish or retry a shared member after its descriptor lookup loses ownership (%s)', async change => {
  const desktop = installPrimaryDesktop('oauth')
  const primaryConnection = { ...await desktop.getConnection(), authenticationIdentity: change === 'missing-authentication' ? undefined : 'opened-authentication', connectionId: 'shared', profile: 'default' }
  const memberConnection = { ...primaryConnection, profile: 'member-b', sharedPrimary: change !== 'changed-registry-authentication', sharedRemote: change === 'changed-registry-authentication' }
  desktop.getConnection.mockImplementation(async profile => {
    if (profile !== 'member-b') {return change === 'changed-primary-authentication' ? { ...primaryConnection, authenticationIdentity: 'new-authentication' } : primaryConnection}

    if (change === 'newer-intent') {
      beginSourceSwitchIntent()
      $activeGatewayProfile.set('member-c')

      return memberConnection
    }

    return change === 'changed-endpoint'
      ? { ...memberConnection, baseUrl: 'https://other.example.test' }
      : change === 'missing-authentication' ? memberConnection : { ...memberConnection, authenticationIdentity: 'new-authentication' }
  })
  desktop.getConnectionFor.mockImplementation(async ({ profile }) => desktop.getConnection(profile))
  const primary = makePrimaryGateway()
  setPrimaryGateway(primary as unknown as HermesGateway, 'default', primaryConnection)
  await ensureGatewayForProfile('default')
  $activeGatewayProfile.set('member-b')
  $connection.set(memberConnection)
  $gatewayState.set('open')
  primary.close()
  primary.request.mockRejectedValueOnce(new Error('connection closed'))
  const { result } = renderHook(() => useGatewayRequest())
  await act(async () => {
    await expect(result.current.requestGateway('session.create', { title: 'B only' })).rejects.toThrow('connection closed')
  })
  expect(primary.request).toHaveBeenCalledTimes(1)

  if (change === 'newer-intent') {
    expect($activeGatewayProfile.get()).toBe('member-c')
    expect($connection.get()).toBe(memberConnection)
  } else {
    expect($activeGatewayProfile.get()).toBe('member-b')
    expect($connection.get()).toBeNull()
  }
})


it('does not treat an unverified selected member as the primary request owner', async () => {
  installPrimaryDesktop('oauth')
  const primary = makePrimaryGateway()
  setPrimaryGateway(primary as unknown as HermesGateway, 'default', { connectionId: 'shared' })
  await ensureGatewayForProfile('default')
  $activeGatewayProfile.set('member-b')
  const { result } = renderHook(() => useGatewayRequest())
  await expect(result.current.requestGateway('session.create', { title: 'B only' })).rejects.toThrow('no verified shared primary route')
  expect(primary.request).not.toHaveBeenCalled()
})

it('does not replay a null-descriptor primary request after a held recovery resolves for a new OAuth account', async () => {
  const desktop = installPrimaryDesktop('oauth')
  const primary = makePrimaryGateway()
  setPrimaryGateway(primary as unknown as HermesGateway, 'default')
  $gateway.set(primary as unknown as HermesGateway)
  $gatewayState.set('closed')
  $connection.set(null)
  primary.connectionState = 'closed'
  primary.request.mockRejectedValueOnce(new Error('connection closed')).mockResolvedValue({ replayed: true })
  let release!: (value: Awaited<ReturnType<typeof desktop.getConnection>>) => void
  const held = new Promise<Awaited<ReturnType<typeof desktop.getConnection>>>(resolve => { release = resolve })
  const next = { ...await desktop.getConnection(), oauthSessionIdentity: 'null-recovery-next' }
  desktop.getConnection.mockReturnValue(held)
  const { result } = renderHook(() => useGatewayRequest())
  const request = result.current.requestGateway('organization.create_objective', { title: 'old intent' })
  await vi.waitFor(() => expect(desktop.getConnection).toHaveBeenCalledTimes(2))
  retireOAuthSession('null-recovery-old', [], 'null-recovery-next')
  release(next)
  await expect(request).rejects.toThrow()
  expect(primary.request).toHaveBeenCalledOnce()
})

it('retires a background physical primary and its runtime producer while preserving unrelated foreground', async () => {
  installRemoteDesktop()
  const primary = makePrimaryGateway()
  primary.request.mockResolvedValue({ session_id: 'background-primary-runtime' })
  setPrimaryGateway(primary as unknown as HermesGateway, 'default', {
    connectionId: 'background-oauth', oauthSessionIdentity: 'background-primary-old'
  })
  $gateway.set(primary as unknown as HermesGateway)
  await requestGatewayForAgent('background-oauth', 'default', 'session.create')
  await ensureGatewayForAgent('unrelated-source', 'research')
  const foreground = activeGateway()
  retireOAuthSession('background-primary-old', ['background-oauth'], 'background-primary-next')
  expect(primary.close).toHaveBeenCalledOnce()
  expect(activeGateway()).toBe(foreground)
  expect(foreground?.connectionState).toBe('open')
  await expect(requestGatewayForAgent('background-oauth', 'default', 'config.set', {
    session_id: 'background-primary-runtime', key: 'fixture', value: 'old intent'
  })).rejects.toThrow()
  await expect(requestGatewayForAgent('background-oauth', 'default', 'profiles.list')).rejects.toThrow()
  expect(primary.request).toHaveBeenCalledOnce()
})
