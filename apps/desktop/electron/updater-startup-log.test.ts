import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { expect, test } from 'vitest'

import { captureUpdaterStartupLog } from './updater-startup-log'

test('startup diagnostics show only this attempt and bound missing, long or replaced logs', () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-updater-log-'))

  try {
    const initial = captureUpdaterStartupLog(home)
    expect(initial.readNewTail()).toBe('')
    fs.mkdirSync(path.dirname(initial.path), { recursive: true })
    fs.writeFileSync(initial.path, 'A previous update failed for another reason\n')
    const current = captureUpdaterStartupLog(home)
    expect(current.readNewTail()).toBe('')
    fs.appendFileSync(current.path, 'xcrun: error: invalid active developer path\n')
    expect(current.readNewTail()).toBe('xcrun: error: invalid active developer path')
    fs.appendFileSync(current.path, 'x'.repeat(2000) + '\nworker could not start\n')
    expect(current.readNewTail().length).toBeLessThanOrEqual(1200)
    expect(current.readNewTail()).toContain('worker could not start')
    expect(current.readNewTail()).not.toContain('previous update')
    fs.writeFileSync(current.path, 'replacement log\n')
    expect(current.readNewTail()).toBe('replacement log')
  } finally {
    fs.rmSync(home, { recursive: true, force: true })
  }
})
