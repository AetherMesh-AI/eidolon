import { test } from 'vitest'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import https from 'node:https'
import { PassThrough } from 'node:stream'
import { EventEmitter, once } from 'node:events'
import { syncBuiltinESMExports } from 'node:module'
import childProcess from 'node:child_process'
import { readInstallStampFromPaths, formatInstallStamp, formatInstallVersion } from '../electron/install-stamp.ts'
import { fromLocalGit } from './write-build-stamp.mjs'
import { resolveInstallScript, runBootstrap, cachedScriptPath } from '../electron/bootstrap-runner.ts'

// Exercise the exported runner, its default HTTPS downloader and disk cache.
// Only the transport and process-launch boundary are substituted.
test('runBootstrap fails closed before spawning after pinned HTTP failure', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-runner-'))
  const pin = 'b'.repeat(40)
  fs.mkdirSync(path.join(dir, 'hermes-agent', 'scripts'), { recursive: true })
  fs.writeFileSync(path.join(dir, 'hermes-agent', 'scripts', 'install.sh'), 'UNVERIFIED INSTALLED BYTES')
  fs.mkdirSync(path.join(dir, 'bootstrap-cache'), { recursive: true })
  const cache = path.join(dir, 'bootstrap-cache', `install-${pin}.sh`)
  fs.writeFileSync(cache, 'LEGACY POISONED CACHE')
  const requests = [], events = [], spawns = []
  const originalGet = https.get, originalSpawn = childProcess.spawn
  const originalStream = fs.createWriteStream, streams = []
  fs.createWriteStream = (...args) => { const stream = originalStream(...args); streams.push(stream); return stream }
  https.get = (url, callback) => {
    requests.push(String(url))
    const request = new EventEmitter()
    queueMicrotask(() => {
      const response = new PassThrough()
      response.statusCode = 404
      callback(response)
      response.end()
    })
    return request
  }
  childProcess.spawn = (...args) => { spawns.push(args); throw new Error('unexpected launch') }
  syncBuiltinESMExports()
  try {
    for (let attempt = 0; attempt < 2; attempt++) {
      const result = await runBootstrap({ hermesHome: dir, installStamp: { commit: pin, branch: 'main' }, onEvent: event => events.push(event) })
      assert.equal(result.ok, false)
      assert.match(result.error, /HTTP 404/)
    }
    assert.equal(requests.length, 2)
    assert.ok(requests.every(url => url === `https://raw.githubusercontent.com/AetherMesh-AI/Eidolon/${pin}/scripts/install.sh`))
    assert.deepEqual(spawns, [])
    assert.ok(events.some(event => event.type === 'failed'))
    assert.equal(fs.readFileSync(cache, 'utf8'), 'LEGACY POISONED CACHE')
  } finally {
    https.get = originalGet; childProcess.spawn = originalSpawn; fs.createWriteStream = originalStream; syncBuiltinESMExports()
    await Promise.all(streams.filter(stream => !stream.closed).map(stream => once(stream, 'close')))
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

for (const status of ['clean', 'untracked', 'tracked', 'detached', 'failed']) {
  test(`real writer artifact and desktop reader/display preserve ${status} status`, () => {
    const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-artifact-')))
    try {
      const scripts = path.join(dir, 'apps', 'desktop', 'scripts')
      fs.mkdirSync(scripts, { recursive: true })
      for (const name of ['write-build-stamp.mjs', 'utils.mjs']) {
        fs.copyFileSync(new URL(name, import.meta.url), path.join(scripts, name))
      }
      fs.mkdirSync(path.join(dir, 'hermes_cli'))
      fs.copyFileSync(new URL('../../../hermes_cli/eidolon_version.py', import.meta.url), path.join(dir, 'hermes_cli/eidolon_version.py'))
      fs.writeFileSync(path.join(dir, '.gitignore'), 'apps/desktop/build/\nhermes_cli/_build_identity.json\n')
      const git = (...args) => execFileSync('/usr/bin/git', args, { cwd: dir, stdio: 'pipe' })
      git('init', '-q', '-b', 'main'); git('add', '.')
      git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
      git('tag', 'alpha-v0.1.0')
      const env = { ...process.env }
      if (status === 'detached') git('checkout', '--detach')
      if (status === 'tracked') fs.appendFileSync(path.join(scripts, 'write-build-stamp.mjs'), '\n// tracked fixture change\n')
      for (const name of ['GITHUB_SHA', 'GITHUB_REF_NAME', 'GITHUB_HEAD_REF']) delete env[name]
      if (status === 'untracked') fs.writeFileSync(path.join(dir, 'untracked-source.ts'), 'export const dirty = true')
      if (status === 'failed') {
        const bin = path.join(dir, 'bin'); fs.mkdirSync(bin)
        fs.writeFileSync(path.join(bin, 'git'), '#!/bin/sh\nif [ "$1" = "status" ]; then exit 1; fi\nexec /usr/bin/git "$@"\n', { mode: 0o755 })
        env.PATH = `${bin}:${env.PATH}`
      }
      const output = execFileSync(process.execPath, [path.join(scripts, 'write-build-stamp.mjs')], { cwd: dir, env, encoding: 'utf8' })
      const artifact = path.join(dir, 'apps', 'desktop', 'build', 'install-stamp.json')
      const payload = JSON.parse(fs.readFileSync(artifact, 'utf8'))
      const expected = status === 'failed' ? null : ['untracked', 'tracked'].includes(status)
      assert.equal(payload.dirty, expected)
      const stamp = readInstallStampFromPaths([artifact])
      assert.equal(stamp.dirty, expected)
      // A mutable release-looking tag in an unrelated repository is not the
      // immutable Eidolon version anchor. Keep provenance explicitly unverified.
      assert.equal(stamp.version, null)
      assert.equal(stamp.distance, null)
      assert.equal(payload.versionSource, 'fallback')
      const version = formatInstallVersion(stamp)
      assert.match(version, /version unavailable\/unverified/)
      if (expected === null) assert.match(version, /source status unknown/)
      else if (expected) assert.match(version, /dirty source/)
      else assert.doesNotMatch(version, /dirty source|source status unknown/)
      const display = formatInstallStamp(stamp)
      if (!expected && status !== 'failed') { assert.doesNotMatch(output, /DIRTY/); assert.doesNotMatch(display, /DIRTY/) }
      else { assert.match(output, status === 'failed' ? /DIRTY UNKNOWN/ : /\[DIRTY\]/); assert.match(display, status === 'failed' ? /DIRTY UNKNOWN/ : /\[DIRTY\]/) }
    } finally { fs.rmSync(dir, { recursive: true, force: true }) }
  })
}


test('version display distinguishes absent and legacy unknown status', () => {
  assert.match(formatInstallVersion(null), /unknown commit.*source status unknown/)
  assert.match(formatInstallVersion({ commit: 'a'.repeat(40) }), /source status unknown/)
})

function temp(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-regression-'))
  t.onTestFinished(() => fs.rmSync(root, { recursive: true, force: true }))
  return root
}

// Converted from the independent reviewer probes: assert safe behavior now.
test('failed pinned download never copies installed bytes or reuses poisoned cache', async t => {
  const home = temp(t)
  const pin = '1'.repeat(40)
  const name = process.platform === 'win32' ? 'install.ps1' : 'install.sh'
  const installed = path.join(home, 'hermes-agent', 'scripts', name)
  fs.mkdirSync(path.dirname(installed), { recursive: true })
  fs.writeFileSync(installed, 'UNVERIFIED INSTALLED BYTES')
  let downloads = 0
  const options = {
    installStamp: { commit: pin, branch: 'main' }, hermesHome: home,
    sourceRepoRoot: null, emit: () => {},
    _download: async () => { downloads++; throw new Error('injected download failure') }
  }
  await assert.rejects(resolveInstallScript(options), /injected download failure/)
  assert.equal(fs.existsSync(cachedScriptPath(home, pin)), false)
  // Simulate the old implementation's poisoned cache, including retries after upgrade.
  const cache = cachedScriptPath(home, pin)
  fs.mkdirSync(path.dirname(cache), { recursive: true })
  fs.writeFileSync(cache, 'POISONED OLD CACHE')
  await assert.rejects(resolveInstallScript(options), /injected download failure/)
  assert.equal(downloads, 2)
  assert.equal(fs.readFileSync(installed, 'utf8'), 'UNVERIFIED INSTALLED BYTES')
})

test('successful pinned fetch replaces old bytes and returns requested pin', async t => {
  const home = temp(t)
  let downloads = 0
  const pin = '2'.repeat(40)
  const options = {
    installStamp: { commit: pin }, hermesHome: home, sourceRepoRoot: null, emit: () => {},
    _download: async (ref, dest) => {
      assert.equal(ref, pin)
      downloads++
      fs.mkdirSync(path.dirname(dest), { recursive: true })
      fs.writeFileSync(dest, `verified download ${downloads}`)
    }
  }
  const first = await resolveInstallScript(options)
  assert.equal(first.commit, pin)
  assert.equal(fs.readFileSync(first.path, 'utf8'), 'verified download 1')
  fs.writeFileSync(first.path, 'POISONED OLD CACHE')
  const second = await resolveInstallScript(options)
  assert.equal(second.source, 'download')
  assert.equal(fs.readFileSync(second.path, 'utf8'), 'verified download 2')
})

test('untracked build input is dirty in real Git checkout', t => {
  const repo = temp(t)
  const git = (...args) => execFileSync('git', args, { cwd: repo, encoding: 'utf8' }).trim()
  git('init', '-b', 'main')
  git('-c', 'user.name=Regression', '-c', 'user.email=regression@example.invalid', 'commit', '--allow-empty', '-m', 'isolated fixture')
  assert.equal(fromLocalGit(repo).dirty, false)
  fs.mkdirSync(path.join(repo, 'src'))
  fs.writeFileSync(path.join(repo, 'src', 'new.ts'), 'export const changed = true\n')
  assert.equal(fromLocalGit(repo).dirty, true)
})

test('failed Git status is unknown, not clean', () => {
  const actual = fromLocalGit('/unused', cmd => cmd.includes('rev-parse HEAD') ? '1'.repeat(40) : cmd.includes('abbrev') ? 'main' : null)
  assert.equal(actual.dirty, null)
})
