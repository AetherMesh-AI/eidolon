import { test } from 'vitest'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { writeBuildStamp } from './write-build-stamp.mjs'
import { buildArguments } from './run-electron-builder.mjs'
import { readInstallStampFromPaths, formatInstallVersion } from '../electron/install-stamp.ts'

// A release source archive carries Python's already-verified identity. Git
// ancestry derivation is covered by tests/hermes_cli/test_eidolon_version.py;
// these tests exercise the real Python -> JS -> packager consumer boundary.
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'version-build-'))
  t.onTestFinished(() => fs.rmSync(root, { recursive: true, force: true }))
  fs.mkdirSync(path.join(root, 'hermes_cli'))
  fs.copyFileSync(new URL('../../../hermes_cli/eidolon_version.py', import.meta.url), path.join(root, 'hermes_cli/eidolon_version.py'))
  const identity = {
    schemaVersion: 1, version: '0.1.2', channel: 'alpha',
    repository: 'AetherMesh-AI/Eidolon', updateBranch: 'main',
    commit: 'a'.repeat(40), shortCommit: 'a'.repeat(12), branch: 'main', dirty: false,
    baseTag: 'alpha-v0.1.0', baseCommit: '437db7394d78a178966fb2ae42792f2978133a9d',
    distance: 2, versionSource: 'stamp'
  }
  const sourceStamp = path.join(root, 'hermes_cli/_build_identity.json')
  fs.writeFileSync(sourceStamp, JSON.stringify(identity))
  return { root, identity, sourceStamp, stampPath: path.join(root, 'apps/desktop/build/install-stamp.json') }
}

test('source archive identity flows through the actual generator, display and numeric packager arguments', t => {
  const { root, identity, sourceStamp, stampPath } = fixture(t)
  const payload = writeBuildStamp({ repoRoot: root })
  assert.equal(payload.version, identity.version)
  assert.equal(payload.versionSource, 'stamp')
  const stamp = readInstallStampFromPaths([stampPath])
  assert.match(formatInstallVersion(stamp), /^0\.1\.2 alpha/)
  assert.ok(formatInstallVersion(stamp).includes(identity.shortCommit))
  assert.doesNotMatch(formatInstallVersion(stamp), /unverified|unknown|dirty/)
  const args = buildArguments({ repoRoot: root, stampPath, argv: ['--dir'] })
  assert.ok(args.includes(`-c.extraMetadata.version=${identity.version}`))
  assert.deepEqual(args.slice(-2), ['--publish', 'never'])
  assert.throws(() => buildArguments({ repoRoot: root, stampPath, argv: ['-c.extraMetadata.version=9.9.9'] }), /override/)
  fs.writeFileSync(sourceStamp, JSON.stringify({ ...identity, dirty: true }))
  assert.throws(() => buildArguments({ repoRoot: root, stampPath }), /Stale|conflicting/)
})

test('missing, legacy and malformed build stamps cannot authorize packaging', t => {
  const { root, stampPath } = fixture(t)
  assert.throws(() => buildArguments({ repoRoot: root, stampPath }))
  const valid = writeBuildStamp({ repoRoot: root })
  for (const change of [{ versionSource: 'fallback' }, { version: '99.0.0' }, { distance: true }, { shortCommit: 'wrong' }, { baseCommit: 'z'.repeat(40) }]) {
    fs.writeFileSync(stampPath, JSON.stringify({ ...valid, ...change }))
    assert.match(formatInstallVersion(readInstallStampFromPaths([stampPath])), /unverified/)
    assert.throws(() => buildArguments({ repoRoot: root, stampPath }))
  }
  fs.writeFileSync(stampPath, JSON.stringify({ commit: valid.commit, dirty: false }))
  assert.match(formatInstallVersion(readInstallStampFromPaths([stampPath])), /unverified/)
  assert.throws(() => buildArguments({ repoRoot: root, stampPath }))
})

test('Git-less Python stamp survives regeneration, but a CI SHA alone cannot replace ancestry', t => {
  const { root, sourceStamp, stampPath } = fixture(t)
  const python = process.env.HERMES_PYTHON || 'python3'
  execFileSync(python, [path.join(root, 'hermes_cli/eidolon_version.py'), '--repo-root', root, '--output', sourceStamp])
  assert.equal(writeBuildStamp({ repoRoot: root }).versionSource, 'stamp')
  fs.rmSync(sourceStamp)
  const payload = writeBuildStamp({ repoRoot: root, env: { ...process.env, GITHUB_SHA: 'a'.repeat(40) } })
  assert.equal(payload.versionSource, 'fallback')
  assert.match(formatInstallVersion(readInstallStampFromPaths([stampPath])), /unverified/)
  assert.throws(() => buildArguments({ repoRoot: root, stampPath }))
})
