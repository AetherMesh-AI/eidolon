/** Synthetic faults at the real renderer → Electron REST bridge. Never records bodies. */
function installHttpReadFaults(ipcMain) {
  const modes = new Map()
  const pending = new Map()
  const evidence = { reads: [], writes: [] }
  const respond = result => {
    if (result.kind === 'error') throw new Error('Synthetic read unavailable.')
    return structuredClone(result.data)
  }
  const control = {
    evidence,
    set(path, result) { modes.set(path, result) },
    pending(path) { return pending.get(path)?.length ?? 0 },
    release(path, result) {
      const held = pending.get(path) ?? []
      pending.delete(path)
      for (const { resolve, reject } of held) {
        try { resolve(respond(result)) } catch (error) { reject(error) }
      }
      return held.length
    }
  }
  const originalHandle = ipcMain.handle
  ipcMain.handle = function (channel, listener) {
    if (channel !== 'hermes:api') return originalHandle.call(this, channel, listener)
    ipcMain.handle = originalHandle
    return originalHandle.call(this, channel, (event, request) => {
      const path = request.path.split('?')[0]
      const method = request.method ?? 'GET'
      const mode = modes.get(path)
      const owned = mode || [...modes.keys()].some(parent => path.startsWith(`${parent}/`))
      if (!owned) return listener(event, request)
      const entry = { path, method, profile: request.profile ?? null, connectionId: request.connectionId ?? null }
      if (path === '/api/profiles/sessions') {
        const archived = new URLSearchParams(request.path.split('?')[1] ?? '').get('archived')
        entry.archived = ['only', 'include', 'exclude'].includes(archived) ? archived : null
      }
      if (method !== 'GET') {
        evidence.writes.push(entry)
        throw new Error('Synthetic reads do not authorize writes.')
      }
      if (!mode) return listener(event, request)
      evidence.reads.push(entry)
      if (mode.kind !== 'pending') return respond(mode)
      return new Promise((resolve, reject) => {
        const held = pending.get(path) ?? []
        held.push({ resolve, reject })
        pending.set(path, held)
      })
    })
  }
  return control
}

module.exports = { installHttpReadFaults }
if (process.versions.electron) {
  globalThis.__eidolonReadFaults = installHttpReadFaults(require('electron').ipcMain)
}
