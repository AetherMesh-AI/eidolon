const test = require('node:test')
const assert = require('node:assert/strict')
const { createHash } = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const os = require('node:os')
const { spawn } = require('node:child_process')
const { fileURLToPath } = require('node:url')
const { canonicalDirectory, sandboxArgs, childEnvironment, validateReceipt, machServices,
  profileFile, probeFile, protocol, poll, settleOwned } = require('./packaged-containment.cjs')
const hash = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex')
const nonce = 'a'.repeat(64)
function probes(parent) {
  const row = (pid, ppid) => ({ pid, ppid, loopback: true, allowedCanary: true, homeMatches: true, nonceSha256: nonce,
    environmentNames: ['EIDOLON_CONTAINMENT_NONCE', 'HOME', 'LANG', 'PATH', 'PYTHONDONTWRITEBYTECODE', 'TMPDIR'],
    ...Object.fromEntries(['tcpNonloopback', 'udp4NoPayload', 'udp6NoPayload', 'credentialCanary', 'symlinkCanary', 'keychainCanary', 'dataVolumeCanary'].map(key => [key, { denied: true, errno: 1 }])),
    machPolicy: machServices.flatMap(name => [2, 12].map(filter => ({ name, filter, result: 1, denied: true }))) })
  return { direct: row(parent + 1, parent), inherited: row(parent + 2, parent + 1) }
}
function healthy() {
  return { version: 1, scope: 'synthetic-only-macos-containment-preflight', platform: 'darwin',
    passed: true, productLaunched: false, profileSha256: hash(profileFile), probeSha256: hash(probeFile), nonceSha256: nonce,
    directProcess: { ownedTreeSettled: true, pid: 101, code: 0, signal: null },
    probes: probes(100), unrestrictedNonloopbackControl: true, nonloopbackConnections: 0, loopbackConnections: 4,
    disconnect: { pid: 200, beforeEntry: true, nonceVerified: true, pidVerified: true, executableVerified: true,
      debuggerDisconnected: true, entryExecutedAfterDisconnect: true, explicitResume: false,
      reason: 'Break on start', exitCode: 0, ownedTreeSettled: true, probes: probes(200) } }
}
test('accepts complete synthetic-only evidence', () => validateReceipt(healthy()))
for (const [name, mutate] of [
  ['wrong platform', r => { r.platform = 'linux' }],
  ['wrong scope', r => { r.scope = 'packaged-proof' }],
  ['product launched', r => { r.productLaunched = true }],
  ['failed preflight', r => { r.passed = false }],
  ['torn profile', r => { r.profileSha256 = 'b'.repeat(64) }],
  ['torn probe', r => { r.probeSha256 = 'b'.repeat(64) }],
  ['foreign nonce', r => { r.probes.direct.nonceSha256 = 'b'.repeat(64) }],
  ['foreign child', r => { r.probes.inherited.ppid++ }],
  ['foreign inspector child', r => { r.disconnect.probes.direct.ppid++ }],
  ['false positive local control', r => { r.unrestrictedNonloopbackControl = false }],
  ['nonloopback connection', r => { r.nonloopbackConnections = 1 }],
  ['missing loopback control', r => { r.loopbackConnections = 3 }],
  ['missing direct settlement', r => { r.directProcess.ownedTreeSettled = false }],
  ['missing inspector settlement', r => { r.disconnect.ownedTreeSettled = false }],
  ['missing pause', r => { r.disconnect.beforeEntry = false }],
  ['unbound nonce', r => { r.disconnect.nonceVerified = false }],
  ['unbound PID', r => { r.disconnect.pidVerified = false }],
  ['unbound executable', r => { r.disconnect.executableVerified = false }],
  ['wrong pause', r => { r.disconnect.reason = 'other' }],
  ['explicit resume', r => { r.disconnect.explicitResume = true }],
  ['no disconnect', r => { r.disconnect.debuggerDisconnected = false }],
  ['no post-loss marker', r => { r.disconnect.entryExecutedAfterDisconnect = false }],
  ['child signal', r => { r.directProcess.signal = 'SIGKILL' }],
  ['child failure', r => { r.disconnect.exitCode = 1 }],
  ['retained failure', r => { r.error = 'failed' }],
  ['proxy environment', r => { r.probes.direct.environmentNames.push('HTTPS_PROXY') }],
  ['inherited auth environment', r => { r.disconnect.probes.inherited.environmentNames.push('SSH_AUTH_SOCK') }],
  ['Keychain Mach access', r => { r.probes.direct.machPolicy[0].denied = false }],
  ['invalid policy query', r => { r.probes.direct.machPolicy[0].result = -1 }],
  ['duplicate Mach query', r => { r.probes.direct.machPolicy[1] = r.probes.direct.machPolicy[0] }],
]) test(`rejects ${name}`, () => { const receipt = healthy(); mutate(receipt); assert.throws(() => validateReceipt(receipt)) })
for (const phase of ['probes', 'disconnect']) {
  for (const kind of ['direct', 'inherited']) {
    for (const key of ['tcpNonloopback', 'udp4NoPayload', 'udp6NoPayload', 'credentialCanary', 'symlinkCanary', 'keychainCanary', 'dataVolumeCanary']) {
      test(`rejects ${phase}/${kind}/${key} non-policy failure`, () => {
        const receipt = healthy()
        const row = phase === 'probes' ? receipt.probes[kind] : receipt.disconnect.probes[kind]
        row[key].errno = 61
        assert.throws(() => validateReceipt(receipt))
      })
    }
  }
}
test('profile denies by default and never grants broad network, credential or host setting access', () => {
  const profile = fs.readFileSync(profileFile, 'utf8')
  assert.match(profile, /\(deny default\)/)
  assert.match(profile, /\(allow network-outbound \(remote ip "localhost:\*"\)\)/)
  assert.doesNotMatch(profile, /\(allow (network\*|default|sysctl-write)/)
  assert.doesNotMatch(profile, /\(subpath "\/(System|usr|private|Library|Users)"\)/)
  assert.doesNotMatch(profile, /\(allow network-outbound \(local/)
  assert.doesNotMatch(profile, /\(global-name "com.apple.(securityd|SecurityServer|trustd|authd|coreservices)/)
  assert.match(profile.trim(), /\(deny file-read\* file-write\* \(regex #"\(\^\|\/\)Keychains\(\/\|\$\)"\)\)$/)
})
test('canonical roots and explicit environment reject substitution and inheritance', () => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'containment-unit-')))
  try {
    for (const child of ['product', 'harness', 'python', 'node', 'state', 'home']) fs.mkdirSync(path.join(root, child))
    const p = { PRODUCT: path.join(root, 'product'), HARNESS: path.join(root, 'harness'),
      PYTHON_RUNTIME: path.join(root, 'python'), NODE_RUNTIME: path.join(root, 'node'), DISPOSABLE: path.join(root, 'state') }
    assert.ok(sandboxArgs(p, process.execPath).includes(profileFile))
    assert.throws(() => sandboxArgs({ ...p, EXTRA: root }, process.execPath))
    assert.throws(() => sandboxArgs({ ...p, PRODUCT: '/' }, process.execPath))
    assert.throws(() => sandboxArgs({ ...p, PRODUCT: p.HARNESS }, process.execPath))
    fs.symlinkSync(p.PRODUCT, path.join(root, 'link'))
    assert.throws(() => canonicalDirectory(path.join(root, 'link')))
    const env = childEnvironment(path.join(root, 'home'), 'synthetic-nonce')
    assert.deepEqual(Object.keys(env).sort(), ['EIDOLON_CONTAINMENT_NONCE', 'HOME', 'LANG', 'PATH', 'PYTHONDONTWRITEBYTECODE', 'TMPDIR'])
    assert.equal(env.EIDOLON_CONTAINMENT_NONCE, 'synthetic-nonce')
  } finally { fs.rmSync(root, { recursive: true, force: true }) }
})
test('real local Node protocol sees ESM dependency before side effect; disconnect resumes it', { timeout: 15000 }, async () => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'containment-protocol-unit-')))
  const marker = path.join(root, 'marker'), dep = path.join(root, 'dep.mjs'), main = path.join(root, 'main.mjs')
  fs.writeFileSync(dep, `import fs from 'node:fs'; fs.writeFileSync(${JSON.stringify(marker)}, 'synthetic-only');`)
  fs.writeFileSync(main, `import './dep.mjs';`)
  const child = spawn(process.execPath, ['--inspect=0', '--inspect-brk=127.0.0.1:0', main], {
    env: childEnvironment(root, 'synthetic-nonce'), stdio: ['ignore', 'pipe', 'pipe'] })
  let first, second
  const done = new Promise(resolve => child.once('close', resolve))
  try {
    const endpoint = await new Promise((resolve, reject) => {
      let stderr = ''
      const timer = setTimeout(() => reject(new Error('Endpoint timeout')), 5000)
      child.stderr.on('data', data => { stderr += data; const match = stderr.match(/Debugger listening on (ws:\/\/[^\s]+)/); if (match) { clearTimeout(timer); resolve(match[1]) } })
    })
    first = await protocol(endpoint); await first.send('Runtime.enable')
    assert.equal(await first.evaluate('process.env.EIDOLON_CONTAINMENT_NONCE'), 'synthetic-nonce')
    second = await protocol(endpoint); await second.send('Runtime.enable')
    await first.send('Debugger.enable'); await first.send('Runtime.runIfWaitingForDebugger')
    const paused = await first.paused
    assert.equal(paused.reason, 'Break on start')
    assert.equal(fileURLToPath(paused.callFrames[0].url || first.scripts.get(paused.callFrames[0].location.scriptId)), dep)
    assert.equal(await first.evaluate('process.pid'), child.pid)
    assert.equal(fs.existsSync(marker), false)
    first.close()
    const deadline = Date.now() + 3000
    while (!fs.existsSync(marker) && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 10))
    assert.equal(fs.readFileSync(marker, 'utf8'), 'synthetic-only')
    second.close(); assert.equal(await done, 0)
  } finally {
    child.kill('SIGKILL'); await done
    first?.close(); second?.close(); fs.rmSync(root, { recursive: true, force: true })
  }
})

test('polling deadlines settle instead of abandoning live loops', async () => {
  let calls = 0
  await assert.rejects(poll(() => { calls++; return false }, 20, 'missing marker'), /timed out/)
  const settled = calls
  await new Promise(resolve => setTimeout(resolve, 30))
  assert.equal(calls, settled)
  await assert.rejects(poll(() => { throw new Error('child signaled') }, 1000, 'endpoint'), /child signaled/)
})

test('failure-time owned cleanup is positively recorded without replacing original failure', async () => {
  const record = {}
  assert.equal(await settleOwned(async () => {}, record), undefined)
  assert.deepEqual(record, { ownedTreeSettled: true })
  const failure = new Error('synthetic cleanup failure')
  const rejected = {}
  assert.equal(await settleOwned(async () => { throw failure }, rejected), failure)
  assert.equal(rejected.ownedTreeSettled, false)
  assert.match(rejected.cleanupError, /synthetic cleanup failure/)
})
