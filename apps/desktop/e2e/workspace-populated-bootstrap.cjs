/** Opt-in synthetic read boundary for native responsive journeys. No credentials or provider calls. */
function installPopulatedWorkspace(ipcMain) {
  const originalHandle = ipcMain.handle
  const evidence = { reads: [], mutations: [] }
  ipcMain.handle = function (channel, listener) {
    if (channel !== 'hermes:api') return originalHandle.call(this, channel, listener)
    ipcMain.handle = originalHandle
    return originalHandle.call(this, channel, (event, request) => {
      const path = request.path.split('?')[0]
      const owned = path.startsWith('/api/messaging/') || path.startsWith('/api/pairing') || /^\/api\/profiles\/[^/]+\/soul$/.test(path)
      if (!owned) return listener(event, request)
      if (request.method && request.method !== 'GET') {
        evidence.mutations.push({ path, method: request.method })
        throw new Error('Synthetic workspace is read-only: unexpected mutation')
      }
      evidence.reads.push(path)
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
module.exports = { installPopulatedWorkspace }
if (process.versions.electron) {
  globalThis.__eidolonWorkspaceFixture = installPopulatedWorkspace(require('electron').ipcMain)
}
