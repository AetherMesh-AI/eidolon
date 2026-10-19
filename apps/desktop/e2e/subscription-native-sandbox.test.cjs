const assert = require('node:assert/strict')
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')
const vm = require('node:vm')
const ts = require('typescript')

// Compile actual checked-in functions. No duplicated sandbox implementation.
const fixturesSource = fs.readFileSync(path.join(__dirname, 'fixtures.ts'), 'utf8')
const parsed = ts.createSourceFile('fixtures.ts', fixturesSource, ts.ScriptTarget.Latest, true)
const matches = parsed.statements.filter(node => ts.isFunctionDeclaration(node) && node.name?.text === 'createSandbox')
assert.equal(matches.length, 1)
const factorySource = matches[0].getText(parsed).replace(/^export /, '')
const compile = source =>
  ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2023,
      module: ts.ModuleKind.CommonJS,
      esModuleInterop: true
    }
  }).outputText
const factoryCode = compile(`(${factorySource})`)
const helperCode = compile(fs.readFileSync(path.join(__dirname, 'subscription-native-sandbox.ts'), 'utf8'))

function harness(overrides = {}) {
  const io = { ...fs, ...overrides }
  const createSandbox = vm.runInNewContext(factoryCode, {
    fs: io,
    os,
    path,
    Math,
    JSON
  })
  const exports = {}
  vm.runInNewContext(helperCode, {
    exports,
    Error,
    process,
    require: name =>
      name === 'node:fs'
        ? io
        : name === 'node:path'
          ? path
          : name === './fixtures'
            ? { createSandbox }
            : assert.fail(`Unexpected import: ${name}`)
  })
  return {
    createSandbox,
    createSubscriptionSandbox: exports.createSubscriptionSandbox
  }
}

function workspace(t) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'subscription-sandbox-proof-')))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  return root
}
const posix = {
  skip: typeof process.getuid !== 'function' && 'Owned POSIX-directory fixture is macOS/Linux only'
}
const container = root => path.join(root, '.subscription-native-sandboxes')
const json = p => JSON.parse(fs.readFileSync(p, 'utf8'))

function assertLayout(sandbox) {
  assert.equal(path.dirname(sandbox.hermesHome), sandbox.root)
  assert.equal(path.dirname(sandbox.userDataDir), sandbox.root)
  assert.deepEqual(json(path.join(sandbox.userDataDir, 'window-state.json')), {
    x: 0,
    y: 0,
    width: 1220,
    height: 800,
    isMaximized: false
  })
  assert.deepEqual(json(path.join(sandbox.userDataDir, 'zoom-state.json')), {
    zoomLevel: 0
  })
}

test('shared factory keeps default temporary placement and exact window/zoom layout', t => {
  const sandbox = harness().createSandbox('unchanged-default')
  t.after(() => sandbox.cleanup())
  assert.equal(path.dirname(sandbox.root), os.tmpdir())
  assertLayout(sandbox)
  sandbox.cleanup()
  sandbox.cleanup()
  assert.equal(fs.existsSync(sandbox.root), false)
})

test('explicit factory parent changes only placement and keeps layout/cleanup parity', t => {
  const root = workspace(t)
  const sandbox = harness().createSandbox('explicit-parent', root)
  assert.equal(path.dirname(sandbox.root), root)
  assertLayout(sandbox)
  sandbox.cleanup()
  assert.equal(fs.existsSync(sandbox.root), false)
  assert.equal(fs.existsSync(root), true)
})

test('native wrapper creates private unique homes under stable workspace and cleans only its own root', posix, t => {
  const root = workspace(t)
  const h = harness()
  const first = h.createSubscriptionSandbox(root)
  const second = h.createSubscriptionSandbox(root)
  const marker = path.join(second.root, 'retained-sibling')
  fs.writeFileSync(marker, 'keep')
  assert.notEqual(first.root, second.root)
  for (const sandbox of [first, second]) {
    assertLayout(sandbox)
    assert.equal(path.dirname(sandbox.root), container(root))
    assert.equal(fs.realpathSync(sandbox.root), sandbox.root)
    assert.equal(fs.statSync(sandbox.root).mode & 0o7777, 0o700)
    assert.equal(fs.statSync(sandbox.hermesHome).mode & 0o7777, 0o700)
  }
  assert.equal(fs.statSync(container(root)).mode & 0o7777, 0o700)
  first.root = second.root // Cleanup authority is captured, not returned mutable fields.
  first.cleanup()
  first.cleanup()
  assert.equal(fs.readFileSync(marker, 'utf8'), 'keep')
  second.cleanup()
  assert.equal(fs.existsSync(container(root)), true)
  assert.deepEqual(fs.readdirSync(container(root)), [])
})

test('simultaneous child processes initialize one container and distinct invocation roots', posix, async t => {
  const root = workspace(t)
  const program = `const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),vm=require('node:vm');
const createSandbox=vm.runInNewContext(${JSON.stringify(factoryCode)},{fs,os,path,Math,JSON});
const exports={};vm.runInNewContext(${JSON.stringify(helperCode)},{exports,Error,process,require:n=>n==='node:fs'?fs:n==='node:path'?path:{createSandbox}});
const sandbox=exports.createSubscriptionSandbox(process.argv[1]);process.stdout.write(JSON.stringify({root:sandbox.root,home:sandbox.hermesHome}));`
  const child = () =>
    new Promise((resolve, reject) => {
      const p = spawn(process.execPath, ['-e', program, root], {
        stdio: ['ignore', 'pipe', 'pipe']
      })
      let stdout = '',
        stderr = ''
      p.stdout.on('data', b => {
        stdout += b
      })
      p.stderr.on('data', b => {
        stderr += b
      })
      p.once('error', reject)
      p.once('close', code => (code === 0 ? resolve(JSON.parse(stdout)) : reject(new Error(stderr))))
    })
  const results = await Promise.all([child(), child()])
  assert.notEqual(results[0].root, results[1].root)
  for (const result of results) {
    assert.equal(path.dirname(result.root), container(root))
    assert.equal(fs.statSync(result.home).mode & 0o7777, 0o700)
  }
  assert.equal(fs.readdirSync(container(root)).length, 2)
})

for (const mode of [0o755, 0o777, 0o1700]) {
  test(`preexisting container mode ${mode.toString(8)} is refused without repair or deletion`, posix, t => {
    const root = workspace(t)
    fs.mkdirSync(container(root), { mode: 0o700 })
    fs.chmodSync(container(root), mode)
    assert.throws(() => harness().createSubscriptionSandbox(root), /subscription_sandbox_unsafe_directory/)
    assert.equal(fs.statSync(container(root)).mode & 0o7777, mode)
    assert.deepEqual(fs.readdirSync(container(root)), [])
  })
}

test('foreign-owner container is refused before allocating a root', posix, t => {
  const root = workspace(t)
  fs.mkdirSync(container(root), { mode: 0o700 })
  let allocated = 0
  const h = harness({
    lstatSync: location => {
      const value = fs.lstatSync(location)
      if (location === container(root)) value.uid = process.getuid() + 1
      return value
    },
    mkdtempSync: (...args) => {
      allocated++
      return fs.mkdtempSync(...args)
    }
  })
  assert.throws(() => h.createSubscriptionSandbox(root), /subscription_sandbox_unsafe_directory/)
  assert.equal(allocated, 0)
})

test('workspace and container symlinks are refused without touching their targets', posix, t => {
  const root = workspace(t)
  const target = path.join(root, 'target')
  fs.mkdirSync(target, { mode: 0o700 })
  fs.writeFileSync(path.join(target, 'marker'), 'keep')
  const alias = path.join(root, 'workspace-alias')
  fs.symlinkSync(target, alias)
  assert.throws(() => harness().createSubscriptionSandbox(alias), /subscription_sandbox_unsafe_directory/)
  fs.symlinkSync(target, container(root))
  assert.throws(() => harness().createSubscriptionSandbox(root), /subscription_sandbox_unsafe_directory/)
  assert.equal(fs.readFileSync(path.join(target, 'marker'), 'utf8'), 'keep')
  assert.deepEqual(fs.readdirSync(target), ['marker'])
})

for (const kind of ['workspace', 'parent', 'root']) {
  test(`cleanup refuses ${kind} inode replacement and leaves both trees untouched`, posix, t => {
    const outer = workspace(t)
    const root = path.join(outer, 'workspace')
    fs.mkdirSync(root, { mode: 0o700 })
    const sandbox = harness().createSubscriptionSandbox(root)
    const selected = kind === 'workspace' ? root : kind === 'parent' ? container(root) : sandbox.root
    const saved = selected + '.saved'
    fs.renameSync(selected, saved)
    fs.mkdirSync(selected, { mode: 0o700 })
    const marker = path.join(selected, 'replacement-marker')
    fs.writeFileSync(marker, 'keep')
    assert.throws(() => sandbox.cleanup(), /subscription_sandbox_identity_changed/)
    assert.equal(fs.readFileSync(marker, 'utf8'), 'keep')
    assert.equal(fs.existsSync(saved), true)
    fs.rmSync(selected, { recursive: true })
    fs.renameSync(saved, selected)
    sandbox.cleanup()
    assert.equal(fs.existsSync(sandbox.root), false)
  })
}

for (const kind of ['parent', 'root']) {
  test(`cleanup refuses ${kind} symlink substitution without deleting outside data`, posix, t => {
    const root = workspace(t)
    const sandbox = harness().createSubscriptionSandbox(root)
    const selected = kind === 'parent' ? container(root) : sandbox.root
    const saved = selected + '.saved'
    const target = path.join(root, 'outside')
    fs.mkdirSync(target, { mode: 0o700 })
    fs.writeFileSync(path.join(target, 'marker'), 'keep')
    fs.renameSync(selected, saved)
    fs.symlinkSync(target, selected)
    assert.throws(() => sandbox.cleanup(), /subscription_sandbox_unsafe_directory/)
    assert.equal(fs.readFileSync(path.join(target, 'marker'), 'utf8'), 'keep')
    fs.unlinkSync(selected)
    fs.renameSync(saved, selected)
    sandbox.cleanup()
  })
}

test('cleanup refuses changed owner or mode, retains siblings and never follows child symlinks', posix, t => {
  const root = workspace(t)
  let foreign = false
  const h = harness({
    lstatSync: location => {
      const value = fs.lstatSync(location)
      if (foreign && path.dirname(location) === container(root)) value.uid = process.getuid() + 1
      return value
    }
  })
  const sandbox = h.createSubscriptionSandbox(root)
  foreign = true
  assert.throws(() => sandbox.cleanup(), /subscription_sandbox_unsafe_directory/)
  foreign = false
  fs.chmodSync(sandbox.root, 0o755)
  assert.throws(() => sandbox.cleanup(), /subscription_sandbox_unsafe_directory/)
  fs.chmodSync(sandbox.root, 0o700)
  const outside = path.join(root, 'outside')
  fs.mkdirSync(outside)
  fs.writeFileSync(path.join(outside, 'marker'), 'keep')
  fs.symlinkSync(outside, path.join(sandbox.root, 'child-link'))
  sandbox.cleanup()
  assert.equal(fs.readFileSync(path.join(outside, 'marker'), 'utf8'), 'keep')
})

for (const stage of ['layout-write', 'root-validation']) {
  test(`initialization failure at ${stage} removes no unrelated or unverified path`, posix, t => {
    const root = workspace(t)
    const outside = path.join(root, 'unrelated')
    fs.mkdirSync(outside)
    fs.writeFileSync(path.join(outside, 'marker'), 'keep')
    let removed = 0
    const h = harness({
      rmSync: (...args) => {
        removed++
        return fs.rmSync(...args)
      },
      writeFileSync: (...args) => {
        if (stage === 'layout-write') throw new Error('synthetic_layout_failure')
        return fs.writeFileSync(...args)
      },
      lstatSync: location => {
        const value = fs.lstatSync(location)
        if (stage === 'root-validation' && path.dirname(location) === container(root)) value.uid = process.getuid() + 1
        return value
      }
    })
    assert.throws(
      () => h.createSubscriptionSandbox(root),
      /synthetic_layout_failure|subscription_sandbox_unsafe_directory/
    )
    assert.equal(removed, 0)
    assert.equal(fs.readFileSync(path.join(outside, 'marker'), 'utf8'), 'keep')
    assert.equal(fs.readdirSync(container(root)).length, 1)
  })
}

test('remaining-entry receipt is bounded, closed-category and contains no names', posix, t => {
  const root = workspace(t)
  const sandbox = harness().createSubscriptionSandbox(root)
  fs.mkdirSync(path.join(sandbox.root, 'user-home'))
  fs.mkdirSync(path.join(sandbox.root, 'evidence'))
  fs.mkdirSync(path.join(sandbox.root, 'python-observer'))
  for (let i = 0; i < 70; i++) fs.writeFileSync(path.join(sandbox.root, `private-unknown-${i}`), '')
  const receipt = JSON.parse(JSON.stringify(sandbox.diagnostic()))
  assert.equal(receipt.state, 'observed')
  assert.equal(receipt.truncated, true)
  assert.equal(
    Object.values(receipt.counts).reduce((a, b) => a + b, 0),
    64
  )
  assert.deepEqual(Object.keys(receipt.counts).sort(), [
    'evidence',
    'home',
    'observer',
    'other',
    'userData',
    'userHome'
  ])
  assert.equal(JSON.stringify(receipt).includes('private-unknown'), false)
  assert.equal(JSON.stringify(receipt).includes(root), false)
  sandbox.cleanup()
  assert.equal(sandbox.diagnostic().state, 'removed')
})

test('remaining-entry receipt refuses changed root before enumeration', posix, t => {
  const root = workspace(t)
  let enumerated = false
  const sandbox = harness({
    opendirSync: () => {
      enumerated = true
      assert.fail('Do not enumerate replacement')
    }
  }).createSubscriptionSandbox(root)
  fs.renameSync(sandbox.root, `${sandbox.root}-original`)
  fs.mkdirSync(sandbox.root, { mode: 0o700 })
  assert.equal(sandbox.diagnostic().state, 'identity_unverified')
  assert.equal(enumerated, false)
  assert.throws(sandbox.cleanup, /identity_changed/)
})

test('one ENOTEMPTY refusal is retained with safe surviving categories and no retry', posix, t => {
  const root = workspace(t)
  let attempts = 0
  const error = Object.assign(new Error('synthetic'), { code: 'ENOTEMPTY' })
  const sandbox = harness({
    rmSync: () => {
      attempts++
      throw error
    }
  }).createSubscriptionSandbox(root)
  assert.throws(sandbox.cleanup, value => value === error)
  assert.equal(attempts, 1)
  const receipt = JSON.parse(JSON.stringify(sandbox.diagnostic()))
  assert.equal(receipt.state, 'observed')
  assert.equal(receipt.counts.home, 1)
  assert.equal(receipt.counts.userData, 1)
})

function failedCleanup(t, overrides = {}) {
  const root = workspace(t)
  let removals = 0
  const error = Object.assign(new Error('private error text'), { code: 'ENOTEMPTY' })
  const sandbox = harness({
    ...overrides,
    rmSync: () => {
      removals++
      throw error
    }
  }).createSubscriptionSandbox(root)
  assert.throws(sandbox.cleanup, value => value === error)
  assert.equal(removals, 1)
  return sandbox
}

test('home identity is failure-only and successful receipt shape stays unchanged', posix, t => {
  const sandbox = harness().createSubscriptionSandbox(workspace(t))
  assert.equal(Object.hasOwn(sandbox.diagnostic(), 'homeIdentity'), false)
  sandbox.cleanup()
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.diagnostic())), {
    state: 'removed', truncated: false,
    counts: { home: 0, userData: 0, userHome: 0, evidence: 0, observer: 0, other: 0 }
  })
})

test('failed cleanup reports the same captured home without enumerating inside it', posix, t => {
  const enumerated = []
  const sandbox = failedCleanup(t, {
    opendirSync: location => { enumerated.push(location); return fs.opendirSync(location) }
  })
  const receipt = sandbox.diagnostic()
  assert.equal(receipt.homeIdentity, 'same')
  assert.deepEqual(enumerated, [sandbox.root])
  assert.equal(JSON.stringify(receipt).includes(sandbox.root), false)
  assert.equal(JSON.stringify(receipt).includes('private error text'), false)
})

test('failure identity reports a valid recreated home without inspecting its contents', posix, t => {
  const sandbox = failedCleanup(t)
  fs.renameSync(sandbox.hermesHome, `${sandbox.hermesHome}-old`)
  fs.mkdirSync(sandbox.hermesHome, { mode: 0o700 })
  fs.writeFileSync(path.join(sandbox.hermesHome, 'private-secret-name'), 'private-secret-content')
  const receipt = sandbox.diagnostic()
  assert.equal(receipt.homeIdentity, 'recreated')
  assert.equal(JSON.stringify(receipt).includes('private-secret'), false)
})

for (const mutation of ['symlink', 'file', 'mode', 'owner', 'group']) {
  test(`failure identity refuses unsafe home ${mutation}`, posix, t => {
    let changed = false
    let selected
    const sandbox = failedCleanup(t, {
      lstatSync: location => {
        const result = fs.lstatSync(location)
        if (changed && location === selected && mutation === 'owner') result.uid++
        if (changed && location === selected && mutation === 'group') result.gid++
        return result
      }
    })
    selected = sandbox.hermesHome
    changed = true
    if (mutation === 'mode') fs.chmodSync(selected, 0o500)
    if (mutation === 'file' || mutation === 'symlink') {
      fs.renameSync(selected, `${selected}-old`)
      if (mutation === 'file') fs.writeFileSync(selected, '')
      else fs.symlinkSync(`${selected}-old`, selected)
    }
    assert.equal(sandbox.diagnostic().homeIdentity, 'unsafe')
  })
}

test('failure identity distinguishes missing home', posix, t => {
  const sandbox = failedCleanup(t)
  fs.rmdirSync(sandbox.hermesHome)
  assert.equal(sandbox.diagnostic().homeIdentity, 'missing')
})

test('failure identity discards differing repeated observations', posix, t => {
  let selected, reads = 0
  const sandbox = failedCleanup(t, {
    lstatSync: location => {
      const result = fs.lstatSync(location)
      if (location === selected) result.ino += ++reads
      return result
    }
  })
  selected = sandbox.hermesHome
  assert.equal(sandbox.diagnostic().homeIdentity, 'inspection_failed')
  assert.equal(reads, 2)
})

test('failure identity refuses replaced root before reading home', posix, t => {
  let selected, reads = 0
  const sandbox = failedCleanup(t, {
    lstatSync: location => {
      if (location === selected) reads++
      return fs.lstatSync(location)
    }
  })
  selected = sandbox.hermesHome
  fs.renameSync(sandbox.root, `${sandbox.root}-old`)
  fs.mkdirSync(sandbox.root, { mode: 0o700 })
  const receipt = sandbox.diagnostic()
  assert.equal(receipt.state, 'identity_unverified')
  assert.equal(receipt.homeIdentity, 'inspection_failed')
  assert.equal(reads, 0)
})

test('failure identity suppresses error text and never invokes a code getter', posix, t => {
  let selected, accesses = 0
  const error = new Error('private failure detail')
  Object.defineProperty(error, 'code', { get: () => { accesses++; return 'ENOENT' } })
  const sandbox = failedCleanup(t, {
    lstatSync: location => {
      if (location === selected) throw error
      return fs.lstatSync(location)
    }
  })
  selected = sandbox.hermesHome
  const receipt = sandbox.diagnostic()
  assert.equal(receipt.homeIdentity, 'inspection_failed')
  assert.equal(accesses, 0)
  assert.equal(JSON.stringify(receipt).includes('private failure detail'), false)
})
