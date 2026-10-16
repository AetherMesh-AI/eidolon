/** Opt-in synthetic read boundary for native responsive journeys. No credentials or provider calls. */
function installPopulatedWorkspace(ipcMain) {
  const originalHandle = ipcMain.handle
  const evidence = { reads: [], mutations: [], capabilities: null }
  ipcMain.handle = function (channel, listener) {
    if (channel !== 'hermes:api') return originalHandle.call(this, channel, listener)
    ipcMain.handle = originalHandle
    return originalHandle.call(this, channel, (event, request) => {
      const path = request.path.split('?')[0]
      const capabilitiesOwned = evidence.capabilities && (
        path === '/api/config' || path === '/api/profiles' || path === '/api/logs' || path === '/api/analytics/usage' ||
        path === '/api/skills' || path.startsWith('/api/skills/') ||
        path.startsWith('/api/tools/') || path.startsWith('/api/mcp/')
      )
      const owned = capabilitiesOwned || path.startsWith('/api/messaging/') || path.startsWith('/api/pairing') || /^\/api\/profiles\/[^/]+\/soul$/.test(path)
      if (!owned) return listener(event, request)
      if (request.method && request.method !== 'GET') {
        evidence.mutations.push({ path, method: request.method })
        throw new Error('Synthetic workspace is read-only: unexpected mutation')
      }
      evidence.reads.push(path)
      if (capabilitiesOwned) return capabilityRead(request.path, evidence.capabilities.mode)
      if (path === '/api/messaging/platforms') return { platforms: [{
        id: 'synthetic', name: 'Synthetic messaging', description: 'Local visual fixture; no service is contacted.',
        configured: false, enabled: false, gateway_running: false, state: 'startup_failed', docs_url: '',
        error_message: 'Synthetic connection error. Review the draft before saving.',
        env_vars: ['Workspace label', 'Destination label'].map((prompt, index) => ({
          key: `SYNTHETIC_FIELD_${index}`, prompt, description: 'Synthetic non-secret text for layout review.',
          is_password: false, is_set: false, redacted_value: null, required: true, url: null
        }))
      }] }
      if (path === '/api/pairing') return { approved: [], pending: [] }
      if (path.endsWith('/soul')) return { content: '# Synthetic profile\n\nKeep this unsaved draft through resize and Back.', exists: true }
      throw new Error(`Unimplemented synthetic read: ${path}`)
    })
  }
  return evidence
}
const capabilityNames = {
  skill: 'synthetic-skill-with-a-long-name-and-distinct-final-identity',
  tool: 'Synthetic tools with a long name and distinct final identity',
  server: 'synthetic-server-with-a-long-name-and-distinct-final-identity',
  official: 'synthetic-catalog-skill-with-a-distinct-final-identity'
}

function capabilityRead(rawPath, mode) {
  const [path, query = ''] = rawPath.split('?')
  if (mode === 'error' && ['/api/skills', '/api/tools/toolsets', '/api/config'].includes(path)) {
    throw new Error('Synthetic capability read unavailable; retry is safe.')
  }
  if (path === '/api/profiles') return { profiles: [{ name: 'default', is_default: true }, { name: 'synthetic-scope', is_default: false }] }
  const empty = mode === 'empty'
  if (path === '/api/skills') return empty ? [] : Array.from({ length: 32 }, (_, index) => ({
    name: index === 0 ? capabilityNames.skill : `synthetic-skill-${String(index).padStart(2, '0')}`,
    description: 'Synthetic skill description for bounded layout review.', category: 'synthetic',
    enabled: index % 2 === 0, usage: 32 - index, provenance: 'bundled'
  }))
  if (path === '/api/skills/content') return {
    name: new URLSearchParams(query).get('name'), path: '/synthetic/SKILL.md',
    content: '# Synthetic skill\n\n' + Array.from({ length: 24 }, (_, index) => `Synthetic instruction ${index + 1}.`).join('\n\n')
  }
  if (path === '/api/skills/hub/official') return { skills: empty ? [] : [{
    name: capabilityNames.official, identifier: 'official/synthetic/layout-review',
    description: 'Synthetic catalog entry; do not install.', category: 'synthetic', tags: [], installed: false
  }] }
  if (path === '/api/skills/hub/preview') return { skill_md: '# Synthetic catalog skill\n\nPreview only; no installation.' }
  if (path === '/api/tools/toolsets') return empty ? [] : Array.from({ length: 24 }, (_, index) => ({
    name: `synthetic_tools_${index}`, label: index === 0 ? capabilityNames.tool : `Synthetic tools ${index}`,
    description: 'Synthetic toolset description.', enabled: true, available: true, configured: true,
    tools: [`synthetic_read_${index}`, `synthetic_inspect_${index}`]
  }))
  if (/^\/api\/tools\/toolsets\/[^/]+\/config$/.test(path)) return { has_category: false, active_provider: null, providers: [] }
  if (path === '/api/config') return { mcp_servers: empty ? {} : Object.fromEntries(Array.from({ length: 24 }, (_, index) => [
    index === 0 ? capabilityNames.server : `synthetic-server-${String(index).padStart(2, '0')}`,
    { command: 'synthetic-not-executable', args: ['--fixture-only'], enabled: false }
  ])) }
  if (path === '/api/mcp/catalog') return { entries: [], diagnostics: [] }
  if (path === '/api/analytics/usage') return { tools: [] }
  if (path === '/api/logs') return { lines: [] }
  throw new Error(`Unimplemented synthetic capability read: ${path}`)
}

module.exports = { installPopulatedWorkspace, capabilityNames }
if (process.versions.electron) {
  globalThis.__eidolonWorkspaceFixture = installPopulatedWorkspace(require('electron').ipcMain)
}
