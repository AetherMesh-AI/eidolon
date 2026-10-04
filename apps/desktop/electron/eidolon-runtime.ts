import fs from 'node:fs'
import path from 'node:path'

/** Only an identified Eidolon checkout may use the pre-rename runtime layout. */
export function isEidolonCheckout(root: string): boolean {
  try {
    const manifest = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'))
    const repository = typeof manifest.repository === 'string' ? manifest.repository : manifest.repository?.url

    return typeof repository === 'string' && /^(?:git\+)?(?:https:\/\/github\.com\/|ssh:\/\/git@github\.com\/|git@github\.com:)AetherMesh-AI\/Eidolon(?:\.git)?\/?$/i.test(repository)
  } catch {
    return false
  }
}

/** Keep an existing fork in place; never discover an upstream ~/.hermes home. */
export function resolveManagedRuntimeRoot(home: string): string {
  const current = path.join(home, 'eidolon-agent')

  if (fs.existsSync(current)) {return current}
  const legacy = path.join(home, 'hermes-agent')

  return isEidolonCheckout(legacy) ? legacy : current
}

export function runtimeCliModule(root?: string): 'eidolon_cli.main' | 'hermes_cli.main' {
  if (root && !fs.existsSync(path.join(root, 'eidolon_cli', 'main.py')) &&
      fs.existsSync(path.join(root, 'hermes_cli', 'main.py')) && isEidolonCheckout(root)) {
    return 'hermes_cli.main'
  }

  return 'eidolon_cli.main'
}

export function hasRuntimeSource(root: string): boolean {
  return fs.existsSync(path.join(root, runtimeCliModule(root).split('.')[0], 'main.py'))
}

export function runtimeShimPath(root: string, isWindows = process.platform === 'win32'): string {
  const directory = path.join(root, 'venv', isWindows ? 'Scripts' : 'bin')
  const current = path.join(directory, isWindows ? 'eidolon.exe' : 'eidolon')
  const legacy = path.join(directory, isWindows ? 'hermes.exe' : 'hermes')

  return !fs.existsSync(current) && isEidolonCheckout(root) && fs.existsSync(legacy) ? legacy : current
}
