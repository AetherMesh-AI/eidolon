import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { expect, it } from 'vitest'

import { createProjectSource } from './organization-project-source'

it('grants the actual newly-created fixture directory when the temporary parent is an OS alias', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-project-root-'))
  try {
    const actual = path.join(root, 'actual')
    const alias = path.join(root, 'alias')
    fs.mkdirSync(actual)
    // Directory junctions work without a Windows symlink privilege; POSIX uses
    // an ordinary directory symlink. Both reproduce an aliased temp parent.
    fs.symlinkSync(actual, alias, 'junction')
    const source = createProjectSource(alias)
    expect(source).toBe(fs.realpathSync(path.join(actual, 'source')))
    expect(source).not.toBe(path.join(alias, 'source'))
    fs.writeFileSync(path.join(source, 'app.py'), 'value = 1\n')
    expect(fs.readFileSync(path.join(actual, 'source', 'app.py'), 'utf8')).toBe('value = 1\n')
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})
