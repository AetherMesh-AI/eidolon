import { createRequire } from 'node:module'

import { describe, expect, it, vi } from 'vitest'

const require = createRequire(import.meta.url)
const { installPopulatedWorkspace, capabilityNames } = require('./workspace-populated-bootstrap.cjs')

function boundary() {
  let handler: (event: unknown, request: { path: string; method: string }) => any

  const ipc = {
    handle: (_channel: string, listener: typeof handler) => {
      handler = listener
    }
  }

  const evidence = installPopulatedWorkspace(ipc)
  const backend = vi.fn(() => ({ untouched: true }))
  ipc.handle('hermes:api', backend)

  return { evidence, backend, read: (path: string, method = 'GET') => handler({}, { path, method }) }
}

describe('native capability content fixture', () => {
  it('is opt-in, serves long synthetic identities and keeps all servers disabled', () => {
    const { evidence, backend, read } = boundary()
    expect(read('/api/skills')).toEqual({ untouched: true })
    evidence.capabilities = { mode: 'populated' }
    expect(read('/api/skills')[0].name).toBe(capabilityNames.skill)
    expect(read('/api/tools/toolsets')[0].label).toBe(capabilityNames.tool)
    const servers = read('/api/config').mcp_servers
    expect(servers[capabilityNames.server].enabled).toBe(false)
    expect(Object.values(servers).every((server: any) => server.enabled === false)).toBe(true)
    expect(read('/api/skills/hub/official').skills[0].installed).toBe(false)
    expect(read('/api/skills/content?name=fixture').content).toContain('Synthetic instruction')
    expect(read('/api/analytics/usage')).toEqual({ tools: [] })
    expect(backend).toHaveBeenCalledTimes(1)
  })

  it('blocks install, toggle, save, probe and provider changes before the backend sees them', () => {
    const { evidence, backend, read } = boundary()
    evidence.capabilities = { mode: 'populated' }

    const writes = [
      ['/api/skills/hub/install', 'POST'],
      ['/api/skills/toggle', 'PUT'],
      ['/api/tools/toolsets/synthetic/provider', 'PUT'],
      ['/api/config', 'PUT'],
      ['/api/mcp/servers', 'PUT'],
      ['/api/mcp/servers/synthetic/test', 'POST'],
      ['/api/mcp/catalog/install', 'POST']
    ]

    for (const [path, method] of writes) {
      expect(() => read(path, method)).toThrow(/read-only/)
    }

    expect(evidence.mutations).toHaveLength(writes.length)
    expect(backend).not.toHaveBeenCalled()
  })

  it('provides separate empty and retryable error states without backend fallthrough', () => {
    const { evidence, backend, read } = boundary()
    evidence.capabilities = { mode: 'empty' }
    expect(read('/api/skills')).toEqual([])
    expect(read('/api/tools/toolsets')).toEqual([])
    expect(read('/api/config')).toEqual({ mcp_servers: {} })
    evidence.capabilities.mode = 'error'
    expect(() => read('/api/skills')).toThrow(/Synthetic capability read unavailable/)
    expect(() => read('/api/config')).toThrow(/Synthetic capability read unavailable/)
    evidence.capabilities.mode = 'populated'
    expect(read('/api/skills')[0].name).toBe(capabilityNames.skill)
    expect(backend).not.toHaveBeenCalled()
  })
})
