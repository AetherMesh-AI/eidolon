import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { expect, it } from 'vitest'

import { writeBuildStamp } from '../scripts/write-build-stamp.mjs'

import { formatInstallVersion, readInstallStampFromPaths } from './install-stamp'

it('generates a build identity and consumes it through the real runtime reader', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'identity-'))
  const env = { PATH: process.env.PATH!, HOME: path.join(root, 'home'), HERMES_HOME: path.join(root, 'home/.hermes'),
    GIT_CONFIG_GLOBAL: os.devNull, GIT_CONFIG_SYSTEM: os.devNull, GIT_CONFIG_NOSYSTEM: '1', GIT_CEILING_DIRECTORIES: path.dirname(root) }
  const git = (...args: string[]) => execFileSync('git', args, { cwd: root, env, encoding: 'utf8' }).trim()
  const before = process.env
  try {
    process.env = { ...env, HERMES_PYTHON: before.HERMES_PYTHON || 'python3' }
    fs.mkdirSync(env.HOME)
    git('init', '-b', 'main'); git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.invalid')
    git('commit', '--allow-empty', '-m', 'anchor')
    const anchor = git('rev-parse', 'HEAD')
    fs.mkdirSync(path.join(root, 'eidolon_cli'))
    fs.writeFileSync(path.join(root, 'eidolon_cli/eidolon_version.py'),
      fs.readFileSync(path.resolve('../../eidolon_cli/eidolon_version.py'), 'utf8').replace('437db7394d78a178966fb2ae42792f2978133a9d', anchor))
    git('add', '.'); git('commit', '-m', 'generator')
    const stamp = writeBuildStamp({ repoRoot: root })
    const runtime = readInstallStampFromPaths([path.join(root, 'apps/desktop/build/install-stamp.json')])
    expect(runtime?.version).toBe(stamp.version)
    expect(formatInstallVersion(runtime)).not.toContain('unverified')
  } finally { process.env = before; fs.rmSync(root, { recursive: true, force: true }) }
})
