/** Fail-closed evidence gate for one real packaged shell + controlled checkout backend. */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { execFileSync } = require('node:child_process')
const { createHash } = require('node:crypto')

const title = 'fresh profile consents, chooses policy, reviews Improve, generates and explicitly creates'
const sha = /^[0-9a-f]{40}$/
const digest = /^[0-9a-f]{64}$/
const inside = (parent, child) => typeof child === 'string' && child.startsWith(parent + '/') && !child.includes('/../')

function readSources(harnessRoot, env = process.env) {
  const pin = name => { assert.match(env[name] ?? '', sha, `Missing exact ${name}`); return env[name] }
  const canonical = root => {
    assert.ok(typeof root === 'string' && path.isAbsolute(root), 'Explicit checkout root required')
    assert.equal(fs.realpathSync(root), root, 'Redirected checkout root')
    return root
  }
  const inspect = (root, commit, tree) => {
    root = canonical(root)
    const git = (...args) => execFileSync('git', args, { cwd: root, encoding: 'utf8' }).trim()
    assert.equal(git('rev-parse', 'HEAD'), commit, 'Wrong checkout commit')
    assert.equal(git('rev-parse', 'HEAD^{tree}'), tree, 'Wrong checkout tree')
    assert.equal(git('write-tree'), tree, 'Changed checkout index')
    assert.equal(git('status', '--porcelain', '--untracked-files=all'), '', 'Dirty checkout')
    return { root, commit, tree, clean: true }
  }
  const product = inspect(env.EIDOLON_PACKAGED_PRODUCT_ROOT,
    pin('EIDOLON_PACKAGED_PROOF_SHA'), pin('EIDOLON_PACKAGED_PROOF_TREE'))
  const harness = inspect(harnessRoot, pin('EIDOLON_PACKAGED_HARNESS_SHA'), pin('EIDOLON_PACKAGED_HARNESS_TREE'))
  const mainTip = execFileSync('git', ['rev-parse', 'refs/remotes/origin/main'], { cwd: product.root, encoding: 'utf8' }).trim()
  assert.equal(mainTip, product.commit, 'Product is not the real checked-out origin/main tip')
  const remoteTip = pin('EIDOLON_PACKAGED_REMOTE_MAIN')
  assert.equal(remoteTip, product.commit, 'Actual main changed; do not substitute source')
  const verifiedAt = env.EIDOLON_PACKAGED_MAIN_VERIFIED_AT
  assert.match(verifiedAt ?? '', /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/)
  const age = Date.now() - Date.parse(verifiedAt)
  assert.ok(Number.isFinite(age) && age >= -60000 && age < 10 * 60 * 1000, 'Main freshness check is stale')
  const eventCommit = pin('EIDOLON_PACKAGED_EVENT_SHA')
  assert.equal(env.GITHUB_SHA, eventCommit, 'Workflow invocation identity was overwritten')
  return { product: { ...product, mainTip }, harness, eventCommit,
    freshness: { repository: 'AetherMesh-AI/Eidolon', ref: 'refs/heads/main', remoteTip, verifiedAt } }
}

function makeExpected(harnessRoot, env = process.env) {
  const sources = readSources(harnessRoot, env)
  const root = sources.product.root
  const desktop = path.join(root, 'apps/desktop')
  const hash = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex')
  const adapter = (owner, relative) => {
    const file = path.join(sources[owner].root, relative)
    assert.equal(fs.realpathSync(file), file, 'Redirected synthetic adapter')
    return { owner, path: file, sha256: hash(file) }
  }
  const pkg = JSON.parse(fs.readFileSync(path.join(desktop, 'package.json'), 'utf8'))
  return { commit: sources.product.commit, tree: sources.product.tree, root,
    harness: sources.harness, eventCommit: sources.eventCommit, freshness: sources.freshness,
    electron: pkg.build.electronVersion, arch: process.arch,
    mainSha256: hash(path.join(desktop, 'dist/electron-main.mjs')),
    rendererSha256: hash(path.join(desktop, 'dist/index.html')),
    adapters: {
      electronNetwork: adapter('harness', 'apps/desktop/e2e/subscription-native-network.cjs'),
      passiveUpdates: adapter('harness', 'apps/desktop/e2e/isolated-update-check.cjs'),
      providerGuard: adapter('harness', 'apps/desktop/e2e/fresh-team-provider-guard.py'),
      subscriptionEdges: adapter('product', 'tests/eidolon_cli/subscription_native_fixture.py'),
    } }
}

function validateReceipt(receipt, expected) {
  assert.equal(receipt.version, 2)
  assert.equal(receipt.scope, 'actual-main-packaged-shell-exact-product-checkout-backend')
  assert.match(expected.commit, sha)
  assert.match(expected.tree, sha)
  assert.deepEqual(receipt.source, { commit: expected.commit, tree: expected.tree, clean: true, mainTip: expected.commit })
  assert.match(expected.harness.commit, sha)
  assert.match(expected.harness.tree, sha)
  assert.equal(expected.harness.clean, true)
  assert.ok(path.isAbsolute(expected.harness.root))
  assert.deepEqual(receipt.harness, expected.harness)
  assert.match(expected.eventCommit, sha)
  assert.equal(receipt.eventCommit, expected.eventCommit)
  assert.equal(expected.freshness.repository, 'AetherMesh-AI/Eidolon')
  assert.equal(expected.freshness.ref, 'refs/heads/main')
  assert.equal(expected.freshness.remoteTip, expected.commit)
  assert.match(expected.freshness.verifiedAt, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/)
  assert.ok(Number.isFinite(Date.parse(expected.freshness.verifiedAt)))
  assert.deepEqual(receipt.freshness, expected.freshness)
  const adapters = {
    electronNetwork: ['harness', 'apps/desktop/e2e/subscription-native-network.cjs'],
    passiveUpdates: ['harness', 'apps/desktop/e2e/isolated-update-check.cjs'],
    providerGuard: ['harness', 'apps/desktop/e2e/fresh-team-provider-guard.py'],
    subscriptionEdges: ['product', 'tests/eidolon_cli/subscription_native_fixture.py'],
  }
  assert.deepEqual(Object.keys(receipt.adapters).sort(), Object.keys(adapters).sort())
  for (const [name, [owner, relative]] of Object.entries(adapters)) {
    assert.equal(receipt.adapters[name].owner, owner)
    assert.equal(receipt.adapters[name].path, (owner === 'harness' ? expected.harness.root : expected.root) + '/' + relative)
    assert.match(expected.adapters[name].sha256, digest)
    assert.deepEqual(receipt.adapters[name], expected.adapters[name])
  }
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
  assert.ok(inside(path.dirname(expected.root) + '/.subscription-native-sandboxes', isolation.home))
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

module.exports = { title, validateReceipt, validateReport, readSources, makeExpected }
if (require.main === module) {
  if (process.argv[2] === '--write-expected') {
    fs.writeFileSync(process.argv[4], JSON.stringify(makeExpected(process.argv[3]), null, 2) + '\n')
    process.exit(0)
  }
  const [report, source, output] = process.argv.slice(2)
  const expected = JSON.parse(fs.readFileSync(source, 'utf8'))
  const receipt = validateReport(JSON.parse(fs.readFileSync(report, 'utf8')), expected)
  fs.writeFileSync(output, JSON.stringify(receipt, null, 2) + '\n')
  console.log('Actual-main packaged Eidolon journey passed once with distinct product/harness, native ABI and settled teardown receipts.')
}
