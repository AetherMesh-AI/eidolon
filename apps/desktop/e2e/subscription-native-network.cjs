/** Installed before the first native window. No external browser/provider requests. */
const { app, net } = require('electron')
const { appendFileSync } = require('node:fs')
const { join } = require('node:path')
const root = process.env.EIDOLON_FRESH_TEAM_GUARD_DIR
const font = 'https://fonts.googleapis.com/css2?family=Courier+Prime:wght@400;700&display=swap'
const installed = new WeakSet()
const record = (kind, value) => appendFileSync(join(root, 'browser-' + kind + '.jsonl'), JSON.stringify(value) + '\n')
const local = url => ['127.0.0.1', '[::1]'].includes(new URL(url).hostname)
function install(session) {
  if (installed.has(session)) return
  session.protocol.handle('https', request => {
    if (request.method === 'GET' && request.url === font) {
      record('fonts', { physicalNetworkSend: false, fulfillment: 'empty-local-css' })
      return new Response('/* Use source-declared system-font fallbacks. */', {
        headers: { 'content-type': 'text/css' }
      })
    }
    if (local(request.url)) return net.fetch(request, { bypassCustomProtocolHandlers: true, redirect: 'error' })
    record('denied', { physicalNetworkSend: false })
    return new Response('Synthetic fixture denied external request', { status: 403 })
  })
  session.webRequest.onBeforeRequest(
    { urls: ['http://*/*', 'https://*/*', 'ws://*/*', 'wss://*/*'] },
    (details, callback) => {
      if (local(details.url) || (details.method === 'GET' && details.url === font)) {
        callback({})
        return
      }
      record('denied', { physicalNetworkSend: false })
      callback({ cancel: true })
    }
  )
  installed.add(session)
  record('installed', { guardedBeforeCreation: true })
}
app.on('session-created', install)
app.on('web-contents-created', (_event, contents) => {
  if (!installed.has(contents.session)) throw new Error('Renderer created before fixture network guard')
  record('windows', { guardedBeforeCreation: true })
})
