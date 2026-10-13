import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const gatewayMocks = vi.hoisted(() => {
  const instances: Array<{
    connectionState: string
    queuedState: (state: string) => () => void
    queuedEvent: (event: { payload?: Record<string, unknown>; session_id?: string; type: string }) => () => void
    emit: (event: { payload?: Record<string, unknown>; session_id?: string; type: string }) => void
    request: ReturnType<typeof vi.fn>
  }> = []

  return { instances }
})

vi.mock('@/eidolon', async importActual => ({
  ...(await importActual<Record<string, unknown>>()),
  setApiRequestConnection: vi.fn(),
  HermesGateway: class {
    connectionState = 'closed'
    private stateHandlers = new Set<(state: string) => void>()
    private eventHandlers = new Set<
      (event: { payload?: Record<string, unknown>; session_id?: string; type: string }) => void
    >()
    request = vi.fn(async (method: string, params: Record<string, unknown>) => ({ method, params }))

    constructor() {
      gatewayMocks.instances.push(this as never)
    }

    connect = async (): Promise<void> => {
      this.connectionState = 'open'

      for (const handler of this.stateHandlers) {handler('open')}
    }

    close = (): void => {
      this.connectionState = 'closed'

      for (const handler of this.stateHandlers) {handler('closed')}
    }

    onEvent = (
      handler: (event: { payload?: Record<string, unknown>; session_id?: string; type: string }) => void
    ): (() => void) => {
      this.eventHandlers.add(handler)

      return () => this.eventHandlers.delete(handler)
    }

    onState = (handler: (state: string) => void): (() => void) => {
      this.stateHandlers.add(handler)

      return () => this.stateHandlers.delete(handler)
    }

    queuedState = (state: string) => {
      const handlers = [...this.stateHandlers]

      return () => { for (const handler of handlers) {handler(state)} }
    }

    queuedEvent = (event: { payload?: Record<string, unknown>; session_id?: string; type: string }) => {
      const handlers = [...this.eventHandlers]

      return () => { for (const handler of handlers) {handler(event)} }
    }

    emit = (event: { payload?: Record<string, unknown>; session_id?: string; type: string }): void => {
      for (const handler of this.eventHandlers) {
        handler(event)
      }
    }
  }
}))

const {
  closeSecondaryGateways,
  configureGatewayRegistry,
  ensureGatewayForProfile,
  pruneSecondaryGateways,
  recordPrimaryRuntimeProducer,
  retireLocalProfileGateways,
  requestGatewayForAgent,
  requestGatewayForProfile,
  setPrimaryGateway
} = await import('./gateway')

const { $profiles } = await import('./profile')

const { $activeSessionId, _resetSessionOwnerHintsForTests, setSessionOwnerHint, setSessions } =
  await import('./session')

const { $gateway } = await import('./gateway')
const { clearAllPrompts } = await import('./prompts')

const {
  $sessionStates,
  $sessionTiles,
  clearAllSessionStates,
  forgetProfileOnlyRuntimeOwners,
  knownOwnerForSession,
  recordSessionEventScope,
  requestForOwnedSession
} = await import('./session-states')

const { isSessionOwnerResolutionError } = await import('./session-owner-resolution')

function installDesktop(): void {
  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
    getConnection: vi.fn(async (profile: string) => ({
      authMode: 'token',
      connectionId: 'local',
      mode: 'local',
      profile,
      token: 'test-token',
      wsUrl: `wss://${profile}.invalid/ws`
    })),
    notify: vi.fn()
  }
}

beforeEach(() => {
  installDesktop()
  gatewayMocks.instances.length = 0
  $profiles.set([{ name: 'default' }, { name: 'research' }] as never)
  $sessionTiles.set([])
  setSessions([])
  clearAllSessionStates()
  clearAllPrompts()
  $activeSessionId.set(null)
  _resetSessionOwnerHintsForTests({ storage: true })

  // This is the exact event fan-in installed by useGatewayBoot: the secondary
  // producer calls config.onEvent, which records scope before UI dispatch.
  configureGatewayRegistry({
    onLocalProfileRetired: forgetProfileOnlyRuntimeOwners,
    onEvent: event => {
      recordSessionEventScope(event)
    }
  })
  const primary = { connectionState: 'open', request: vi.fn() }
  $gateway.set(primary as never)
  setPrimaryGateway(primary as never, 'default')
})

afterEach(() => {
  closeSecondaryGateways()
  vi.useRealTimers()
  clearAllSessionStates()
  $sessionTiles.set([])
  $profiles.set([])
  setSessions([])
  _resetSessionOwnerHintsForTests({ storage: true })
  clearAllPrompts()
  $activeSessionId.set(null)
  $gateway.set(null)
  delete (window as { hermesDesktop?: unknown }).hermesDesktop
})

describe('profile-only secondary approval ownership', () => {
  it('keeps a profile-only secondary event owner across a focus switch and dispatches approval on that secondary', async () => {
    await expect(ensureGatewayForProfile('research')).resolves.toBeUndefined()
    const secondary = gatewayMocks.instances[0]

    expect(secondary).toBeTruthy()
    // No tile, row, hint, or runtime→stored state mirror is available.
    expect($sessionTiles.get()).toEqual([])
    expect($sessionStates.get()).toEqual({})

    secondary.emit({ session_id: 'rt-secondary', type: 'approval.request' })
    await expect(ensureGatewayForProfile('default')).resolves.toBeUndefined()

    expect(knownOwnerForSession('rt-secondary')).toBe('research')

    const ambient = vi.fn(async () => ({ via: 'ambient' }))
    await expect(
      requestForOwnedSession('rt-secondary', ambient as never, 'approval.respond', {
        choice: 'once',
        session_id: 'rt-secondary'
      })
    ).resolves.toEqual({
      method: 'approval.respond',
      params: { choice: 'once', session_id: 'rt-secondary' }
    })

    expect(secondary.request).toHaveBeenCalledWith('approval.respond', {
      choice: 'once',
      session_id: 'rt-secondary'
    })
    expect(ambient).not.toHaveBeenCalled()
  })

  it('answers an established secondary approval after the physical primary respawns with the same profile', async () => {
    await ensureGatewayForProfile('research')
    const secondary = gatewayMocks.instances[0]
    secondary.emit({ session_id: 'rt-before-respawn', type: 'approval.request' })
    const replacement = { connectionState: 'open', request: vi.fn() }
    setPrimaryGateway(replacement as never, 'research', { connectionId: 'local' })
    const ambient = vi.fn()
    await expect(requestForOwnedSession('rt-before-respawn', ambient as never, 'approval.respond', {
      session_id: 'rt-before-respawn', choice: 'once'
    })).resolves.toEqual({ method: 'approval.respond', params: { session_id: 'rt-before-respawn', choice: 'once' } })
    expect(secondary.request).toHaveBeenCalledWith('approval.respond', { session_id: 'rt-before-respawn', choice: 'once' })
    expect(replacement.request).not.toHaveBeenCalled()
    expect(ambient).not.toHaveBeenCalled()
  })

  it('keeps paired local/profile approvals on their producing generations and releases the actual prompt hold at terminal', async () => {
    vi.useFakeTimers()
    await ensureGatewayForProfile('research')
    const secondary = gatewayMocks.instances[0]
    secondary.emit({ session_id: 'old-runtime', type: 'approval.request' })
    // Durable exact owner hints can name the same logical tuple as the new
    // primary. The original producer remains the authority for this runtime.
    setSessionOwnerHint('old-runtime', { connectionId: 'local', profile: 'research' })
    const replacement = { connectionState: 'open', request: vi.fn(async () => ({ via: 'new-primary' })) }
    setPrimaryGateway(replacement as never, 'research', { connectionId: 'local' })
    const event = { session_id: 'new-runtime', type: 'approval.request', profile: 'research', connectionId: 'local' }
    recordPrimaryRuntimeProducer(event)
    recordSessionEventScope(event)
    const ambient = vi.fn()
    await expect(requestForOwnedSession('old-runtime', ambient as never, 'approval.respond', { session_id: 'old-runtime' }))
      .resolves.toEqual({ method: 'approval.respond', params: { session_id: 'old-runtime' } })
    await expect(requestForOwnedSession('new-runtime', ambient as never, 'approval.respond', { session_id: 'new-runtime' }))
      .resolves.toEqual({ via: 'new-primary' })
    expect(replacement.request).toHaveBeenCalledTimes(1)
    secondary.request.mockResolvedValue({ status: 'streaming' })
    await requestForOwnedSession('old-runtime', ambient as never, 'prompt.submit', { session_id: 'old-runtime', prompt: 'continue' })
    await ensureGatewayForProfile('research')
    pruneSecondaryGateways(new Set())
    expect(secondary.connectionState).toBe('open')
    secondary.emit({ session_id: 'old-runtime', type: 'session.info', payload: { running: false } })
    await vi.advanceTimersByTimeAsync(501)
    pruneSecondaryGateways(new Set())
    expect(secondary.connectionState).toBe('closed')
    expect(ambient).not.toHaveBeenCalled()
    expect(replacement.request).toHaveBeenCalledTimes(1)
    await expect(requestForOwnedSession('old-runtime', ambient as never, 'approval.respond', { session_id: 'old-runtime' }))
      .rejects.toThrow('runtime transport is unavailable')
    expect(replacement.request).toHaveBeenCalledTimes(1)
  })

  it('does not let a retired generation hold pin an idle replacement at the same profile', async () => {
    await ensureGatewayForProfile('research')
    const retired = gatewayMocks.instances[0]
    retired.emit({ session_id: 'retired-turn', type: 'approval.request' })
    retired.request.mockResolvedValue({ status: 'streaming' })
    await requestForOwnedSession('retired-turn', vi.fn() as never, 'prompt.submit', { session_id: 'retired-turn' })
    const holds = (globalThis as unknown as Record<symbol, { turnLeases: Map<string, unknown> }>)[Symbol.for('hermes.desktop.gatewayRegistryState')].turnLeases
    expect(holds.size).toBe(1)
    retireLocalProfileGateways('research')
    expect(holds.size).toBe(0)
    expect(retired.connectionState).toBe('closed')
    await ensureGatewayForProfile('research')
    const replacement = gatewayMocks.instances[1]
    expect(replacement.connectionState).toBe('open')
    await ensureGatewayForProfile('default')
    pruneSecondaryGateways(new Set())
    expect(replacement.connectionState).toBe('closed')
  })

  it('ignores retired callbacks while a same-scope replacement owns a live generation hold', async () => {
    vi.useFakeTimers()
    await ensureGatewayForProfile('research')
    const retired = gatewayMocks.instances[0]
    retired.emit({ session_id: 'aba-runtime', type: 'approval.request' })
    retired.request.mockResolvedValue({ status: 'streaming' })
    await requestForOwnedSession('aba-runtime', vi.fn() as never, 'prompt.submit', { session_id: 'aba-runtime' })
    const lateClose = retired.queuedState('closed')
    const lateTerminal = retired.queuedEvent({ session_id: 'aba-runtime', type: 'session.info', payload: { running: false } })
    retireLocalProfileGateways('research')
    await ensureGatewayForProfile('research')
    const replacement = gatewayMocks.instances[1]
    replacement.emit({ session_id: 'aba-runtime', type: 'approval.request' })
    replacement.request.mockResolvedValue({ status: 'streaming' })
    await requestForOwnedSession('aba-runtime', vi.fn() as never, 'prompt.submit', { session_id: 'aba-runtime' })
    await ensureGatewayForProfile('default')
    lateClose()
    lateTerminal()
    await vi.advanceTimersByTimeAsync(501)
    pruneSecondaryGateways(new Set())
    expect(replacement.connectionState).toBe('open')
    replacement.emit({ session_id: 'aba-runtime', type: 'session.info', payload: { running: false } })
    await vi.advanceTimersByTimeAsync(501)
    pruneSecondaryGateways(new Set())
    expect(replacement.connectionState).toBe('closed')
  })

  it.each(['session.resume', 'session.delete'])('routes durable %s IDs by logical owner despite colliding runtime IDs', async method => {
    await ensureGatewayForProfile('research')
    const secondary = gatewayMocks.instances[0]
    secondary.emit({ session_id: 'stored-on-primary', type: 'approval.request' })
    const primary = { connectionState: 'open', request: vi.fn(async () => ({ session_id: 'resumed-primary' })) }
    setPrimaryGateway(primary as never, 'research', { connectionId: 'local' })
    recordPrimaryRuntimeProducer({ type: 'approval.request', session_id: 'stored-on-secondary' })
    await expect(requestGatewayForAgent('local', 'research', method, { session_id: 'stored-on-primary' }))
      .resolves.toEqual({ session_id: 'resumed-primary' })
    expect(primary.request).toHaveBeenCalledWith(method, { session_id: 'stored-on-primary' })
    expect(secondary.request).not.toHaveBeenCalled()
    await requestGatewayForProfile('research', method, { session_id: 'stored-on-secondary' })
    expect(secondary.request).toHaveBeenCalledWith(method, { session_id: 'stored-on-secondary' })
    expect(primary.request).toHaveBeenCalledTimes(1)
  })

  it.each(['session.resume', 'session.delete'])('does not let retired runtime proof veto a durable %s identity', async method => {
    const retired = { connectionState: 'open', request: vi.fn() }
    setPrimaryGateway(retired as never, 'research', { connectionId: 'local' })
    recordPrimaryRuntimeProducer({ type: 'approval.request', session_id: 'stored-collision' })
    retired.connectionState = 'closed'
    const replacement = { connectionState: 'open', request: vi.fn(async () => ({ session_id: 'new-runtime' })) }
    setPrimaryGateway(replacement as never, 'research', { connectionId: 'local' })
    await expect(requestGatewayForAgent('local', 'research', method, { session_id: 'stored-collision' }))
      .resolves.toEqual({ session_id: 'new-runtime' })
    await expect(requestGatewayForProfile('research', method, { session_id: 'stored-collision' }))
      .resolves.toEqual({ session_id: 'new-runtime' })
    expect(replacement.request).toHaveBeenCalledTimes(2)
    expect(retired.request).not.toHaveBeenCalled()
  })

  it('does not route an exact remote primary owner through a same-named legacy secondary', async () => {
    await ensureGatewayForProfile('research')
    const secondary = gatewayMocks.instances[0]
    const remote = { connectionState: 'open', request: vi.fn(async () => ({ via: 'remote-primary' })) }
    setPrimaryGateway(remote as never, 'research', { connectionId: 'remote-owner' })
    await expect(requestGatewayForAgent('remote-owner', 'research', 'approval.respond', { session_id: 'remote' }))
      .resolves.toEqual({ via: 'remote-primary' })
    expect(remote.request).toHaveBeenCalledWith('approval.respond', { session_id: 'remote' })
    expect(secondary.request).not.toHaveBeenCalled()
  })

  it('rejects unproven profiles and retires only transient local ownership, never durable exact routes', async () => {
    recordSessionEventScope({ profile: 'research', session_id: 'rt-unproven' })
    expect(knownOwnerForSession('rt-unproven')).toBeUndefined()
    await expect(requestForOwnedSession('rt-unproven', vi.fn() as never, 'approval.respond', {})).rejects.toSatisfy(
      isSessionOwnerResolutionError
    )

    await ensureGatewayForProfile('research')
    const secondary = gatewayMocks.instances[0]
    secondary.emit({ session_id: 'rt-retired', type: 'approval.request' })
    secondary.emit({ session_id: 'rt-durable', type: 'approval.request' })
    expect(knownOwnerForSession('rt-retired')).toBe('research')
    const exact = { connectionId: 'remote-same-name', profile: 'research' }
    setSessionOwnerHint('rt-durable', exact)
    recordSessionEventScope({ ...exact, session_id: 'rt-remote' })
    expect(knownOwnerForSession('rt-durable')).toEqual(exact)
    retireLocalProfileGateways('research')
    expect(knownOwnerForSession('rt-retired')).toBeUndefined()
    expect(knownOwnerForSession('rt-durable')).toEqual(exact)
    expect(knownOwnerForSession('rt-remote')).toEqual(exact)
    const getConnection = window.hermesDesktop!.getConnection
    vi.mocked(getConnection).mockClear()
    await expect(requestForOwnedSession('rt-retired', vi.fn() as never, 'approval.respond', {})).rejects.toSatisfy(
      isSessionOwnerResolutionError
    )
    expect(getConnection).not.toHaveBeenCalled()
  })
})


it.each(['session.title', 'slash.exec', 'handoff.state', 'handoff.fail', 'process.list', 'process.kill',
  'rollback.list', 'rollback.restore', 'rollback.diff', 'command.dispatch', 'tools.configure', 'tools.show', 'tools.list', 'toolsets.list', 'model.options', 'reload.mcp', 'session.set_hidden', 'subagent.list', 'subagent.interrupt', 'subagent.tail'])('routes live %s to its same-profile producer and rejects retired proof', async method => {
  await ensureGatewayForProfile('research')
  const secondary = gatewayMocks.instances[0]
  secondary.emit({ session_id: 'runtime-command', type: 'approval.request' })
  const replacement = { connectionState: 'open', request: vi.fn() }
  setPrimaryGateway(replacement as never, 'research', { connectionId: 'local' })
  const params = { session_id: 'runtime-command', title: 'Updated title', command: '/title Updated title' }
  await expect(requestGatewayForAgent('local', 'research', method, params)).resolves.toEqual({ method, params })
  expect(secondary.request).toHaveBeenLastCalledWith(method, params)
  expect(replacement.request).not.toHaveBeenCalled()
  retireLocalProfileGateways('research')
  await expect(requestGatewayForAgent('local', 'research', method, params)).rejects.toThrow('runtime transport is unavailable')
  expect(replacement.request).not.toHaveBeenCalled()
})


it('keeps session config.set on its producer and never falls through to replacement global settings', async () => {
  await ensureGatewayForProfile('research')
  const secondary = gatewayMocks.instances[0]
  secondary.emit({ session_id: 'runtime-model', type: 'session.info' })
  let globalWrites = 0

  const replacement = { connectionState: 'open', request: vi.fn(async () => { globalWrites++;

 return { saved: true } }) }

  setPrimaryGateway(replacement as never, 'research', { connectionId: 'local' })
  const params = { session_id: 'runtime-model', key: 'model', value: 'fixture-model --session' }
  await expect(requestGatewayForAgent('local', 'research', 'config.set', params)).resolves.toEqual({ method: 'config.set', params })
  expect(secondary.request).toHaveBeenLastCalledWith('config.set', params)
  expect(globalWrites).toBe(0)
  retireLocalProfileGateways('research')
  await expect(requestGatewayForAgent('local', 'research', 'config.set', params)).rejects.toThrow('runtime transport is unavailable')
  expect(globalWrites).toBe(0)
  await expect(requestGatewayForAgent('local', 'research', 'config.set', { key: 'model', value: 'explicit-profile-default' })).resolves.toEqual({ saved: true })
  expect(globalWrites).toBe(1)
})
