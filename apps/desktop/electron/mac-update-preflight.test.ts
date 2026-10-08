import { execFileSync, spawn } from 'node:child_process'
import { once } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { expect, test } from 'vitest'

import { macUpdatePreflight } from './mac-update-preflight'

function fixture() {
  const root = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'eidolon-preflight-'))
  const parent = path.join(root, 'Applications')
  const app = path.join(parent, 'Eidolon.app')
  const executable = path.join(app, 'Contents', 'MacOS', 'Eidolon')
  fs.mkdirSync(path.dirname(executable), { recursive: true })
  fs.writeFileSync(executable, '#!/bin/sh\nexit 0\n', { mode: 0o755 })

  return { root, parent, app, executable }
}

function fixturePath(root: string, target: string) {
  // Fixture-only permission changes must never follow a link out of the fresh
  // private temporary root, even during failure cleanup.
  expect(path.dirname(root)).toBe(fs.realpathSync(os.tmpdir()))
  expect(path.basename(root)).toMatch(/^eidolon-preflight-/)
  expect(target.startsWith(`${root}${path.sep}`)).toBe(true)
  expect(fs.realpathSync(target)).toBe(target)
  expect(fs.lstatSync(target).isSymbolicLink()).toBe(false)
  expect(fs.statSync(target).uid).toBe(process.getuid())

  return target
}

function aclListing(target: string) {
  return execFileSync('/bin/ls', ['-ldebn', target], { encoding: 'utf8', env: { ...process.env, LC_ALL: 'C' } })
}

async function withDeniedAcl(root: string, target: string, rights: string, check: () => Promise<void>) {
  const before = aclListing(fixturePath(root, target))
  const identity = fs.statSync(target)
  execFileSync('/bin/chmod', ['+a#', '0', `everyone deny ${rights}`, target])

  try {
    await check()
  } finally {
    fixturePath(root, target)
    expect(fs.statSync(target).ino).toBe(identity.ino)
    // Remove only the deny entry inserted on our fixture, preserving its ACL.
    execFileSync('/bin/chmod', ['-a#', '0', target])
    expect(aclListing(target)).toBe(before)
  }
}

test.skipIf(process.platform !== 'darwin')('preflight preserves working apps and every interrupted-update artifact', async () => {
  const { root, parent, app, executable } = fixture()

  try {
    const before = fs.readFileSync(executable)
    const inode = fs.statSync(app).ino
    expect(await macUpdatePreflight(app)).toBeNull()
    expect(fs.readdirSync(parent)).toEqual(['Eidolon.app'])

    for (const suffix of ['.old', '.new', '.eidolon-update']) {
      const leftover = `${app}${suffix}`
      fs.mkdirSync(leftover)
      fs.writeFileSync(path.join(leftover, 'recovery-evidence'), 'keep this copy')
      const result = await macUpdatePreflight(app)
      expect(result?.error).toBe('mac-update-recovery-required')
      expect(result?.message).toContain(leftover)
      expect(result?.message).toContain('keep running')
      expect(fs.readFileSync(path.join(leftover, 'recovery-evidence'), 'utf8')).toBe('keep this copy')
      expect(fs.readFileSync(executable)).toEqual(before)
      expect(fs.statSync(app).ino).toBe(inode)
      fs.rmSync(leftover, { recursive: true })
      fs.symlinkSync(path.join(root, 'missing'), leftover)
      expect((await macUpdatePreflight(app))?.error).toBe('mac-update-recovery-required')
      expect(fs.lstatSync(leftover).isSymbolicLink()).toBe(true)
      fs.unlinkSync(leftover)
    }

    const alias = path.join(parent, 'Alias.app')
    fs.symlinkSync(app, alias)
    expect((await macUpdatePreflight(alias))?.error).toBe('mac-update-preflight-failed')
    const linkedParent = path.join(root, 'Linked Applications')
    fs.symlinkSync(parent, linkedParent)
    expect((await macUpdatePreflight(path.join(linkedParent, 'Eidolon.app')))?.error).toBe('mac-update-preflight-failed')
    expect((await macUpdatePreflight(null))?.error).toBe('mac-update-preflight-failed')
    expect((await macUpdatePreflight(path.join(parent, 'Missing.app')))?.error).toBe('mac-update-preflight-failed')
    expect(await macUpdatePreflight(app)).toBeNull()
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
}, 20_000)

test.skipIf(process.platform !== 'darwin')('denied preflight preserves a running fixture and its native unprivileged restrictions', async () => {
  expect(process.geteuid(), 'Run the macOS fixture as an unprivileged account').not.toBe(0)
  const { root, parent, app, executable } = fixture()
  fs.writeFileSync(executable, '#!/bin/sh\nprintf "ready\\n"\nIFS= read -r message\n')
  // This proves filesystem/preflight preservation with a real process. It does
  // not load Electron or exercise main.ts's app.quit ordering.
  const runningApp = spawn(executable, { cwd: root, stdio: ['pipe', 'pipe', 'pipe'] })

  try {
    await once(runningApp, 'spawn', { signal: AbortSignal.timeout(5000) })
    const [ready] = await once(runningApp.stdout, 'data', { signal: AbortSignal.timeout(5000) })
    expect(String(ready)).toBe('ready\n')
    const originalPid = runningApp.pid
    const originalInode = fs.statSync(app).ino
    const original = fs.readFileSync(executable)

    const assertDenied = async () => {
      const result = await macUpdatePreflight(app)
      expect(result?.error).toBe('mac-update-permission-denied')
      expect(result?.message).toContain('keep running')
      expect(result?.message).toContain('manually')
      expect(fs.readFileSync(executable)).toEqual(original)
      expect(fs.statSync(app).ino).toBe(originalInode)
      expect(runningApp.pid).toBe(originalPid)
      expect(runningApp.exitCode).toBeNull()
      expect(runningApp.signalCode).toBeNull()
      expect(() => process.kill(originalPid, 0)).not.toThrow()
    }

    const mode = fs.statSync(parent).mode & 0o7777
    fs.chmodSync(fixturePath(root, parent), 0o555)

    try {
      await assertDenied()
      expect(fs.statSync(parent).mode & 0o7777).toBe(0o555)
    } finally {
      fs.chmodSync(fixturePath(root, parent), mode)
    }

    for (const [target, rights] of [[app, 'delete'], [parent, 'delete_child'], [parent, 'add_subdirectory']]) {
      await withDeniedAcl(root, target, rights, assertDenied)
      expect(await macUpdatePreflight(app)).toBeNull()
    }

    // Protecting the parent directory itself must not disable child updates.
    await withDeniedAcl(root, parent, 'delete', async () => {
      expect(await macUpdatePreflight(app)).toBeNull()
    })

    for (const target of [app, parent]) {
      fixturePath(root, target)
      const before = execFileSync('/usr/bin/stat', ['-f', '%Uf', target], { encoding: 'utf8' })
      expect(Number(before) & 2).toBe(0)
      execFileSync('/usr/bin/chflags', ['uchg', target])

      try {
        await assertDenied()
        expect(Number(execFileSync('/usr/bin/stat', ['-f', '%Uf', target], { encoding: 'utf8' })) & 2).toBe(2)
      } finally {
        execFileSync('/usr/bin/chflags', ['nouchg', fixturePath(root, target)])
        expect(execFileSync('/usr/bin/stat', ['-f', '%Uf', target], { encoding: 'utf8' })).toBe(before)
      }
    }

    expect(await macUpdatePreflight(app)).toBeNull()
    expect(fs.readdirSync(parent)).toEqual(['Eidolon.app'])
  } finally {
    if (runningApp.pid && runningApp.exitCode === null && runningApp.signalCode === null) {
      const stopped = once(runningApp, 'exit', { signal: AbortSignal.timeout(5000) })
      runningApp.kill('SIGTERM')
      await stopped
    }

    fs.rmSync(root, { recursive: true, force: true })
  }
}, 20_000)
