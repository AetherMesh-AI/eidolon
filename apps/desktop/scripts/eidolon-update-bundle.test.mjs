import { test } from 'vitest'
import assert from 'node:assert/strict'
import fs from 'node:fs'

test('macOS updater discovers both native Eidolon output directories', () => {
  const script = fs.readFileSync(new URL('../../../scripts/desktop-update/posix.sh', import.meta.url), 'utf8')
  const swap = script.slice(script.indexOf('mac_swap()'), script.indexOf('deliver_outcome()'))
  assert.ok(swap.includes('$INSTALL_ROOT/apps/desktop/release/mac-arm64/Eidolon.app'))
  assert.ok(swap.includes('$INSTALL_ROOT/apps/desktop/release/mac/Eidolon.app'))
  assert.ok(!swap.includes('/Hermes.app'))
})
