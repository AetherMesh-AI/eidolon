/** Opt-in attachment proof. Synthetic transcript metadata never names a real file. */
const profile = 'synthetic-attachment-owner'
const sessionId = 'synthetic-attachment-session'
const sessionPath = `/api/sessions/${sessionId}/messages`
const listPath = '/api/profiles/sessions'
const sessionTitle = 'Synthetic attachment source session'

function sessionPage(ids = [sessionId]) {
  return {
    sessions: ids.map(id => ({ id, title: sessionTitle, profile: 'default', source: 'desktop', message_count: 1, created_at: 1700000000, updated_at: 1700000000 })),
    limit: 30, offset: 0, total: ids.length
  }
}

function transcript(label = 'synthetic-report', id = sessionId) {
  return { session_id: id, messages: [{ role: 'assistant', content: `MEDIA:/synthetic-only/${label}.pdf`, timestamp: 1700000000 }] }
}

function installSessionArtifactRecovery(ipcMain) {
  const originalHandle = ipcMain.handle
  const evidence = { blockedActions: [], pendingProfiles: [], apiReads: [], ambientStubs: [] }
  const blocked = new Set([
    'hermes:openExternal', 'hermes:saveGatewayFile', 'hermes:saveImageFromUrl', 'hermes:saveImageBuffer',
    'hermes:fs:readDir', 'hermes:fs:reveal', 'hermes:fs:openDir', 'hermes:fs:rename', 'hermes:fs:writeText', 'hermes:fs:trash'
  ])
  ipcMain.handle = function (channel, listener) {
    // Runtime plugin discovery is ambient to the shell. Suppress the root
    // getters before their mkdir/read/watch path, rather than allowing any
    // native filesystem inspection in an attachment-only proof.
    if (channel === 'hermes:fs:desktopPluginsRoot' || channel === 'hermes:fs:agentPluginsRoot') {
      return originalHandle.call(this, channel, () => {
        evidence.ambientStubs.push({ channel, result: 'no-synthetic-plugin-roots', backendDispatch: false })
        return ''
      })
    }
    if (blocked.has(channel)) {
      return originalHandle.call(this, channel, () => {
        evidence.blockedActions.push({ channel })
        throw new Error('Synthetic attachment proof does not open or change files.')
      })
    }
    if (channel === 'hermes:connection' || channel === 'hermes:connection:for') {
      return originalHandle.call(this, channel, (event, target, ...rest) => {
        const selected = typeof target === 'string' ? target : target?.profile
        if (selected !== profile) return listener(event, target, ...rest)
        evidence.pendingProfiles.push({ channel, profile })
        // Deliberately unresolved until this isolated app closes: production
        // selection owns the pending state; no profile backend is started.
        return new Promise(() => {})
      })
    }
    if (channel === 'hermes:connection-config:get') {
      return originalHandle.call(this, channel, (event, selected, ...rest) =>
        selected === profile ? { mode: 'local' } : listener(event, selected, ...rest))
    }
    if (channel !== 'hermes:api') return originalHandle.call(this, channel, listener)
    return originalHandle.call(this, channel, (event, request) => {
      const path = request.path.split('?')[0]
      const method = request.method ?? 'GET'
      // Session enumeration runs this migration automatically. An isolated
      // read fixture has no legacy owners to stamp; never dispatch its POST.
      if (path === '/api/sessions/owner-backfill' && method === 'POST') {
        evidence.ambientStubs.push({ channel, path, method, result: 'no-synthetic-legacy-owners', backendDispatch: false })
        return { ok: true, profile: 'default', stamped: 0 }
      }
      if (path.startsWith('/api/fs/') || path === '/api/media' ||
          (method !== 'GET' && (path.startsWith('/api/sessions') || path.startsWith('/api/profiles')))) {
        evidence.blockedActions.push({ channel, path, method })
        throw new Error('Synthetic attachment proof is read-only.')
      }
      if (path === '/api/profiles') {
        evidence.apiReads.push({ path, method })
        return { profiles: [{ name: 'default', is_default: true }, { name: profile, is_default: false }] }
      }
      return listener(event, request)
    })
  }
  return evidence
}

module.exports = { installSessionArtifactRecovery, profile, sessionId, sessionPath, listPath, sessionTitle, sessionPage, transcript }
if (process.versions.electron) {
  // Install the attachment guard first so the reusable read seam can restore
  // this registration wrapper after it wraps hermes:api.
  globalThis.__eidolonAttachmentRecovery = installSessionArtifactRecovery(require('electron').ipcMain)
  require('./http-read-fault-bootstrap.cjs')
}
