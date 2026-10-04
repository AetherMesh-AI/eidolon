import assert from 'node:assert/strict'
import { test } from 'vitest'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

function run(scenario) {
  const child = spawnSync(process.execPath, [
    '--import', fileURLToPath(new URL('./eidolon-title-native.fixture.mjs', import.meta.url)),
    fileURLToPath(new URL('./eidolon-title-native.mjs', import.meta.url)),
    '12345', 'file:///owned/Eidolon.app/', JSON.stringify({ pid: 42, startedAt: 0, logPath: '/owned/home/.eidolon/logs/desktop.log' }),
  ], { env: { TITLE_SCENARIO: scenario }, encoding: 'utf8', timeout: 5000 })
  assert.ifError(child.error)
  return { status: child.status, output: child.stdout + child.stderr }
}

for (const scenario of ['initial-null', 'initial-about']) test(`recovers ${scenario}`, () => {
  const result = run(scenario)
  assert.equal(result.status, 0, result.output)
  assert.match(result.output, /fixtureSamples":3/)
})

test('delayed IPC renderer text is polled rather than prematurely failed', () => {
  const result = run('delayed-ipc')
  assert.equal(result.status, 0, result.output)
  assert.match(result.output, /fixtureSamples":3/)
})

test('terminal gate retains emitted progress evidence without a live resolving label', () => {
  const result = run('terminal')
  assert.equal(result.status, 0, result.output)
  assert.match(result.output, /current-process IPC/)
  assert.match(result.output, /isolated-gate-error/)
})

test('immediate renderer with no body eventually reaches the assertions', () => {
  const result = run('null-body')
  assert.equal(result.status, 0, result.output)
  assert.match(result.output, /fixtureSamples":2/)
})

test('waits for delayed labels but fails diagnostically at a bounded deadline', () => {
  const delayed = run('delayed-label')
  assert.equal(delayed.status, 0, delayed.output)
  assert.match(delayed.output, /fixtureSamples":3/)
  const missing = run('deadline')
  assert.equal(missing.status, 1, missing.output)
  assert.match(missing.output, /Native title readiness timed out/)
  assert.match(missing.output, /absent progress evidence/)
  assert.match(missing.output, /fixtureElapsed":20000/)
  const old = run('old-title')
  assert.equal(old.status, 1, old.output)
  assert.match(old.output, /Hermes/)
  assert.doesNotMatch(old.output, /readiness timed out/)
})

for (const [scenario, cause] of [
  ['absent', /absent progress evidence/], ['old-event', /Boot progress branding mismatch/],
  ['stale', /stale progress evidence/], ['unrelated', /absent progress evidence/],
  ['unknown-error', /Unknown terminal error state/], ['beyond-deadline', /absent progress evidence/],
  ['wrong-url', /Owned renderer URL changed/], ['wrong-log-path', /Boot log ownership path mismatch/],
  ['missing-preload', /Missing preload IPC/], ['missing-ipc-text', /hermes:connection/],
  ['missing-attribution', /Uses the connected Hermes session/], ['missing-setup', /get you setup with Eidolon/],
  ['missing-starting', /Starting Eidolon/], ['null-only', /state=pending/], ['blank', /state=pending/],
]) test(`rejects ${scenario} for its own cause`, () => {
  const result = run(scenario)
  assert.equal(result.status, 1, result.output)
  assert.match(result.output, cause)
})
test('live resolving still requires independently emitted progress', () => {
  const result = run('live')
  assert.equal(result.status, 0, result.output)
  assert.match(result.output, /live-resolving/)
})
for (const scenario of ['http-stall', 'http-body-stall', 'ws-stall', 'cdp-stall', 'logs-stall', 'late-cdp-stall', 'wrong-target', 'close-stall']) test(`total deadline bounds ${scenario}`, () => {
  const result = run(scenario)
  assert.equal(result.status, 1, result.output)
  assert.match(result.output, /Native title deadline exceeded after 20000ms/)
  assert.match(result.output, /elapsed=20000/)
  if (['late-cdp-stall', 'logs-stall'].includes(scenario)) assert.match(result.output, /Desktop boot failed/)
})
