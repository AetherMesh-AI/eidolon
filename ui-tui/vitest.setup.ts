import http from 'node:http'
import https from 'node:https'
import { syncBuiltinESMExports } from 'node:module'

const rejectNetwork = (): never => {
  throw new Error('Live network is disabled in TUI unit tests; install an explicit mock.')
}

// Install the worker's baseline before test modules load. Test-level mock
// cleanup must restore these guards, never the host's real transports.
globalThis.fetch = async () => rejectNetwork()
http.request = rejectNetwork as typeof http.request
http.get = rejectNetwork as typeof http.get
https.request = rejectNetwork as typeof https.request
https.get = rejectNetwork as typeof https.get
syncBuiltinESMExports()
