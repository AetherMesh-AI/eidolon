import { createRequire } from 'node:module'

import { expect, it, vi } from 'vitest'

const { installHttpReadFaults } = createRequire(import.meta.url)('./http-read-fault-bootstrap.cjs')

it('holds real API reads, releases only the current generation, and blocks owned writes without retaining bodies', async () => {
  const handlers = new Map<string, (...args: any[]) => any>()
  const ipc = { handle: (name: string, handler: (...args: any[]) => any) => handlers.set(name, handler) }
  const control = installHttpReadFaults(ipc)
  const backend = vi.fn(() => 'unowned')
  ipc.handle('hermes:api', backend)
  const request = handlers.get('hermes:api')!
  control.set('/api/env', { kind: 'pending' })
  const first = request({}, { path: '/api/env?fixture=1', profile: 'synthetic' })
  expect(control.pending('/api/env')).toBe(1)
  control.set('/api/env', { kind: 'success', data: {} })
  expect(request({}, { path: '/api/env' })).toEqual({})
  expect(control.release('/api/env', { kind: 'error' })).toBe(1)
  await expect(first).rejects.toThrow('Synthetic read unavailable.')
  expect(() => request({}, { path: '/api/env/reveal', method: 'POST', body: { secret: 'never retain this' } })).toThrow()
  expect(JSON.stringify(control.evidence)).not.toContain('never retain this')
  expect(control.evidence.writes).toEqual([{ path: '/api/env/reveal', method: 'POST', profile: null, connectionId: null }])
  expect(request({}, { path: '/unowned' })).toBe('unowned')
  expect(backend).toHaveBeenCalledTimes(1)
})

it('keeps only allowlisted archive modes and never stores credential-bearing query strings', () => {
  const handlers = new Map<string, (...args: any[]) => any>()
  const ipc = { handle: (name: string, handler: (...args: any[]) => any) => handlers.set(name, handler) }
  const control = installHttpReadFaults(ipc)
  ipc.handle('hermes:api', vi.fn())
  control.set('/api/profiles/sessions', { kind: 'success', data: { sessions: [] } })
  const request = handlers.get('hermes:api')!
  request({}, { path: '/api/profiles/sessions?archived=only&token=synthetic-private-query' })
  expect(control.evidence.reads[0]).toEqual({ path: '/api/profiles/sessions', method: 'GET', profile: null, connectionId: null, archived: 'only' })
  request({}, { path: '/api/profiles/sessions?archived=synthetic-private-query' })
  expect(control.evidence.reads[1].archived).toBeNull()
  expect(JSON.stringify(control.evidence)).not.toContain('synthetic-private-query')
  expect(JSON.stringify(control.evidence)).not.toContain('token')
})
