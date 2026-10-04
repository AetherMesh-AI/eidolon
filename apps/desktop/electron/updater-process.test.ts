import assert from 'node:assert/strict'
import type { SpawnOptions } from 'node:child_process'
import path from 'node:path'

import { test } from 'vitest'

import {
  collectRelaunchArgs,
  MARKER_SELF_ADOPT_EPOCH_MS,
  observeUpdaterHandoff,
  resolvePosixScriptHandoff,
  resolveStagedUpdaterBinary,
  resolveUpdateScriptHandoff,
  sandboxFallbackFromEnv,
  spawnUpdaterProcess,
  stagedUpdaterSupportsPrewrittenMarker,
  wrapHandoffForDetachedConsole
} from './updater-process'

const DAY_MS = 24 * 60 * 60 * 1000

test('stagedUpdaterSupportsPrewrittenMarker rejects installers predating the self-adopt fix', () => {
  // The real-world trap: an installer staged at first install months ago, never
  // refreshed because copy_self_to_eidolon_home no-ops during --update.
  assert.equal(
    stagedUpdaterSupportsPrewrittenMarker('C:\\Hermes\\hermes-setup.exe', {
      stagedMtimeMs: () => MARKER_SELF_ADOPT_EPOCH_MS - 60 * DAY_MS
    }),
    false
  )
})

test('stagedUpdaterSupportsPrewrittenMarker accepts installers from the fix onward', () => {
  assert.equal(
    stagedUpdaterSupportsPrewrittenMarker('C:\\Hermes\\hermes-setup.exe', {
      stagedMtimeMs: () => MARKER_SELF_ADOPT_EPOCH_MS
    }),
    true
  )
  assert.equal(
    stagedUpdaterSupportsPrewrittenMarker('C:\\Hermes\\hermes-setup.exe', {
      stagedMtimeMs: () => MARKER_SELF_ADOPT_EPOCH_MS + 30 * DAY_MS
    }),
    true
  )
})

test('stagedUpdaterSupportsPrewrittenMarker treats an unreadable mtime as unsupported', () => {
  // Bias toward the path that can always make progress: a skipped pre-write
  // loses anti-respawn hardening, a wedged updater can never update again.
  assert.equal(
    stagedUpdaterSupportsPrewrittenMarker('C:\\Hermes\\hermes-setup.exe', {
      stagedMtimeMs: () => null
    }),
    false
  )
})

test('resolveStagedUpdaterBinary still returns a stale staged updater on Windows', () => {
  // Staleness gates only the marker PRE-WRITE, never the hand-off itself:
  // the stale binary is the only updater these users have, and it works fine
  // once it is allowed to write its own claim.
  assert.equal(
    resolveStagedUpdaterBinary('C:\\Hermes', {
      fileExists: candidate => candidate.endsWith('hermes-setup.exe'),
      isWindows: true,
      stagedMtimeMs: () => MARKER_SELF_ADOPT_EPOCH_MS - 60 * DAY_MS
    }),
    path.join('C:\\Hermes', 'hermes-setup.exe')
  )
})

test('spawnUpdaterProcess hides the updater console and detaches the child on Windows', () => {
  const calls: Array<{ args: string[]; command: string; options: SpawnOptions }> = []
  let unrefCalls = 0

  const child = {
    pid: 4242,
    unref: () => {
      unrefCalls += 1
    }
  }

  const result = spawnUpdaterProcess(
    'hermes-setup.exe',
    ['--update', '--branch', 'main'],
    { cwd: 'C:\\Hermes', detached: true, stdio: 'ignore' },
    {
      isWindows: true,
      spawnProcess: (command, args, options) => {
        calls.push({ args, command, options })

        return child
      }
    }
  )

  assert.equal(result, child)
  assert.equal(unrefCalls, 1)
  assert.deepEqual(calls, [
    {
      args: ['--update', '--branch', 'main'],
      command: 'hermes-setup.exe',
      options: { cwd: 'C:\\Hermes', detached: true, stdio: 'ignore', windowsHide: true }
    }
  ])
})

test('spawnUpdaterProcess preserves updater options off Windows', () => {
  let capturedOptions: SpawnOptions | undefined

  spawnUpdaterProcess(
    'hermes-setup',
    ['--update'],
    { detached: true, stdio: 'ignore' },
    {
      isWindows: false,
      spawnProcess: (_command, _args, options) => {
        capturedOptions = options

        return { unref: () => {} }
      }
    }
  )

  assert.deepEqual(capturedOptions, { detached: true, stdio: 'ignore' })
})

test('resolveStagedUpdaterBinary hands Windows the staged installer it finds', () => {
  const home = 'C:\\Users\\hermes\\AppData\\Local\\hermes'
  const staged = path.join(home, 'hermes-setup.exe')
  const probed: string[] = []

  const resolved = resolveStagedUpdaterBinary(home, {
    fileExists: candidate => {
      probed.push(candidate)

      return candidate === staged
    },
    isWindows: true
  })

  assert.equal(resolved, staged)
  assert.deepEqual(probed, [path.join(home, 'eidolon-setup.exe'), staged])
})

test('resolveStagedUpdaterBinary returns null off Windows even when hermes-setup is staged (#74836)', () => {
  const home = '/Users/hermes/.hermes'
  let probes = 0

  const resolved = resolveStagedUpdaterBinary(home, {
    // The installer stages hermes-setup on macOS/Linux too, so "it exists" is
    // the normal case — and precisely the one that must not win.
    fileExists: () => {
      probes += 1

      return true
    },
    isWindows: false
  })

  assert.equal(resolved, null)
  assert.equal(probes, 0)
})

test('resolveStagedUpdaterBinary returns null on Windows when nothing is staged', () => {
  const resolved = resolveStagedUpdaterBinary('C:\\Users\\hermes\\AppData\\Local\\hermes', {
    fileExists: () => false,
    isWindows: true
  })

  assert.equal(resolved, null)
})

test('resolveUpdateScriptHandoff prefers the repo script on Windows when present', () => {
  const root = String.raw`C:\Users\hermes\AppData\Local\hermes\hermes-agent`
  const expected = path.join(root, 'scripts', 'desktop-update', 'windows.ps1')

  const handoff = resolveUpdateScriptHandoff(root, {
    isWindows: true,
    fileExists: candidate => candidate === expected
  })

  assert.ok(handoff)
  assert.equal(handoff.command, 'powershell')
  assert.equal(handoff.scriptPath, expected)
  assert.deepEqual(handoff.args, ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', expected])
})

test('resolveUpdateScriptHandoff falls back to the pre-reorg flat path', () => {
  const root = String.raw`C:\Users\hermes\AppData\Local\hermes\hermes-agent`
  const legacy = path.join(root, 'scripts', 'desktop-update.ps1')

  const handoff = resolveUpdateScriptHandoff(root, {
    isWindows: true,
    fileExists: candidate => candidate === legacy
  })

  assert.ok(handoff)
  assert.equal(handoff.scriptPath, legacy)
})

test('resolveUpdateScriptHandoff returns null when the checkout predates the script', () => {
  const handoff = resolveUpdateScriptHandoff(String.raw`C:\Users\hermes\AppData\Local\hermes\hermes-agent`, {
    isWindows: true,
    fileExists: () => false
  })

  assert.equal(handoff, null)
})

test('resolveUpdateScriptHandoff is Windows-only (POSIX updates in place)', () => {
  const handoff = resolveUpdateScriptHandoff('/home/hermes/.hermes/hermes-agent', {
    isWindows: false,
    fileExists: () => true
  })

  assert.equal(handoff, null)
})

test('wrapHandoffForDetachedConsole routes through cmd start with own console', () => {
  const root = String.raw`C:\Users\hermes\AppData\Local\hermes\hermes-agent`
  const expected = path.join(root, 'scripts', 'desktop-update', 'windows.ps1')

  const handoff = resolveUpdateScriptHandoff(root, {
    isWindows: true,
    fileExists: candidate => candidate === expected
  })

  assert.ok(handoff)
  const wrapped = wrapHandoffForDetachedConsole(handoff, ['-InstallRoot', root, '-Branch', 'main'])

  assert.equal(wrapped.command, 'cmd.exe')
  assert.deepEqual(wrapped.args, [
    '/d',
    '/s',
    '/c',
    'start',
    '',
    '/min',
    'powershell',
    '-NoProfile',
    '-ExecutionPolicy',
    'Bypass',
    '-File',
    expected,
    '-InstallRoot',
    root,
    '-Branch',
    'main'
  ])
})

test('resolvePosixScriptHandoff returns the bash recipe when the script exists', () => {
  const root = '/home/hermes/.hermes/hermes-agent'
  const expected = path.join(root, 'scripts', 'desktop-update', 'posix.sh')

  const handoff = resolvePosixScriptHandoff(root, {
    isWindows: false,
    fileExists: candidate => candidate === expected
  })

  assert.ok(handoff)
  assert.equal(handoff.command, '/bin/bash')
  assert.deepEqual(handoff.args, [expected])
})

test('resolvePosixScriptHandoff is null when the checkout predates the script', () => {
  const handoff = resolvePosixScriptHandoff('/home/hermes/.hermes/hermes-agent', {
    isWindows: false,
    fileExists: () => false
  })

  assert.equal(handoff, null)
})

test('resolvePosixScriptHandoff is null on Windows', () => {
  const handoff = resolvePosixScriptHandoff(String.raw`C:\Users\hermes\AppData\Local\hermes\hermes-agent`, {
    isWindows: true,
    fileExists: () => true
  })

  assert.equal(handoff, null)
})

test('collectRelaunchArgs drops Electron internals, keeps user/launcher args', () => {
  const argv = [
    '--type=renderer',
    '--user-data-dir=/tmp/x',
    '--enable-features=A,B',
    '--field-trial-handle=123',
    '--enable-logging',
    '--log-file=/tmp/log',
    '--lang=en-US',
    '--inspect=9229',
    '--remote-debugging-port=9222',
    '--no-sandbox',
    'hermes://open/session/abc',
    '--profile=work'
  ]

  assert.deepEqual(collectRelaunchArgs(argv), ['--no-sandbox', 'hermes://open/session/abc', '--profile=work'])
  assert.deepEqual(collectRelaunchArgs(undefined), [])
})

test('sandboxFallbackFromEnv: ELECTRON_DISABLE_SANDBOX / --no-sandbox opt out', () => {
  assert.equal(sandboxFallbackFromEnv({ ELECTRON_DISABLE_SANDBOX: '1' }, []), true)
  assert.equal(sandboxFallbackFromEnv({ ELECTRON_DISABLE_SANDBOX: 'true' }, []), true)
  assert.equal(sandboxFallbackFromEnv({}, ['--no-sandbox']), true)
  assert.equal(sandboxFallbackFromEnv({ ELECTRON_DISABLE_SANDBOX: '0' }, []), false)
  assert.equal(sandboxFallbackFromEnv({}, []), false)
})

// ── observeUpdaterHandoff (#66753) ──────────────────────────────────────────

class FakeChild {
  pid = 1234
  listeners = new Map<string, Array<(...args: unknown[]) => void>>()
  removed: string[] = []

  unref() {}

  once(event: string, listener: (...args: unknown[]) => void) {
    const arr = this.listeners.get(event) ?? []

    arr.push(listener)
    this.listeners.set(event, arr)

    return this
  }

  removeListener(event: string, _listener: (...args: unknown[]) => void) {
    this.removed.push(event)

    return this
  }

  emit(event: string, ...args: unknown[]) {
    for (const listener of this.listeners.get(event) ?? []) {
      listener(...args)
    }
  }
}

function manualTimer() {
  const pending = new Map<number, { callback: () => void; at: number }>()
  let now = 0
  let id = 0

  const advance = (ms: number) => {
    const target = now + ms

    for (;;) {
      const next = [...pending.entries()].sort((a, b) => a[1].at - b[1].at)[0]

      if (!next || next[1].at > target) { break }

      now = next[1].at
      pending.delete(next[0])
      next[1].callback()
    }

    now = target
  }

  return {
    deps: {
      nowFn: () => now,
      setTimeoutFn: (callback: () => void, ms: number) => {
        pending.set(++id, { callback, at: now + ms })

        return id
      },
      clearTimeoutFn: (timer: unknown) => { pending.delete(Number(timer)) }
    },
    advance,
    pendingCount: () => pending.size,
    fire: () => {
      const next = Math.min(...[...pending.values()].map(timer => timer.at))

      if (Number.isFinite(next)) { advance(next - now) }
    }
  }
}

test('observeUpdaterHandoff reports a spawn error instead of settling ok', async () => {
  const child = new FakeChild()
  const timer = manualTimer()
  const outcomePromise = observeUpdaterHandoff(child, 2500, timer.deps)

  const err: Error & { code?: string } = new Error('spawn ENOENT')

  err.code = 'ENOENT'
  child.emit('error', err)

  const outcome = await outcomePromise

  assert.equal(outcome.ok, false)
  assert.equal(outcome.reason, 'spawn-error')
  assert.match(outcome.message ?? '', /ENOENT/)
})

test('observeUpdaterHandoff reports a non-zero early exit', async () => {
  const child = new FakeChild()
  const timer = manualTimer()
  const outcomePromise = observeUpdaterHandoff(child, 2500, timer.deps)

  child.emit('exit', 127, null)

  const outcome = await outcomePromise

  assert.equal(outcome.ok, false)
  assert.equal(outcome.reason, 'early-exit')
  assert.equal(outcome.code, 127)
})

test('observeUpdaterHandoff reports a signal death inside the window', async () => {
  const child = new FakeChild()
  const timer = manualTimer()
  const outcomePromise = observeUpdaterHandoff(child, 2500, timer.deps)

  child.emit('exit', null, 'SIGTERM')

  const outcome = await outcomePromise

  assert.equal(outcome.ok, false)
  assert.equal(outcome.reason, 'early-exit')
  assert.equal(outcome.signal, 'SIGTERM')
})

test('observeUpdaterHandoff accepts a clean exit 0 (Windows cmd start wrapper)', async () => {
  const child = new FakeChild()
  const timer = manualTimer()
  const outcomePromise = observeUpdaterHandoff(child, 2500, timer.deps)

  child.emit('exit', 0, null)

  const outcome = await outcomePromise

  assert.equal(outcome.ok, true)
  assert.equal(outcome.code, 0)
})

test.each([false, true])('detached POSIX launcher exit needs the real worker acknowledgement (%s)', async ready => {
  const child = new FakeChild()
  const timer = manualTimer()
  const outcomePromise = observeUpdaterHandoff(child, 2500, { ...timer.deps, isReady: () => ready })

  child.emit('exit', 0, null)
  timer.advance(ready ? 2500 : 10_000)

  const outcome = await outcomePromise
  assert.equal(outcome.ok, ready)

  if (!ready) { assert.equal(outcome.reason, 'not-ready') }
})

test('POSIX readiness preserves the minimum dwell and accepts a delayed cold worker', async () => {
  const child = new FakeChild()
  const timer = manualTimer()
  const checks: number[] = []

  const outcomePromise = observeUpdaterHandoff(child, 2500, {
    ...timer.deps,
    isReady: () => {
      checks.push(timer.deps.nowFn())

      return timer.deps.nowFn() >= 3100
    }
  })

  child.emit('exit', 0, null)
  timer.advance(2499)
  assert.deepEqual(checks, [])
  timer.advance(601)

  assert.deepEqual(await outcomePromise, { ok: true })
  assert.deepEqual(checks, [2500, 2600, 2700, 2800, 2900, 3000, 3100])
  assert.equal(timer.pendingCount(), 0)
  assert.deepEqual(child.removed.sort(), ['error', 'exit'])
})

test('POSIX readiness fails closed after ten seconds without a live worker acknowledgement', async () => {
  const child = new FakeChild()
  const timer = manualTimer()
  const checks: number[] = []

  const outcomePromise = observeUpdaterHandoff(child, 2500, {
    ...timer.deps,
    isReady: () => { checks.push(timer.deps.nowFn());

 return false }
  })

  child.emit('exit', 0, null)
  timer.advance(9999)
  assert.equal(timer.pendingCount(), 1)
  timer.advance(1)

  assert.equal((await outcomePromise).reason, 'not-ready')
  assert.equal(checks[0], 2500)
  assert.equal(checks.at(-1), 10_000)
  assert.equal(timer.pendingCount(), 0)
  assert.deepEqual(child.removed.sort(), ['error', 'exit'])
})

test('POSIX readiness grace still fails immediately on a spawn error', async () => {
  const child = new FakeChild()
  const timer = manualTimer()
  const outcomePromise = observeUpdaterHandoff(child, 2500, { ...timer.deps, isReady: () => false })

  timer.advance(2500)
  child.emit('error', new Error('worker launch failed'))

  assert.equal((await outcomePromise).reason, 'spawn-error')
  assert.equal(timer.deps.nowFn(), 2500)
  assert.equal(timer.pendingCount(), 0)
})

test('observeUpdaterHandoff settles ok when the child survives the window', async () => {
  const child = new FakeChild()
  const timer = manualTimer()
  const outcomePromise = observeUpdaterHandoff(child, 2500, timer.deps)

  timer.fire()

  const outcome = await outcomePromise

  assert.equal(outcome.ok, true)
  assert.equal(outcome.reason, undefined)
  // Listeners must be detached so a post-quit late exit can't fire them.
  assert.deepEqual(child.removed.sort(), ['error', 'exit'])
})

test('observeUpdaterHandoff ignores events after the first settle', async () => {
  const child = new FakeChild()
  const timer = manualTimer()
  const outcomePromise = observeUpdaterHandoff(child, 2500, timer.deps)

  child.emit('exit', 1, null)
  child.emit('error', new Error('late'))
  timer.fire()

  const outcome = await outcomePromise

  assert.equal(outcome.ok, false)
  assert.equal(outcome.reason, 'early-exit')
})

test('observeUpdaterHandoff settles ok for children without an event interface', async () => {
  const timer = manualTimer()
  const outcomePromise = observeUpdaterHandoff({ pid: 1, unref: () => {} }, 2500, timer.deps)

  timer.fire()

  const outcome = await outcomePromise

  assert.equal(outcome.ok, true)
})
