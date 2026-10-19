import assert from 'node:assert/strict'
import { type ChildProcess, spawn } from 'node:child_process'
import { once } from 'node:events'

import { test, vi } from 'vitest'

import { createBackendConnectionState } from './backend-connection-state'
import { createBackendShutdownCoordinator } from './backend-ownership'

function deferred() {
  let resolve!: () => void

  const promise = new Promise<void>(done => {
    resolve = done
  })

  return { promise, resolve }
}

function waitForExit(child: ChildProcess) {
  return child.exitCode !== null || child.signalCode !== null ? Promise.resolve() : once(child, 'exit').then(() => {})
}

test('shutdown seals admission across pending starts and keeps one completion even after failure', async () => {
  for (const failTeardown of [false, true]) {
    const blockedStart = deferred()
    const stopped = deferred()
    const error = new Error('bounded teardown failed')

    const teardown = vi.fn(async owned => {
      assert.equal(owned.length, 0)
      await stopped.promise

      if (failTeardown) {
        throw error
      }
    })

    const shutdown = createBackendShutdownCoordinator(teardown)
    const spawnError = new Error('synchronous spawn failed')
    assert.throws(
      () =>
        shutdown.spawnChild(() => {
          throw spawnError
        }),
      spawnError
    )

    const factory = vi.fn(
      () =>
        new (class {
          once() {
            return this
          }
        })()
    )

    const pending = (async () => {
      shutdown.assertCanStart()
      await blockedStart.promise

      return shutdown.spawnChild(factory)
    })()

    const pendingRejection = assert.rejects(pending, /shutting down/)
    const completion = shutdown.run()

    const completed = failTeardown ? assert.rejects(completion, error) : completion

    assert.equal(shutdown.run(), completion)
    assert.throws(() => shutdown.assertCanStart(), /shutting down/)
    assert.throws(() => shutdown.spawnChild(factory), /shutting down/)
    blockedStart.resolve()
    await pendingRejection
    assert.equal(factory.mock.calls.length, 0)
    assert.equal(teardown.mock.calls.length, 1)
    stopped.resolve()
    await completed
    assert.equal(shutdown.run(), completion)
    assert.throws(() => shutdown.spawnChild(factory), /shutting down/)
  }
})

test('shutdown retains real owned children across claims and cache replacement until observed exit', async () => {
  const children: ChildProcess[] = []
  const state = createBackendConnectionState<ChildProcess, string>()
  const pool = new Map<string, ChildProcess>()
  const snapshots: ChildProcess[][] = []

  const shutdown = createBackendShutdownCoordinator<ChildProcess>(async owned => {
    snapshots.push([...owned])
    state.invalidate()
    pool.clear()
    await Promise.all(
      owned.map(async child => {
        const exited = waitForExit(child)
        child.kill('SIGTERM')
        await exited
      })
    )
  })

  const startChild = async () => {
    const child = shutdown.spawnChild(() =>
      spawn(
        process.execPath,
        ['-e', "process.on('SIGTERM',()=>process.exit(0));process.send('ready');setInterval(()=>{},1000)"],
        { stdio: ['ignore', 'ignore', 'ignore', 'ipc'] }
      )
    )

    children.push(child)
    await once(child, 'message')

    return child
  }

  try {
    const failed = shutdown.spawnChild(() =>
      spawn(`${process.execPath}.eidolon-missing-executable`, [], {
        stdio: 'ignore'
      })
    )

    let spawnFailed = false
    failed.on('error', () => {
      spawnFailed = true
    })
    await new Promise<void>(resolve => {
      failed.once('close', () => resolve())
    })
    assert.equal(spawnFailed, true)

    const oldAttempt = state.startAttempt('old')
    const old = await startChild()
    state.attachProcess(oldAttempt, old)
    state.setPromise(oldAttempt, Promise.resolve('old'))
    // Ordinary re-home remains open. The old direct handle outlives its cache.
    state.invalidate()
    const replacementAttempt = state.startAttempt('new')
    const replacement = await startChild()
    state.attachProcess(replacementAttempt, replacement)
    const replacementPromise = Promise.resolve('new')
    state.setPromise(replacementAttempt, replacementPromise)
    assert.equal(state.clearPromiseForAttempt(oldAttempt), false)
    assert.equal(state.getPromise(), replacementPromise)

    // A primary in its identity-claim gap has no connection-state attachment.
    const claiming = await startChild()
    // An evicted pool entry also has no routing-cache handle during teardown.
    const pooled = await startChild()
    pool.set('profile', pooled)
    pool.delete('profile')
    const exited = await startChild()
    const exitedBeforeShutdown = waitForExit(exited)
    exited.kill('SIGTERM')
    await exitedBeforeShutdown

    await shutdown.run()
    assert.deepEqual(new Set(snapshots[0]), new Set([old, replacement, claiming, pooled]))
    assert.equal(state.getProcess(), null)

    for (const child of children) {
      assert.ok(child.exitCode !== null || child.signalCode !== null)
    }

    assert.throws(() => shutdown.assertCanStart(), /shutting down/)
  } finally {
    // Cleanup is restricted to handles created by this test, even if it fails.
    await Promise.all(
      children.map(async child => {
        const exited = waitForExit(child)

        if (child.exitCode === null && child.signalCode === null) {
          child.kill('SIGKILL')
        }

        await exited
      })
    )
  }
}, 15_000)
