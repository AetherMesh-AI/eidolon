// Read-only smoke regression against an explicitly launched, backend-gated app.
// Usage: node scripts/eidolon-title-native.mjs PORT file:///absolute/app/path/ LAUNCH_JSON
// LAUNCH_JSON: {pid, startedAt: epoch milliseconds captured before spawn, logPath}.
// Caller must independently verify PID/executable/asar and CDP listener ownership.
// Launch/cleanup belong to the caller; this script never starts an app/backend.
import assert from 'node:assert/strict'
import { test } from 'node:test'

const [port, prefix, launchJSON] = process.argv.slice(2)
const launch = JSON.parse(launchJSON ?? '{}')
assert.ok(Number.isInteger(launch.pid) && launch.pid > 0 && Number.isFinite(launch.startedAt) && typeof launch.logPath === 'string', 'Explicit launch PID/start/log path required')
assert.ok(/^\d+$/.test(port ?? '') && prefix?.startsWith('file:///'), 'Explicit port and owned app file URL prefix required')
// One budget covers discovery, HTTP bodies, connection, evaluation, and close.
const started = Date.now()
const deadline = started + 20_000
let observed
let evidence
let records = []
let state = 'pending'
let stage = 'target discovery'
let ws
const watchdog = setTimeout(() => {
  console.error(`Native title deadline exceeded after 20000ms; elapsed=${Date.now() - started}; stage=${stage}; Native title readiness timed out; ${evidence ? 'renderer state not ready (requires hermes:connection)' : records.length ? 'stale progress evidence' : 'absent progress evidence'}; state=${state}; last=${JSON.stringify(observed)}`)
  ws?.close()
  // A nonresponsive peer must not hold the CLI open during the close handshake.
  process.exit(1)
}, 20_000)
const remaining = () => Math.max(1, deadline - Date.now())
let page
try {
  while (!page) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(remaining()) })).json()
      page = targets.find(target => target.type === 'page' && target.url.startsWith(prefix) && target.url.includes('/dist/index.html'))
    } catch {}
    if (!page) await new Promise(resolve => setTimeout(resolve, Math.min(200, remaining())))
  }
  const endpoint = new URL(page.webSocketDebuggerUrl)
  assert.ok(endpoint.protocol === 'ws:' && endpoint.hostname === '127.0.0.1' && endpoint.port === port, 'Owned loopback CDP endpoint required')
  stage = 'CDP connection'
  ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject })
} catch (error) { clearTimeout(watchdog); ws?.close(); throw error }
let id = 0
async function evaluate(expression) {
  stage = 'CDP evaluation'
  const requestId = ++id
  return await new Promise((resolve, reject) => {
    const listener = event => {
      const data = JSON.parse(event.data)
      if (data.id !== requestId) return
      ws.removeEventListener('message', listener)
      if (data.error || data.result.exceptionDetails) reject(new Error(JSON.stringify(data)))
      else resolve(data.result.result.value)
    }
    ws.addEventListener('message', listener)
    ws.send(JSON.stringify({ id: requestId, method: 'Runtime.evaluate', params: { expression, returnByValue: true, awaitPromise: true, timeout: remaining() } }))
  })
}
const gate = 'Isolated visible-launch gate: real backend intentionally not started'
const progressLabels = ['Resolving Eidolon backend', 'Resolving Hermes backend']
try {
  while (true) {
    observed = await evaluate(`({title: document.title, url: location.href, text: document.body?.innerText ?? ''})`)
    // The selected file target can expose its initial blank execution context.
    // It is never accepted as ready or used to retrieve evidence.
    if (observed.url === 'about:blank' && !observed.text) {
      await new Promise(resolve => setTimeout(resolve, Math.min(200, Math.max(0, deadline - Date.now()))))
      continue
    }
    assert.ok(observed.url.startsWith(prefix) && observed.url.includes('/dist/index.html'), `Owned renderer URL changed: ${JSON.stringify(observed)}`)
    observed.logs = await evaluate(`window.hermesDesktop?.getRecentLogs?.()` )
    assert.ok(observed.logs, 'Missing preload IPC getRecentLogs')
    assert.equal(observed.logs.path, launch.logPath, 'Boot log ownership path mismatch')
    records = observed.logs.lines.map(line => ({ line, match: /^\[(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z)\] \[eidolon\] \[boot\] (Resolving (?:Eidolon|Hermes) backend)$/.exec(line) })).filter(record => record.match)
    evidence = records.find(record => Date.parse(record.match[1]) >= launch.startedAt && Date.parse(record.match[1]) <= Date.now())
    state = observed.text.includes('Desktop boot failed')
      ? observed.text.includes(gate) ? 'isolated-gate-error' : 'unknown-error'
      : progressLabels.some(label => observed.text.includes(label)) ? 'live-resolving' : 'pending'
    assert.notEqual(state, 'unknown-error', 'Unknown terminal error state')
    if (evidence && state !== 'pending' && observed.text.includes("'hermes:connection'")) break
    const remaining = deadline - Date.now()
    if (remaining <= 0) throw new Error(`Native title readiness timed out; ${evidence ? 'renderer state not ready (requires hermes:connection)' : records.length ? 'stale progress evidence' : 'absent progress evidence'}; state=${state}; last=${JSON.stringify(observed)}`)
    await new Promise(resolve => setTimeout(resolve, Math.min(200, remaining)))
  }
} finally {
  stage = 'CDP close'
  await new Promise(resolve => {
    ws.addEventListener('close', resolve, { once: true })
    ws.close()
  })
  clearTimeout(watchdog)
}
console.log(JSON.stringify({ source: 'current-process IPC hermes:logs:recent', launch, matchedBootRecord: evidence.line, state, observed }, null, 2))
// Emitted progress branding is not evidence that the transient label was painted.
test('emitted resolving progress identifies Eidolon', () => assert.equal(evidence.match[2], 'Resolving Eidolon backend', 'Boot progress branding mismatch'))
test('packaged document identifies the desktop as Eidolon', () => assert.equal(observed.title, 'Eidolon'))
test('visible bootstrap introduces Eidolon', () => {
  assert.ok(observed.text.includes("Let's get you setup with Eidolon"))
  assert.ok(observed.text.includes('Starting Eidolon…'))
  assert.ok(!observed.text.includes('Starting Hermes'))
})
test('Hermes engine attribution and native IPC namespace remain intact', () => {
  assert.ok(observed.text.includes('Uses the connected Hermes session'))
  assert.ok(observed.text.includes("'hermes:connection'"))
})
