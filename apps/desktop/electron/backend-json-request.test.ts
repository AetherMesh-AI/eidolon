import http from 'node:http'
import type { AddressInfo } from 'node:net'

import { expect, test, vi } from 'vitest'

import { withRetry } from './api-transport'
import {
  type BackendJsonOptions,
  type BackendJsonTransportOptions,
  type BackendJsonTransports,
  requestBackendJson
} from './backend-json-request'
import type { ResolvedConnectionDescriptor } from './connection-registry'

interface ReceivedRequest {
  path: string
  method: string
  headers: http.IncomingHttpHeaders
  body: string
}

async function fixture() {
  const received: ReceivedRequest[] = []
  let canonicalStatus = 404
  let legacyStatus = 200
  let disconnect = false
  let malformed = false

  const server = http.createServer(async (request, response) => {
    const chunks: Buffer[] = []

    for await (const chunk of request) {
      chunks.push(Buffer.from(chunk))
    }

    received.push({
      path: request.url,
      method: request.method,
      headers: request.headers,
      body: Buffer.concat(chunks).toString()
    })

    if (disconnect) {
      request.socket.destroy()

      return
    }

    response.statusCode = request.url.startsWith('/gateway/api/hermes/') ? legacyStatus : canonicalStatus
    response.setHeader('Content-Type', 'application/json')
    // These words in an HTTP body must not be mistaken for a socket failure.
    response.end(malformed ? 'not JSON' : JSON.stringify({ message: 'read ECONNRESET: socket hang up', ok: true }))
  })

  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  const baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}/gateway`

  function transport(url: string, headers: Record<string, unknown>, options: BackendJsonTransportOptions) {
    return withRetry(
      state =>
        new Promise((resolve, reject) => {
          const body = options.body === undefined ? undefined : JSON.stringify(options.body)

          const request = http.request(
            url,
            {
              method: options.method || 'GET',
              headers: {
                ...headers,
                ...(body ? { 'Content-Length': Buffer.byteLength(body) } : {})
              } as http.OutgoingHttpHeaders
            },
            response => {
              const chunks: Buffer[] = []
              response.on('data', chunk => chunks.push(chunk))
              response.on('error', reject)
              response.on('end', () => {
                const text = Buffer.concat(chunks).toString()

                if (response.statusCode >= 400) {
                  reject(
                    Object.assign(new Error(`${response.statusCode}: ${text}`), { statusCode: response.statusCode })
                  )

                  return
                }

                try {
                  resolve(JSON.parse(text))
                } catch (error) {
                  reject(error)
                }
              })
            }
          )

          request.on('error', reject)
          state.bodySent = true
          request.end(body)
        }),
      { method: options.method, delayFn: async () => {} }
    )
  }

  const transports: BackendJsonTransports = {
    ensureNativeAccessToken: vi.fn(async () => 'native-bearer'),
    fetchJson: vi.fn((url, token, options) =>
      transport(
        url,
        {
          ...options.headers,
          ...(token ? { 'X-Hermes-Session-Token': token } : {}),
          ...(options.bearer ? { Authorization: `Bearer ${options.bearer}` } : {})
        },
        options
      )
    ),
    fetchJsonViaOauthSession: vi.fn((url, options) =>
      transport(url, { ...options.headers, Cookie: 'session=partition-cookie' }, options)
    )
  }

  return {
    baseUrl,
    received,
    transports,
    respond(status: number, legacy = 200, reset = false, invalidJson = false) {
      canonicalStatus = status
      legacyStatus = legacy
      disconnect = reset
      malformed = invalidJson
      received.length = 0
      vi.clearAllMocks()
    },
    close: () => new Promise<void>((resolve, reject) => server.close(error => (error ? reject(error) : resolve())))
  }
}

test('older remote update requests preserve scope, body and one selected auth transport on a single 404 fallback', async () => {
  const f = await fixture()

  try {
    for (const auth of ['token', 'bearer', 'cookie']) {
      for (const endpoint of ['update/check', 'update', 'update/receipt']) {
        for (const status of [200, 404]) {
          f.respond(status)
          vi.mocked(f.transports.ensureNativeAccessToken).mockResolvedValue(auth === 'cookie' ? null : 'native-bearer')

          const descriptor: ResolvedConnectionDescriptor = {
            baseUrl: f.baseUrl,
            mode: 'remote',
            connectionId: 'selected-remote',
            authMode: auth === 'token' ? 'token' : 'oauth',
            token: 'selected-session-token',
            headers: { 'X-Selected-Gateway': 'keep-this-header' }
          }

          const query = '?force=true&profile=research%20one&filter=a%2Bb&filter=c'
          const path = `/api/eidolon/${endpoint}${query}`

          const options: BackendJsonOptions = {
            method: endpoint === 'update' ? 'POST' : 'GET',
            ...(endpoint === 'update'
              ? { body: { profile: 'research one', branch: 'main', nested: { keep: [1, false] } } }
              : {}),
            timeoutMs: 17_000
          }

          await expect(requestBackendJson(descriptor, path, options, f.transports)).resolves.toMatchObject({ ok: true })
          expect(f.received.map(request => request.path)).toEqual([
            `/gateway/api/eidolon/${endpoint}${query}`,
            ...(status === 404 ? [`/gateway/api/hermes/${endpoint}${query}`] : [])
          ])

          for (const request of f.received) {
            expect(request.method).toBe(options.method)
            expect(request.body).toBe(options.body === undefined ? '' : JSON.stringify(options.body))
            expect(request.headers.host).toBe(new URL(f.baseUrl).host)
            expect(request.headers['x-selected-gateway']).toBe('keep-this-header')
            expect(request.headers['x-hermes-session-token']).toBe(auth === 'token' ? descriptor.token : undefined)
            expect(request.headers.authorization).toBe(auth === 'bearer' ? 'Bearer native-bearer' : undefined)
            expect(request.headers.cookie).toBe(auth === 'cookie' ? 'session=partition-cookie' : undefined)
          }

          expect(f.transports.ensureNativeAccessToken).toHaveBeenCalledTimes(auth === 'token' ? 0 : 1)

          const calls =
            auth === 'cookie'
              ? vi.mocked(f.transports.fetchJsonViaOauthSession).mock.calls.map(call => call[1])
              : vi.mocked(f.transports.fetchJson).mock.calls.map(call => call[2])

          expect(calls).toHaveLength(status === 404 ? 2 : 1)

          for (const call of calls) {
            expect(call).toBe(calls[0])
            expect(call.body).toBe(options.body)
            expect(call.headers).toBe(descriptor.headers)
            expect(call.timeoutMs).toBe(options.timeoutMs)
          }
        }
      }
    }
  } finally {
    await f.close()
  }
})

test('fallback never broadens routes, retargets local backends, or retries auth/server/ambiguous failures', async () => {
  const f = await fixture()
  const descriptor: ResolvedConnectionDescriptor = { baseUrl: f.baseUrl, mode: 'remote', token: 'selected-token' }
  const options = { method: 'POST', body: { profile: 'keep-me' } }

  try {
    for (const status of [400, 401, 403, 405, 409, 429, 500, 502, 503, 504]) {
      f.respond(status)
      await expect(requestBackendJson(descriptor, '/api/eidolon/update', options, f.transports)).rejects.toMatchObject({
        statusCode: status
      })
      expect(f.received.map(request => request.path)).toEqual(['/gateway/api/eidolon/update'])
    }

    for (const path of [
      '/api/eidolon/update/',
      '/api/eidolon/update/check/extra',
      '/api/eidolon/update/other',
      '/api/eidolon/status',
      '/api/hermes/update',
      '/api/eidolon/update?force=true#fragment',
      '//other.example/api/eidolon/update',
      'https://other.example/api/eidolon/update'
    ]) {
      f.respond(404, 404)
      await expect(requestBackendJson(descriptor, path, options, f.transports)).rejects.toMatchObject({
        statusCode: 404
      })
      expect(f.received).toHaveLength(1)
    }

    f.respond(404)
    await expect(
      requestBackendJson({ ...descriptor, mode: 'local' }, '/api/eidolon/update', options, f.transports)
    ).rejects.toMatchObject({ statusCode: 404 })
    expect(f.received).toHaveLength(1)

    f.respond(404, 404)
    await expect(requestBackendJson(descriptor, '/api/eidolon/update', options, f.transports)).rejects.toMatchObject({
      statusCode: 404
    })
    expect(f.received.map(request => request.path)).toEqual([
      '/gateway/api/eidolon/update',
      '/gateway/api/hermes/update'
    ])

    for (const [disconnect, malformed] of [
      [true, false],
      [false, true]
    ]) {
      f.respond(200, 200, disconnect, malformed)
      await expect(requestBackendJson(descriptor, '/api/eidolon/update', options, f.transports)).rejects.toThrow()
      expect(f.received.map(request => request.path)).toEqual(['/gateway/api/eidolon/update'])
    }

    f.respond(404)
    const unconfirmed = new Error('404: missing route')
    vi.mocked(f.transports.fetchJson).mockRejectedValueOnce(unconfirmed)
    await expect(requestBackendJson(descriptor, '/api/eidolon/update', options, f.transports)).rejects.toBe(unconfirmed)
    expect(f.received).toHaveLength(0)
    expect(f.transports.fetchJson).toHaveBeenCalledTimes(1)
  } finally {
    await f.close()
  }
})
