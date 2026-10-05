import fs from 'node:fs'
import path from 'node:path'

/** Canonicalize only the fixture-owned directory before granting its real root. */
export function createProjectSource(sandboxRoot: string): string {
  const source = path.join(sandboxRoot, 'source')
  fs.mkdirSync(source)
  return fs.realpathSync(source)
}
