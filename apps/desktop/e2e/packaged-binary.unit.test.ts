import { chmodSync, mkdirSync, mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { describe, expect, it } from 'vitest'

import { packagedBinaryPath, requirePackagedBinary } from './packaged-binary'

describe('packaged Eidolon binary', () => {
  it('matches electron-builder output names and both macOS architectures', () => {
    expect(packagedBinaryPath('/release', 'darwin', 'arm64')).toBe(path.join('/release','mac-arm64','Eidolon.app','Contents','MacOS','Eidolon'))
    expect(packagedBinaryPath('/release', 'darwin', 'x64')).toBe(path.join('/release','mac','Eidolon.app','Contents','MacOS','Eidolon'))
    expect(packagedBinaryPath('/release', 'win32', 'x64')).toBe(path.join('/release','win-unpacked','Eidolon.exe'))
    expect(packagedBinaryPath('/release', 'linux', 'arm64')).toBe(path.join('/release','linux-arm64-unpacked','Eidolon'))
    expect(() => packagedBinaryPath('/release', 'darwin', 'ia32')).toThrow('architecture')
  })

  it.skipIf(process.platform === 'win32')('fails closed for missing, nonexecutable and redirected bundles', () => {
    const root = realpathSync(mkdtempSync(path.join(os.tmpdir(), 'packaged-binary-')))
    const binary = packagedBinaryPath(root, 'darwin', 'arm64')

    try {
      expect(() => requirePackagedBinary(root, 'darwin', 'arm64')).toThrow('missing or invalid')
      mkdirSync(path.dirname(binary), { recursive: true })
      writeFileSync(binary, 'synthetic fixture')
      chmodSync(binary, 0o600)
      expect(() => requirePackagedBinary(root, 'darwin', 'arm64')).toThrow('missing or invalid')
      chmodSync(binary, 0o700)
      expect(requirePackagedBinary(root, 'darwin', 'arm64')).toBe(binary)
      rmSync(binary)
      const stock = path.join(root, 'Electron')
      writeFileSync(stock, 'synthetic stock Electron')
      chmodSync(stock, 0o700)
      symlinkSync(stock, binary)
      expect(() => requirePackagedBinary(root, 'darwin', 'arm64')).toThrow('missing or invalid')
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })
})
