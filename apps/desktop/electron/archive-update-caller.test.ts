import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { expect, it } from 'vitest'

// Fixture installer is POSIX; do not simulate Windows stage selection.
it.skipIf(process.platform === 'win32')('runs the production archive caller and backend selection with disposable transport and installer', () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'archive-caller-'))
  const repo = path.resolve('../..')
  try {
    const output = execFileSync(process.execPath, [path.join(repo, 'tests/fixtures/desktop_archive_update.cjs'), repo, base], {
      encoding: 'utf8', timeout: 60000,
      env: { PATH: process.env.PATH, HOME: path.join(base, 'home'), HERMES_HOME: path.join(base, 'home/.eidolon'),
        GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_SYSTEM: '/dev/null', GIT_TERMINAL_PROMPT: '0',
        GIT_CEILING_DIRECTORIES: base, TYPESCRIPT_JS: path.join(repo, 'node_modules/typescript/lib/typescript.js') }
    })
    expect(output).toContain('PASS: archive check')
  } finally { fs.rmSync(base, { recursive: true, force: true }) }
}, 65000)
