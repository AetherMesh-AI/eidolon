import { createRequire } from 'node:module'

import { describe, expect, it, vi } from 'vitest'

const require = createRequire(import.meta.url)
const { installHttpReadFaults } = require('./http-read-fault-bootstrap.cjs')
const { installSessionArtifactRecovery, profile, sessionId, sessionPage, transcript } = require('./session-artifact-recovery-bootstrap.cjs')

function boundary() {
  const handlers = new Map<string, (...args: any[]) => any>()
  const ipc = { handle: (channel: string, listener: (...args: any[]) => any) => handlers.set(channel, listener) }
  const evidence = installSessionArtifactRecovery(ipc)
  const backend = vi.fn(() => ({ untouched: true }))
  for (const channel of ['hermes:api', 'hermes:connection', 'hermes:connection:for', 'hermes:connection-config:get',
    'hermes:fs:desktopPluginsRoot', 'hermes:fs:agentPluginsRoot', 'hermes:openExternal', 'hermes:saveGatewayFile', 'hermes:fs:readDir', 'hermes:fs:reveal', 'hermes:fs:writeText']) {
    ipc.handle(channel, backend)
  }
  return { evidence, backend, invoke: (channel: string, ...args: any[]) => handlers.get(channel)!({}, ...args) }
}

describe('synthetic session attachment boundary', () => {
  it('serves only fake transcript metadata and leaves other reads alone', () => {
    const { invoke, backend } = boundary()
    expect(sessionPage().sessions[0]).toMatchObject({ id: sessionId, profile: 'default' })
    expect(transcript().messages[0].content).toBe('MEDIA:/synthetic-only/synthetic-report.pdf')
    expect(invoke('hermes:api', { path: '/api/profiles' }).profiles).toHaveLength(2)
    expect(invoke('hermes:api', { path: '/api/status' })).toEqual({ untouched: true })
    expect(invoke('hermes:connection', 'default')).toEqual({ untouched: true })
    expect(invoke('hermes:connection-config:get', profile)).toEqual({ mode: 'local' })
    expect(backend).toHaveBeenCalledTimes(2)
  })

  it('isolates ambient plugin discovery and owner migration without filesystem or write dispatch', () => {
    const { invoke, backend, evidence } = boundary()
    for (const channel of ['hermes:fs:desktopPluginsRoot', 'hermes:fs:agentPluginsRoot']) {
      expect(invoke(channel)).toBe('')
    }
    expect(invoke('hermes:api', { path: '/api/sessions/owner-backfill', method: 'POST', body: 'never-record-this-value' }))
      .toEqual({ ok: true, profile: 'default', stamped: 0 })
    expect(evidence.ambientStubs).toHaveLength(3)
    expect(evidence.ambientStubs.every((entry: { backendDispatch: boolean }) => entry.backendDispatch === false)).toBe(true)
    expect(evidence.blockedActions).toEqual([])
    expect(JSON.stringify(evidence)).not.toContain('never-record-this-value')
    expect(backend).not.toHaveBeenCalled()
    // No broad mutation or filesystem exception accompanies those no-ops.
    expect(() => invoke('hermes:fs:readDir', '/synthetic-only')).toThrow(/does not open or change files/)
    expect(() => invoke('hermes:api', { path: '/api/sessions/owner-backfill', method: 'DELETE' })).toThrow(/read-only/)
    expect(() => invoke('hermes:api', { path: '/api/sessions/owner-backfill/other', method: 'POST' })).toThrow(/read-only/)
    expect(backend).not.toHaveBeenCalled()
  })

  it('holds the synthetic profile before any backend starts without retaining descriptor secrets', () => {
    const { invoke, backend, evidence } = boundary()
    expect(invoke('hermes:connection', profile)).toBeInstanceOf(Promise)
    expect(invoke('hermes:connection:for', { connectionId: 'local', profile })).toBeInstanceOf(Promise)
    expect(evidence.pendingProfiles).toEqual([
      { channel: 'hermes:connection', profile }, { channel: 'hermes:connection:for', profile }
    ])
    expect(backend).not.toHaveBeenCalled()
  })

  it('composes the shared deferred read seam without bypassing attachment guards', async () => {
    const handlers = new Map<string, (...args: any[]) => any>()
    const ipc = { handle: (channel: string, listener: (...args: any[]) => any) => handlers.set(channel, listener) }
    const attachments = installSessionArtifactRecovery(ipc)
    const reads = installHttpReadFaults(ipc)
    const backend = vi.fn(() => ({ untouched: true }))
    ipc.handle('hermes:api', backend)
    ipc.handle('hermes:openExternal', backend)
    reads.set('/api/profiles/sessions', { kind: 'pending' })
    const pending = handlers.get('hermes:api')!({}, { path: '/api/profiles/sessions?limit=30', profile: 'default' })
    expect(reads.pending('/api/profiles/sessions')).toBe(1)
    reads.release('/api/profiles/sessions', { kind: 'success', data: sessionPage() })
    await expect(pending).resolves.toEqual(sessionPage())
    const barrierPath = '/api/synthetic-attachment-transport-barrier'
    reads.set(barrierPath, { kind: 'success', data: { syntheticTransportBarrier: true } })
    expect(handlers.get('hermes:api')!({}, { path: barrierPath, method: 'GET' })).toEqual({ syntheticTransportBarrier: true })
    expect(reads.evidence.writes).toEqual([])
    expect(() => handlers.get('hermes:openExternal')!({}, 'never-record-this-value')).toThrow(/does not open or change files/)
    expect(attachments.blockedActions).toEqual([{ channel: 'hermes:openExternal' }])
    expect(backend).not.toHaveBeenCalled()
  })

  it('blocks file inspection, open/download and session writes before backend dispatch', () => {
    const { invoke, backend, evidence } = boundary()
    for (const channel of ['hermes:openExternal', 'hermes:saveGatewayFile', 'hermes:fs:readDir', 'hermes:fs:reveal', 'hermes:fs:writeText']) {
      expect(() => invoke(channel, 'never-record-this-value')).toThrow(/does not open or change files/)
    }
    for (const request of [
      { path: '/api/fs/read?path=never-record-this-value' }, { path: '/api/media' },
      { path: '/api/sessions/synthetic', method: 'DELETE', body: 'never-record-this-value' },
      { path: '/api/profiles', method: 'POST', body: 'never-record-this-value' }
    ]) expect(() => invoke('hermes:api', request)).toThrow(/read-only/)
    expect(evidence.blockedActions).toHaveLength(9)
    expect(JSON.stringify(evidence)).not.toContain('never-record-this-value')
    expect(backend).not.toHaveBeenCalled()
  })
})
