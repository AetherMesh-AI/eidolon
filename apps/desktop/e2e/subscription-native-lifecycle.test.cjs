const assert = require('node:assert/strict')
const { execFile: realExecFile } = require('node:child_process')
const { EventEmitter } = require('node:events')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')
const vm = require('node:vm')
const ts = require('typescript')

const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, 'subscription-native-lifecycle.ts'), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.CommonJS }
}).outputText
const root = { pid: 1234, birth: 12.5, uid: 501 }
const other = { pid: 2345, birth: 13.5, uid: 501 }
const success = { ok: true, reason: 'none', capturedCount: 2, initialLive: 1, remainingLive: 0, rootExited: true }
function harness(responses = [{ ok: true, root }, { ok: true, identities: [root, other] }, success], exec = undefined) {
  const requests = []
  const child = {
    pid: root.pid,
    exitCode: null,
    signalCode: null,
    kill: () => assert.fail('Never signal the application')
  }
  const exports = {}
  const run =
    exec ||
    ((_python, args, options, callback) => {
      const stdin = new EventEmitter()
      stdin.end = value => {
        requests.push({ args, options, input: JSON.parse(value) })
        const next = responses.shift()
        queueMicrotask(() => {
          if (typeof next === 'function') next(callback, child)
          else callback(null, JSON.stringify(next))
        })
      }
      return { stdin }
    })
  vm.runInNewContext(code, {
    exports,
    process: { getuid: () => root.uid },
    Date,
    Error,
    Set,
    JSON,
    require: name => (name === 'node:child_process' ? { execFile: run } : assert.fail(name))
  })
  return {
    child,
    requests,
    cleanup: exports.cleanupSubscriptionFixture,
    report: exports.cleanupAndReport,
    lifecycle: exports.createSubscriptionLifecycle(child, { python: 'python3', script: 'probe.py', cwd: __dirname }),
    create: exports.createSubscriptionLifecycle
  }
}
const plain = value => JSON.parse(JSON.stringify(value))

test('exact live owner is bound, captured before close, and waited with private identities', async () => {
  const h = harness()
  await h.lifecycle.bind()
  await h.lifecycle.capture()
  h.child.exitCode = 0
  await h.lifecycle.wait()
  assert.deepEqual(
    h.requests.map(x => x.input.mode),
    ['root', 'capture', 'wait']
  )
  assert.deepEqual(
    h.requests.map(x => x.options.timeout),
    [2000, 2000, 6000]
  )
  assert.deepEqual(plain(h.requests[0].args), ['-I', 'probe.py'])
  assert.equal(h.requests[2].input.identities.length, 2)
  assert.ok(h.requests[2].input.deadlineEpochMs <= Date.now() + 6000)
  const report = plain(h.lifecycle.diagnostic())
  assert.deepEqual(report, {
    phase: 'settled',
    reason: 'none',
    capturedCount: 2,
    initialLive: 1,
    remainingLive: 0,
    rootExited: true
  })
  for (const forbidden of ['pid', 'birth', 'uid', '1234', '2345'])
    assert.equal(JSON.stringify(report).includes(forbidden), false)
})

test('original owned handle must be live before and after root identity capture', async () => {
  const before = harness()
  before.child.exitCode = 0
  await assert.rejects(before.lifecycle.bind(), /identity_changed/)
  assert.equal(before.requests.length, 0)
  const after = harness([
    (callback, child) => {
      child.signalCode = 'SIGTERM'
      callback(null, JSON.stringify({ ok: true, root }))
    }
  ])
  await assert.rejects(after.lifecycle.bind(), /identity_changed/)
})

test('root replies reject partial, extra, different owner and malformed identities', async () => {
  for (const result of [
    null,
    {},
    { ok: true },
    { ok: true, root, extra: 'private' },
    { ok: true, root: { ...root, uid: 502 } },
    { ok: true, root: { ...root, pid: 999 } },
    { ok: true, root: { ...root, birth: 'secret' } },
    { ok: false, reason: 'private exception text' }
  ]) {
    const h = harness([result])
    await assert.rejects(h.lifecycle.bind())
    assert.equal(JSON.stringify(h.lifecycle.diagnostic()).includes('private'), false)
  }
})

test('capture rejects identity substitution, duplicate PID, overflow, extra and partial rows', async () => {
  const cases = [
    [],
    [{ ...root, birth: 99 }],
    [root, { ...other, uid: 502 }],
    [root, root],
    Array.from({ length: 66 }, (_, i) => ({ ...root, pid: root.pid + i })),
    [root, { pid: 2345 }]
  ]
  for (const identities of cases) {
    const h = harness([
      { ok: true, root },
      { ok: true, identities }
    ])
    await h.lifecycle.bind()
    await assert.rejects(h.lifecycle.capture(), /invalid/)
  }
  const h = harness([
    { ok: true, root },
    { ok: true, identities: [root], extra: 1 }
  ])
  await h.lifecycle.bind()
  await assert.rejects(h.lifecycle.capture(), /invalid/)
})

test('capturing after the original child has exited refuses without inspecting reused PID', async () => {
  const h = harness()
  await h.lifecycle.bind()
  h.child.exitCode = 0
  await assert.rejects(h.lifecycle.capture(), /identity_changed/)
  assert.equal(h.requests.length, 1)
})

test('helper timeout is fixed-category and survives later cleanup errors', async () => {
  const h = harness([callback => callback(Object.assign(new Error('private path'), { killed: true }))])
  await assert.rejects(h.lifecycle.bind(), /timeout/)
  await assert.rejects(h.lifecycle.wait())
  assert.equal(h.lifecycle.diagnostic().reason, 'timeout')
  assert.equal(JSON.stringify(h.lifecycle.diagnostic()).includes('private'), false)
})

test('wait refuses survivors, inspection uncertainty, malformed summaries and success contradictions', async () => {
  for (const result of [
    { ...success, ok: false, reason: 'timeout', remainingLive: 1, rootExited: false },
    { ...success, ok: false, reason: 'inspection_denied', remainingLive: null, rootExited: null },
    { ...success, remainingLive: 1 },
    { ...success, rootExited: false },
    { ...success, initialLive: null },
    { ...success, capturedCount: 3 },
    { ...success, extra: 'private' },
    { ok: false, reason: 'timeout' }
  ]) {
    const h = harness([{ ok: true, root }, { ok: true, identities: [root, other] }, result])
    await h.lifecycle.bind()
    await h.lifecycle.capture()
    await assert.rejects(h.lifecycle.wait())
    assert.equal(h.lifecycle.diagnostic().phase, 'refused')
  }
})

test('cleanup waits for captured descendants before one removal and closes in order', async () => {
  const h = harness()
  const calls = []
  let release
  const barrier = new Promise(resolve => {
    release = resolve
  })
  const pending = h.cleanup({
    capture: async () => calls.push('capture'),
    observer: async () => calls.push('observer'),
    app: async () => calls.push('app'),
    wait: async () => {
      calls.push('wait')
      await barrier
    },
    mock: async () => calls.push('mock'),
    remove: () => calls.push('remove')
  })
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(calls, ['capture', 'observer', 'app', 'wait'])
  release()
  await pending
  assert.deepEqual(calls, ['capture', 'observer', 'app', 'wait', 'mock', 'remove'])
})

test('every uncertain close path retains first error, closes other resources and never removes', async () => {
  const h = harness()
  for (const failed of ['capture', 'observer', 'app', 'wait', 'mock']) {
    const first = new Error(failed)
    const calls = []
    const steps = Object.fromEntries(
      ['capture', 'observer', 'app', 'wait', 'mock'].map(name => [
        name,
        async () => {
          calls.push(name)
          if (name === failed) throw first
          if (calls.includes(failed) && name === 'mock') throw new Error('secondary')
        }
      ])
    )
    await assert.rejects(
      h.cleanup({ ...steps, remove: () => assert.fail('Do not remove on uncertain close') }),
      error => error === first
    )
    assert.deepEqual(calls, ['capture', 'observer', 'app', 'wait', 'mock'])
  }
})

test('removal failure propagates unchanged with exactly one attempt', async () => {
  const h = harness()
  const error = Object.assign(new Error('synthetic'), { code: 'ENOTEMPTY' })
  let attempts = 0
  const noop = async () => {}
  await assert.rejects(
    h.cleanup({
      capture: noop,
      observer: noop,
      app: noop,
      wait: noop,
      mock: noop,
      remove: () => {
        attempts++
        throw error
      }
    }),
    value => value === error
  )
  assert.equal(attempts, 1)
})

test('hung helper inspection is bounded and terminates only its owned probe process', { timeout: 10000 }, async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'subscription-lifecycle-proof-'))
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }))
  const script = path.join(directory, 'hung.py')
  fs.writeFileSync(script, 'import sys,time\nsys.stdin.read()\ntime.sleep(60)\n')
  const h = harness([], realExecFile)
  const lifecycle = h.create(h.child, { python: 'python3', script, cwd: directory })
  const start = Date.now()
  await assert.rejects(lifecycle.bind(), /timeout/)
  assert.ok(Date.now() - start >= 1900)
  assert.ok(Date.now() - start < 8000)
  assert.equal(lifecycle.diagnostic().reason, 'timeout')
})

test('malformed stdout, missing stdin and stdin error refuse without exposing payloads', async () => {
  for (const response of ['{', '[]', 'null', 'private raw output']) {
    const h = harness([callback => callback(null, response)])
    await assert.rejects(h.lifecycle.bind(), /invalid/)
  }
  const missing = harness([], (_python, _args, _options, _callback) => ({}))
  await assert.rejects(missing.lifecycle.bind(), /inspection_failed/)
  const broken = harness([], (_python, _args, _options, _callback) => {
    const stdin = new EventEmitter()
    stdin.end = () => queueMicrotask(() => stdin.emit('error', new Error('private stream message')))
    return { stdin }
  })
  await assert.rejects(broken.lifecycle.bind(), /inspection_failed/)
  assert.equal(JSON.stringify(broken.lifecycle.diagnostic()).includes('private'), false)
})

test('maximum64 captured descendants plus root is accepted without expanding the bound', async () => {
  const identities = Array.from({ length: 65 }, (_, i) => ({ ...root, pid: root.pid + i }))
  const h = harness([
    { ok: true, root },
    { ok: true, identities },
    { ...success, capturedCount: 65 }
  ])
  await h.lifecycle.bind()
  await h.lifecycle.capture()
  await h.lifecycle.wait()
  assert.equal(h.lifecycle.diagnostic().capturedCount, 65)
})

test('exactly one receipt follows success or failure; missing success receipt fails and original errors win', async () => {
  const h = harness()
  const cleanupError = new Error('cleanup')
  const reportError = new Error('attachment')
  for (const cleanupFails of [false, true])
    for (const reportFails of [false, true]) {
      const calls = []
      const result = h.report(
        async () => {
          calls.push('cleanup')
          if (cleanupFails) throw cleanupError
        },
        async () => {
          calls.push('receipt')
          if (reportFails) throw reportError
        }
      )
      if (cleanupFails || reportFails)
        await assert.rejects(result, value => value === (cleanupFails ? cleanupError : reportError))
      else await result
      assert.deepEqual(calls, ['cleanup', 'receipt'])
    }
})


test('capture diagnostic narrows refusal without authorizing cleanup or changing receipts', async () => {
  const h = harness([{ ok: true, root }, {
    ok: false, reason: 'identity_changed',
    diagnostic: { check: 'snapshot', scope: 'descendant', component: 'birth' }
  }])
  await h.lifecycle.bind()
  const calls = []
  await assert.rejects(h.cleanup({
    capture: () => h.lifecycle.capture(),
    observer: async () => calls.push('observer'),
    app: async () => calls.push('app'),
    wait: async () => { calls.push('wait'); await h.lifecycle.wait() },
    mock: async () => calls.push('mock'),
    remove: async () => calls.push('remove')
  }), /^Error: subscription_fixture_lifecycle_identity_changed \[snapshot\/descendant\/birth\]$/)
  assert.deepEqual(calls, ['observer', 'app', 'wait', 'mock'])
  assert.deepEqual(plain(h.lifecycle.diagnostic()), {
    phase: 'refused', reason: 'identity_changed', capturedCount: 0,
    initialLive: null, remainingLive: null, rootExited: null
  })
  assert.deepEqual(h.requests.map(x => x.input.mode), ['root', 'capture'])
})

test('diagnostic replies reject extra, unknown and misplaced fields without leaking values', async () => {
  const detail = { check: 'verify', scope: 'root', component: 'uid' }
  for (const diagnostic of [null, {}, { ...detail, pid: 1234 },
    { ...detail, check: 'private/path' }, { ...detail, scope: 1234 },
    { ...detail, component: { secret: 'private' } }]) {
    const h = harness([{ ok: true, root }, { ok: false, reason: 'identity_changed', diagnostic }])
    await h.lifecycle.bind()
    await assert.rejects(h.lifecycle.capture(), /^Error: subscription_fixture_lifecycle_invalid$/)
    assert.equal(JSON.stringify(h.lifecycle.diagnostic()).includes('private'), false)
  }
  const rootReply = harness([{ ok: false, reason: 'identity_changed', diagnostic: detail }])
  await assert.rejects(rootReply.lifecycle.bind(), /^Error: subscription_fixture_lifecycle_invalid$/)
  const wrongReason = harness([{ ok: true, root }, { ok: false, reason: 'timeout', diagnostic: detail }])
  await wrongReason.lifecycle.bind()
  await assert.rejects(wrongReason.lifecycle.capture(), /^Error: subscription_fixture_lifecycle_invalid$/)
})
