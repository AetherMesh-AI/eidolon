const assert = require('node:assert/strict')
const test = require('node:test')
const { title, validateReceipt, validateReport, readSources } = require('./check-packaged-macos-proof.cjs')

const root = '/workspace/product'
const expected = { commit: 'a'.repeat(40), tree: 'b'.repeat(40), root, arch: 'arm64', electron: '40.10.2', mainSha256: 'c'.repeat(64), rendererSha256: 'c'.repeat(64) }
expected.harness = { root: '/workspace/harness', commit: 'e'.repeat(40), tree: 'f'.repeat(40), clean: true }
expected.eventCommit = 'd'.repeat(40)
expected.freshness = { repository: 'AetherMesh-AI/Eidolon', ref: 'refs/heads/main', remoteTip: expected.commit, verifiedAt: '2026-10-19T11:00:00Z' }
expected.adapters = Object.fromEntries(Object.entries({
  electronNetwork: ['harness', 'apps/desktop/e2e/subscription-native-network.cjs'],
  passiveUpdates: ['harness', 'apps/desktop/e2e/isolated-update-check.cjs'],
  providerGuard: ['harness', 'apps/desktop/e2e/fresh-team-provider-guard.py'],
  subscriptionEdges: ['product', 'tests/eidolon_cli/subscription_native_fixture.py'],
}).map(([key, [owner, relative]]) => [key, { owner, path: (owner === 'harness' ? '/workspace/harness' : root) + '/' + relative, sha256: 'c'.repeat(64) }]))
const resources = root + '/apps/desktop/release/mac-arm64/Eidolon.app/Contents/Resources'
const home = '/workspace/.subscription-native-sandboxes/case'
const receipt = {
  version: 2, scope: 'actual-main-packaged-shell-exact-product-checkout-backend', source: { commit: expected.commit, tree: expected.tree, clean: true, mainTip: expected.commit },
  harness: expected.harness, eventCommit: expected.eventCommit, freshness: expected.freshness, adapters: expected.adapters,
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
  ['old single-source claim', r => { r.scope = 'packaged-shell-exact-checkout-backend' }],
  ['wrong harness commit', r => { r.harness.commit = 'd'.repeat(40) }],
  ['wrong harness tree', r => { r.harness.tree = 'd'.repeat(40) }],
  ['wrong event identity', r => { r.eventCommit = expected.commit }],
  ['stale actual main', r => { r.freshness.remoteTip = 'd'.repeat(40) }],
  ['wrong freshness repository', r => { r.freshness.repository = 'other/repository' }],
  ['wrong freshness ref', r => { r.freshness.ref = 'refs/heads/side' }],
  ['missing freshness', r => { delete r.freshness }],
  ['harness backend substitution', r => { r.backend.root = '/harness' }],
  ['unrecorded adapter', r => { delete r.adapters.providerGuard }],
  ['outside adapter', r => { r.adapters.providerGuard.path = '/tmp/guard.py' }],
  ['changed adapter', r => { r.adapters.providerGuard.sha256 = 'd'.repeat(64) }],
  ['candidate product relabeled main', r => { r.source.mainTip = 'd'.repeat(40) }],
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


// Local throwaway repositories are test data only; acceptance never rewrites refs.
function checkoutFixture() {
  const fs = require('node:fs')
  const path = require('node:path')
  const { execFileSync } = require('node:child_process')
  const dir = fs.realpathSync(fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'packaged-source-control-')))
  const git = (root, ...args) => execFileSync('git', args, { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
  const init = name => {
    const root = path.join(dir, name)
    fs.mkdirSync(root)
    git(root, 'init', '--initial-branch=main')
    fs.writeFileSync(path.join(root, 'fixture.txt'), name)
    git(root, 'add', '.')
    git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'Synthetic source control')
    const commit = git(root, 'rev-parse', 'HEAD')
    const tree = git(root, 'rev-parse', 'HEAD^{tree}')
    git(root, 'update-ref', 'refs/remotes/origin/main', commit)
    return { root, commit, tree }
  }
  const product = init('product')
  const harness = init('harness')
  const env = {
    EIDOLON_PACKAGED_PRODUCT_ROOT: product.root,
    EIDOLON_PACKAGED_PROOF_SHA: product.commit, EIDOLON_PACKAGED_PROOF_TREE: product.tree,
    EIDOLON_PACKAGED_HARNESS_SHA: harness.commit, EIDOLON_PACKAGED_HARNESS_TREE: harness.tree,
    GITHUB_SHA: 'a'.repeat(40), EIDOLON_PACKAGED_EVENT_SHA: 'a'.repeat(40), EIDOLON_PACKAGED_REMOTE_MAIN: product.commit,
    EIDOLON_PACKAGED_MAIN_VERIFIED_AT: new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'),
  }
  return { product, harness, env, fs, path, git, cleanup: () => fs.rmSync(dir, { recursive: true }) }
}

test('reads independent exact product/harness identities and actual-main freshness', () => {
  const f = checkoutFixture()
  try {
    const actual = readSources(f.harness.root, f.env)
    assert.deepEqual(actual.product, { ...f.product, clean: true, mainTip: f.product.commit })
    assert.deepEqual(actual.harness, { ...f.harness, clean: true })
    assert.equal(actual.eventCommit, f.env.EIDOLON_PACKAGED_EVENT_SHA)
  } finally { f.cleanup() }
})

for (const [label, change] of [
  ['missing product root', f => { delete f.env.EIDOLON_PACKAGED_PRODUCT_ROOT }],
  ['wrong product commit', f => { f.env.EIDOLON_PACKAGED_PROOF_SHA = 'b'.repeat(40) }],
  ['wrong product tree', f => { f.env.EIDOLON_PACKAGED_PROOF_TREE = 'b'.repeat(40) }],
  ['wrong harness commit', f => { f.env.EIDOLON_PACKAGED_HARNESS_SHA = 'b'.repeat(40) }],
  ['wrong harness tree', f => { f.env.EIDOLON_PACKAGED_HARNESS_TREE = 'b'.repeat(40) }],
  ['dirty product', f => { f.fs.appendFileSync(f.path.join(f.product.root, 'fixture.txt'), 'changed') }],
  ['untracked harness source', f => { f.fs.writeFileSync(f.path.join(f.harness.root, 'untracked.py'), 'changed') }],
  ['missing origin main', f => { f.git(f.product.root, 'update-ref', '-d', 'refs/remotes/origin/main') }],
  ['changed remote main', f => { f.env.EIDOLON_PACKAGED_REMOTE_MAIN = 'b'.repeat(40) }],
  ['expired main check', f => { f.env.EIDOLON_PACKAGED_MAIN_VERIFIED_AT = '2000-01-01T00:00:00Z' }],
  ['overwritten invocation identity', f => { f.env.GITHUB_SHA = f.product.commit }],
  ['missing event identity', f => { delete f.env.EIDOLON_PACKAGED_EVENT_SHA }],
]) {
  test('checkout proof rejects ' + label, () => {
    const f = checkoutFixture()
    try { change(f); assert.throws(() => readSources(f.harness.root, f.env)) }
    finally { f.cleanup() }
  })
}
