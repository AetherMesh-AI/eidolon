import assert from 'node:assert/strict'

import { test, vi } from 'vitest'

import { createSourcePythonBackend } from './archive-update-caller'
import { createFirstRunSetupGate } from './first-run-setup-gate'
import {
  capturePrimaryBackendLaunch,
  createPrimaryRemoteConnection,
  FirstRunSetupResetError,
  runPrimaryBackendStartup
} from './primary-backend-startup'

const bootstrapBackend = {
  activeRoot: '/tmp/hermes-home/hermes-agent',
  kind: 'bootstrap-needed',
  platform: 'linux'
}

function startupOptions(overrides: Record<string, unknown> = {}) {
  return {
    connectRemote: vi.fn(async remote => ({ baseUrl: remote.baseUrl, mode: 'remote' as const })),
    ensureLocalRuntime: vi.fn(async backend => ({ ...backend, command: 'hermes' })),
    prepareLocalBackend: vi.fn(async () => bootstrapBackend),
    resolveRemote: vi.fn(async () => null),
    waitForDecision: vi.fn(async () => 'continue-local' as const),
    waitForLocalStart: vi.fn(async () => {}),
    ...overrides
  }
}

test('primary remote descriptor preserves a resolved registry connection id', () => {
  const connection = createPrimaryRemoteConnection(
    {
      authMode: 'token',
      baseUrl: 'https://gateway.example.com',
      connectionId: 'skateway',
      remoteKind: 'url',
      source: 'settings',
      token: 'secret',
      wsUrl: 'wss://gateway.example.com/api/ws'
    },
    ['ready'],
    { isFullscreen: false }
  )

  assert.equal(connection.connectionId, 'skateway')
  assert.equal(connection.mode, 'remote')
  assert.deepEqual(connection.logs, ['ready'])
  assert.equal(connection.isFullscreen, false)
})

test('primary remote descriptor preserves the effective SSH dialing identity', () => {
  const ssh = {
    effectiveConfigFingerprint: 'effective-config',
    host: 'build-host',
    remoteHermesPath: '/srv/hermes',
    remoteProfile: 'default',
    user: 'alice'
  }

  const connection = createPrimaryRemoteConnection(
    {
      baseUrl: 'http://127.0.0.1:49152',
      remoteKind: 'ssh',
      ssh,
      token: 'secret',
      wsUrl: 'ws://127.0.0.1:49152/api/ws'
    },
    [],
    {}
  )

  assert.equal(connection.ssh, ssh)
  assert.equal(connection.ssh?.effectiveConfigFingerprint, 'effective-config')
})

test('primary remote descriptor keeps legacy unregistered routes unqualified', () => {
  const connection = createPrimaryRemoteConnection(
    {
      baseUrl: 'https://env.example.com',
      source: 'env',
      token: 'secret',
      wsUrl: 'wss://env.example.com/api/ws'
    },
    [],
    {}
  )

  assert.equal('connectionId' in connection, false)
})

test('remote apply re-resolves the saved connection without ensuring a local runtime', async () => {
  const gate = createFirstRunSetupGate({ stuckAfterMs: 0 })
  const savedRemote = { baseUrl: 'https://gateway.example.com/hermes' }
  let configuredRemote: typeof savedRemote | null = null

  const options = startupOptions({
    resolveRemote: vi.fn(async () => configuredRemote),
    waitForDecision: gate.wait
  })

  const pending = runPrimaryBackendStartup(options)

  await vi.waitFor(() => assert.equal(gate.hasWaiter(), true))
  configuredRemote = savedRemote
  assert.equal(gate.abandonForRemoteApply(), true)

  assert.deepEqual(await pending, {
    kind: 'remote',
    connection: { baseUrl: savedRemote.baseUrl, mode: 'remote' }
  })
  assert.deepEqual(options.resolveRemote.mock.calls, [[], []])
  assert.deepEqual(options.connectRemote.mock.calls, [[savedRemote]])
  assert.equal(options.ensureLocalRuntime.mock.calls.length, 0)
})

test('an already-saved remote bypasses every local startup step', async () => {
  const savedRemote = { baseUrl: 'https://gateway.example.com/hermes' }
  const options = startupOptions({ resolveRemote: vi.fn(async () => savedRemote) })

  assert.deepEqual(await runPrimaryBackendStartup(options), {
    kind: 'remote',
    connection: { baseUrl: savedRemote.baseUrl, mode: 'remote' }
  })
  assert.equal(options.waitForLocalStart.mock.calls.length, 0)
  assert.equal(options.prepareLocalBackend.mock.calls.length, 0)
  assert.equal(options.waitForDecision.mock.calls.length, 0)
  assert.equal(options.ensureLocalRuntime.mock.calls.length, 0)
})

test('remote apply fails clearly when no saved remote can be resolved', async () => {
  const gate = createFirstRunSetupGate({ stuckAfterMs: 0 })
  const options = startupOptions({ waitForDecision: gate.wait })
  const pending = runPrimaryBackendStartup(options)

  await vi.waitFor(() => assert.equal(gate.hasWaiter(), true))
  gate.abandonForRemoteApply()

  await assert.rejects(pending, /without a saved remote backend/)
  assert.equal(options.connectRemote.mock.calls.length, 0)
  assert.equal(options.ensureLocalRuntime.mock.calls.length, 0)
})

test('continue local waits for update exclusion and ensures the prepared runtime exactly once', async () => {
  const gate = createFirstRunSetupGate({ stuckAfterMs: 0 })
  const runtimeBackend = { ...bootstrapBackend, command: 'hermes' }

  const options = startupOptions({
    ensureLocalRuntime: vi.fn(async () => runtimeBackend),
    waitForDecision: gate.wait
  })

  const pending = runPrimaryBackendStartup(options)

  await vi.waitFor(() => assert.equal(gate.hasWaiter(), true))
  gate.continueLocal()

  assert.deepEqual(await pending, { kind: 'local', backend: runtimeBackend })
  assert.deepEqual(options.waitForLocalStart.mock.calls, [[]])
  assert.deepEqual(options.prepareLocalBackend.mock.calls, [[]])
  assert.deepEqual(options.ensureLocalRuntime.mock.calls, [[bootstrapBackend]])
  assert.deepEqual(options.resolveRemote.mock.calls, [[]])
})

test('reset rejects with a typed error and never enters either backend', async () => {
  const gate = createFirstRunSetupGate({ stuckAfterMs: 0 })
  const options = startupOptions({ waitForDecision: gate.wait })
  const pending = runPrimaryBackendStartup(options)

  await vi.waitFor(() => assert.equal(gate.hasWaiter(), true))
  gate.resetForRetry()

  await assert.rejects(pending, error => error instanceof FirstRunSetupResetError && error.firstRunSetupReset)
  assert.equal(options.connectRemote.mock.calls.length, 0)
  assert.equal(options.ensureLocalRuntime.mock.calls.length, 0)
})


for (const initial of ['observation-a', 'default', null]) {
  test(`deferred local startup keeps captured profile/argv/home when preference ${initial} changes`, async () => {
    let preference = initial
    const launch = capturePrimaryBackendLaunch(preference, '/isolated/home')
    let release!: () => void
    const waiting = new Promise<void>(resolve => { release = resolve })

    const options = startupOptions({
      waitForLocalStart: () => waiting,
      prepareLocalBackend: () => createSourcePythonBackend('/isolated/runtime', 'fixture', launch.backendArgs, {
        hermesHome: '/isolated/home', findSystemPython: () => '/isolated/python'
      }),
      ensureLocalRuntime: async backend => backend
    })

    const pending = runPrimaryBackendStartup(options)
    preference = 'observation-b'
    release()
    const result = await pending
    assert.equal(result.kind, 'local')

    if (result.kind !== 'local') {throw new Error('Expected local startup')}
    assert.equal(launch.profile, initial || 'default')
    assert.notEqual(launch.profile, preference)
    assert.deepEqual(result.backend.args.slice(2), [
      ...(initial ? ['--profile', initial] : []), 'serve', '--host', '127.0.0.1', '--port', '0'
    ])
    assert.equal(launch.home, '/isolated/home')
    assert.equal(result.backend.command, '/isolated/python')
  })
}
