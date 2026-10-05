import { execFileSync, spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { expect, test } from 'vitest'

import { handoffResultPath, readAndConsumeHandoffResult } from './handoff-result'
import { hasReadyPosixUpdater, markerPath, readLiveUpdateMarker, writeUpdateMarker } from './update-marker'
import { observeUpdaterHandoff, spawnUpdaterProcess } from './updater-process'

const WINDOWS_SCRIPT_TIMEOUT_MS = 60_000

test.skipIf(process.platform === 'win32').each(['managed-source', 'external-checkout', 'legacy-default'])(
  'POSIX update preserves its owning home across %s checkout layouts', layout => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-update-home-'))
    const home = path.join(tmp, 'profile')

    const root = layout === 'managed-source' ? path.join(home, 'desktop-source', 'checkout-fixture')
      : layout === 'external-checkout' ? path.join(tmp, 'source') : path.join(home, 'eidolon-agent')

    const bin = path.join(root, 'venv', 'bin')

    const env = { ...process.env, HOME: tmp, TMPDIR: tmp, HERMES_HOME: home,
      HERMES_UPDATE_SHIM_GRACE_SECONDS: '0' }

    if (layout === 'legacy-default') { delete env.HERMES_HOME }

    try {
      fs.mkdirSync(bin, { recursive: true })
      fs.mkdirSync(home, { recursive: true })
      const cli = path.join(bin, 'eidolon')
      fs.writeFileSync(cli, '#!/bin/bash\ncase "$*" in *--help*) echo --keep-stash; exit 0 ;; esac\n' +
        'printf "%s" "$HERMES_HOME" > "$HOME/observed-home"\n' +
        'test -f "$HERMES_HOME/.eidolon-update-in-progress" || exit 9\n')
      fs.chmodSync(cli, 0o755)
      execFileSync('/bin/bash', [path.resolve('../../scripts/desktop-update/posix.sh'),
        '--daemonized', '--install-root', root, '--no-ui'], { env, timeout: 10_000 })

      expect(fs.readFileSync(path.join(tmp, 'observed-home'), 'utf8')).toBe(home)
      expect(fs.existsSync(handoffResultPath(home))).toBe(true)
      expect(readAndConsumeHandoffResult(home)).toMatchObject({ ok: true, manual: false, exitCode: 0 })
      expect(fs.existsSync(markerPath(home))).toBe(false)
      expect(fs.existsSync(path.join(home, 'logs', 'desktop-update-handoff.log'))).toBe(true)

      if (path.dirname(root) !== home) {
        expect(fs.existsSync(handoffResultPath(path.dirname(root)))).toBe(false)
      }
    } finally { fs.rmSync(tmp, { recursive: true, force: true }) }
  }, 15_000)

test.skipIf(process.platform === 'win32').each(['python3', 'python'])(
  'real detached worker starts with managed %s despite inherited Python paths and unavailable developer tools', async interpreter => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-update-detach-'))
  const home = path.join(tmp, 'profile')
  const root = path.join(home, 'desktop-source', 'checkout-fixture')
  const bin = path.join(root, 'venv', 'bin')
  const desktop = spawn(process.execPath, ['-e', 'setTimeout(() => {}, 15000)'], { stdio: 'ignore' })
  let workerPid: number | undefined

  try {
    fs.mkdirSync(bin, { recursive: true })
    const cli = path.join(bin, 'eidolon')
    fs.writeFileSync(cli, '#!/bin/bash\nexit 0\n')
    fs.chmodSync(cli, 0o755)
    const hostPython = execFileSync('python3', ['-c', 'import sys; print(sys.executable)'], { encoding: 'utf8' }).trim()
    const managedPython = path.join(bin, interpreter)
    fs.writeFileSync(managedPython, '#!/bin/bash\n' +
      'printf "%s\\n" "$$" >> "$HOME/daemon-python-used"\n' + `exec ${JSON.stringify(hostPython)} "$@"\n`)
    fs.chmodSync(managedPython, 0o755)

    if (interpreter === 'python') {
      // A broken python3 alias must not hide the healthy anchored interpreter.
      fs.writeFileSync(path.join(bin, 'python3'), '#!/bin/bash\necho broken-python3-alias >&2\nexit 1\n')
      fs.chmodSync(path.join(bin, 'python3'), 0o755)
      // This recovery path invokes Python as a module rather than the CLI shim.
      fs.mkdirSync(path.join(root, 'eidolon_cli'))
      fs.writeFileSync(path.join(root, 'eidolon_cli', '__init__.py'), '')
      fs.writeFileSync(path.join(root, 'eidolon_cli', 'main.py'), 'print("--keep-stash")\n')
    }

    const startedAt = Math.floor(Date.now() / 1000)

    const child = spawnUpdaterProcess('/bin/bash', [path.resolve('../../scripts/desktop-update/posix.sh'),
      '--install-root', root, '--desktop-pid', String(desktop.pid), '--no-ui'], {
      env: { ...process.env, HOME: tmp, TMPDIR: tmp, HERMES_HOME: home,
        HERMES_UPDATE_STARTED_AT: String(startedAt), PYTHONHOME: path.join(tmp, 'missing-python'),
        PYTHONPATH: path.join(tmp, 'missing-modules'),
        // On a native Mac this makes Apple's /usr/bin/python3 shim fail. The
        // managed interpreter still works and must own the daemon handoff.
        DEVELOPER_DIR: path.join(tmp, 'missing-developer-tools') },
      detached: true, stdio: 'ignore'
    })

    writeUpdateMarker(home, child.pid, { startedAt })
    expect(hasReadyPosixUpdater(home, child.pid, startedAt)).toBe(false)

    const outcome = await observeUpdaterHandoff(child, 2500, {
      isReady: () => hasReadyPosixUpdater(home, child.pid, startedAt)
    })

    // Capture the daemon's own evidence before stale-marker cleanup and fixture
    // removal, so a native startup failure retains its actual cause in CI logs.
    const evidence = [markerPath(home), path.join(home, 'logs', 'desktop-update-handoff.log')]
      .map(file => {
        try { return `${file}:\n${fs.readFileSync(file, 'utf8')}` }
        catch { return `${file}: missing` }
      }).join('\n')

    workerPid = readLiveUpdateMarker(home)?.pid
    expect(outcome.ok, `${JSON.stringify(outcome)}\n${evidence}`).toBe(true)
    const interpreterPids = fs.readFileSync(path.join(tmp, 'daemon-python-used'), 'utf8').trim().split('\n')
    expect(interpreterPids).toContain(String(workerPid))
    expect(workerPid).not.toBe(child.pid)
    expect(hasReadyPosixUpdater(home, child.pid, startedAt - 1)).toBe(false)
    desktop.kill()
    await expect.poll(() => fs.existsSync(handoffResultPath(home)), { timeout: 10_000 }).toBe(true)
    expect(readAndConsumeHandoffResult(home)).toMatchObject({ ok: true, exitCode: 0 })
  } finally {
    desktop.kill()
    workerPid ??= readLiveUpdateMarker(home)?.pid

    if (workerPid) { try { process.kill(workerPid, 'SIGKILL') } catch { /* Fixture already completed. */ } }
    fs.rmSync(tmp, { recursive: true, force: true })
  }
}, 30_000)

// Leave room outside the bounded PowerShell startup for assertions and cleanup.
test.skipIf(process.platform !== 'win32')('Windows handoff writes markers and results to the caller-selected home', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-update-home-'))
  const home = path.join(tmp, 'profile')
  const root = path.join(home, 'desktop-source', 'checkout-fixture')

  try {
    fs.mkdirSync(root, { recursive: true })
    execFileSync('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
      path.resolve('../../scripts/desktop-update/windows.ps1'), '-InstallRoot', root, '-NoUi', '-SelfTestMarker'],
    { env: { ...process.env, HERMES_HOME: home, TEMP: tmp }, timeout: WINDOWS_SCRIPT_TIMEOUT_MS })
    expect(readAndConsumeHandoffResult(home)).toMatchObject({ ok: true, exitCode: 0 })
    expect(fs.existsSync(markerPath(home))).toBe(false)
    expect(fs.existsSync(handoffResultPath(path.dirname(root)))).toBe(false)
  } finally { fs.rmSync(tmp, { recursive: true, force: true }) }
}, WINDOWS_SCRIPT_TIMEOUT_MS + 10_000)
