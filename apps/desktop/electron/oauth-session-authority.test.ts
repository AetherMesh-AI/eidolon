import { describe, expect, it, vi } from 'vitest'

import { requestBackendJson, requestProfileBackendJson } from './backend-json-request'
import type { NativeTokenSet } from './native-oauth'
import { createOAuthCookieCleanupQueue, createOAuthSessionAuthority, createOwnedNativeAccountResolver, refreshOwnedNativeAccount } from './oauth-session-authority'
import { createPrimaryRemoteConnection, remoteAuthenticationIdentity } from './primary-backend-startup'
import { createRegistryGatewayWsUrlHandler, refreshPrimaryGatewayWsUrl } from './remote-ws-headers'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })

  return { promise, resolve, reject }
}

const accountA: NativeTokenSet = { accessToken: 'synthetic-a', refreshToken: 'refresh-a', expiresAt: 0, provider: 'fixture', userId: 'a' }
const accountB: NativeTokenSet = { ...accountA, accessToken: 'synthetic-b', userId: 'b' }

describe('OAuth account authority across production request seams', () => {
  it.each(['primary', 'registry'] as const)('retires held %s tickets and cached descriptors without dialing the new account', async route => {
    const retired = vi.fn()
    const authority = createOAuthSessionAuthority(retired)
    const scope = 'synthetic-partition'
    const owner = authority.capture(scope)
    const baseUrl = 'https://fixture.invalid'

    const descriptor = createPrimaryRemoteConnection({
      authMode: 'oauth', baseUrl, token: null, headers: {},
      oauthSessionIdentity: owner,
      authenticationIdentity: remoteAuthenticationIdentity(baseUrl, 'oauth', null, {}, owner),
      wsUrl: 'wss://fixture.invalid/api/ws?ticket=initial'
    }, [], {})

    const held = deferred<string>()
    const rememberHeaders = vi.fn()
    const mintTicket = vi.fn(() => held.promise)

    const dependencies = {
      assertAuthority: (connection: typeof descriptor) => authority.assertCurrent(scope, connection.oauthSessionIdentity),
      mintTicket, rememberHeaders,
      buildTicketUrl: (_base: string, ticket: string) => `wss://fixture.invalid/api/ws?ticket=${ticket}`
    }

    const refresh = (connection: typeof descriptor) => route === 'primary'
      ? refreshPrimaryGatewayWsUrl(connection, dependencies)
      : createRegistryGatewayWsUrlHandler({ ...dependencies, ensureBackend: async () => connection })({})

    const pending = refresh(descriptor)
    await vi.waitFor(() => expect(mintTicket).toHaveBeenCalledOnce())
    const rejected = expect(pending).rejects.toThrow('account session changed')
    const change = authority.beginChange(scope)
    expect(() => authority.assertCurrent(scope, authority.capture(scope))).toThrow('account session changed')
    change.finish()
    held.resolve('ticket-for-a')
    await rejected
    expect(rememberHeaders).not.toHaveBeenCalled()
    await expect(refresh(descriptor)).rejects.toThrow('account session changed')
    expect(mintTicket).toHaveBeenCalledOnce()
    const nextOwner = authority.capture(scope)

    const next = { ...descriptor, oauthSessionIdentity: nextOwner,
      authenticationIdentity: remoteAuthenticationIdentity(baseUrl, 'oauth', null, {}, nextOwner) }

    expect(next.authenticationIdentity).not.toBe(descriptor.authenticationIdentity)
    mintTicket.mockResolvedValue('ticket-for-b')
    await expect(refresh(next)).resolves.toContain('ticket-for-b')
    expect(retired).toHaveBeenCalledExactlyOnceWith(owner, scope)
  })

  it.each(['success', 'rejection'] as const)('prevents an old native refresh %s from overwriting a new account', async outcome => {
    const authority = createOAuthSessionAuthority()
    const scope = 'native-fixture'
    const held = deferred<NativeTokenSet>()
    const publish = vi.fn()
    const clear = vi.fn()

    const pending = refreshOwnedNativeAccount({ authority, scope, tokens: accountA,
      needsRefresh: () => true, refresh: () => held.promise, publish, clear })

    const rejected = expect(pending).rejects.toThrow('account session changed')
    const change = authority.beginChange(scope)
    change.finish(() => publish(accountB))

    if (outcome === 'success') {held.resolve({ ...accountA, accessToken: 'rotated-a' })}
    else {held.reject(Object.assign(new Error('expired-a'), { statusCode: 401 }))}

    await rejected
    expect(publish).toHaveBeenCalledExactlyOnceWith(accountB)
    expect(clear).not.toHaveBeenCalled()
    const owner = authority.capture(scope)
    await expect(refreshOwnedNativeAccount({ authority, scope, tokens: accountB,
      needsRefresh: () => true, refresh: async () => ({ ...accountB, accessToken: 'rotated-b' }), publish, clear })).resolves.toBe('rotated-b')
    expect(authority.capture(scope)).toBe(owner)
    expect(() => authority.assertCurrent(scope, owner)).not.toThrow()
  })

  it('blocks old REST responses and compatibility retries while allowing a new owned request', async () => {
    const authority = createOAuthSessionAuthority()
    const scope = 'cookie-fixture'
    const descriptor = { mode: 'remote' as const, authMode: 'oauth', baseUrl: 'https://fixture.invalid', oauthSessionIdentity: authority.capture(scope) }
    const held = deferred<unknown>()

    const transports = {
      assertAuthority: (connection: typeof descriptor) => authority.assertCurrent(scope, connection.oauthSessionIdentity),
      ensureNativeAccessToken: async () => null,
      fetchJson: vi.fn(), fetchJsonViaOauthSession: vi.fn(() => held.promise)
    }

    const pending = requestBackendJson(descriptor, '/api/eidolon/update', { method: 'POST' }, transports)
    await vi.waitFor(() => expect(transports.fetchJsonViaOauthSession).toHaveBeenCalledOnce())
    const rejected = expect(pending).rejects.toThrow('account session changed')
    authority.beginChange(scope).finish()
    held.reject(Object.assign(new Error('missing'), { statusCode: 404 }))
    await rejected
    expect(transports.fetchJsonViaOauthSession).toHaveBeenCalledOnce()
    const next = { ...descriptor, oauthSessionIdentity: authority.capture(scope) }
    transports.fetchJsonViaOauthSession.mockResolvedValue({ ok: true })
    await expect(requestBackendJson(next, '/api/status', {}, transports)).resolves.toEqual({ ok: true })
  })
})

it('coalesces same-account refresh and never joins another account lifetime', async () => {
  const authority = createOAuthSessionAuthority()
  const resolver = createOwnedNativeAccountResolver()
  const held = deferred<NativeTokenSet>()
  const refresh = vi.fn(() => held.promise)
  const publish = vi.fn()
  const options = { authority, scope: 'fixture', tokens: accountA, needsRefresh: () => true, refresh, publish, clear: vi.fn() }
  const first = resolver('fixture-url', options)
  expect(resolver('fixture-url', options)).toBe(first)
  await vi.waitFor(() => expect(refresh).toHaveBeenCalledOnce())
  const rejected = expect(first).rejects.toThrow('account session changed')
  authority.beginChange('fixture').finish()
  const current = resolver('fixture-url', { ...options, tokens: accountB, refresh: async () => ({ ...accountB, accessToken: 'rotated-b' }) })
  await expect(current).resolves.toBe('rotated-b')
  held.resolve({ ...accountA, accessToken: 'rotated-a' })
  await rejected
  expect(publish).toHaveBeenCalledExactlyOnceWith({ ...accountB, accessToken: 'rotated-b' })
})

it('cancels old cookie operations on account transitions and prevents a superseded login from publishing', () => {
  const authority = createOAuthSessionAuthority()
  const scope = 'cookie-partition'
  const owner = authority.capture(scope)
  const cancel = vi.fn()
  const unrelated = vi.fn()
  authority.track(scope, owner, cancel)
  authority.track('other-partition', authority.capture('other-partition'), unrelated)
  const firstLogin = authority.beginChange(scope)
  expect(cancel).toHaveBeenCalledOnce()
  const secondLogin = authority.beginChange(scope)
  const publish = vi.fn()
  expect(() => firstLogin.finish(publish)).toThrow('session changed during sign-in')
  expect(publish).not.toHaveBeenCalled()
  secondLogin.finish(publish)
  expect(publish).toHaveBeenCalledOnce()
  expect(unrelated).not.toHaveBeenCalled()
})

it('waits for old cookie cleanup before a replacement login can publish', async () => {
  const authority = createOAuthSessionAuthority()
  const cleanup = createOAuthCookieCleanupQueue()
  const held = deferred<void>()
  const events: string[] = []
  const clear = cleanup.clear('shared', async () => { await held.promise; events.push('old cookies removed') })
  const login = authority.beginChange('shared')
  const next = cleanup.wait('shared').then(() => { login.assertCurrent(); login.finish(() => events.push('new login')) })
  await Promise.resolve()
  expect(events).toEqual([])
  held.resolve()
  await Promise.all([clear, next])
  expect(events).toEqual(['old cookies removed', 'new login'])
})

it.each(['retired', 'same-account'] as const)('owns profile REST mutation across a held native refresh: %s', async scenario => {
  const authority = createOAuthSessionAuthority()
  const scope = 'profile-rest'
  const revision = authority.captureRequest()

  const descriptor = { mode: 'remote' as const, authMode: 'oauth', baseUrl: 'https://fixture.invalid',
    oauthSessionIdentity: authority.capture(scope) }

  const held = deferred<NativeTokenSet>()
  const refresh = vi.fn(() => held.promise)

  const transports = {
    assertAuthority: (connection: typeof descriptor) => authority.assertRequest(scope, connection.oauthSessionIdentity, revision),
    ensureNativeAccessToken: () => refreshOwnedNativeAccount({ authority, scope, tokens: accountA,
      needsRefresh: () => true, refresh, publish: vi.fn(), clear: vi.fn() }),
    fetchJson: vi.fn(async () => ({ updated: true })),
    fetchJsonViaOauthSession: vi.fn(async () => ({ wrongAccountWrite: true }))
  }

  const pending = requestProfileBackendJson('worker', '/api/sessions/fixture', { method: 'PATCH', body: { title: 'original intent' } },
    async () => descriptor, transports)

  const settled = pending.then(value => ({ value }), error => ({ error }))
  await vi.waitFor(() => expect(refresh).toHaveBeenCalledOnce())

  if (scenario === 'retired') {authority.beginChange(scope).finish()}
  held.resolve({ ...accountA, accessToken: 'rotated-a' })
  const outcome = await settled
  expect(transports.fetchJsonViaOauthSession).not.toHaveBeenCalled()

  if (scenario === 'retired') {
    expect(outcome).toHaveProperty('error')
    expect(transports.fetchJson).not.toHaveBeenCalled()
  } else {
    expect(outcome).toEqual({ value: { updated: true } })
    expect(transports.fetchJson).toHaveBeenCalledWith('https://fixture.invalid/api/sessions/fixture', null,
      expect.objectContaining({ method: 'PATCH', body: { title: 'original intent' }, bearer: 'rotated-a' }))
  }
})

it.each(['same-scope', 'unrelated-scope'] as const)('owns profile REST before its first descriptor: %s', async scenario => {
  const authority = createOAuthSessionAuthority()
  const revision = authority.captureRequest()
  const scope = 'unresolved-profile-rest'
  const held = deferred<{ mode: 'remote'; authMode: string; baseUrl: string; oauthSessionIdentity: string }>()

  const transports = {
    assertAuthority: (connection: { oauthSessionIdentity?: string }) => authority.assertRequest(scope, connection.oauthSessionIdentity, revision),
    ensureNativeAccessToken: vi.fn(async () => 'current-token'),
    fetchJson: vi.fn(async () => ({ updated: true })),
    fetchJsonViaOauthSession: vi.fn()
  }

  const pending = requestProfileBackendJson('worker', '/api/sessions/fixture', { method: 'PATCH' }, () => held.promise, transports)
  const settled = pending.then(value => ({ value }), error => ({ error }))
  authority.beginChange(scenario === 'same-scope' ? scope : 'unrelated-profile-rest').finish()
  held.resolve({ mode: 'remote', authMode: 'oauth', baseUrl: 'https://fixture.invalid', oauthSessionIdentity: authority.capture(scope) })
  const outcome = await settled

  if (scenario === 'same-scope') {
    expect(outcome).toHaveProperty('error')
    expect(transports.ensureNativeAccessToken).not.toHaveBeenCalled()
    expect(transports.fetchJson).not.toHaveBeenCalled()
  } else {
    expect(outcome).toEqual({ value: { updated: true } })
    expect(transports.fetchJson).toHaveBeenCalledOnce()
  }

  expect(transports.fetchJsonViaOauthSession).not.toHaveBeenCalled()
})

// Run the real IPC admission and routing functions, replacing only machine and
// transport dependencies. Starting at a JSON helper would miss late capture.
it.each(['registry', 'generic', 'owner-lookup'] as const)('retains IPC admission ownership through held %s resolution', async door => {
  const { AsyncLocalStorage } = await import('node:async_hooks')
  const fs = await import('node:fs')
  const vm = await import('node:vm')
  const ts = await import('typescript')
  const source = fs.readFileSync(new URL('./main.ts', import.meta.url), 'utf8')
  const ast = ts.createSourceFile('main.ts', source, ts.ScriptTarget.Latest, true)

  const names = new Set(['assertOAuthDescriptorAuthority', 'backendJsonTransports', 'fetchJsonForBackend',
    'fetchJsonForProfile', 'requestJsonForProfile', 'dispatchRegistryApiRequest', 'handleHermesApiRequest', 'interceptSessionRequestForRemote'])

  const nodes = ast.statements.filter(node =>
    (ts.isFunctionDeclaration(node) && node.name && names.has(node.name.text)) ||
    (ts.isVariableStatement(node) && node.declarationList.declarations.some(declaration => declaration.name.getText(ast) === 'oauthRequestAuthority')) ||
    (ts.isExpressionStatement(node) && node.getText(ast).startsWith("ipcMain.handle('hermes:api',")))

  const authority = createOAuthSessionAuthority()
  const scope = 'ipc-fixture'
  authority.capture(scope)
  const held = deferred<void>()
  const entered = vi.fn()
  const writes = vi.fn(async () => ({ updated: true }))
  const cookieWrites = vi.fn(async () => ({ wrongCookieWrite: true }))
  let waiting = true
  let handler!: (event: unknown, request: unknown) => Promise<unknown>
  const descriptor = () => ({ mode: 'remote', authMode: 'oauth', baseUrl: 'https://fixture.invalid', oauthSessionIdentity: authority.capture(scope) })

  const resolve = async () => {
    if (waiting && door !== 'owner-lookup') { entered(); await held.promise }

    return descriptor()
  }

  const dependencies = {
    AsyncLocalStorage, URL, URLSearchParams, oauthAccountAuthority: authority, oauthAccountScope: () => scope,
    requestBackendJson, requestProfileBackendJson,
    ipcMain: { handle: (_name: string, callback: typeof handler) => { handler = callback } },
    DEFAULT_FETCH_TIMEOUT_MS: 1000,
    apiRequestRegistryConnectionId: (request: { connectionId?: string }) => request.connectionId,
    backendDialClaims: { run: (_key: unknown, callback: () => unknown) => callback() },
    backendScopeKey: () => 'fixture', ensureRegistryBackend: resolve, ensureBackend: resolve,
    pathForRegistryBackendRequest: (path: string) => path, resolveTimeoutMs: () => 1000,
    ensureNativeAccessToken: vi.fn(async () => 'current-account-token'), fetchJson: writes, fetchJsonViaOauthSession: cookieWrites,
    profileNameFromDeleteRequest: () => null, profileRenameFromRequest: () => null,
    prepareProfileRenameRequest: async () => null, prepareProfileDeleteRequest: async () => null,
    resolveProfileApiRequest: (_profile: unknown, path: string) => ({ requestPath: path }),
    profileRouteOptions: () => ({}), resolveRouteProfile: () => null,
    profileHasRemoteOverride: () => door === 'owner-lookup', globalRemoteActive: () => false,
    remoteOwnerProfileForSession: async () => {
      if (waiting) { entered(); await held.promise }

      return 'remote-worker'
    }
  }

  const code = ts.transpileModule(nodes.map(node => node.getText(ast)).join('\n'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS }
  }).outputText

  vm.runInNewContext(code, dependencies)

  const request = { method: 'PATCH', path: '/api/sessions/fixture', body: { title: 'original intent' },
    ...(door === 'registry' ? { connectionId: 'fixture' } : door === 'generic' ? { profile: 'worker' } : {}) }

  const pending = handler(null, request).then(value => ({ value }), error => ({ error }))
  await vi.waitFor(() => expect(entered).toHaveBeenCalledOnce())
  authority.beginChange(scope).finish()
  waiting = false
  // A concurrent fresh admission must not replace the held request's context.
  await expect(handler(null, { ...request, body: { title: 'fresh intent' } })).resolves.toEqual({ updated: true })
  held.resolve()
  expect(await pending).toHaveProperty('error')
  expect(cookieWrites).not.toHaveBeenCalled()
  expect(writes).toHaveBeenCalledOnce()
  expect(writes).toHaveBeenCalledWith(expect.any(String), null, expect.objectContaining({ method: 'PATCH', body: { title: 'fresh intent' } }))
})
