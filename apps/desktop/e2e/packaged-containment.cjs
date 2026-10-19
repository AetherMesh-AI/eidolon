/** Synthetic-only containment preflight. It does not launch Electron or a product backend. */
const assert = require('node:assert/strict')
const { spawn, execFileSync } = require('node:child_process')
const { createHash, randomUUID } = require('node:crypto')
const fs = require('node:fs')
const net = require('node:net')
const os = require('node:os')
const path = require('node:path')
const { fileURLToPath } = require('node:url')

const profileFile = path.join(__dirname, 'packaged-containment.sb')
const probeFile = path.join(__dirname, 'packaged-containment-probe.py')
const hash = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex')
const inside = (root, file) => file.startsWith(root + path.sep)
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
async function poll(read, ms, label) {
  const end = Date.now() + ms
  while (Date.now() < end) { const result = read(); if (result) return result; await delay(10) }
  throw new Error(label + ' timed out')
}
const machServices = [
  'com.apple.securityd', 'com.apple.securityd.xpc', 'com.apple.securityd.system',
  'com.apple.SecurityServer', 'com.apple.trustd', 'com.apple.trustd.agent',
  'com.apple.authd', 'com.apple.cfnetwork.AuthBrokerAgent',
  'com.apple.coreservices.launchservicesd', 'com.apple.coreservices.appleevents',
]
const timeout = (promise, ms, label) => {
  let timer
  return Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(label + ' timed out')), ms)
  })]).finally(() => clearTimeout(timer))
}

function canonicalDirectory(root) {
  assert.equal(typeof root, 'string')
  assert.ok(path.isAbsolute(root) && !root.includes('\n') && !root.includes('\0'))
  assert.equal(fs.realpathSync(root), root, 'Directory is redirected')
  assert.ok(fs.statSync(root).isDirectory())
  assert.ok(!['/', '/Users', '/Library', '/System', '/usr', '/private', '/private/var', '/opt', os.homedir()].includes(root),
    'Overbroad containment root')
  return root
}

function sandboxArgs(parameters, command, args = []) {
  assert.deepEqual(Object.keys(parameters).sort(), ['DISPOSABLE', 'HARNESS', 'NODE_RUNTIME', 'PRODUCT', 'PYTHON_RUNTIME'])
  for (const root of Object.values(parameters)) canonicalDirectory(root)
  assert.ok(parameters.DISPOSABLE !== parameters.PRODUCT && parameters.DISPOSABLE !== parameters.HARNESS &&
    !inside(parameters.PRODUCT, parameters.DISPOSABLE) && !inside(parameters.HARNESS, parameters.DISPOSABLE))
  assert.ok(parameters.PRODUCT !== parameters.HARNESS && !inside(parameters.PRODUCT, parameters.HARNESS) &&
    !inside(parameters.HARNESS, parameters.PRODUCT), 'Checkouts must be independent')
  assert.ok(path.isAbsolute(command) && fs.statSync(command).isFile())
  const result = ['-f', profileFile]
  for (const [key, value] of Object.entries(parameters)) result.push('-D', `${key}=${value}`)
  return [...result, command, ...args]
}

function childEnvironment(home, nonce) {
  canonicalDirectory(home)
  return { HOME: home, TMPDIR: home, PATH: '/usr/bin:/bin:/usr/sbin:/sbin',
    LANG: 'en_US.UTF-8', PYTHONDONTWRITEBYTECODE: '1', EIDOLON_CONTAINMENT_NONCE: nonce }
}

function ownedSpawn(command, args, options) {
  const child = spawn(command, args, { ...options, detached: true, stdio: ['ignore', 'pipe', 'pipe'] })
  let stdout = '', stderr = ''
  child.stdout.on('data', data => { stdout = (stdout + data).slice(-65536) })
  child.stderr.on('data', data => { stderr = (stderr + data).slice(-65536) })
  const done = new Promise((resolve, reject) => {
    child.once('error', reject)
    child.once('close', (code, signal) => resolve({ code, signal, stdout, stderr }))
  })
  void done.catch(() => undefined)
  const stop = async () => {
    if (child.pid) {
      try { process.kill(-child.pid, 'SIGKILL') } catch (error) { if (error.code !== 'ESRCH') throw error }
    }
    await timeout(done, 5000, 'Owned child exit')
    if (child.pid) {
      try { process.kill(-child.pid, 0); throw new Error('Owned process group remains') }
      catch (error) { if (error.code !== 'ESRCH') throw error }
    }
  }
  return { child, done, stop, stderr: () => stderr }
}

async function settleOwned(stop, record) {
  try { await stop(); record.ownedTreeSettled = true; return undefined }
  catch (error) {
    record.ownedTreeSettled = false
    record.cleanupError = String(error?.stack || error).slice(0, 2000)
    return error
  }
}

async function protocol(url) {
  const endpoint = new URL(url)
  assert.equal(endpoint.protocol, 'ws:')
  assert.equal(endpoint.hostname, '127.0.0.1')
  assert.match(endpoint.pathname, /^\/[a-f0-9-]+$/)
  const ws = new WebSocket(url)
  const pending = new Map(), scripts = new Map()
  let next = 0, pausedResolve, pausedReject
  const paused = new Promise((resolve, reject) => { pausedResolve = resolve; pausedReject = reject })
  void paused.catch(() => undefined)
  const fail = () => {
    const error = new Error('Inspector closed')
    for (const item of pending.values()) item.reject(error)
    pending.clear()
    pausedReject(error)
  }
  ws.addEventListener('close', fail)
  ws.addEventListener('error', fail)
  ws.addEventListener('message', event => {
    let message
    try { message = JSON.parse(event.data) } catch { fail(); return }
    if (message.id) {
      const item = pending.get(message.id)
      if (!item) return
      pending.delete(message.id)
      if (message.error) item.reject(new Error(message.error.message)); else item.resolve(message.result)
    } else if (message.method === 'Debugger.scriptParsed') {
      scripts.set(message.params.scriptId, message.params.url)
    } else if (message.method === 'Debugger.paused') pausedResolve(message.params)
  })
  try {
    await timeout(new Promise((resolve, reject) => {
      ws.addEventListener('open', resolve, { once: true })
      ws.addEventListener('error', reject, { once: true })
    }), 5000, 'Inspector open')
  } catch (error) { ws.close(); throw error }
  const send = async (method, params = {}) => {
    const id = ++next
    try {
      return await timeout(new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject })
        ws.send(JSON.stringify({ id, method, params }))
      }), 5000, method)
    } finally { pending.delete(id) }
  }
  const evaluate = async expression => {
    const response = await send('Runtime.evaluate', { expression, returnByValue: true, includeCommandLineAPI: true })
    assert.equal(response.exceptionDetails, undefined, 'Inspector evaluation failed')
    return response.result.value
  }
  return { send, evaluate, scripts, paused, close: () => ws.close() }
}

async function disconnectControl({ parameters, env, python, configPath, disposable }) {
  const entry = path.join(disposable, 'inspect-entry.mjs')
  const dependency = path.join(disposable, 'inspect-dependency.mjs')
  const marker = path.join(disposable, 'inspect-result.json')
  const code = `import { execFileSync } from 'node:child_process';\nimport { writeFileSync } from 'node:fs';\nconst result = execFileSync(${JSON.stringify(python)}, ['-I', ${JSON.stringify(probeFile)}, ${JSON.stringify(configPath)}], {encoding:'utf8',timeout:20000});\nwriteFileSync(${JSON.stringify(marker)}, result);\n`
  fs.writeFileSync(dependency, code)
  fs.writeFileSync(entry, `import './inspect-dependency.mjs';\n`)
  const launch = ownedSpawn('/usr/bin/sandbox-exec', sandboxArgs(parameters, process.execPath,
    ['--inspect=0', '--inspect-brk=127.0.0.1:0', entry]), { env, cwd: env.HOME })
  let first, second
  try {
    const endpoint = await poll(() => {
      const match = launch.stderr().match(/Debugger listening on (ws:\/\/[^\s]+)/)
      if (match) return match[1]
      if (launch.child.exitCode !== null || launch.child.signalCode !== null) throw new Error('Inspector child exited before endpoint')
    }, 10000, 'Inspector endpoint')
    first = await protocol(endpoint)
    await first.send('Runtime.enable')
    assert.equal(await first.evaluate('process.env.EIDOLON_CONTAINMENT_NONCE'), env.EIDOLON_CONTAINMENT_NONCE)
    assert.equal(fs.existsSync(marker), false)
    second = await protocol(endpoint)
    await second.send('Runtime.enable') // Playwright-shaped second client, no Debugger domain.
    await first.send('Debugger.enable')
    await first.send('Runtime.runIfWaitingForDebugger')
    const paused = await timeout(first.paused, 10000, 'First script pause')
    assert.equal(paused.reason, 'Break on start')
    const frame = paused.callFrames[0]
    const url = frame.url || first.scripts.get(frame.location.scriptId)
    assert.equal(fileURLToPath(url), dependency)
    const identity = await first.evaluate('({pid:process.pid,ppid:process.ppid,executable:process.execPath,nonce:process.env.EIDOLON_CONTAINMENT_NONCE})')
    assert.equal(identity.pid, launch.child.pid)
    assert.equal(identity.ppid, process.pid)
    assert.equal(fs.realpathSync(identity.executable), fs.realpathSync(process.execPath))
    assert.equal(identity.nonce, env.EIDOLON_CONTAINMENT_NONCE)
    assert.equal(fs.existsSync(marker), false, 'Entry side effect preceded pause')
    // Deliberately lose the Debugger client; no resume or adapter is installed.
    first.close()
    // Runtime-only client remains briefly, matching the observed unsafe race.
    await poll(() => {
      if (fs.existsSync(marker)) return true
      if (launch.child.exitCode !== null || launch.child.signalCode !== null) throw new Error('Inspector child exited without marker')
    }, 25000, 'Disconnect marker')
    second.close()
    const ended = await timeout(launch.done, 5000, 'Disconnect child exit')
    assert.equal(ended.code, 0, ended.stderr)
    return { beforeEntry: true, nonceVerified: true, pidVerified: true, executableVerified: true,
      reason: paused.reason, script: dependency, scriptSha256: hash(dependency),
      debuggerDisconnected: true, explicitResume: false, entryExecutedAfterDisconnect: true,
      pid: launch.child.pid, ownedTreeSettled: true, probes: JSON.parse(fs.readFileSync(marker, 'utf8')), exitCode: ended.code }
  } finally {
    // Owned tree exit is confirmed before intentionally closing any remaining inspector.
    await launch.stop()
    first?.close()
    second?.close()
  }
}

function validateProbes(result, parentPid, nonceSha256) {
  for (const [kind, row] of Object.entries(result)) {
    assert.ok(['direct', 'inherited'].includes(kind))
    assert.ok(Number.isSafeInteger(row.pid) && row.pid > 0)
    assert.equal(row.loopback, true)
    assert.equal(row.allowedCanary, true)
    assert.equal(row.homeMatches, true)
    assert.equal(row.nonceSha256, nonceSha256)
    assert.deepEqual(row.environmentNames, ['EIDOLON_CONTAINMENT_NONCE', 'HOME', 'LANG', 'PATH', 'PYTHONDONTWRITEBYTECODE', 'TMPDIR'])
    for (const key of ['tcpNonloopback', 'udp4NoPayload', 'udp6NoPayload', 'credentialCanary', 'symlinkCanary', 'keychainCanary', 'dataVolumeCanary']) {
      assert.equal(row[key]?.denied, true, `${kind} ${key} was not denied`)
      assert.ok([1, 13].includes(row[key].errno), `${kind} ${key} was not a policy denial`)
    }
    assert.deepEqual(row.machPolicy.map(({ name, filter }) => ({ name, filter })),
      machServices.flatMap(name => [2, 12].map(filter => ({ name, filter }))))
    for (const policy of row.machPolicy) assert.ok(policy.denied && policy.result === 1, 'Credential/broker Mach permission allowed or query invalid')
  }
  assert.deepEqual(Object.keys(result).sort(), ['direct', 'inherited'])
  assert.equal(result.inherited.ppid, result.direct.pid)
  if (parentPid) assert.equal(result.direct.ppid, parentPid)
}

async function listener(host) {
  let connections = 0
  const server = net.createServer(socket => { connections++; socket.end() })
  await timeout(new Promise((resolve, reject) => {
    server.once('error', reject); server.listen(0, host, resolve)
  }), 5000, 'Local control server')
  return { port: server.address().port, count: () => connections, reset: () => { connections = 0 },
    close: () => timeout(new Promise(resolve => server.close(resolve)), 5000, 'Local control server close') }
}

async function preflight({ product, harness, python, output }) {
  assert.equal(process.platform, 'darwin', 'Native macOS is mandatory; no skipped or emulated proof')
  fs.accessSync('/usr/bin/sandbox-exec', fs.constants.X_OK)
  product = canonicalDirectory(product); harness = canonicalDirectory(harness)
  output = canonicalDirectory(output)
  assert.ok(output !== product && output !== harness && !inside(product, output) && !inside(harness, output), 'Evidence must stay outside both checkouts')
  const disposable = fs.realpathSync(fs.mkdtempSync(path.join(output, 'disposable-')))
  const canaryRoot = fs.realpathSync(fs.mkdtempSync(path.join(output, 'denied-canary-')))
  const home = path.join(disposable, 'home'); fs.mkdirSync(home, { mode: 0o700 })
  const configPath = path.join(disposable, 'probe-config.json')
  const nonce = randomUUID()
  const nonceSha256 = createHash('sha256').update(nonce).digest('hex')
  const env = childEnvironment(home, nonce)
  const pythonRuntime = fs.realpathSync(execFileSync(python, ['-I', '-c', 'import sys; print(sys.base_prefix)'], { env, encoding: 'utf8', timeout: 5000 }).trim())
  const parameters = { PRODUCT: product, HARNESS: harness, PYTHON_RUNTIME: pythonRuntime,
    NODE_RUNTIME: fs.realpathSync(path.dirname(path.dirname(process.execPath))), DISPOSABLE: disposable }
  const nonloopback = Object.values(os.networkInterfaces()).flat().find(row => row && row.family === 'IPv4' && !row.internal)?.address
  assert.ok(nonloopback, 'A local nonloopback address is required for payload-free isolation checks')
  const receipt = { version: 1, scope: 'synthetic-only-macos-containment-preflight', platform: process.platform,
    arch: process.arch, node: process.versions.node, profileSha256: hash(profileFile), probeSha256: hash(probeFile),
    parameters, nonceSha256, childEnvironmentNames: Object.keys(env).sort(), passed: false, productLaunched: false }
  let external, local
  try {
    external = await listener(nonloopback); local = await listener('127.0.0.1')
    await timeout(new Promise((resolve, reject) => {
      const socket = net.connect(external.port, nonloopback, () => { socket.end(); resolve() }); socket.once('error', reject)
    }), 5000, 'Unrestricted local positive control')
    await poll(() => external.count() === 1, 3000, 'Local positive connection count'); external.reset()
    const deniedCanary = path.join(canaryRoot, 'synthetic-credential.txt')
    const allowedCanary = path.join(disposable, 'synthetic-allowed.txt')
    const symlinkCanary = path.join(disposable, 'synthetic-redirect.txt')
    const keychainCanary = path.join(disposable, 'Keychains', 'synthetic.txt')
    fs.writeFileSync(deniedCanary, 'synthetic-secret-no-real-credential\n', { mode: 0o600 })
    fs.writeFileSync(allowedCanary, 'synthetic-allowed\n')
    fs.symlinkSync(deniedCanary, symlinkCanary)
    fs.mkdirSync(path.dirname(keychainCanary)); fs.writeFileSync(keychainCanary, 'synthetic-keychain\n')
    assert.equal(fs.readFileSync(deniedCanary, 'utf8'), 'synthetic-secret-no-real-credential\n')
    assert.equal(fs.readFileSync(symlinkCanary, 'utf8'), 'synthetic-secret-no-real-credential\n')
    assert.equal(fs.readFileSync(keychainCanary, 'utf8'), 'synthetic-keychain\n')
    const dataVolumeCanary = '/System/Volumes/Data' + deniedCanary
    assert.equal(fs.statSync(dataVolumeCanary).ino, fs.statSync(deniedCanary).ino, 'Data-volume alias is not the same synthetic canary')
    assert.equal(fs.statSync(dataVolumeCanary).dev, fs.statSync(deniedCanary).dev)
    const config = { loopbackPort: local.port, nonloopback, nonloopbackPort: external.port,
      allowedCanary, deniedCanary, symlinkCanary, keychainCanary, dataVolumeCanary, home }
    fs.writeFileSync(configPath, JSON.stringify(config))
    fs.copyFileSync(profileFile, path.join(output, 'profile.sb'))
    sandboxArgs(parameters, python) // Validate before spawn.
    const child = ownedSpawn('/usr/bin/sandbox-exec', sandboxArgs(parameters, python, ['-I', probeFile, configPath]), { env, cwd: home })
    let probeFailure
    receipt.directProcess = { pid: child.child.pid }
    try {
      const result = await timeout(child.done, 25000, 'Direct containment control')
      receipt.directProcess = { pid: child.child.pid, code: result.code, signal: result.signal, stderr: result.stderr }
      assert.equal(result.code, 0, result.stderr)
      receipt.probes = JSON.parse(result.stdout)
      assert.equal(receipt.probes.direct.pid, child.child.pid)
      validateProbes(receipt.probes, process.pid, nonceSha256)
    } catch (error) { probeFailure = error; throw error }
    finally {
      const cleanupFailure = await settleOwned(child.stop, receipt.directProcess)
      if (cleanupFailure && !probeFailure) throw cleanupFailure
    }
    assert.equal(external.count(), 0)
    receipt.disconnect = await disconnectControl({ parameters, env, python, configPath, disposable })
    validateProbes(receipt.disconnect.probes, receipt.disconnect.pid, nonceSha256)
    assert.equal(external.count(), 0)
    assert.equal(local.count(), 4)
    receipt.nonloopbackConnections = external.count()
    receipt.loopbackConnections = local.count()
    receipt.unrestrictedNonloopbackControl = true
    receipt.passed = true
    validateReceipt(receipt)
  } catch (error) {
    receipt.passed = false
    receipt.error = String(error?.stack || error).slice(0, 12000)
    throw error
  } finally {
    receipt.nonloopbackConnections = external?.count()
    receipt.loopbackConnections = local?.count()
    const closing = await Promise.allSettled([external?.close(), local?.close()])
    const failures = closing.filter(item => item.status === 'rejected')
    if (failures.length) {
      receipt.passed = false
      receipt.cleanupErrors = failures.map(item => String(item.reason).slice(0, 1000))
    }
    fs.writeFileSync(path.join(output, 'containment-receipt.json'), JSON.stringify(receipt, null, 2))
    // Preserve synthetic profile, canaries, scripts and raw receipt for review, not real secrets.
    if (failures.length) throw new Error('Containment control server cleanup failed')
  }
  return receipt
}

function validateReceipt(receipt) {
  assert.equal(receipt.version, 1)
  assert.equal(receipt.scope, 'synthetic-only-macos-containment-preflight')
  assert.equal(receipt.platform, 'darwin')
  assert.equal(receipt.passed, true)
  assert.equal(receipt.productLaunched, false)
  assert.equal(receipt.profileSha256, hash(profileFile))
  assert.equal(receipt.probeSha256, hash(probeFile))
  assert.match(receipt.nonceSha256, /^[0-9a-f]{64}$/)
  assert.equal(receipt.directProcess.ownedTreeSettled, true)
  assert.equal(receipt.disconnect.ownedTreeSettled, true)
  assert.equal(receipt.directProcess.pid, receipt.probes.direct.pid)
  assert.equal(receipt.directProcess.code, 0)
  assert.equal(receipt.directProcess.signal, null)
  assert.equal(receipt.unrestrictedNonloopbackControl, true)
  assert.equal(receipt.nonloopbackConnections, 0)
  assert.equal(receipt.loopbackConnections, 4)
  validateProbes(receipt.probes, undefined, receipt.nonceSha256)
  validateProbes(receipt.disconnect.probes, receipt.disconnect.pid, receipt.nonceSha256)
  for (const key of ['beforeEntry', 'nonceVerified', 'pidVerified', 'executableVerified', 'debuggerDisconnected', 'entryExecutedAfterDisconnect']) assert.equal(receipt.disconnect[key], true)
  assert.equal(receipt.disconnect.explicitResume, false)
  assert.equal(receipt.disconnect.reason, 'Break on start')
  assert.equal(receipt.disconnect.exitCode, 0)
  assert.equal(receipt.error, undefined)
  assert.equal(receipt.cleanupErrors, undefined)
}

module.exports = { settleOwned, poll, validateReceipt, machServices, profileFile, probeFile, canonicalDirectory, sandboxArgs, childEnvironment, validateProbes, preflight, protocol, disconnectControl }
if (require.main === module) {
  const [product, harness, python, output] = process.argv.slice(2)
  preflight({ product, harness, python, output }).then(() => console.log('Synthetic containment controls passed; no product was launched.'))
    .catch(error => {
      console.error(error)
      if (output && fs.existsSync(output)) fs.writeFileSync(path.join(output, 'failure.json'), JSON.stringify({ productLaunched: false, error: String(error.stack || error) }, null, 2))
      process.exitCode = 1
    })
}
