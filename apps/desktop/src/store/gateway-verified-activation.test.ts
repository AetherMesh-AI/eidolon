import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import type { HermesConnection } from '@/global'

import { deferred } from '../test/deferred'

let connectGate: Promise<void> | null = null

const sockets: Array<{
  connectionState: string
  emit: (state: string) => void
  request: ReturnType<typeof vi.fn>
  close: ReturnType<typeof vi.fn>
}> = []

vi.mock('@/eidolon', () => ({
  HermesGateway: class {
    connectionState = 'closed'
    state = (_state: string) => {}
    emit(state: string) {
      this.connectionState = state
      this.state(state)
    }
    connect = vi.fn(async () => {
      if (connectGate) {
        await connectGate
      }

      this.emit('open')
    })
    request = vi.fn(async () => ({ state: 'ready' }))
    close = vi.fn(() => {
      this.connectionState = 'closed'
    })
    onEvent = vi.fn(() => () => {})
    onState = vi.fn((listener: (state: string) => void) => {
      this.state = listener

      return () => {}
    })
    constructor() {
      sockets.push(this)
    }
  },
  setApiRequestConnection: vi.fn()
}))
vi.mock('@/store/session', () => ({ setConnection: vi.fn(), setGatewayState: vi.fn() }))
vi.mock('@/store/notify-baseline', () => ({ markNativeNotifyBaseline: vi.fn() }))

const {
  activeGateway,
  closeSecondaryGateways,
  configureGatewayRegistry,
  ensureGatewayForAgent,
  pruneSecondaryGateways,
  setPrimaryGateway
} = await import('./gateway')

const primary = { connectionState: 'open', request: vi.fn() }
let descriptor: HermesConnection
const publish = vi.fn()
beforeEach(() => {
  closeSecondaryGateways()
  sockets.length = 0
  connectGate = null
  publish.mockClear()
  descriptor = {
    mode: 'local',
    connectionId: 'local',
    registryScoped: true,
    profile: 'org-one',
    baseUrl: 'http://127.0.0.1:6161',
    token: 'synthetic',
    wsUrl: 'ws://127.0.0.1:6161/ws'
  } as HermesConnection
  window.hermesDesktop = {
    getConnectionFor: vi.fn(async () => descriptor),
    getGatewayWsUrlFor: vi.fn(async () => descriptor.wsUrl)
  } as never
  configureGatewayRegistry({ onEvent: vi.fn(), onActiveConnectionChanged: publish })
  setPrimaryGateway(primary as never, 'default')
})
afterEach(() => {
  closeSecondaryGateways()
  vi.useRealTimers()
  delete (window as { hermesDesktop?: unknown }).hermesDesktop
})

it('proves the retained dedicated socket before reset and publishes that exact opened descriptor', async () => {
  const proof = deferred<void>()
  const entered = deferred<void>()
  const beforeActivate = vi.fn(() => true)
  const verified = vi.fn()

  const verify = vi.fn(async (gateway, connection) => {
    expect(connection).toBe(descriptor)
    expect(gateway).toBe(sockets[0])
    await gateway.request('organization.descriptor', {})
    entered.resolve()
    await proof.promise
  })

  const operation = ensureGatewayForAgent('local', 'org-one', {
    verify,
    beforeActivate,
    onVerifiedConnection: verified
  })

  await entered.promise
  expect(beforeActivate).not.toHaveBeenCalled()
  expect(activeGateway()).toBe(primary)
  pruneSecondaryGateways(new Set())
  expect(sockets[0].close).not.toHaveBeenCalled()
  const opened = descriptor
  descriptor = { ...descriptor, baseUrl: 'http://127.0.0.1:7777' }
  proof.resolve()
  await expect(operation).resolves.toBe(true)
  expect(beforeActivate).toHaveBeenCalledTimes(1)
  expect(publish).toHaveBeenCalledWith(opened)
  expect(verified).toHaveBeenCalledWith(opened)
  expect(activeGateway()).toBe(sockets[0])

  // Matching the physical primary coordinate must still dial a separate socket.
  closeSecondaryGateways()
  setPrimaryGateway(primary as never, 'default', { connectionId: 'local' })
  publish.mockClear()
  descriptor = { ...descriptor, profile: 'default' }
  await expect(
    ensureGatewayForAgent('local', 'default', {
      verify: async gateway => {
        expect(gateway).not.toBe(primary)
        expect(activeGateway()).toBe(primary)
        expect(publish).not.toHaveBeenCalled()
        await gateway.request('organization.descriptor', {})
      },
      beforeActivate: () => true
    })
  ).resolves.toBe(true)
  expect(activeGateway()).toBe(sockets.at(-1))
  expect(primary.request).not.toHaveBeenCalled()
  expect(sockets.at(-1)!.request).toHaveBeenCalledWith('organization.descriptor', {})
})

it('rejects mismatched, cancelled, timed-out or changed transport proof without activating a foreign owner', async () => {
  for (const failure of [
    'mismatch',
    'remote',
    'shared',
    'abort',
    'timeout',
    'reconnect',
    'reset-false',
    'reset-throw',
    'reset-reconnect',
    'queued-source',
    'reset-source'
  ]) {
    closeSecondaryGateways()
    setPrimaryGateway(primary as never, 'default')
    publish.mockClear()
    descriptor = { ...descriptor, mode: failure === 'remote' ? 'remote' : 'local', sharedRemote: failure === 'shared' }
    const controller = new AbortController()
    let sourceCurrent = true

    const beforeActivate = vi.fn(() => {
      if (failure === 'reset-throw') {
        throw new Error('reset failed')
      }

      if (failure === 'reset-reconnect') {
        sockets.at(-1)!.emit('open')
      }

      if (failure === 'reset-source') {
        sourceCurrent = false
      }

      return failure !== 'reset-false'
    })

    let proofSignal: AbortSignal | undefined

    const verify = vi.fn(async (_gateway, _connection, signal: AbortSignal) => {
      proofSignal = signal

      if (failure === 'mismatch') {
        throw new Error('wrong owner')
      }

      if (failure === 'abort') {
        controller.abort()
        await new Promise(() => {})
      }

      if (failure === 'timeout') {
        await new Promise(() => {})
      }

      if (failure === 'reconnect') {
        sockets.at(-1)!.emit('open')
      }

      if (failure === 'queued-source') {
        queueMicrotask(() => {
          sourceCurrent = false
        })
      }

      return () => sourceCurrent
    })

    if (failure === 'timeout') {
      vi.useFakeTimers()
    }

    const operation = ensureGatewayForAgent('local', 'org-one', { verify, beforeActivate, signal: controller.signal })
    const settled = operation.catch(() => false)

    if (failure === 'timeout') {
      await vi.advanceTimersByTimeAsync(16_000)
    }

    expect(await settled, failure).toBe(false)
    expect(activeGateway(), failure).toBe(primary)
    expect(publish, failure).not.toHaveBeenCalled()

    if (!failure.startsWith('reset')) {
      expect(beforeActivate, failure).not.toHaveBeenCalled()
    }

    if (failure === 'abort' || failure === 'timeout') {
      expect(proofSignal?.aborted).toBe(true)
    }

    vi.useRealTimers()
  }

  closeSecondaryGateways()
  setPrimaryGateway(primary as never, 'default')
  descriptor = { ...descriptor, mode: 'local', sharedRemote: false }
  const dial = deferred<void>()
  connectGate = dial.promise
  const oldController = new AbortController()
  const old = ensureGatewayForAgent('local', 'org-one', { verify: async () => {}, signal: oldController.signal })
  oldController.abort()
  const entered = deferred<void>()
  const proof = deferred<void>()

  const replacement = ensureGatewayForAgent('local', 'org-one', {
    verify: async () => {
      entered.resolve()
      await proof.promise
    }
  })

  dial.resolve()
  await entered.promise
  expect(await old).toBe(false)
  pruneSecondaryGateways(new Set())
  expect(sockets.at(-1)!.close).not.toHaveBeenCalled()
  proof.resolve()
  expect(await replacement).toBe(true)
})
