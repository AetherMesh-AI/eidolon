import { test } from 'vitest'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'

const require = createRequire(import.meta.url)
// Load the public entry first: importing WinPackager first creates a circular import.
require('app-builder-lib')
const { WinPackager } = require('app-builder-lib/out/winPackager.js')
const resources = require('app-builder-lib/out/util/resEdit.js')
const pkg = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'))

test('Windows packaging edits the application icon without entering executable signing', async () => {
  assert.equal(require('app-builder-lib/package.json').version, pkg.devDependencies['electron-builder'])
  const dir = await mkdtemp(path.join(os.tmpdir(), 'eidolon-signing-policy-'))
  const exe = path.join(dir, `${pkg.build.productName}.exe`)
  const icon = path.resolve(fileURLToPath(new URL('..', import.meta.url)), pkg.build.win.icon)
  const originalEdit = resources.editWindowsResources
  const edited = [], signed = []
  // Only native resource editing and credential-signing boundaries are replaced.
  // Real signApp -> signAndEditResources -> signIf dispatch remains intact.
  resources.editWindowsResources = async options => { edited.push(options) }
  const context = {
    platformSpecificBuildOptions: pkg.build.win,
    config: { ...pkg.build, electronDist: dir }, // No signing-manager cache/credential discovery.
    appInfo: {
      productFilename: pkg.build.productName,
      productName: pkg.build.productName,
      copyright: 'Test fixture',
      shortVersion: '1.0.0',
      shortVersionWindows: '1.0.0',
    },
    forceCodeSigning: false,
    signingQueue: Promise.resolve(true),
    getIconPath: async () => icon,
    shouldSignFile: WinPackager.prototype.shouldSignFile,
    signAndEditResources: WinPackager.prototype.signAndEditResources,
    signIf: WinPackager.prototype.signIf,
    _sign: async file => { signed.push(file); return true },
  }
  try {
    await writeFile(exe, 'Not a native executable: dispatch fixture only')
    await WinPackager.prototype.signApp.call(context, { appOutDir: dir, outDir: dir, arch: 1 }, false)
    assert.equal(edited.length, 1, 'application resources must still be edited')
    assert.equal(edited[0].file, exe)
    assert.equal(edited[0].iconPath, icon, 'configured ICO must reach resource editing')
    assert.deepEqual(signed, [], 'executable signing must remain disabled')

    // Sensitivity control: the same real dispatch must reach the signing boundary
    // when the explicit disable is removed. No credential signer is ever invoked.
    context.platformSpecificBuildOptions = { ...pkg.build.win }
    delete context.platformSpecificBuildOptions.signExecutable
    await WinPackager.prototype.signApp.call(context, { appOutDir: dir, outDir: dir, arch: 1 }, false)
    assert.equal(edited.length, 2)
    assert.deepEqual(signed, [exe])
  } finally {
    resources.editWindowsResources = originalEdit
    await rm(dir, { recursive: true, force: true })
  }
})
