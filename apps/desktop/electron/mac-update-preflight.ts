import { execFile } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { promisify } from 'node:util'

const runFile = promisify(execFile)
// Darwin <sys/stat.h>: immutable, append-only, data-vault and restricted flags.
// The containing directory's no-unlink flag protects the directory itself,
// not its children (for example /Applications), so only apply it to the app.
const WRITE_RESTRICTED_FLAGS = 0x00000002 | 0x00000004 | 0x00000080 | 0x00020000 | 0x00040000 | 0x00080000
const NO_UNLINK_FLAG = 0x00100000

export interface MacUpdatePreflightFailure {
  ok: false
  error: 'mac-update-recovery-required' | 'mac-update-permission-denied' | 'mac-update-preflight-failed'
  message: string
}

function failure(error: MacUpdatePreflightFailure['error'], message: string): MacUpdatePreflightFailure {
  return { ok: false, error, message: `${message} Eidolon will keep running.` }
}

function permissionFailure(targetApp: string, reason: string): MacUpdatePreflightFailure {
  return failure('mac-update-permission-denied',
    `Eidolon cannot safely replace ${targetApp} with the current permissions: ${reason}. ` +
    'Install the updated app manually in a location your account can manage, or ask your administrator to replace it. ' +
    'Retrying the update in a terminal will not bypass this restriction.')
}

async function pathPresent(candidate: string): Promise<boolean> {
  try {
    await fs.promises.lstat(candidate)

    return true
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      return false
    }

    throw error
  }
}

async function readProtection(target: string) {
  const options = { encoding: 'utf8' as const, timeout: 5000, maxBuffer: 64 * 1024, env: { ...process.env, LC_ALL: 'C' } }

  const [stat, listing] = await Promise.all([
    runFile('/usr/bin/stat', ['-f', '%Uf', target], options),
    // Escape path control characters so a filename cannot masquerade as an ACL
    // entry. Numeric names also avoid directory-service lookups during preflight.
    runFile('/bin/ls', ['-ldebn', target], options)
  ])

  const flags = stat.stdout.trim()

  if (!/^\d+$/.test(flags) || !listing.stdout || stat.stderr || listing.stderr) {
    throw new Error(`Could not inspect filesystem protections for ${target}`)
  }

  const denied = listing.stdout.split('\n').flatMap(line => {
    const entry = /^\s*\d+:\s+.*\sdeny\s+(.+)$/.exec(line)

    return entry ? entry[1].split(',') : []
  })

  return { flags: Number(flags), denied }
}

/**
 * Read-only macOS preflight before giving up the running app. Passing is not a
 * promise that rename will succeed: ACL principal/order resolution, app-management
 * policy and races remain the transaction helper's responsibility. In particular,
 * reject relevant visible deny entries conservatively, without changing any ACL.
 */
export async function macUpdatePreflight(targetApp: string | null): Promise<MacUpdatePreflightFailure | null> {
  if (!targetApp || !path.isAbsolute(targetApp) || !targetApp.endsWith('.app')) {
    return failure('mac-update-preflight-failed',
      'The running app bundle could not be identified. Reopen your installed Eidolon app before trying the update again.')
  }

  try {
    // lstat intentionally includes dangling symlinks. Never delete or assume
    // these paths are disposable: they may hold the only recoverable app.
    for (const suffix of ['.old', '.new', '.eidolon-update']) {
      const leftover = `${targetApp}${suffix}`

      if (await pathPresent(leftover)) {
        return failure('mac-update-recovery-required',
          `An earlier update needs review at ${leftover}. No app files were changed. ` +
          'Keep that recovery copy and ask your administrator or support to inspect it before retrying.')
      }
    }

    const parentPath = path.dirname(targetApp)
    const [appStat, parentStat] = await Promise.all([fs.promises.lstat(targetApp), fs.promises.lstat(parentPath)])

    if (!appStat.isDirectory() || !parentStat.isDirectory() || appStat.dev !== parentStat.dev) {
      return failure('mac-update-preflight-failed',
        `The app at ${targetApp} is not a replaceable local bundle. Install and open a regular copy of Eidolon before retrying.`)
    }

    const parent = await fs.promises.realpath(parentPath)

    // access() consults the filesystem, including ACLs and read-only mounts;
    // mode bits alone cannot determine whether this account can stage an app.
    await fs.promises.access(parent, fs.constants.W_OK | fs.constants.X_OK)

    const uid = process.geteuid()

    if ((parentStat.mode & 0o1000) && uid !== 0 && uid !== parentStat.uid && uid !== appStat.uid) {
      return permissionFailure(targetApp, 'the containing folder restricts replacement of apps owned by another account')
    }

    const [appProtection, parentProtection] = await Promise.all([readProtection(targetApp), readProtection(parent)])

    if ((appProtection.flags & (WRITE_RESTRICTED_FLAGS | NO_UNLINK_FLAG)) ||
        (parentProtection.flags & WRITE_RESTRICTED_FLAGS)) {
      return permissionFailure(targetApp, 'the app or its containing folder has a protected filesystem flag')
    }

    if (appProtection.denied.includes('delete') ||
        parentProtection.denied.some(permission => ['add_subdirectory', 'delete_child', 'search'].includes(permission))) {
      return permissionFailure(targetApp, 'the app or its containing folder has a restrictive access-control entry')
    }

    return null
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code

    if (['EACCES', 'EPERM', 'EROFS'].includes(code)) {
      return permissionFailure(targetApp, `filesystem access was denied (${code})`)
    }

    return failure('mac-update-preflight-failed',
      `Eidolon could not verify that ${targetApp} can be replaced (${code || (error as Error).message}). ` +
      'Check that the app and its containing folder are available, then retry. If this continues, install the updated app manually.')
  }
}
