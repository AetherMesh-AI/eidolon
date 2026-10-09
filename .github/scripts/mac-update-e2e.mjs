/** Native, real packaged Electron IPC -> source update -> rebuild -> swap -> relaunch.
 * Run only on an ephemeral macOS CI runner. No updater/build/relaunch substitutes.
 * Inputs: --app --source --old-sha --new-sha --home --evidence.
 * The prepared source must be OLD with a real venv and origin already at NEW.
 */
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { execFileSync } from 'node:child_process'

const args = Object.fromEntries(Array.from({ length: (process.argv.length - 2) / 2 }, (_, i) =>
  [process.argv[2 + i * 2].replace(/^--/, ''), process.argv[3 + i * 2]]))
for (const key of ['app', 'source', 'old-sha', 'new-sha', 'home', 'evidence']) assert(args[key], `Missing --${key}`)
assert.equal(process.platform, 'darwin', 'Native macOS required; no platform simulation')
assert.equal(process.arch, 'arm64', 'Native Apple Silicon required')
assert.equal(process.env.GITHUB_ACTIONS, 'true', 'Ephemeral GitHub CI only')
assert.notEqual(process.getuid(), 0, 'Unprivileged account required')
const temp = fs.realpathSync(process.env.RUNNER_TEMP)
const home = path.resolve(args.home)
assert(home.startsWith(`${temp}${path.sep}`), 'Home must be a fresh child of RUNNER_TEMP')
assert(!fs.existsSync(home), 'Refuse existing home, including any user install')
fs.mkdirSync(home, { mode: 0o700 })
assert.equal(fs.realpathSync(home), home)
const evidence = path.resolve(args.evidence)
fs.mkdirSync(evidence, { recursive: true })
const source = fs.realpathSync(args.source)
const run = (cmd, argv, options = {}) => execFileSync(cmd, argv, { encoding: 'utf8', ...options }).trim()
const git = (...argv) => run('git', ['-C', source, ...argv])
assert.equal(git('rev-parse', 'HEAD'), args['old-sha'])
assert.notEqual(args['old-sha'], args['new-sha'])
const python = path.join(source, 'venv/bin/python')
assert(fs.existsSync(path.join(source, 'venv/bin/eidolon')), 'Real installed CLI required')
const hermesHome = path.join(home, '.eidolon')
const applications = path.join(home, 'Applications')
fs.mkdirSync(hermesHome)
fs.mkdirSync(applications)
const appPath = path.join(applications, 'Eidolon.app')
run('/usr/bin/ditto', [fs.realpathSync(args.app), appPath])
const stamp = bundle => JSON.parse(fs.readFileSync(path.join(bundle, 'Contents/Resources/install-stamp.json')))
assert.equal(stamp(appPath).commit, args['old-sha'])
const env = { ...process.env, HOME: home, HERMES_HOME: hermesHome,
  HERMES_DESKTOP_HERMES_ROOT: source, CSC_IDENTITY_AUTO_DISCOVERY: 'false' }
for (const key of ['EIDOLON_HOME', 'HERMES_DESKTOP_USER_DATA_DIR', 'HERMES_DESKTOP_BOOT_FAKE',
  'HERMES_DESKTOP_DEV_SERVER', 'ELECTRON_RUN_AS_NODE', 'GITHUB_SHA', 'GITHUB_REF_NAME']) delete env[key]
const report = { schema: 1, startedAt: new Date().toISOString(), platform: process.platform,
  architecture: process.arch, oldSha: args['old-sha'], newSha: args['new-sha'],
  home, source, appPath, signing: 'Ad-hoc signed; not notarized',
  scope: 'Owned two-commit source fixture; real packaged Electron, production IPC, CLI fetch/build, transaction and LaunchServices relaunch', stages: [] }
const save = () => fs.writeFileSync(path.join(evidence, 'report.json'), JSON.stringify(report, null, 2))
const stage = (name, details = {}) => { report.stages.push({ name, at: new Date().toISOString(), ...details }); save(); console.log(name, JSON.stringify(details)) }
const read = file => { try { return fs.readFileSync(file, 'utf8') } catch (error) { if (error.code === 'ENOENT') return ''; throw error } }
const alive = pid => { try { process.kill(pid, 0); return true } catch (error) { if (error.code === 'ESRCH') return false; throw error } }
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
async function until(predicate, timeout, label) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) { const value = await predicate(); if (value) return value; await sleep(500) }
  throw new Error(`Timed out: ${label}`)
}
const sentinels = new Map([
  ['config.yaml', 'model:\n  default: ci-no-provider\n'],
  ['.env', '# Synthetic fixture only; no credentials\n'],
  ['profiles/proof/config.yaml', 'model:\n  default: ci-profile-no-provider\n'],
  ['profiles/proof/memories/MEMORY.md', 'Synthetic preserved profile memory.\n'],
  ['profiles/proof/sessions/preserved.json', '{"synthetic":true,"message":"preserve this conversation fixture"}\n']
])
for (const [relative, contents] of sentinels) {
  const file = path.join(hermesHome, relative)
  fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, contents)
}
// A synthetic SQLite row proves payload persistence without pretending it is
// a provider conversation. The running backend may legitimately add its schema.
const database = path.join(hermesHome, 'profiles/proof/state.db')
run(python, ['-c', 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("create table preservation_proof(value text)"); c.execute("insert into preservation_proof values (?)",("synthetic profile persistence",)); c.commit(); c.close()', database], { env })
function verifyData() {
  for (const [relative, contents] of sentinels) {
    if (!relative.endsWith('config.yaml')) {
      // Do not ask assert to construct unbounded diffs of changing files.
      assert(read(path.join(hermesHome, relative)) === contents, `Preserved fixture changed: ${relative}`)
    }
  }
  // Runtime migrations may add defaults; preserve explicit settings, not byte
  // layout or generated schema pages in a live database.
  for (const [relative, expected] of [['config.yaml', 'ci-no-provider'], ['profiles/proof/config.yaml', 'ci-profile-no-provider']]) {
    const actual = run(python, ['-c', 'import sys,yaml; print(yaml.safe_load(open(sys.argv[1]))["model"]["default"])', path.join(hermesHome, relative)], { env })
    assert(actual === expected, `Configured model changed: ${relative}`)
  }
  const persisted = JSON.parse(run(python, ['-c', 'import sqlite3,sys,json; c=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True,timeout=10); print(json.dumps({"integrity":c.execute("pragma integrity_check").fetchone()[0],"rows":c.execute("select value from preservation_proof").fetchall()})); c.close()', database], { env }))
  assert.equal(persisted.integrity, 'ok')
  assert(JSON.stringify(persisted.rows) === JSON.stringify([['synthetic profile persistence']]), 'Profile SQLite payload changed')
}

const require = createRequire(path.join(source, 'apps/desktop/package.json'))
const { _electron: electron } = require('playwright')
let instance, oldPid, newPid, polling
const handoffLog = path.join(hermesHome, 'logs/desktop-update-handoff.log')
const resultPath = path.join(hermesHome, '.eidolon-update-result.json')
const oldQuitPath = path.join(evidence, 'old-before-quit.json')
const launchScript = `ObjC.import('AppKit'); function run(argv) {
  const apps=$.NSWorkspace.sharedWorkspace.runningApplications;
  for(let i=0;i<apps.count;i++){const a=apps.objectAtIndex(i);
    if(!a.bundleURL.isNil() && ObjC.unwrap(a.bundleURL.path)===argv[0] && !a.isTerminated)
      return JSON.stringify({pid:Number(a.processIdentifier),ready:Boolean(a.isFinishedLaunching),
      executable:ObjC.unwrap(a.executableURL.path),started:Number(a.launchDate.timeIntervalSince1970)});
  } return ''; }`
function runningApp() {
  const out = run('/usr/bin/osascript', ['-l', 'JavaScript', '-e', launchScript, appPath])
  return out ? JSON.parse(out) : null
}
try {
  stage('fixture-prepared', { oldStamp: stamp(appPath) })
  instance = await electron.launch({ executablePath: path.join(appPath, 'Contents/MacOS/Eidolon'), env, timeout: 120_000 })
  oldPid = instance.process().pid
  const main = await instance.evaluate(({ app }) => ({ packaged: app.isPackaged, ready: app.isReady(), version: app.getVersion(), userData: app.getPath('userData'), execPath: process.execPath, pid: process.pid }))
  assert.equal(main.packaged, true); assert.equal(main.pid, oldPid)
  assert.equal(main.userData, path.join(hermesHome, 'desktop'))
  await instance.evaluate(({ app }, file) => {
    const fs = process.getBuiltinModule('fs')
    app.on('before-quit', () => fs.writeFileSync(file, JSON.stringify({ pid: process.pid, at: Date.now() })))
  }, oldQuitPath)
  const page = await instance.firstWindow({ timeout: 120_000 })
  await page.waitForFunction(() => Boolean(window.hermesDesktop?.updates?.apply), undefined, { timeout: 120_000 })
  await page.screenshot({ path: path.join(evidence, 'old-app.png') })
  stage('old-packaged-app-ready', { ...main, displayedVersion: await page.evaluate(() => window.hermesDesktop.getVersion()) })
  const oldInode = fs.statSync(appPath).ino
  const beforeAcl = run('/bin/ls', ['-ldebn', appPath])
  // Only this freshly copied CI fixture is permission-restricted, never /Applications.
  assert.equal(fs.realpathSync(appPath), appPath)
  assert.equal(fs.statSync(appPath).uid, process.getuid())
  stage('applying-fixture-only-deny-acl', { pid: oldPid })
  run('/bin/chmod', ['+a#', '0', 'everyone deny delete', appPath])
  stage('invoking-real-ipc-under-deny-acl', { pid: oldPid })
  try {
    const denied = await page.evaluate(() => window.hermesDesktop.updates.apply({}))
    stage('deny-acl-ipc-returned', { result: denied })
    assert.equal(denied.error, 'mac-update-permission-denied')
    await sleep(3000)
    assert(alive(oldPid)); assert.equal(fs.statSync(appPath).ino, oldInode)
    assert(!fs.existsSync(oldQuitPath)); assert(!fs.existsSync(path.join(hermesHome, '.eidolon-update-in-progress')))
    assert.equal(git('rev-parse', 'HEAD'), args['old-sha']); stage('checking-denied-update-profile-data'); verifyData()
    stage('real-ipc-preflight-denial-before-quit', { pid: oldPid, result: denied })
  } finally {
    assert.equal(fs.realpathSync(appPath), appPath); assert.equal(fs.statSync(appPath).ino, oldInode)
    run('/bin/chmod', ['-a#', '0', appPath])
    assert.equal(run('/bin/ls', ['-ldebn', appPath]), beforeAcl)
  }
  for (const suffix of ['.old', '.new', '.eidolon-update']) {
    const obstruction = `${appPath}${suffix}`
    fs.mkdirSync(obstruction)
    const marker = path.join(obstruction, 'owned-recovery-evidence.txt')
    fs.writeFileSync(marker, `Preserve existing ${suffix} recovery data.\n`)
    const bytes = fs.readFileSync(marker)
    const result = await page.evaluate(() => window.hermesDesktop.updates.apply({}))
    assert.equal(result.error, 'mac-update-recovery-required')
    assert.deepEqual(fs.readFileSync(marker), bytes)
    assert(alive(oldPid)); assert.equal(fs.statSync(appPath).ino, oldInode)
    assert(!fs.existsSync(oldQuitPath))
    stage('real-ipc-recovery-refusal-preserves-old-app', { suffix, pid: oldPid, result })
    // Delete exactly the two test-owned entries; retain unexpected additions.
    fs.unlinkSync(marker)
    fs.rmdirSync(obstruction)
  }
  // Capture the transient result before the new app consumes it on boot.
  polling = setInterval(() => {
    const result = read(resultPath)
    if (result) fs.writeFileSync(path.join(evidence, 'handoff-result-observed.json'), result)
    const marker = read(path.join(hermesHome, '.eidolon-update-in-progress'))
    if (marker) fs.writeFileSync(path.join(evidence, 'handoff-marker-observed.txt'), marker)
  }, 25)
  const handoff = await page.evaluate(() => window.hermesDesktop.updates.apply({}))
  assert.equal(handoff.ok, true); assert.equal(handoff.handedOff, true)
  stage('real-ipc-updater-handoff', handoff)
  await until(() => !alive(oldPid), 90_000, 'old Electron process exits itself')
  assert.equal(JSON.parse(read(oldQuitPath)).pid, oldPid)
  stage('old-process-quit', { oldPid })
  await until(() => /Relaunched app verified: pid=/.test(read(handoffLog)), 40 * 60_000, 'real source update, rebuild, swap and verified relaunch')
  assert.equal(git('rev-parse', 'HEAD'), args['new-sha'])
  const newStamp = stamp(appPath)
  assert.equal(newStamp.commit, args['new-sha'])
  assert.equal(newStamp.version, report.stages[0].oldStamp.version)
  assert.equal(newStamp.distance, report.stages[0].oldStamp.distance + 1)
  const observed = runningApp()
  assert(observed?.ready); newPid = observed.pid; assert.notEqual(newPid, oldPid); assert(alive(newPid))
  assert.equal(observed.executable, path.join(appPath, 'Contents/MacOS/Eidolon'))
  await sleep(3000); assert.equal(runningApp()?.pid, newPid); assert(alive(newPid))
  const archives = fs.readdirSync(applications).filter(name => name.startsWith('Eidolon.app.eidolon-update-'))
  assert.equal(archives.length, 1)
  const archive = path.join(applications, archives[0])
  const receipt = JSON.parse(read(path.join(archive, 'transaction.json')))
  assert.equal(receipt.phase, 'installed'); assert.equal(receipt.target, appPath)
  assert.notEqual(receipt.previous.sha256, receipt.expected.sha256)
  assert.equal(stamp(path.join(archive, 'previous.app')).commit, args['old-sha'])
  // Independently re-hash both complete bundles with the production identity owner.
  const identities = JSON.parse(run(python, ['-c', 'import json,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from mac_transaction import bundle_identity; print(json.dumps([bundle_identity(Path(p)) for p in sys.argv[2:]]))', path.join(source, 'scripts/desktop-update'), appPath, path.join(archive, 'previous.app')], { env }))
  assert.deepEqual(identities[0], receipt.expected); assert.deepEqual(identities[1], receipt.previous)
  verifyData()
  fs.copyFileSync(path.join(archive, 'transaction.json'), path.join(evidence, 'transaction.json'))
  stage('replacement-ready-and-profile-preserved', { oldPid, newPid, observed, newStamp, recoveryArchive: archive, receipt })
  report.ok = true
  report.readinessScope = 'Native LaunchServices isFinishedLaunching, stable fresh PID and exact executable; renderer/provider conversation after relaunch is not asserted.'
} catch (error) {
  report.ok = false; report.error = error.stack; throw error
} finally {
  if (polling) clearInterval(polling)
  report.finishedAt = new Date().toISOString(); save()
  if (fs.existsSync(path.join(hermesHome, 'logs'))) fs.cpSync(path.join(hermesHome, 'logs'), path.join(evidence, 'logs'), { recursive: true })
  // Stop only processes whose exact fixture app path was verified above.
  if (newPid && alive(newPid)) process.kill(newPid, 'SIGTERM')
  if (instance && oldPid && alive(oldPid)) await instance.close()
  // Preserve the isolated install, previous.app and transaction journal for diagnosis.
}
