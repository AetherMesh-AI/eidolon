import { createHmac, randomBytes } from 'node:crypto'

import type { FirstRunSetupDecision } from './first-run-setup-gate'

export interface PrimaryBackendStartupOptions<Backend, RuntimeBackend, Remote, Connection> {
  connectRemote: (remote: Remote) => Promise<Connection>
  ensureLocalRuntime: (backend: Backend) => Promise<RuntimeBackend>
  prepareLocalBackend: () => Backend | Promise<Backend>
  resolveRemote: () => Promise<Remote | null>
  waitForDecision: (backend: Backend) => Promise<FirstRunSetupDecision>
  waitForLocalStart: () => Promise<unknown>
}

export type PrimaryBackendStartupResult<RuntimeBackend, Connection> =
  { kind: 'local'; backend: RuntimeBackend } | { kind: 'remote'; connection: Connection }

// Process-local keyed identity: no token/header values or reusable unkeyed
// credential digest crosses the bridge. Capture before asynchronous dialing and
// retain it on cached descriptors; never relabel an old connection at lookup.
const remoteAuthenticationKey = randomBytes(32)

export function remoteAuthenticationIdentity(baseUrl: string, authMode: string, token: unknown, headers?: Record<string, string>) {
  // HTTP field names are case-insensitive, but values remain exact. Reject
  // duplicate spellings rather than guessing how a particular dialer merges them.
  const canonicalHeaders = new Map<string, string>()

  for (const [name, value] of Object.entries(headers ?? {})) {
    const key = name.toLowerCase()

    if (canonicalHeaders.has(key)) {
      throw new Error('Ambiguous duplicate remote authentication header names.')
    }

    canonicalHeaders.set(key, value)
  }

  return createHmac('sha256', remoteAuthenticationKey)
    .update(JSON.stringify([baseUrl, authMode, token, [...canonicalHeaders].sort(([a], [b]) => a.localeCompare(b))]))
    .digest('hex')
}

export function assertRemoteAuthenticationIdentity(opened: string | undefined, configured: string) {
  if (!opened || opened !== configured) {
    throw new Error('Eidolon remote authentication configuration changed; reconnect the source before retrying.')
  }
}

interface ResolvedPrimaryRemote {
  authenticationIdentity?: string
  authMode?: 'oauth' | 'token'
  baseUrl: string
  connectionId?: string
  headers?: Record<string, string>
  remoteHermesVersion?: string
  remoteHost?: string
  remoteKind?: 'cloud' | 'ssh' | 'url'
  source?: string
  ssh?: {
    effectiveConfigFingerprint?: string
    host?: string
    keyPath?: string
    port?: number
    remoteHermesPath?: string
    remoteProfile?: string
    user?: string
  }
  token: unknown
  wsUrl: string
}

/**
 * Build the renderer-facing primary remote descriptor without dropping route
 * identity. Tests cross this same seam, so adding a field to the resolved
 * remote cannot silently disappear during primary startup.
 */
export function createPrimaryRemoteConnection<State extends object>(
  remote: ResolvedPrimaryRemote,
  logs: string[],
  windowState: State
) {
  return {
    authenticationIdentity: remote.authenticationIdentity,
    baseUrl: remote.baseUrl,
    headers: remote.headers === undefined ? undefined : Object.freeze({ ...remote.headers }),
    mode: 'remote' as const,
    source: remote.source,
    authMode: remote.authMode || 'token',
    remoteHost: remote.remoteHost,
    remoteKind: remote.remoteKind,
    remoteHermesVersion: remote.remoteHermesVersion,
    ...(remote.connectionId ? { connectionId: remote.connectionId } : {}),
    ...(remote.ssh ? { ssh: remote.ssh } : {}),
    token: remote.token,
    wsUrl: remote.wsUrl,
    logs,
    ...windowState
  }
}

export class FirstRunSetupResetError extends Error {
  readonly firstRunSetupReset = true

  constructor() {
    super('First-run setup was reset before a choice completed.')
    this.name = 'FirstRunSetupResetError'
  }
}

// Owns the production startHermes path up to the local process spawn. Keeping
// the full ordering here makes the first-run remote boundary executable in a
// test: an already-saved remote wins immediately; otherwise update exclusion
// and local backend resolution happen before the setup gate, and a remote Apply
// re-resolves persisted config without ever entering ensureRuntime/bootstrap.
export async function runPrimaryBackendStartup<Backend, RuntimeBackend, Remote, Connection>({
  connectRemote,
  ensureLocalRuntime,
  prepareLocalBackend,
  resolveRemote,
  waitForDecision,
  waitForLocalStart
}: PrimaryBackendStartupOptions<Backend, RuntimeBackend, Remote, Connection>): Promise<
  PrimaryBackendStartupResult<RuntimeBackend, Connection>
> {
  const savedRemote = await resolveRemote()

  if (savedRemote) {
    return { kind: 'remote', connection: await connectRemote(savedRemote) }
  }

  await waitForLocalStart()

  const backend = await prepareLocalBackend()
  const decision = await waitForDecision(backend)

  if (decision === 'remote-applied') {
    const appliedRemote = await resolveRemote()

    if (!appliedRemote) {
      throw new Error('First-run remote setup completed without a saved remote backend.')
    }

    return { kind: 'remote', connection: await connectRemote(appliedRemote) }
  }

  if (decision === 'reset') {
    throw new FirstRunSetupResetError()
  }

  return { kind: 'local', backend: await ensureLocalRuntime(backend) }
}


/** Capture before boot waits: remembering a workspace cannot retarget this
 * generation. Null deliberately preserves the legacy launch without --profile. */
export function capturePrimaryBackendLaunch(preference: string | null, home: string) {
  return {
    profile: preference || 'default',
    home,
    backendArgs: [...(preference ? ['--profile', preference] : []), 'serve', '--host', '127.0.0.1', '--port', '0']
  }
}
