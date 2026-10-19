const assert = require('node:assert/strict')
const test = require('node:test')
const { title, validateReceipt, validateReport } = require('./check-packaged-macos-proof.cjs')

const root = '/source'
const expected = { commit: 'a'.repeat(40), tree: 'b'.repeat(40), root, arch: 'arm64', electron: '40.10.2', mainSha256: 'c'.repeat(64), rendererSha256: 'c'.repeat(64) }
const resources = root + '/apps/desktop/release/mac-arm64/Eidolon.app/Contents/Resources'
const home = root + '/.subscription-native-sandboxes/case'
const receipt = {
  version: 1, scope: 'packaged-shell-exact-checkout-backend', source: { commit: expected.commit, tree: expected.tree, clean: true },
  host: { platform: 'darwin', arch: 'arm64', electron: '40.10.2', node: '24.0.0', modules: '143' },
  bundle: {
    executable: resources.replace('/Resources', '/MacOS/Eidolon'), resources, app: resources + '/app.asar',
    main: resources + '/app.asar/dist/electron-main.mjs', mainPhysical: resources + '/app.asar.unpacked/dist/electron-main.mjs',
    renderer: resources + '/app.asar.unpacked/dist/index.html',
    isPackaged: true, productName: 'Eidolon', executableName: 'Eidolon',
    ptyModule: resources + '/app.asar.unpacked/dist/node_modules/node-pty/lib/index.js',
    nativeModules: [{ path: resources + '/app.asar.unpacked/dist/node_modules/node-pty/prebuilds/darwin-arm64/pty.node', sha256: 'c'.repeat(64) }],
    nativePty: { exitCode: 0, sentinel: true }, executableSha256: 'c'.repeat(64), asarSha256: 'c'.repeat(64),
    mainSha256: 'c'.repeat(64), rendererSha256: 'c'.repeat(64), stampCommit: expected.commit, stampDirty: false, visibleWindows: 1,
  },
  backend: { kind: 'controlled-checkout', root, python: root + '/.venv/bin/python', cliSha256: 'c'.repeat(64), gatewaySha256: 'c'.repeat(64),
    liveGateways: [{ pid: 42, birth: 123, pythonMatches: true, rootMatches: true, guardInstalled: true, homeMatches: true }] },
  isolation: { home: home + '/user-home', profile: home + '/hermes-home', userData: home + '/electron-user-data', guardRoot: home + '/evidence', bootFake: false, devServer: false, sourceAppArgument: false },
}
const teardown = { version: 1, cleanupError: 'none', lifecycle: { phase: 'settled', reason: 'none', capturedCount: 1, remainingLive: 0, rootExited: true }, remaining: { state: 'removed', truncated: false, counts: { home: 0, userData: 0, userHome: 0, evidence: 0, observer: 0, other: 0 } } }
const attachment = (name, body) => ({ name, contentType: 'application/json', body: Buffer.from(JSON.stringify(body)).toString('base64') })
const report = () => ({ errors: [], suites: [{ specs: [{ file: 'subscription-native-journey.spec.ts', title, tests: [{ status: 'expected', results: [{ status: 'passed', retry: 0, attachments: [attachment('packaged-macos-proof', receipt), attachment('subscription-teardown-diagnostic', teardown)] }] }] }] }] })

test('accepts healthy unpacked renderer, native PTY and actual backend evidence', () => {
  assert.deepEqual(validateReceipt(receipt, expected), receipt)
  assert.deepEqual(validateReport(report(), expected), receipt)
})

for (const [label, change] of [
  ['stock Electron', r => { r.bundle.isPackaged = false }],
  ['wrong product', r => { r.bundle.executableName = 'Electron' }],
  ['source renderer', r => { r.bundle.renderer = root + '/apps/desktop/dist/index.html' }],
  ['redirected physical main', r => { r.bundle.mainPhysical = root + '/apps/desktop/dist/electron-main.mjs' }],
  ['torn renderer bytes', r => { r.bundle.rendererSha256 = 'd'.repeat(64) }],
  ['source main', r => { r.bundle.main = root + '/apps/desktop/dist/electron-main.mjs' }],
  ['ancestor node-pty fallback', r => { r.bundle.ptyModule = root + '/node_modules/node-pty/lib/index.js' }],
  ['native binary outside bundle', r => { r.bundle.nativeModules[0].path = '/tmp/pty.node' }],
  ['missing native load', r => { r.bundle.nativeModules = [] }],
  ['wrong ABI PTY failure', r => { r.bundle.nativePty.exitCode = 1 }],
  ['missing PTY output', r => { r.bundle.nativePty.sentinel = false }],
  ['fake boot', r => { r.isolation.bootFake = true }],
  ['source app launch', r => { r.isolation.sourceAppArgument = true }],
  ['host profile', r => { r.isolation.home = '/Users/runner' }],
  ['wrong backend', r => { r.backend.liveGateways[0].pythonMatches = false }],
  ['old runtime root', r => { r.backend.liveGateways[0].rootMatches = false }],
  ['missing network guard', r => { r.backend.liveGateways[0].guardInstalled = false }],
  ['missing live gateway', r => { r.backend.liveGateways = [] }],
  ['wrong source commit', r => { r.source.commit = 'd'.repeat(40) }],
  ['wrong source tree', r => { r.source.tree = 'd'.repeat(40) }],
  ['stale bundle stamp', r => { r.bundle.stampCommit = 'd'.repeat(40) }],
  ['different Electron version', r => { r.host.electron = '41.0.0' }],
  ['wrong bundle bytes', r => { r.bundle.mainSha256 = 'd'.repeat(64) }],
  ['dirty bundle', r => { r.bundle.stampDirty = true }],
]) {
  test('rejects ' + label, () => {
    const candidate = structuredClone(receipt)
    change(candidate)
    assert.throws(() => validateReceipt(candidate, expected))
    const candidateReport = report()
    candidateReport.suites[0].specs[0].tests[0].results[0].attachments[0] = attachment('packaged-macos-proof', candidate)
    assert.throws(() => validateReport(candidateReport, expected))
  })
}

for (const [label, change] of [
  ['missing test', r => { r.suites = [] }],
  ['duplicated test', r => { r.suites.push(structuredClone(r.suites[0])) }],
  ['permissive smoke substituted', r => { r.suites[0].specs[0].file = 'launch-packaged-app.spec.ts' }],
  ['skipped test', r => { r.suites[0].specs[0].tests[0].results[0].status = 'skipped' }],
  ['retry', r => { r.suites[0].specs[0].tests[0].results[0].retry = 1 }],
  ['missing receipt', r => { r.suites[0].specs[0].tests[0].results[0].attachments = [] }],
  ['global failure', r => { r.errors.push({ message: 'failed' }) }],
  ['unsettled teardown', r => { r.suites[0].specs[0].tests[0].results[0].attachments[1] = attachment('subscription-teardown-diagnostic', { ...teardown, lifecycle: { ...teardown.lifecycle, remainingLive: 1 } }) }],
]) {
  test('report rejects ' + label, () => {
    const candidate = report()
    change(candidate)
    assert.throws(() => validateReport(candidate, expected))
  })
}
