/** Fixed synthetic sentinels only. Collection success is never containment acceptance. */
const assert = require('node:assert/strict')
const { execFileSync } = require('node:child_process')
const { createHash, randomUUID } = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { canonicalDirectory, sandboxArgs, childEnvironment, ownedSpawn, settleOwned, profileFile } = require('./packaged-containment.cjs')
const hash = file => createHash('sha256').update(fs.readFileSync(file)).digest('hex')
const sentinels = {
  true: ['', ''], printf: ['EIDOLON_PRINTF_SENTINEL\n', ''],
  node: ['EIDOLON_NODE_STDOUT\n', 'EIDOLON_NODE_STDERR\n'],
  python: ['EIDOLON_PYTHON_STDOUT\n', 'EIDOLON_PYTHON_STDERR\n'],
}
const names = Object.keys(sentinels)

function validateStartupMatrix(receipt) {
  assert.equal(receipt.version, 1)
  assert.equal(receipt.scope, 'synthetic-only-fixed-startup-diagnostic')
  assert.equal(receipt.collected, true)
  assert.equal(receipt.productLaunched, false)
  assert.equal(receipt.nativeContainmentAccepted, false)
  assert.equal(receipt.profileSha256, hash(profileFile))
  assert.match(receipt.nonce, /^[a-f0-9-]{36}$/)
  assert.deepEqual(receipt.childEnvironmentNames, ['EIDOLON_CONTAINMENT_NONCE', 'HOME', 'LANG', 'PATH', 'PYTHONDONTWRITEBYTECODE', 'TMPDIR'])
  assert.deepEqual(receipt.children.map(row => row.name), names)
  for (const row of receipt.children) {
    assert.ok(Number.isSafeInteger(row.pid) && row.pid > 0)
    assert.equal(row.ownedTreeSettled, true)
    assert.equal(row.cleanupError, undefined)
    assert.equal(row.error, undefined)
    assert.ok((Number.isInteger(row.code) && row.signal === null) ||
      (row.code === null && typeof row.signal === 'string' && row.signal.startsWith('SIG')))
    assert.deepEqual([row.expectedStdout, row.expectedStderr], sentinels[row.name])
    assert.equal(typeof row.stdout, 'string')
    assert.equal(typeof row.stderr, 'string')
    assert.equal(row.sentinelObserved, row.code === 0 && row.stdout === row.expectedStdout && row.stderr === row.expectedStderr)
  }
}

async function startupMatrix({ product, harness, python, output }) {
  assert.equal(process.platform, 'darwin', 'Native macOS only')
  fs.accessSync('/usr/bin/sandbox-exec', fs.constants.X_OK)
  product = canonicalDirectory(product); harness = canonicalDirectory(harness); output = canonicalDirectory(output)
  assert.ok(output !== product && output !== harness && !output.startsWith(product + '/') && !output.startsWith(harness + '/'))
  const disposable = fs.realpathSync(fs.mkdtempSync(path.join(output, 'startup-')))
  const home = path.join(disposable, 'home'); fs.mkdirSync(home, { mode: 0o700 })
  const env = childEnvironment(home, randomUUID())
  const pythonRuntime = fs.realpathSync(execFileSync(python, ['-I', '-c', 'import sys; print(sys.base_prefix)'], { env, encoding: 'utf8', timeout: 5000 }).trim())
  const parameters = { PRODUCT: product, HARNESS: harness, PYTHON_RUNTIME: pythonRuntime,
    NODE_RUNTIME: fs.realpathSync(path.dirname(path.dirname(process.execPath))), DISPOSABLE: disposable }
  assert.ok(fs.realpathSync(python).startsWith(pythonRuntime + '/'), 'Python executable is outside its declared runtime')
  // Fixed commands only. None reads a credential, imports product code, or opens a socket.
  const cases = [
    { name: 'true', command: '/usr/bin/true', args: [], stdout: '', stderr: '' },
    { name: 'printf', command: '/usr/bin/printf', args: ['EIDOLON_PRINTF_SENTINEL\n'], stdout: 'EIDOLON_PRINTF_SENTINEL\n', stderr: '' },
    { name: 'node', command: process.execPath,
      args: ['-e', 'process.stdout.write("EIDOLON_NODE_STDOUT\\n");process.stderr.write("EIDOLON_NODE_STDERR\\n")'],
      stdout: 'EIDOLON_NODE_STDOUT\n', stderr: 'EIDOLON_NODE_STDERR\n' },
    { name: 'python', command: python,
      args: ['-I', '-c', 'import sys;sys.stdout.write("EIDOLON_PYTHON_STDOUT\\n");sys.stdout.flush();sys.stderr.write("EIDOLON_PYTHON_STDERR\\n");sys.stderr.flush()'],
      stdout: 'EIDOLON_PYTHON_STDOUT\n', stderr: 'EIDOLON_PYTHON_STDERR\n' },
  ]
  const receipt = { version: 1, scope: 'synthetic-only-fixed-startup-diagnostic', platform: process.platform, arch: process.arch,
    profileSha256: hash(profileFile), parameters, nonce: env.EIDOLON_CONTAINMENT_NONCE, childEnvironmentNames: Object.keys(env).sort(),
    collected: false, productLaunched: false, nativeContainmentAccepted: false, children: [] }
  fs.copyFileSync(profileFile, path.join(output, 'profile.sb'))
  try {
    for (const control of cases) {
      const row = { name: control.name, requestedExecutable: control.command, physicalExecutable: fs.realpathSync(control.command),
        expectedStdout: control.stdout, expectedStderr: control.stderr, startedAt: new Date().toISOString() }
      receipt.children.push(row)
      const launched = ownedSpawn('/usr/bin/sandbox-exec', sandboxArgs(parameters, control.command, control.args), { env, cwd: home })
      row.pid = launched.child.pid
      let timer, failure
      try {
        const result = await Promise.race([launched.done, new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error('Fixed startup child timed out')), 10000)
        })])
        Object.assign(row, result)
        row.sentinelObserved = result.code === 0 && result.stdout === control.stdout && result.stderr === control.stderr
      } catch (error) {
        row.error = String(error.stack || error).slice(0, 2000)
        failure = error
      } finally {
        clearTimeout(timer)
        const cleanup = await settleOwned(launched.stop, row)
        // Retain the final exit/signal and output even when the bounded wait failed.
        if (row.ownedTreeSettled) Object.assign(row, await launched.done)
        row.settledAt = new Date().toISOString()
        failure ||= cleanup
      }
      if (failure) throw failure
    }
    receipt.collected = true
    validateStartupMatrix(receipt)
  } catch (error) {
    receipt.collected = false
    receipt.error = String(error.stack || error).slice(0, 4000)
    throw error
  } finally {
    fs.writeFileSync(path.join(output, 'startup-diagnostic.json'), JSON.stringify(receipt, null, 2))
  }
  return receipt
}

module.exports = { validateStartupMatrix, startupMatrix }
if (require.main === module) {
  const [product, harness, python, output] = process.argv.slice(2)
  startupMatrix({ product, harness, python, output }).then(() => console.log('Fixed startup diagnostics collected. Native containment is not accepted; no product was launched.'))
    .catch(error => { console.error(error); process.exitCode = 1 })
}
