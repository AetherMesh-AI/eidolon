/** Persistent-profile-shaped native fixture; never relaxes protected-store checks. */
import { chmodSync, lstatSync, mkdirSync, opendirSync, realpathSync, rmSync, type Stats } from 'node:fs'
import path from 'node:path'

import { createSandbox, type Sandbox } from './fixtures'

const containerName = '.subscription-native-sandboxes'

function identity(value: Stats): number[] {
  return [value.dev, value.ino, value.mode, value.uid, value.gid]
}

function sameIdentity(left: number[], right: number[]): boolean {
  return left.every((value, index) => value === right[index])
}

function directory(location: string, uid: number, privateMode = false): number[] {
  const value = lstatSync(location)

  if (
    !value.isDirectory() ||
    value.isSymbolicLink() ||
    value.uid !== uid ||
    (privateMode && (value.mode & 0o7777) !== 0o700) ||
    realpathSync(location) !== location
  ) {
    throw new Error('subscription_sandbox_unsafe_directory')
  }

  return identity(value)
}

export interface RemainingEntries {
  state: 'observed' | 'removed' | 'identity_unverified' | 'inspection_failed'
  truncated: boolean
  counts: { home: number; userData: number; userHome: number; evidence: number; observer: number; other: number }
}
export interface SubscriptionSandbox extends Sandbox {
  diagnostic: () => RemainingEntries
}

export function createSubscriptionSandbox(workspace: string): SubscriptionSandbox {
  if (typeof process.getuid !== 'function' || !path.isAbsolute(workspace) || path.resolve(workspace) !== workspace) {
    throw new Error('subscription_sandbox_unsupported_workspace')
  }

  const uid = process.getuid()
  const workspaceIdentity = directory(workspace, uid)
  const container = path.join(workspace, containerName)

  // Concurrent invocations may share this one private container. Neither
  // initialization nor cleanup adopts, repairs, enumerates or prunes it.
  try {
    mkdirSync(container, { mode: 0o700 })
  } catch (error) {
    if (!(error instanceof Error) || (error as NodeJS.ErrnoException).code !== 'EEXIST') {
      throw error
    }
  }

  const parentIdentity = directory(container, uid, true)

  if (!sameIdentity(directory(workspace, uid), workspaceIdentity)) {
    throw new Error('subscription_sandbox_workspace_changed')
  }

  const sandbox = createSandbox('subscription-native', container)
  const ownedRoot = sandbox.root
  const rootIdentity = directory(ownedRoot, uid, true)

  if (path.dirname(ownedRoot) !== container) {
    throw new Error('subscription_sandbox_outside_container')
  }

  for (const child of [sandbox.hermesHome, sandbox.userDataDir]) {
    if (path.dirname(child) !== ownedRoot) {
      throw new Error('subscription_sandbox_outside_root')
    }

    directory(child, uid)
  }

  chmodSync(sandbox.hermesHome, 0o700)

  let closed = false

  const verify = () => {
    if (
      !sameIdentity(directory(workspace, uid), workspaceIdentity) ||
      !sameIdentity(directory(container, uid, true), parentIdentity) ||
      !sameIdentity(directory(ownedRoot, uid, true), rootIdentity)
    ) {
      throw new Error('subscription_sandbox_identity_changed')
    }
  }

  verify()

  return {
    ...sandbox,
    diagnostic: () => {
      const result: RemainingEntries = {
        state: closed ? 'removed' : 'observed',
        truncated: false,
        counts: { home: 0, userData: 0, userHome: 0, evidence: 0, observer: 0, other: 0 }
      }

      if (closed) {
        return result
      }

      try {
        verify()
      } catch {
        result.state = 'identity_unverified'

        return result
      }

      try {
        const directory = opendirSync(ownedRoot)

        const categories: Record<string, keyof RemainingEntries['counts']> = {
          'hermes-home': 'home',
          'electron-user-data': 'userData',
          'user-home': 'userHome',
          evidence: 'evidence',
          'python-observer': 'observer'
        }

        try {
          for (let count = 0; count <= 64; count++) {
            const entry = directory.readSync()

            if (!entry) {
              break
            }

            if (count === 64) {
              result.truncated = true

              break
            }

            const category = Object.hasOwn(categories, entry.name) ? categories[entry.name] : 'other'
            result.counts[category]++
          }
        } finally {
          directory.closeSync()
        }
      } catch {
        result.state = 'inspection_failed'
      }

      return result
    },
    cleanup: () => {
      if (closed) {
        return
      }

      // Refuse observed substitution. Node's recursive removal does not follow
      // child symlinks; this is not an atomic compare-and-remove guarantee.
      verify()
      rmSync(ownedRoot, { recursive: true })
      closed = true
    }
  }
}
