import { accessSync, constants, realpathSync } from 'node:fs'
import path from 'node:path'

/** electron-builder defaults to an unsuffixed x64 directory. Never fall back to stock Electron. */
export function packagedBinaryPath(release: string, platform = process.platform, arch = process.arch): string {
  if (!['x64', 'arm64'].includes(arch)) {
    throw new Error(`Unsupported packaged app architecture: ${arch}`)
  }

  const suffix = arch === 'x64' ? '' : `-${arch}`

  if (platform === 'darwin') {
    return path.join(release, `mac${suffix}`, 'Eidolon.app', 'Contents', 'MacOS', 'Eidolon')
  }

  if (platform === 'win32') {
    return path.join(release, `win${suffix}-unpacked`, 'Eidolon.exe')
  }

  if (platform === 'linux') {
    return path.join(release, `linux${suffix}-unpacked`, 'Eidolon')
  }

  throw new Error(`Unsupported packaged app platform: ${platform}`)
}

export function requirePackagedBinary(release: string, platform = process.platform, arch = process.arch): string {
  const binary = packagedBinaryPath(release, platform, arch)

  try {
    accessSync(binary, constants.X_OK)

    // A symlink to a source Electron executable is not packaged-app evidence.
    if (realpathSync(binary) !== binary) {
      throw new Error('Packaged binary resolves outside its expected path')
    }
  } catch {
    throw new Error(`Packaged Eidolon binary is missing or invalid: ${binary}. Run npm run pack first.`)
  }

  return binary
}
