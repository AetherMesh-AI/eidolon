import { randomUUID } from 'node:crypto'

import type { NativeTokenSet } from './native-oauth'

/** Owns an authenticated account lifetime, not rotating access credentials. */
export function createOAuthSessionAuthority(onRetired: (identity: string, scope: string) => void = () => {}) {
  let revision = 0
  const operations = new Map<string, Set<() => void>>()
  const scopes = new Map<string, { identity: string; changing: boolean; revision: number }>()

  const state = (scope: string) => {
    let value = scopes.get(scope)

    if (!value) {
      value = { identity: randomUUID(), changing: false, revision: 0 }
      scopes.set(scope, value)
    }

    return value
  }

  const assertCurrent = (scope: string, identity: string | undefined) => {
    const current = state(scope)

    if (!identity || current.identity !== identity || current.changing) {
      throw new Error('Eidolon OAuth account session changed; reconnect before retrying.')
    }
  }

  return {
    captureRequest: () => revision,
    assertRequest(scope: string, identity: string | undefined, requestRevision: number) {
      assertCurrent(scope, identity)

      if (state(scope).revision > requestRevision) {
        throw new Error('Eidolon OAuth account session changed while resolving the request.')
      }
    },
    capture: (scope: string) => state(scope).identity,
    assertCurrent,
    track(scope: string, identity: string, cancel: () => void) {
      assertCurrent(scope, identity)
      const owned = operations.get(identity) ?? new Set<() => void>()
      owned.add(cancel)
      operations.set(identity, owned)

      return () => {
        owned.delete(cancel)

        if (owned.size === 0) { operations.delete(identity) }
      }
    },
    beginChange(scope: string) {
      const previous = state(scope).identity
      const identity = randomUUID()
      scopes.set(scope, { identity, changing: true, revision: ++revision })

      for (const cancel of operations.get(previous) ?? []) { cancel() }
      operations.delete(previous)
      onRetired(previous, scope)

      const assertChange = () => {
        if (state(scope).identity !== identity) {
          throw new Error('Eidolon OAuth account session changed during sign-in.')
        }
      }

      return {
        assertCurrent: assertChange,
        finish(publish?: () => void) {
          assertChange()
          const current = state(scope)

          publish?.()
          current.changing = false
        }
      }
    },
    async run<T>(scope: string, identity: string | undefined, action: () => Promise<T>) {
      assertCurrent(scope, identity)
      const result = await action()
      assertCurrent(scope, identity)

      return result
    }
  }
}

export async function refreshOwnedNativeAccount(options: {
  authority: ReturnType<typeof createOAuthSessionAuthority>
  scope: string
  tokens: NativeTokenSet | null
  needsRefresh: (tokens: NativeTokenSet) => boolean
  refresh: (tokens: NativeTokenSet) => Promise<NativeTokenSet>
  publish: (tokens: NativeTokenSet) => void
  clear: () => void
}) {
  const { authority, scope, tokens } = options
  const identity = authority.capture(scope)
  authority.assertCurrent(scope, identity)

  if (!tokens) { return null }

  if (!options.needsRefresh(tokens)) { return tokens.accessToken }

  if (!tokens.refreshToken) {
    options.clear()

    return null
  }

  let rotated: NativeTokenSet

  try {
    rotated = await options.refresh(tokens)
  } catch (error) {
    authority.assertCurrent(scope, identity)

    if (error instanceof Error && 'statusCode' in error && error.statusCode === 401) {
      options.clear()

      return null
    }

    throw error
  }

  authority.assertCurrent(scope, identity)

  if (rotated.userId !== tokens.userId || rotated.provider !== tokens.provider) {
    const change = authority.beginChange(scope)
    change.finish(() => options.publish(rotated))
    throw new Error('Eidolon OAuth account changed during token refresh; reconnect.')
  }

  options.publish(rotated)

  return rotated.accessToken
}

export function createOwnedNativeAccountResolver() {
  const pending = new Map<string, { identity: string; promise: Promise<string | null> }>()

  return (key: string, options: Parameters<typeof refreshOwnedNativeAccount>[0]) => {
    const identity = options.authority.capture(options.scope)
    options.authority.assertCurrent(options.scope, identity)
    const existing = pending.get(key)

    if (existing?.identity === identity) { return existing.promise }

    const request = {
      identity,
      promise: Promise.resolve().then(() => {
        options.authority.assertCurrent(options.scope, identity)

        return refreshOwnedNativeAccount(options)
      }).finally(() => {
        if (pending.get(key) === request) { pending.delete(key) }
      })
    }

    pending.set(key, request)

    return request.promise
  }
}

/** A later login must not install cookies while an earlier logout is removing them. */
export function createOAuthCookieCleanupQueue() {
  const pending = new Map<string, Promise<void>>()

  return {
    wait: (scope: string) => pending.get(scope) ?? Promise.resolve(),
    clear(scope: string, remove: () => Promise<void>) {
      const previous = pending.get(scope) ?? Promise.resolve()

      const next = previous.then(remove).finally(() => {
        if (pending.get(scope) === next) { pending.delete(scope) }
      })

      pending.set(scope, next)

      return next
    }
  }
}
