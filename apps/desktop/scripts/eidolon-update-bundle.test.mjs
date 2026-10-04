import { test } from 'vitest'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const helper = fileURLToPath(new URL('../../../scripts/desktop-update/mac-bundle.sh', import.meta.url))
const posixTest = test.skipIf(process.platform === 'win32')

function artifact(release, directory, name, modified, mode = 0o755) {
  const bundle = path.join(release, directory, `${name}.app`)
  const executable = path.join(bundle, 'Contents', 'MacOS', name)
  fs.mkdirSync(path.dirname(executable), { recursive: true })
  fs.writeFileSync(executable, '#!/bin/sh\nexit 0\n', { mode })
  fs.utimesSync(executable, modified, modified)
  return bundle
}

function discover(release, architecture) {
  return spawnSync('/bin/bash', ['-c', '. "$1" && find_macos_update_bundle "$2" "$3"',
    'bundle-probe', helper, release, architecture], { encoding: 'utf8' })
}

posixTest.each([
  ['arm64', 'mac-arm64', 'mac'],
  ['x86_64', 'mac', 'mac-arm64']
])('bundle discovery respects brand and compatible artifacts for %s', (architecture, native, foreign) => {
  const release = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-mac-bundles-'))
  try {
    artifact(release, foreign, 'Eidolon', 500)
    const legacy = artifact(release, native, 'Hermes', 400)
    const current = artifact(release, native, 'Eidolon', 100)
    const universal = artifact(release, 'mac-universal', 'Eidolon', 200)
    let result = discover(release, architecture)
    assert.equal(result.status, 0, result.stderr)
    assert.equal(result.stdout.trim(), universal)
    artifact(release, native, 'Eidolon', 300)
    result = discover(release, architecture)
    assert.equal(result.status, 0, result.stderr)
    assert.equal(result.stdout.trim(), current)
    fs.rmSync(current, { recursive: true })
    fs.rmSync(universal, { recursive: true })
    assert.equal(discover(release, architecture).stdout.trim(), legacy)
  } finally {
    fs.rmSync(release, { recursive: true, force: true })
  }
})

posixTest.each(['missing', 'foreign', 'non-executable', 'directory', 'unknown-architecture'])(
  'bundle discovery rejects %s output', kind => {
    const release = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-mac-bundles-'))
    try {
      if (kind === 'foreign') { artifact(release, 'mac', 'Eidolon', 100) }
      if (kind === 'non-executable') { artifact(release, 'mac-arm64', 'Eidolon', 100, 0o644) }
      if (kind === 'directory') {
        fs.mkdirSync(path.join(release, 'mac-arm64/Eidolon.app/Contents/MacOS/Eidolon'), { recursive: true })
      }
      if (kind === 'unknown-architecture') { artifact(release, 'mac-universal', 'Eidolon', 100) }
      const result = discover(release, kind === 'unknown-architecture' ? 'unsupported' : 'arm64')
      assert.equal(result.status, 1, result.stderr)
      assert.equal(result.stdout, '')
    } finally {
      fs.rmSync(release, { recursive: true, force: true })
    }
})
