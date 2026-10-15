// Temporary diagnostic only: preserve dispatch and return values; retain no payloads.
const { ipcMain } = require('electron')
const original = ipcMain.handle
const allowed = new Set(['/api/model/info', '/api/model/options', '/api/model/auxiliary', '/api/model/moa', '/api/config'])
const events = []
let sequence = 0
const evidence = { timeOriginMs: performance.timeOrigin, events, droppedEvents: 0 }
function record(value) { if (events.length < 2048) events.push(value); else evidence.droppedEvents += 1 }
globalThis.__settingsHydrationDiagnostic = evidence
ipcMain.handle = function (channel, listener) {
  if (channel !== 'hermes:api') return original.call(this, channel, listener)
  return original.call(this, channel, async function (event, request) {
    const endpoint = typeof request?.path === 'string' ? request.path.split('?')[0] : ''
    if (!allowed.has(endpoint)) return listener.call(this, event, request)
    const id = ++sequence
    const start = performance.now()
    record({ id, phase: 'start', endpoint, atMs: start, profile: request.profile === 'default' ? 'default' : request.profile == null ? 'implicit' : 'other', explicitConnection: Boolean(request.connectionId) })
    try {
      const result = await listener.call(this, event, request)
      record({ id, phase: 'end', endpoint, atMs: performance.now(), durationMs: performance.now() - start })
      return result
    } catch (error) {
      record({ id, phase: 'error', endpoint, atMs: performance.now(), durationMs: performance.now() - start })
      throw error
    }
  })
}
