import assert from 'node:assert/strict'

import { test } from 'vitest'

import { createBackendConnectionState } from './backend-connection-state'
import { resolveProfileBackendRoute } from './connection-config'

type FakeProcess = { id: string }

function deferred<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(next => {
    resolve = next
  })

  return { promise, resolve }
}

test('an invalidated remote attempt cannot publish a late descriptor', async () => {
  const state = createBackendConnectionState<FakeProcess, string>()
  const oldProbe = deferred<string>()
  const oldAttempt = state.startAttempt()

  const oldResult = oldProbe.promise.then(descriptor => {
    if (!state.isCurrentAttempt(oldAttempt)) {
      throw new Error('Eidolon backend start was superseded by a newer connection attempt.')
    }

    return descriptor
  })

  state.setPromise(oldAttempt, oldResult)
  state.invalidate()

  const newAttempt = state.startAttempt()
  const newResult = Promise.resolve('https://new.example')

  state.setPromise(newAttempt, newResult)
  assert.equal(await newResult, 'https://new.example')

  oldProbe.resolve('https://old.example')
  await assert.rejects(oldResult, /superseded by a newer connection attempt/)
  assert.equal(state.getPromise(), newResult)
})

test('a stale backend exit cannot clear a newer connection attempt', () => {
  const state = createBackendConnectionState<FakeProcess, string>()
  const oldAttempt = state.startAttempt()
  const oldPromise = Promise.resolve('old')

  state.setPromise(oldAttempt, oldPromise)
  const oldOwner = state.attachProcess(oldAttempt, { id: 'old' })
  assert.ok(oldOwner)

  state.invalidate()

  const newAttempt = state.startAttempt()
  const newPromise = Promise.resolve('new')
  const newProcess = { id: 'new' }

  state.setPromise(newAttempt, newPromise)
  assert.ok(state.attachProcess(newAttempt, newProcess))

  assert.equal(state.clearForCurrentProcess(oldOwner), false)
  assert.equal(state.getProcess(), newProcess)
  assert.equal(state.getPromise(), newPromise)
})

test('the current backend exit clears its process and connection promise', () => {
  const state = createBackendConnectionState<FakeProcess, string>()
  const attempt = state.startAttempt()

  state.setPromise(attempt, Promise.resolve('current'))
  const owner = state.attachProcess(attempt, { id: 'current' })
  assert.ok(owner)

  assert.equal(state.clearForCurrentProcess(owner), true)
  assert.equal(state.clearPromiseForAttempt(attempt), true)
  assert.equal(state.getProcess(), null)
  assert.equal(state.getPromise(), null)
})

test('a stale rejected attempt cannot clear a newer connection promise', () => {
  const state = createBackendConnectionState<FakeProcess, string>()
  const oldAttempt = state.startAttempt()

  state.setPromise(oldAttempt, Promise.resolve('old'))
  state.invalidate()

  const newAttempt = state.startAttempt()
  const newPromise = Promise.resolve('new')

  state.setPromise(newAttempt, newPromise)

  assert.equal(state.clearPromiseForAttempt(oldAttempt), false)
  assert.equal(state.getPromise(), newPromise)
})

test('an invalidated attempt cannot attach a late-spawned process', () => {
  const state = createBackendConnectionState<FakeProcess, string>()
  const staleAttempt = state.startAttempt()

  state.invalidate()

  assert.equal(state.attachProcess(staleAttempt, { id: 'late' }), null)
  assert.equal(state.getProcess(), null)
})


test('remembering a workspace cannot relabel the live primary or redirect a named-profile return', () => {
  const state = createBackendConnectionState<FakeProcess, string>()
  let preference = 'default'
  const primaryProfile = () => state.getProfile() ?? preference
  const boot = state.startAttempt(primaryProfile())
  state.setPromise(boot, Promise.resolve('default backend'))
  const owner = state.attachProcess(boot, { id: 'default' })!

  for (const selected of ['observation-a', 'observation-b', 'observation-a']) {
    preference = selected
    assert.equal(primaryProfile(), 'default')
    assert.equal(resolveProfileBackendRoute(selected, { primaryProfile: primaryProfile() }).backend, 'pool')
    assert.equal(resolveProfileBackendRoute('default', { primaryProfile: primaryProfile() }).backend, 'primary')
  }

  // A genuine teardown/next boot adopts the remembered selection.
  state.invalidate()
  assert.equal(primaryProfile(), 'observation-a')
  const replacement = state.startAttempt(primaryProfile())
  state.setPromise(replacement, Promise.resolve('a backend'))
  assert.equal(state.clearForCurrentProcess(owner), false)
  assert.equal(state.clearPromiseForAttempt(boot), false)
  assert.equal(state.setPromise(boot, Promise.resolve('late default')), false)
  assert.equal(state.getProfile(), 'observation-a')
  assert.equal(resolveProfileBackendRoute('observation-a', { primaryProfile: primaryProfile() }).backend, 'primary')
})

test('failed or exited primary generations release their captured profile', () => {
  const state = createBackendConnectionState<FakeProcess, string>()
  const failed = state.startAttempt('a')
  state.setPromise(failed, Promise.resolve('failed'))
  assert.equal(state.getProfile(), 'a')
  assert.equal(state.clearPromiseForAttempt(failed), true)
  assert.equal(state.getProfile(), null)
  const next = state.startAttempt('b')
  state.setPromise(next, Promise.resolve('b'))
  const owner = state.attachProcess(next, { id: 'b' })!
  assert.equal(state.clearForCurrentProcess(owner), true)
  assert.equal(state.getProfile(), null)
})
