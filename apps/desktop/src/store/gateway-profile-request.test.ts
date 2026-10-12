import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const secondaryGateways: Array<{
  close: ReturnType<typeof vi.fn>
  connect: ReturnType<typeof vi.fn>
  connectionState: string
  request: ReturnType<typeof vi.fn>
}> = []

let connectGate: Promise<void> | null = null

vi.mock('@/eidolon', () => ({
  HermesGateway: class {
    connectionState = 'closed'
    connect = vi.fn(async () => {
      if (this.connectionState === 'connecting') {
        return
      }

      this.connectionState = 'connecting'

      if (connectGate) {
        await connectGate
      }

      this.connectionState = 'open'
    })
    request = vi.fn(async (method: string, params: Record<string, unknown>) => {
      if (this.connectionState !== 'open') {
        throw new Error('gateway is not connected')
      }

      return { method, params }
    })
    close = vi.fn()
    onEvent = vi.fn(() => () => {})
    onState = vi.fn(() => () => {})

    constructor() {
      secondaryGateways.push(this)
    }
  },
  setApiRequestConnection: vi.fn()
}))
vi.mock('@/store/session', () => ({ setConnection: vi.fn(), setGatewayState: vi.fn() }))
vi.mock('@/store/notify-baseline', () => ({ markNativeNotifyBaseline: vi.fn() }))

const {
  $gateway,
  closeSecondaryGateways,
  configureGatewayRegistry,
  ensureGatewayForAgent,
  ensureGatewayForProfile,
  gatewayActivationEpoch,
  disposeSecondariesForConnection,
  retireOAuthSession,
  openGatewayForAgent,
  pruneSecondaryGateways,
  recordPrimaryRuntimeProducer,
  requestGatewayForAgent,
  requestGatewayForProfile,
  retainGatewayForAgent,
  setPrimaryGateway,
  setPrimaryGatewayConnection
} = await import('./gateway')

function installDesktop(getConnection: ReturnType<typeof vi.fn>): void {
  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
    getConnection,
    touchBackend: vi.fn(async () => undefined)
  }
}

function makePrimary() {
  return {
    connectionState: 'open',
    request: vi.fn(async (method: string, params: Record<string, unknown>) => ({ method, params }))
  }
}

beforeEach(async () => {
  secondaryGateways.length = 0
  connectGate = null
  configureGatewayRegistry({ onEvent: vi.fn() })
  closeSecondaryGateways()
})

afterEach(() => {
  closeSecondaryGateways()
  vi.clearAllMocks()
  delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
})

describe('requestGatewayForProfile', () => {
  it('requests through a pooled profile gateway without changing the active gateway', async () => {
    const primary = makePrimary()
    setPrimaryGateway(primary as never, 'default')
    installDesktop(
      vi.fn(async (profile: null | string) =>
        profile ? { port: 5151, profile, token: 'secondary-token' } : { port: 4242, token: 'primary-token' }
      )
    )
    await ensureGatewayForProfile('default')

    const result = await requestGatewayForProfile('worker', 'profiles.list', { include_sessions: true })

    expect(result).toEqual({ method: 'profiles.list', params: { include_sessions: true } })
    expect(secondaryGateways).toHaveLength(1)
    expect(secondaryGateways[0].request).toHaveBeenCalledWith('profiles.list', { include_sessions: true })
    expect(secondaryGateways[0].close).toHaveBeenCalledOnce()
    expect($gateway.get()).toBe(primary)
  })

  it('uses the primary socket and adds profile scope for a shared global remote route', async () => {
    const primary = makePrimary()
    setPrimaryGateway(primary as never, 'default')
    installDesktop(
      vi.fn(async (profile: null | string) => ({
        port: 4242,
        ...(profile ? { profile, sharedPrimary: true } : {}),
        token: 'primary-token'
      }))
    )
    await ensureGatewayForProfile('default')

    const result = await requestGatewayForProfile('venture', 'session.list', { limit: 20, profile: 'wrong' })

    expect(result).toEqual({ method: 'session.list', params: { limit: 20, profile: 'venture' } })
    expect(primary.request).toHaveBeenCalledWith('session.list', { limit: 20, profile: 'venture' })
    expect(secondaryGateways).toHaveLength(0)
    expect($gateway.get()).toBe(primary)
  })

  it('serializes concurrent requests while a secondary gateway is connecting', async () => {
    let releaseConnect: () => void = () => undefined
    connectGate = new Promise<void>(resolve => {
      releaseConnect = resolve
    })

    const primary = makePrimary()
    setPrimaryGateway(primary as never, 'default')
    installDesktop(
      vi.fn(async (profile: null | string) =>
        profile ? { port: 5151, profile, token: 'secondary-token' } : { port: 4242, token: 'primary-token' }
      )
    )
    await ensureGatewayForProfile('default')

    const first = requestGatewayForProfile('worker', 'profiles.list')
    await vi.waitFor(() => expect(secondaryGateways[0]?.connect).toHaveBeenCalledOnce())
    const second = requestGatewayForProfile('worker', 'profiles.list')
    const guardedSecond = second.catch(error => error)

    await Promise.resolve()
    releaseConnect()

    const [firstResult, secondResult] = await Promise.all([first, guardedSecond])

    expect(firstResult).toEqual({ method: 'profiles.list', params: {} })
    expect(secondResult).toEqual({ method: 'profiles.list', params: {} })
    expect(secondaryGateways[0].connect).toHaveBeenCalledOnce()
    expect(secondaryGateways[0].request).toHaveBeenCalledTimes(2)
    expect(secondaryGateways[0].close).toHaveBeenCalledOnce()
    expect($gateway.get()).toBe(primary)
  })

  it('prevents pruning while a background request is connecting or in flight', async () => {
    let releaseConnect: () => void = () => undefined
    connectGate = new Promise<void>(resolve => {
      releaseConnect = resolve
    })

    const primary = makePrimary()
    setPrimaryGateway(primary as never, 'default')
    installDesktop(
      vi.fn(async (profile: null | string) =>
        profile ? { port: 5151, profile, token: 'secondary-token' } : { port: 4242, token: 'primary-token' }
      )
    )
    await ensureGatewayForProfile('default')

    const request = requestGatewayForProfile('worker', 'profiles.list')
    await vi.waitFor(() => expect(secondaryGateways[0]?.connect).toHaveBeenCalledOnce())

    pruneSecondaryGateways(new Set())
    releaseConnect()

    await expect(request).resolves.toEqual({ method: 'profiles.list', params: {} })
    expect(secondaryGateways[0].close).toHaveBeenCalledOnce()

    pruneSecondaryGateways(new Set())
    expect(secondaryGateways[0].close).toHaveBeenCalledOnce()
  })
})

describe('requestGatewayForAgent', () => {
  it('reuses the active primary socket when its registry connection owns the session', async () => {
    const primary = makePrimary()

    const getConnectionFor = vi.fn(async ({ connectionId, profile }) => ({
      connectionId,
      port: 5151,
      profile,
      token: 'secondary-token'
    }))

    setPrimaryGateway(primary as never, 'default')
    setPrimaryGatewayConnection({ connectionId: 'remote-primary' })

    ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
      getConnection: vi.fn(),
      getConnectionFor,
      getGatewayWsUrlFor: vi.fn(async () => ({ ok: true as const, wsUrl: 'wss://remote.invalid/api/ws' })),
      touchBackend: vi.fn(async () => undefined)
    }
    await ensureGatewayForProfile('default')

    await openGatewayForAgent('remote-primary', 'default')
    await ensureGatewayForAgent('remote-primary', 'default')

    const result = await requestGatewayForAgent('remote-primary', 'default', 'session.resume', {
      session_id: 'stored-session'
    })

    expect(result).toEqual({
      method: 'session.resume',
      params: { session_id: 'stored-session' }
    })
    expect(primary.request).toHaveBeenCalledWith('session.resume', { session_id: 'stored-session' })
    expect(getConnectionFor).not.toHaveBeenCalled()
    expect(secondaryGateways).toHaveLength(0)
    expect($gateway.get()).toBe(primary)
  })

  it('keeps another profile on the same registry source isolated from the primary', async () => {
    const primary = makePrimary()

    const getConnectionFor = vi.fn(async ({ connectionId, profile }) => ({
      connectionId,
      port: 5151,
      profile,
      token: 'secondary-token'
    }))

    setPrimaryGateway(primary as never, 'default')
    setPrimaryGatewayConnection({ connectionId: 'remote-primary' })

    ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
      getConnection: vi.fn(),
      getConnectionFor,
      getGatewayWsUrlFor: vi.fn(async () => ({ ok: true as const, wsUrl: 'wss://remote.invalid/api/ws' })),
      touchBackend: vi.fn(async () => undefined)
    }

    await requestGatewayForAgent('remote-primary', 'research', 'session.resume', {
      session_id: 'research-session'
    })

    expect(getConnectionFor).toHaveBeenCalledWith({ connectionId: 'remote-primary', profile: 'research' })
    expect(primary.request).not.toHaveBeenCalled()
    expect(secondaryGateways).toHaveLength(1)
  })

  it('leases separate registry sockets for duplicate profile names without changing the active gateway', async () => {
    const primary = makePrimary()
    const getConnection = vi.fn(async (profile: null | string) => ({ port: 4242, profile, token: 'legacy-token' }))

    const getConnectionFor = vi.fn(async ({ connectionId, profile }) => ({
      connectionId,
      port: connectionId === 'source-a' ? 5151 : 5252,
      profile,
      token: `${connectionId}-token`
    }))

    const getGatewayWsUrlFor = vi.fn(async ({ connectionId, profile }) => ({
      ok: true as const,
      wsUrl: `ws://${connectionId}/${profile}`
    }))

    setPrimaryGateway(primary as never, 'default')
    ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
      getConnection,
      getConnectionFor,
      getGatewayWsUrlFor,
      touchBackend: vi.fn(async () => undefined)
    }
    await ensureGatewayForProfile('default')

    const [fromA, fromB] = await Promise.all([
      requestGatewayForAgent('source-a', 'research', 'session.list', { limit: 1 }),
      requestGatewayForAgent('source-b', 'research', 'session.list', { limit: 2 })
    ])

    expect(fromA).toEqual({ method: 'session.list', params: { limit: 1 } })
    expect(fromB).toEqual({ method: 'session.list', params: { limit: 2 } })
    expect(getConnectionFor).toHaveBeenCalledWith({ connectionId: 'source-a', profile: 'research' })
    expect(getConnectionFor).toHaveBeenCalledWith({ connectionId: 'source-b', profile: 'research' })
    expect(getGatewayWsUrlFor).toHaveBeenCalledWith({ connectionId: 'source-a', profile: 'research' })
    expect(getGatewayWsUrlFor).toHaveBeenCalledWith({ connectionId: 'source-b', profile: 'research' })
    expect(getConnection).not.toHaveBeenCalled()
    expect(secondaryGateways).toHaveLength(2)
    expect(secondaryGateways[0].close).toHaveBeenCalledOnce()
    expect(secondaryGateways[1].close).toHaveBeenCalledOnce()
    expect($gateway.get()).toBe(primary)
  })

  it('routes an explicit local registry descriptor through getConnectionFor', async () => {
    const primary = makePrimary()

    const getConnection = vi.fn(async (profile: null | string) => ({
      mode: 'remote',
      profile,
      wsUrl: 'wss://legacy-remote.invalid/api/ws?token=legacy'
    }))

    const getConnectionFor = vi.fn(async ({ connectionId, profile }) => ({
      connectionId,
      mode: 'local',
      port: 5151,
      profile
    }))

    setPrimaryGateway(primary as never, 'default')
    ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
      getConnection,
      getConnectionFor,
      getGatewayWsUrlFor: vi.fn(async ({ connectionId, profile }) => ({
        ok: true as const,
        wsUrl: `ws://${connectionId}/${profile}`
      })),
      touchBackend: vi.fn(async () => undefined)
    }
    await ensureGatewayForProfile('default')

    await expect(requestGatewayForAgent('local', 'worker', 'profiles.list')).resolves.toEqual({
      method: 'profiles.list',
      params: {}
    })
    expect(getConnectionFor).toHaveBeenCalledWith({ connectionId: 'local', profile: 'worker' })
    expect(getConnection).not.toHaveBeenCalled()
    expect($gateway.get()).toBe(primary)
  })

  it('evicts registry sockets when their source is edited or removed', async () => {
    const primary = makePrimary()

    const getConnectionFor = vi.fn(async ({ connectionId, profile }) => ({ connectionId, port: 5151, profile }))
    const onActiveConnectionInvalidated = vi.fn()

    setPrimaryGateway(primary as never, 'default')
    configureGatewayRegistry({ onActiveConnectionInvalidated, onEvent: vi.fn() })
    ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
      getConnection: vi.fn(),
      getConnectionFor,
      getGatewayWsUrlFor: vi.fn(async ({ connectionId, profile }) => ({
        ok: true as const,
        wsUrl: `ws://${connectionId}/${profile}`
      })),
      touchBackend: vi.fn(async () => undefined)
    }

    await openGatewayForAgent('source-a', 'research')
    await requestGatewayForAgent('source-a', 'research', 'session.list')
    disposeSecondariesForConnection('source-a')

    expect(secondaryGateways[0].close).toHaveBeenCalledOnce()
    expect(onActiveConnectionInvalidated).not.toHaveBeenCalled()

    await requestGatewayForAgent('source-a', 'research', 'session.list')
    expect(secondaryGateways).toHaveLength(2)
    expect(getConnectionFor).toHaveBeenCalledTimes(2)
  })

  it('invalidates an active pinned source with its window profile and a route epoch', async () => {
    const primary = makePrimary()
    const onActiveConnectionChanged = vi.fn()
    const onActiveConnectionInvalidated = vi.fn()

    setPrimaryGateway(primary as never, 'pinned')
    configureGatewayRegistry({ onActiveConnectionChanged, onActiveConnectionInvalidated, onEvent: vi.fn() })
    ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
      getConnection: vi.fn(),
      getConnectionFor: vi.fn(async ({ connectionId, profile }) => ({ connectionId, port: 5151, profile })),
      getGatewayWsUrlFor: vi.fn(async ({ connectionId, profile }) => ({
        ok: true as const,
        wsUrl: `ws://${connectionId}/${profile}`
      })),
      touchBackend: vi.fn(async () => undefined)
    }

    await ensureGatewayForAgent('source-a', 'pinned')
    disposeSecondariesForConnection('source-a')

    const invalidationEpoch = gatewayActivationEpoch()
    expect(onActiveConnectionInvalidated).toHaveBeenCalledWith('pinned', invalidationEpoch)
    expect($gateway.get()).toBe(primary)

    // Same profile, different source advances the guard as soon as activation
    // starts, before its deferred socket connect can finish.
    let releaseConnect: () => void = () => undefined
    connectGate = new Promise<void>(resolve => {
      releaseConnect = resolve
    })
    const sourceBActivation = ensureGatewayForAgent('source-b', 'pinned')
    await vi.waitFor(() => expect(secondaryGateways[1]?.connect).toHaveBeenCalledOnce())
    expect(gatewayActivationEpoch()).toBeGreaterThan(invalidationEpoch)
    releaseConnect()
    await sourceBActivation
    expect(onActiveConnectionChanged).toHaveBeenLastCalledWith(expect.objectContaining({ connectionId: 'source-b' }))
  })

  it('does not activate or publish a source invalidated while its dial is pending', async () => {
    const primary = makePrimary()
    const onActiveConnectionChanged = vi.fn()

    setPrimaryGateway(primary as never, 'default')
    configureGatewayRegistry({ onActiveConnectionChanged, onEvent: vi.fn() })
    ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
      getConnection: vi.fn(async profile => ({ port: 4242, profile })),
      getConnectionFor: vi.fn(async ({ connectionId, profile }) => ({ connectionId, port: 5151, profile })),
      getGatewayWsUrlFor: vi.fn(async ({ connectionId, profile }) => ({
        ok: true as const,
        wsUrl: `ws://${connectionId}/${profile}`
      })),
      touchBackend: vi.fn(async () => undefined)
    }
    await ensureGatewayForProfile('default')

    let releaseConnect: () => void = () => undefined
    connectGate = new Promise<void>(resolve => {
      releaseConnect = resolve
    })
    const pendingActivation = ensureGatewayForAgent('source-b', 'default')
    await vi.waitFor(() => expect(secondaryGateways[0]?.connect).toHaveBeenCalledOnce())

    disposeSecondariesForConnection('source-b')
    releaseConnect()
    await pendingActivation

    expect(secondaryGateways[0].close).toHaveBeenCalled()
    expect(onActiveConnectionChanged).not.toHaveBeenCalled()
    expect($gateway.get()).toBe(primary)
  })

  it('does not activate or publish a source whose activation owner aborted while dialing', async () => {
    const primary = makePrimary()
    const onActiveConnectionChanged = vi.fn()
    const controller = new AbortController()

    setPrimaryGateway(primary as never, 'default')
    configureGatewayRegistry({ onActiveConnectionChanged, onEvent: vi.fn() })
    ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
      getConnection: vi.fn(async profile => ({ port: 4242, profile })),
      getConnectionFor: vi.fn(async ({ connectionId, profile }) => ({ connectionId, port: 5151, profile })),
      getGatewayWsUrlFor: vi.fn(async ({ connectionId, profile }) => ({
        ok: true as const,
        wsUrl: `ws://${connectionId}/${profile}`
      })),
      touchBackend: vi.fn(async () => undefined)
    }
    await ensureGatewayForProfile('default')

    let releaseConnect: () => void = () => undefined
    connectGate = new Promise<void>(resolve => {
      releaseConnect = resolve
    })
    const abandoned = ensureGatewayForAgent('source-b', 'default', { signal: controller.signal })
    await vi.waitFor(() => expect(secondaryGateways[0]?.connect).toHaveBeenCalledOnce())

    controller.abort()
    releaseConnect()

    expect(await abandoned).toBe(false)
    expect(onActiveConnectionChanged).not.toHaveBeenCalled()
    expect($gateway.get()).toBe(primary)
  })
})

describe('retainGatewayForAgent (#93602)', () => {
  function installRegistryDesktop() {
    ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
      getConnection: vi.fn(async (profile: null | string) => ({ port: 4242, profile, token: 't' })),
      getConnectionFor: vi.fn(async ({ connectionId, profile }) => ({ connectionId, port: 5151, profile })),
      getGatewayWsUrlFor: vi.fn(async ({ connectionId, profile }) => ({
        ok: true as const,
        wsUrl: `ws://${connectionId}/${profile}`
      })),
      touchBackend: vi.fn(async () => undefined)
    }
  }

  it('holds the registry socket across multiple leased requests — no mid-turn disposal', async () => {
    const primary = makePrimary()
    setPrimaryGateway(primary as never, 'default')
    installRegistryDesktop()
    await ensureGatewayForProfile('default')

    const release = await retainGatewayForAgent('mini', 'helper')

    // The whole member-turn RPC sequence: each call takes and releases its own
    // per-request lease. Without the retain, refcount 0 between calls disposes
    // the socket and the runtime session with it.
    await requestGatewayForAgent('mini', 'helper', 'session.create', { title: 'g' })
    await requestGatewayForAgent('mini', 'helper', 'image.attach_bytes', { session_id: 'rt-1' })
    await requestGatewayForAgent('mini', 'helper', 'prompt.submit', { session_id: 'rt-1', text: 'hi' })

    expect(secondaryGateways).toHaveLength(1)
    expect(secondaryGateways[0].close).not.toHaveBeenCalled()
    expect(secondaryGateways[0].request).toHaveBeenCalledTimes(3)

    release()
    expect(secondaryGateways[0].close).toHaveBeenCalledOnce()
  })

  it('release is idempotent — a double release never underflows into a foreign disposal', async () => {
    const primary = makePrimary()
    setPrimaryGateway(primary as never, 'default')
    installRegistryDesktop()
    await ensureGatewayForProfile('default')

    const release = await retainGatewayForAgent('mini', 'helper')
    release()
    release()

    expect(secondaryGateways[0].close).toHaveBeenCalledOnce()

    // A fresh retain still works after the double release.
    const again = await retainGatewayForAgent('mini', 'helper')
    await requestGatewayForAgent('mini', 'helper', 'prompt.submit', { session_id: 'rt-2', text: 'hi' })
    expect(secondaryGateways[1].close).not.toHaveBeenCalled()
    again()
    expect(secondaryGateways[1].close).toHaveBeenCalledOnce()
  })

  it('without the retain, the leased socket closes after each request (the #93602 race)', async () => {
    const primary = makePrimary()
    setPrimaryGateway(primary as never, 'default')
    installRegistryDesktop()
    await ensureGatewayForProfile('default')

    await requestGatewayForAgent('mini', 'helper', 'session.create', { title: 'g' })

    // Refcount hit 0 → disposed: this is the socket close that reaps the
    // runtime session server-side and makes the later prompt.submit 4001.
    expect(secondaryGateways[0].close).toHaveBeenCalledOnce()
  })

  it('plain-profile retain leases the pooled profile socket and releases it', async () => {
    const primary = makePrimary()
    setPrimaryGateway(primary as never, 'default')
    installDesktop(
      vi.fn(async (profile: null | string) =>
        profile ? { port: 5151, profile, token: 'secondary-token' } : { port: 4242, token: 'primary-token' }
      )
    )
    await ensureGatewayForProfile('default')

    const release = await retainGatewayForAgent(null, 'worker')
    await requestGatewayForProfile('worker', 'session.create', { title: 'g' })
    await requestGatewayForProfile('worker', 'prompt.submit', { session_id: 'rt-1', text: 'hi' })

    expect(secondaryGateways).toHaveLength(1)
    expect(secondaryGateways[0].close).not.toHaveBeenCalled()

    release()
    expect(secondaryGateways[0].close).toHaveBeenCalledOnce()
  })
})

describe('attached shared-remote group turns (#96493)', () => {
  function installAttachedSharedRemote() {
    ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
      getConnection: vi.fn(async (profile: null | string) => ({ port: 4242, profile, token: 't' })),
      getConnectionFor: vi.fn(async ({ connectionId, profile }: { connectionId: string; profile: string }) => ({
        connectionId,
        port: 9119,
        profile,
        sharedRemote: true
      })),
      getGatewayWsUrlFor: vi.fn(async ({ connectionId, profile }: { connectionId: string; profile: string }) => ({
        ok: true as const,
        wsUrl: `ws://${connectionId}/${profile}`
      })),
      touchBackend: vi.fn(async () => undefined)
    }
  }

  it('reuses the primary socket for a named profile on the attached shared remote', async () => {
    const primary = makePrimary()
    setPrimaryGateway(primary as never, 'default')
    setPrimaryGatewayConnection({ connectionId: 'homelab' })
    installAttachedSharedRemote()
    await ensureGatewayForProfile('default')

    const release = await retainGatewayForAgent('homelab', 'voter')
    await requestGatewayForAgent('homelab', 'voter', 'session.create', { title: 'Group: room' })
    await requestGatewayForAgent('homelab', 'voter', 'prompt.submit', { session_id: 'rt-1', text: 'hi' })

    expect(secondaryGateways).toHaveLength(0)
    expect(primary.request).toHaveBeenCalledTimes(2)
    expect(primary.request).toHaveBeenNthCalledWith(1, 'session.create', {
      title: 'Group: room',
      profile: 'voter'
    })
    expect(primary.request).toHaveBeenNthCalledWith(2, 'prompt.submit', {
      session_id: 'rt-1',
      text: 'hi',
      profile: 'voter'
    })

    release()
    expect(secondaryGateways).toHaveLength(0)
  })

  it('still dials a secondary when the attached source is not a shared remote', async () => {
    const primary = makePrimary()
    setPrimaryGateway(primary as never, 'default')
    setPrimaryGatewayConnection({ connectionId: 'homelab' })
    ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
      getConnection: vi.fn(async (profile: null | string) => ({ port: 4242, profile, token: 't' })),
      getConnectionFor: vi.fn(async ({ connectionId, profile }: { connectionId: string; profile: string }) => ({
        connectionId,
        port: 5151,
        profile,
        sharedRemote: false
      })),
      getGatewayWsUrlFor: vi.fn(async ({ connectionId, profile }: { connectionId: string; profile: string }) => ({
        ok: true as const,
        wsUrl: `ws://${connectionId}/${profile}`
      })),
      touchBackend: vi.fn(async () => undefined)
    }
    await ensureGatewayForProfile('default')

    await requestGatewayForAgent('homelab', 'voter', 'session.create', { title: 'g' })

    expect(secondaryGateways).toHaveLength(1)
    expect(primary.request).not.toHaveBeenCalled()
  })

  it('reuses the primary when the shared-remote probe fails instead of dialing a ghost secondary', async () => {
    const primary = makePrimary()
    setPrimaryGateway(primary as never, 'default')
    setPrimaryGatewayConnection({ connectionId: 'homelab' })
    ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
      getConnection: vi.fn(async (profile: null | string) => ({ port: 4242, profile, token: 't' })),
      getConnectionFor: vi.fn(async () => {
        throw new Error('Timed out connecting to profile "voter"')
      }),
      getGatewayWsUrlFor: vi.fn(async () => ({ ok: true as const, wsUrl: 'ws://homelab/voter' })),
      touchBackend: vi.fn(async () => undefined)
    }
    await ensureGatewayForProfile('default')

    await requestGatewayForAgent('homelab', 'voter', 'session.create', { title: 'g' })

    expect(secondaryGateways).toHaveLength(0)
    expect(primary.request).toHaveBeenCalledOnce()
  })

  it('openGatewayForAgent and ensureGatewayForAgent do not dial a secondary', async () => {
    const primary = makePrimary()
    setPrimaryGateway(primary as never, 'default')
    setPrimaryGatewayConnection({ connectionId: 'homelab' })
    installAttachedSharedRemote()
    await ensureGatewayForProfile('default')

    await openGatewayForAgent('homelab', 'voter')
    expect(await ensureGatewayForAgent('homelab', 'voter')).toBe(true)
    expect(secondaryGateways).toHaveLength(0)
  })

  it('ensureGatewayForAgent is false when the attached primary socket is closed', async () => {
    const primary = { connectionState: 'closed', request: vi.fn() }
    setPrimaryGateway(primary as never, 'default')
    setPrimaryGatewayConnection({ connectionId: 'homelab' })
    installAttachedSharedRemote()

    expect(await ensureGatewayForAgent('homelab', 'voter')).toBe(false)
    expect(secondaryGateways).toHaveLength(0)
  })
})


it.each([false, true])('keeps silent shared-primary runtime B controls scoped across an event (event: %s)', async withEvent => {
  const primary = makePrimary()
  setPrimaryGateway(primary as never, 'default', { connectionId: 'shared' })
  installDesktop(vi.fn(async (profile: null | string) => ({ port: 4242,
    ...(profile ? { profile, sharedPrimary: true } : {}), token: 'primary-token' })))
  primary.request.mockResolvedValueOnce({ session_id: 'runtime-b' } as never)
  await requestGatewayForProfile('venture', 'session.resume', { session_id: 'stored-b', profile: 'wrong' })
  expect(primary.request).toHaveBeenLastCalledWith('session.resume', { session_id: 'stored-b', profile: 'venture' })

  if (withEvent) {
    const source = recordPrimaryRuntimeProducer({ type: 'session.info', session_id: 'runtime-b', profile: 'default' })
    expect(source).toMatchObject({ profile: 'venture', connectionId: 'shared' })
  }

  for (const method of ['session.control', 'session.control.read']) {
    await requestGatewayForProfile('venture', method, { session_id: 'runtime-b', profile: 'wrong' })
    expect(primary.request).toHaveBeenLastCalledWith(method, { session_id: 'runtime-b', profile: 'venture' })
    const count = primary.request.mock.calls.length
    await expect(requestGatewayForProfile('default', method, { session_id: 'runtime-b' })).rejects.toThrow('profile')
    expect(primary.request).toHaveBeenCalledTimes(count)
  }

  expect(secondaryGateways).toHaveLength(0)
})

it('rejects a secondary request opened before an OAuth account change, then permits a fresh request', async () => {
  let release!: (value: unknown) => void
  const held = new Promise(resolve => { release = resolve })

  const connection = { connectionId: 'oauth-opening', profile: 'worker', authMode: 'oauth',
    oauthSessionIdentity: 'new-account', baseUrl: 'https://fixture.invalid', wsUrl: 'wss://fixture.invalid/api/ws?ticket=new' }

  const getConnectionFor = vi.fn().mockReturnValueOnce(held).mockResolvedValue(connection)

  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
    getConnectionFor,
    getGatewayWsUrlFor: vi.fn(async () => ({ ok: true, wsUrl: connection.wsUrl })),
    touchBackend: vi.fn(async () => undefined)
  }
  const pending = requestGatewayForAgent('oauth-opening', 'worker', 'organization.create_objective', { title: 'old intent' })
  await vi.waitFor(() => expect(getConnectionFor).toHaveBeenCalledOnce())
  retireOAuthSession('old-account', ['oauth-opening'])
  release(connection)
  await expect(pending).rejects.toThrow('OAuth account session changed')
  expect(secondaryGateways.every(gateway => gateway.request.mock.calls.length === 0)).toBe(true)
  await expect(requestGatewayForAgent('oauth-opening', 'worker', 'organization.create_objective', { title: 'new intent' }))
    .resolves.toMatchObject({ params: { title: 'new intent' } })
})

it('closes a late OAuth handshake after account retirement before sending the held request', async () => {
  let release!: () => void
  connectGate = new Promise<void>(resolve => { release = resolve })

  const connection = { connectionId: 'oauth-handshake', profile: 'worker', authMode: 'oauth',
    oauthSessionIdentity: 'handshake-old-account', baseUrl: 'https://fixture.invalid', wsUrl: 'wss://fixture.invalid/api/ws?ticket=old' }

  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
    getConnectionFor: vi.fn(async () => connection),
    getGatewayWsUrlFor: vi.fn(async () => ({ ok: true, wsUrl: connection.wsUrl })),
    touchBackend: vi.fn(async () => undefined)
  }
  const pending = requestGatewayForAgent('oauth-handshake', 'worker', 'organization.create_objective', { title: 'old intent' })
  await vi.waitFor(() => expect(secondaryGateways[0]?.connect).toHaveBeenCalledOnce())
  retireOAuthSession('handshake-old-account', ['oauth-handshake'])
  const closeCount = secondaryGateways[0].close.mock.calls.length
  release()
  await expect(pending).rejects.toThrow('OAuth account session changed')
  expect(secondaryGateways[0].request).not.toHaveBeenCalled()
  expect(secondaryGateways[0].close.mock.calls.length).toBeGreaterThan(closeCount)
})

it.each(['changed', 'unrelated'] as const)('scopes descriptorless legacy opening to its resolved OAuth account: %s', async scenario => {
  let release!: (value: unknown) => void
  const held = new Promise(resolve => { release = resolve })

  const connection = { profile: 'legacy-worker', authMode: 'oauth', oauthSessionIdentity: scenario === 'changed' ? 'legacy-next-changed' : 'legacy-unrelated-current',
    baseUrl: 'https://legacy.invalid', wsUrl: 'wss://legacy.invalid/api/ws?ticket=new' }

  const getConnection = vi.fn().mockReturnValueOnce(held).mockResolvedValue(connection)

  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
    getConnection, getGatewayWsUrl: vi.fn(async () => ({ ok: true, wsUrl: connection.wsUrl })), touchBackend: vi.fn(async () => undefined)
  }
  const pending = requestGatewayForProfile('legacy-worker', 'organization.create_objective', { title: 'old legacy intent' })
  await vi.waitFor(() => expect(getConnection).toHaveBeenCalledOnce())
  retireOAuthSession(`legacy-old-${scenario}`, [], `legacy-next-${scenario}`)
  release(connection)

  if (scenario === 'changed') {
    await expect(pending).rejects.toThrow('OAuth account session changed')
    expect(secondaryGateways.every(gateway => gateway.request.mock.calls.length === 0)).toBe(true)
  } else {
    await expect(pending).resolves.toMatchObject({ params: { title: 'old legacy intent' } })
  }

  await expect(requestGatewayForProfile('legacy-worker', 'organization.create_objective', { title: 'new legacy intent' }))
    .resolves.toMatchObject({ params: { title: 'new legacy intent' } })
})

it.each(['oauth', 'token', 'local'] as const)('retires a pending primary before replay and preserves its %s successor', async successorKind => {
  const { JsonRpcGatewayClient } = await import('@aethermesh/shared')
  const { connectPrimaryGateway, primaryGatewayOAuthSessionIdentity } = await import('./gateway')

  class Socket extends EventTarget {
    readyState = 0
    frames: unknown[] = []
    send(payload: string) { this.frames.push(JSON.parse(payload)) }
    close() { this.readyState = 3; this.dispatchEvent(new Event('close')) }
    open() { this.readyState = 1; this.dispatchEvent(new Event('open')) }
  }
  const sockets: Socket[] = []

  const client = new JsonRpcGatewayClient({ connectTimeoutMs: 100, socketFactory: () => {
    const socket = new Socket()
    sockets.push(socket)

    return socket as never
  } })

  const gateway = client as unknown as Parameters<typeof setPrimaryGateway>[0]

  const accountA = `pending-primary-a-${successorKind}`
  const accountB = `pending-primary-b-${successorKind}`
  const accountC = `pending-primary-c-${successorKind}`

  const descriptor = (identity: string) => {
    const successor = identity === accountC && successorKind !== 'oauth'

    return { mode: successor && successorKind === 'local' ? 'local' : 'remote', connectionId: 'pending-primary',
      profile: 'default', authMode: successor ? successorKind : 'oauth', baseUrl: 'https://fixture.invalid',
      wsUrl: 'wss://fixture.invalid/api/ws', oauthSessionIdentity: successor ? undefined : identity }
  }

  setPrimaryGateway(gateway, 'default')

  try {
    const first = connectPrimaryGateway(gateway!, descriptor(accountA) as never, 'wss://fixture.invalid/api/ws')
    await Promise.resolve()
    sockets[0].open()
    await first
    sockets[0].dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ jsonrpc: '2.0', method: 'event',
      params: { type: 'message.delta', session_id: 'old-session', seq: 3, payload: {} } }) }))
    expect(client.getSeqWatermarks()).toEqual({ 'old-session': 3 })
    retireOAuthSession(accountA, ['pending-primary'], accountB)

    const pending = connectPrimaryGateway(gateway!, descriptor(accountB) as never, 'wss://fixture.invalid/api/ws')
      .then(() => ({ ok: true }), error => ({ error }))

    await Promise.resolve()
    retireOAuthSession(accountB, ['pending-primary'], accountC)
    sockets[1].open()
    expect(sockets[1].frames).toEqual([])
    const successor = connectPrimaryGateway(gateway!, descriptor(accountC) as never, 'wss://fixture.invalid/api/ws')
    await Promise.resolve()
    expect(primaryGatewayOAuthSessionIdentity()).toBe(successorKind === 'oauth' ? accountC : undefined)
    retireOAuthSession(accountA)
    expect(sockets[2].readyState).toBe(0)
    sockets[2].open()
    await successor
    expect(await pending).toHaveProperty('error')
    expect(client.connectionState).toBe('open')
    expect(sockets[2].readyState).toBe(1)
    expect(sockets[2].frames).toEqual([])
    expect(client.getSeqWatermarks()).toEqual({})

    // A normal reconnect under C must retain C's legitimate resume cursor.
    sockets[2].dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ jsonrpc: '2.0', method: 'event',
      params: { type: 'message.delta', session_id: 'c-session', seq: 1, payload: {} } }) }))
    client.close()
    const sameAccount = connectPrimaryGateway(gateway!, descriptor(accountC) as never, 'wss://fixture.invalid/api/ws')
    await Promise.resolve()
    sockets[3].open()
    await sameAccount
    expect(sockets[3].frames).toEqual([expect.objectContaining({ method: 'session.events.since',
      params: { session_id: 'c-session', last_seen: 1 } })])
  } finally {
    client.close()
    setPrimaryGateway(null)
  }
})

it('does not let a retired partial replay publish or flush a successor account replay hold', async () => {
  const { JsonRpcGatewayClient } = await import('@aethermesh/shared')

  class Socket extends EventTarget {
    readyState = 0
    frames: Array<{ id: string; method: string; params: Record<string, unknown> }> = []
    send(payload: string) { this.frames.push(JSON.parse(payload)) }
    close() { this.readyState = 3; this.dispatchEvent(new Event('close')) }
    open() { this.readyState = 1; this.dispatchEvent(new Event('open')) }
    message(frame: unknown) { this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(frame) })) }
    event(session: string, seq: number) {
      this.message({ jsonrpc: '2.0', method: 'event', params: { type: 'message.delta', session_id: session, seq, payload: {} } })
    }
  }
  const sockets: Socket[] = []

  const client = new JsonRpcGatewayClient({ socketFactory: () => {
    const socket = new Socket()
    sockets.push(socket)

    return socket as never
  } })

  const observed: unknown[] = []
  client.onEvent(event => observed.push(event))

  try {
    const first = client.connect('wss://fixture.invalid/api/ws', 'batch-a')
    sockets[0].open()
    await first
    sockets[0].event('a-first', 1)
    sockets[0].event('a-second', 1)
    client.close()
    const replayA = client.connect('wss://fixture.invalid/api/ws', 'batch-a')
    sockets[1].open()
    await replayA
    expect(sockets[1].frames).toHaveLength(2)
    sockets[1].message({ jsonrpc: '2.0', id: sockets[1].frames[0].id, result: { epoch: 'a-epoch',
      events: [{ type: 'message.delta', session_id: 'a-first', seq: 2 }] } })
    sockets[1].event('a-first', 3)

    // Switch before A's second replay response settles. Closing rejects it;
    // A's already-fulfilled first result must not publish later under C.
    const firstC = client.connect('wss://fixture.invalid/api/ws', 'batch-c')
    sockets[2].open()
    expect(sockets[2].frames).toEqual([])
    sockets[2].event('c-only', 1)
    client.close()
    const replayC = client.connect('wss://fixture.invalid/api/ws', 'batch-c')
    sockets[3].open()
    expect(sockets[3].frames).toEqual([expect.objectContaining({ method: 'session.events.since',
      params: { session_id: 'c-only', last_seen: 1 } })])
    sockets[3].event('c-only', 3)
    observed.length = 0
    await Promise.all([firstC, replayC])
    await Promise.resolve()
    await Promise.resolve()
    expect(observed).toEqual([])
    expect(client.getSeqWatermarks()).toEqual({ 'c-only': 1 })
    sockets[3].message({ jsonrpc: '2.0', id: sockets[3].frames[0].id, result: { epoch: 'c-epoch',
      events: [{ type: 'message.delta', session_id: 'c-only', seq: 2 }] } })
    await vi.waitFor(() => expect(observed).toEqual([
      expect.objectContaining({ session_id: 'c-only', seq: 2 }),
      expect.objectContaining({ session_id: 'c-only', seq: 3 })
    ]))
    expect(client.getSeqWatermarks()).toEqual({ 'c-only': 3 })
  } finally { client.close() }
})
