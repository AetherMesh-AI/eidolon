import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'

import { expect, it, vi } from 'vitest'

const { installHttpReadFaults } = createRequire(import.meta.url)('./http-read-fault-bootstrap.cjs')

it.each(['ru', 'ar'])('preserves %s profile locale until the Model case explicitly owns config', locale => {
  const handlers = new Map<string, (...args: any[]) => any>()
  const ipc = { handle: (name: string, handler: (...args: any[]) => any) => handlers.set(name, handler) }
  const control = installHttpReadFaults(ipc)
  runInNewContext(readFileSync(new URL('./settings-read-state-bootstrap.cjs', import.meta.url), 'utf8'), {
    module: { exports: {} }, process: { versions: { electron: 'synthetic' } },
    globalThis: { __eidolonReadFaults: control }, require: () => ({})
  })
  const backend = vi.fn(() => ({ display: { language: locale } }))
  ipc.handle('hermes:api', backend)
  const request = handlers.get('hermes:api')!
  expect(request({}, { path: '/api/config' })).toEqual({ display: { language: locale } })
  expect(backend).toHaveBeenCalledTimes(1)
  control.set('/api/config', { kind: 'success', data: { display: { language: 'en' } } })
  expect(request({}, { path: '/api/config' })).toEqual({ display: { language: 'en' } })
  expect(() => request({}, { path: '/api/config', method: 'PUT' })).toThrow('Synthetic reads do not authorize writes.')
  expect(() => request({}, { path: '/api/model/set', method: 'POST' })).toThrow('Synthetic reads do not authorize writes.')
  expect(control.evidence.writes.map((entry: { path: string }) => entry.path)).toEqual(['/api/config', '/api/model/set'])
  expect(backend).toHaveBeenCalledTimes(1)
})
