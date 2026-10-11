/** Test-process bootstrap, loaded before the real Electron main module. */
function installIsolatedUpdateCheck(ipcMain) {
  const originalHandle = ipcMain.handle
  const evidence = { registered: false, checks: 0, senders: [] }
  ipcMain.handle = function (channel, listener) {
    if (channel !== 'hermes:updates:check') {
      return originalHandle.call(this, channel, listener)
    }
    // Intercept the initial registration only. Dedicated updater scenarios can
    // replace this handler normally to exercise real notification behavior.
    ipcMain.handle = originalHandle
    evidence.registered = true
    return originalHandle.call(this, channel, event => {
      evidence.checks += 1
      evidence.senders.push(event.sender.id)
      return { supported: true, branch: 'main', behind: 0, updateAvailable: false, fetchedAt: Date.now() }
    })
  }
  return evidence
}

module.exports = { installIsolatedUpdateCheck }
if (process.versions.electron) {
  const { app, ipcMain } = require('electron')
  const evidence = installIsolatedUpdateCheck(ipcMain)
  evidence.windows = []
  app.on('web-contents-created', (_event, contents) => {
    evidence.windows.push({ id: contents.id, registeredBeforeCreation: evidence.registered })
  })
  globalThis.__eidolonUpdateFixture = evidence
}
