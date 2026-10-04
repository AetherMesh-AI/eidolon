import type { ResolvedConnectionDescriptor } from './connection-registry'

export interface BackendJsonOptions {
  method?: string
  body?: unknown
  upload?: unknown
  timeoutMs?: number
}

export interface BackendJsonTransportOptions extends BackendJsonOptions {
  bearer?: string
  headers?: Record<string, unknown>
}

export interface BackendJsonTransports {
  ensureNativeAccessToken: (baseUrl: string) => Promise<null | string>
  fetchJson: (url: string, token: unknown, options: BackendJsonTransportOptions) => Promise<unknown>
  fetchJsonViaOauthSession: (url: string, options: BackendJsonTransportOptions) => Promise<unknown>
}

const LEGACY_UPDATE_PATHS = new Map([
  ['/api/eidolon/update/check', '/api/hermes/update/check'],
  ['/api/eidolon/update', '/api/hermes/update'],
  ['/api/eidolon/update/receipt', '/api/hermes/update/receipt']
])

/** A selected older remote may still expose the original update routes.
 * Only a confirmed missing route permits one replay; authentication failures
 * and ambiguous transport failures must never resubmit an update elsewhere.
 */
export async function requestBackendJson(
  descriptor: ResolvedConnectionDescriptor,
  path: string,
  options: BackendJsonOptions,
  transports: BackendJsonTransports
): Promise<unknown> {
  const { baseUrl, headers, token } = descriptor
  const requestOptions: BackendJsonTransportOptions = { ...options, headers }
  let send: (url: string) => Promise<unknown>

  if (descriptor.authMode === 'oauth') {
    if (options.upload) {
      throw new Error('File uploads are not supported against OAuth-gated remote backends yet.')
    }

    // Resolve auth once so the fallback cannot change credentials or partition.
    const nativeAt = await transports.ensureNativeAccessToken(baseUrl).catch(() => null)

    if (nativeAt) {
      requestOptions.bearer = nativeAt
      send = url => transports.fetchJson(url, null, requestOptions)
    } else {
      send = url => transports.fetchJsonViaOauthSession(url, requestOptions)
    }
  } else {
    send = url => transports.fetchJson(url, token, requestOptions)
  }

  try {
    return await send(`${baseUrl}${path}`)
  } catch (error) {
    const queryStart = path.indexOf('?')
    const pathname = queryStart < 0 ? path : path.slice(0, queryStart)
    const legacyPath = LEGACY_UPDATE_PATHS.get(pathname)

    if (
      descriptor.mode !== 'remote' ||
      !legacyPath ||
      path.includes('#') ||
      options.upload ||
      !(error instanceof Error) ||
      !('statusCode' in error) ||
      error.statusCode !== 404
    ) {
      throw error
    }

    // Keep the resolved origin/base path and exact query/body/auth. Do not
    // re-resolve a descriptor, normalize the query, or retry the fallback.
    return send(`${baseUrl}${legacyPath}${queryStart < 0 ? '' : path.slice(queryStart)}`)
  }
}
