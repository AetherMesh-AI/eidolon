import { test } from 'vitest'
import assert from 'node:assert/strict'
import fs from 'node:fs'
const pkg = JSON.parse(fs.readFileSync(new URL('../package.json', import.meta.url)))
test('Eidolon desktop native metadata owns a distinct product identity', () => {
  assert.equal(pkg.productName, 'Eidolon')
  assert.equal(pkg.build.productName, pkg.productName)
  assert.equal(pkg.build.executableName, pkg.productName)
  assert.equal(pkg.build.appId, 'com.aethermesh-ai.eidolon')
  for (const key of ['CFBundleName', 'CFBundleDisplayName', 'CFBundleExecutable']) {
    assert.equal(pkg.build.mac.extendInfo[key], pkg.productName)
  }
  assert.equal(pkg.build.win.signAndEditExecutable, true)
  assert.equal(pkg.build.win.signExecutable, false)
  assert.equal(pkg.author, 'AetherMesh contributors')
})
