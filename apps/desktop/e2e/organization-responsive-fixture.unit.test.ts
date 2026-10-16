import { createRequire } from 'node:module'

import { describe, expect, it, vi } from 'vitest'

const require = createRequire(import.meta.url)
const { installPopulatedWorkspace } = require('./workspace-populated-bootstrap.cjs') as {
  installPopulatedWorkspace: (ipc: any) => { reads: string[]; mutations: { path: string; method: string }[] }
}
const { installIsolatedUpdateCheck } = require('./isolated-update-check.cjs')

describe('native responsive synthetic boundary', () => {
  it('passes unrelated APIs through, serves bounded synthetic reads, and blocks writes without swallowing their evidence', () => {
    const handlers = new Map<string, (...args: any[]) => any>()
    const ipc = { handle: vi.fn((channel: string, listener: (...args: any[]) => any) => handlers.set(channel, listener)) }
    const originalHandle = ipc.handle
    const updates = installIsolatedUpdateCheck(ipc)
    const evidence = installPopulatedWorkspace(ipc)
    const backend = vi.fn(() => ({ real: true }))
    ipc.handle('unrelated', backend)
    ipc.handle('hermes:api', backend)
    ipc.handle('hermes:updates:check', backend)
    expect(ipc.handle).toBe(originalHandle)
    expect(updates.registered).toBe(true)
    expect(handlers.get('hermes:updates:check')!({ sender: { id: 1 } }).updateAvailable).toBe(false)
    const request = (path: string, method = 'GET') => handlers.get('hermes:api')!({}, { path, method })
    expect(request('/api/config')).toEqual({ real: true })
    expect(request('/api/profiles')).toEqual({ real: true })
    const platforms = request('/api/messaging/platforms?profile=default').platforms
    expect(platforms).toHaveLength(1)
    expect(platforms[0].env_vars.every((field: any) => !field.is_password && !field.is_set)).toBe(true)
    expect(platforms[0].error_message).toMatch(/Synthetic/)
    expect(request('/api/pairing')).toEqual({ approved: [], pending: [] })
    expect(request('/api/profiles/default/soul').content).toContain('Synthetic profile')
    for (const [path, method] of [['/api/messaging/platforms/synthetic', 'PUT'], ['/api/pairing/approve', 'POST'], ['/api/profiles/default/soul', 'PUT']]) {
      expect(() => request(path, method)).toThrow(/read-only/)
    }
    expect(evidence.mutations).toHaveLength(3)
    expect(backend).toHaveBeenCalledTimes(2)
  })
})
