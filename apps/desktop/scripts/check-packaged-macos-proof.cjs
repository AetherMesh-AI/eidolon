/** Fail-closed evidence gate for one real packaged shell + controlled checkout backend. */
const assert = require('node:assert/strict')
const fs = require('node:fs')

const title = 'fresh profile consents, chooses policy, reviews Improve, generates and explicitly creates'
const sha = /^[0-9a-f]{40}$/
const digest = /^[0-9a-f]{64}$/
const inside = (parent, child) => typeof child === 'string' && child.startsWith(parent + '/') && !child.includes('/../')

function validateReceipt(receipt, expected) {
  assert.equal(receipt.version, 1)
  assert.equal(receipt.scope, 'packaged-shell-exact-checkout-backend')
  assert.match(expected.commit, sha)
  assert.match(expected.tree, sha)
  assert.deepEqual(receipt.source, { commit: expected.commit, tree: expected.tree, clean: true })
  assert.equal(receipt.host.platform, 'darwin')
  assert.ok(['x64', 'arm64'].includes(receipt.host.arch))
  assert.equal(receipt.host.arch, expected.arch)
  assert.equal(receipt.host.electron, expected.electron)
  assert.match(receipt.host.electron, /^\d+\.\d+\.\d+$/)
  assert.match(receipt.host.node, /^\d+\.\d+\.\d+$/)
  assert.match(receipt.host.modules, /^\d+$/)
  const { bundle, backend, isolation } = receipt
  assert.equal(bundle.isPackaged, true)
  assert.equal(bundle.productName, 'Eidolon')
  assert.equal(bundle.executableName, 'Eidolon')
  assert.equal(bundle.executable, expected.root + '/apps/desktop/release/mac' +
    (expected.arch === 'arm64' ? '-arm64' : '') + '/Eidolon.app/Contents/MacOS/Eidolon')
  const contents = bundle.executable.slice(0, -'/MacOS/Eidolon'.length)
  assert.equal(bundle.resources, contents + '/Resources')
  assert.equal(bundle.app, bundle.resources + '/app.asar')
  assert.equal(bundle.main, bundle.app + '/dist/electron-main.mjs')
  assert.equal(bundle.mainPhysical, bundle.resources + '/app.asar.unpacked/dist/electron-main.mjs')
  assert.equal(bundle.renderer, bundle.resources + '/app.asar.unpacked/dist/index.html')
  assert.ok(inside(bundle.resources + '/app.asar.unpacked/dist/node_modules/node-pty', bundle.ptyModule))
  assert.ok(Array.isArray(bundle.nativeModules) && bundle.nativeModules.length >= 1)
  for (const native of bundle.nativeModules) {
    assert.ok(inside(bundle.resources + '/app.asar.unpacked/dist/node_modules/node-pty', native.path))
    assert.ok(native.path.endsWith('.node'))
    assert.match(native.sha256, digest)
  }
  for (const field of ['executableSha256', 'asarSha256', 'mainSha256', 'rendererSha256']) assert.match(bundle[field], digest)
  assert.equal(bundle.mainSha256, expected.mainSha256)
  assert.equal(bundle.rendererSha256, expected.rendererSha256)
  assert.equal(bundle.stampCommit, expected.commit)
  assert.equal(bundle.stampDirty, false)
  assert.deepEqual(bundle.nativePty, { exitCode: 0, sentinel: true })
  assert.ok(Number.isInteger(bundle.visibleWindows) && bundle.visibleWindows > 0)
  assert.equal(backend.kind, 'controlled-checkout')
  assert.equal(backend.root, expected.root)
  assert.equal(backend.python, expected.root + '/.venv/bin/python')
  assert.match(backend.cliSha256, digest)
  assert.match(backend.gatewaySha256, digest)
  assert.ok(Array.isArray(backend.liveGateways) && backend.liveGateways.length > 0 && backend.liveGateways.length <= 16)
  for (const gateway of backend.liveGateways) {
    assert.ok(Number.isInteger(gateway.pid) && gateway.pid > 0)
    assert.ok(typeof gateway.birth === 'number' && gateway.birth > 0)
    for (const field of ['pythonMatches', 'rootMatches', 'guardInstalled', 'homeMatches']) assert.equal(gateway[field], true)
  }
  assert.ok(inside(expected.root + '/.subscription-native-sandboxes', isolation.home))
  const sandbox = isolation.home.slice(0, -'/user-home'.length)
  assert.equal(isolation.home, sandbox + '/user-home')
  assert.equal(isolation.profile, sandbox + '/hermes-home')
  assert.equal(isolation.userData, sandbox + '/electron-user-data')
  assert.equal(isolation.guardRoot, sandbox + '/evidence')
  for (const field of ['bootFake', 'devServer', 'sourceAppArgument']) assert.equal(isolation[field], false)
  return receipt
}

function attachment(result, name) {
  const matches = (result.attachments ?? []).filter(row => row.name === name)
  assert.equal(matches.length, 1, `Missing or duplicated ${name}`)
  const row = matches[0]
  assert.equal(row.contentType, 'application/json')
  assert.ok(typeof row.body === 'string' && row.body.length > 0 && row.body.length < 65536)
  const bytes = Buffer.from(row.body, 'base64')
  assert.equal(bytes.toString('base64'), row.body, 'Noncanonical evidence encoding')
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes))
}

function validateReport(report, expected) {
  assert.equal(report.errors?.length ?? 0, 0, 'Global Playwright errors')
  const specs = []
  function collect(suite) {
    specs.push(...(suite.specs ?? []))
    for (const child of suite.suites ?? []) collect(child)
  }
  for (const suite of report.suites ?? []) collect(suite)
  assert.equal(specs.length, 1, 'Exactly one packaged journey must be selected')
  const spec = specs[0]
  assert.ok(spec.file.replaceAll('\\', '/').endsWith('/subscription-native-journey.spec.ts') || spec.file === 'subscription-native-journey.spec.ts')
  assert.equal(spec.title, title)
  assert.equal(spec.tests?.length, 1)
  const test = spec.tests[0]
  assert.equal(test.status, 'expected')
  assert.equal(test.results?.length, 1, 'Retries are not acceptance')
  const result = test.results[0]
  assert.equal(result.status, 'passed', 'Missing/skipped/failed journey')
  assert.equal(result.retry, 0)
  const receipt = validateReceipt(attachment(result, 'packaged-macos-proof'), expected)
  const teardown = attachment(result, 'subscription-teardown-diagnostic')
  assert.equal(teardown.version, 1)
  assert.equal(teardown.cleanupError, 'none')
  assert.equal(teardown.lifecycle.phase, 'settled')
  assert.equal(teardown.lifecycle.reason, 'none')
  assert.ok(teardown.lifecycle.capturedCount >= 1)
  assert.equal(teardown.lifecycle.remainingLive, 0)
  assert.equal(teardown.lifecycle.rootExited, true)
  assert.equal(teardown.remaining.state, 'removed')
  assert.equal(teardown.remaining.truncated, false)
  assert.deepEqual(teardown.remaining.counts, { home: 0, userData: 0, userHome: 0, evidence: 0, observer: 0, other: 0 })
  return receipt
}

module.exports = { title, validateReceipt, validateReport }
if (require.main === module) {
  const [report, source, output] = process.argv.slice(2)
  const expected = JSON.parse(fs.readFileSync(source, 'utf8'))
  const receipt = validateReport(JSON.parse(fs.readFileSync(report, 'utf8')), expected)
  fs.writeFileSync(output, JSON.stringify(receipt, null, 2) + '\n')
  console.log('Packaged Eidolon journey passed once with source, native ABI and settled teardown receipts.')
}
