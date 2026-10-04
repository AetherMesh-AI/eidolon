import http, { get as httpGet, request as httpRequest } from 'node:http'
import https, { get as httpsGet, request as httpsRequest } from 'node:https'

import { afterEach, describe, expect, it, vi } from 'vitest'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('unit-test transport isolation', () => {
  it('rejects native fetch and both HTTP import styles without opening a connection', async () => {
    await expect(fetch('https://network-guard.invalid')).rejects.toThrow('Live network is disabled')

    for (const request of [
      http.request,
      http.get,
      https.request,
      https.get,
      httpRequest,
      httpGet,
      httpsRequest,
      httpsGet
    ]) {
      expect(() => request('https://network-guard.invalid')).toThrow('Live network is disabled')
    }
  })

  it('restores the rejecting default after a test replaces fetch with a local fixture', async () => {
    const fixture = vi.fn(async () => new Response('local fixture'))

    vi.stubGlobal('fetch', fixture)
    expect(await (await fetch('https://network-guard.invalid')).text()).toBe('local fixture')
    expect(fixture).toHaveBeenCalledTimes(1)

    vi.unstubAllGlobals()
    await expect(fetch('https://network-guard.invalid')).rejects.toThrow('Live network is disabled')
  })
})
