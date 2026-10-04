// Deterministic CDP boundary for the real CLI; never native acceptance evidence.
import vm from 'node:vm'
const scenario = process.env.TITLE_SCENARIO
let clock = 0
Date.now = () => clock
const timers = new Map()
let timerId = 0
let scheduled = false
function drain() {
  if (scheduled) return
  scheduled = true
  setImmediate(() => {
    scheduled = false
    const next = [...timers].sort((a, b) => a[1].at - b[1].at)[0]
    if (!next) return
    timers.delete(next[0]); clock = next[1].at; next[1].fn()
    if (timers.size) drain()
  })
}
globalThis.setTimeout = (fn, ms) => {
  const id = ++timerId
  timers.set(id, { at: clock + ms, fn }); drain(); return id
}
globalThis.clearTimeout = id => timers.delete(id)
const prefix = 'file:///owned/Eidolon.app/'
const never = () => new Promise(() => {})
globalThis.fetch = async () => scenario === 'http-stall' ? await never() : ({ json: async () => scenario === 'http-body-stall' ? await never() : [{ type: 'page', url: (scenario === 'wrong-target' ? 'file:///other/' : prefix) + 'dist/index.html', webSocketDebuggerUrl: 'ws://127.0.0.1:12345/test' }] })
let samples = 0
const gate = 'Isolated visible-launch gate: real backend intentionally not started'
const base = "Let's get you setup with Eidolon\nStarting Eidolon…\nUses the connected Hermes session\n'hermes:connection'"
globalThis.WebSocket = class extends EventTarget {
  constructor() { super(); if (scenario !== 'ws-stall') queueMicrotask(() => this.onopen()) }
  async send(raw) {
    const { id, params } = JSON.parse(raw)
    if (params.expression.includes('document.title')) samples++
    if (scenario === 'cdp-stall' || (scenario === 'late-cdp-stall' && clock >= 19800)) return
    let text = base + (scenario === 'live' ? '\nResolving Eidolon backend' : '\nDesktop boot failed\n' + (scenario === 'unknown-error' ? 'Unrelated disk failure' : gate))
    if (scenario === 'missing-ipc-text' || (scenario === 'delayed-ipc' && samples < 3)) text = text.replace("'hermes:connection'", '')
    if (scenario === 'missing-attribution') text = text.replace('Uses the connected Hermes session', '')
    if (scenario === 'missing-setup') text = text.replace("Let's get you setup with Eidolon", '')
    if (scenario === 'missing-starting') text = text.replace('Starting Eidolon…', '')
    if (scenario === 'blank') text = ''
    const body = scenario === 'null-only' || (scenario === 'null-body' && samples === 1) || (['initial-null','initial-about'].includes(scenario) && samples < 3) ? null : { innerText: text }
    let lines = ['[1970-01-01T00:00:00.000Z] [eidolon] [boot] Resolving Eidolon backend']
    if (['deadline', 'absent', 'late-cdp-stall'].includes(scenario) || (scenario === 'delayed-label' && samples < 3) || (scenario === 'beyond-deadline' && clock <= 20000)) lines = []
    if (scenario === 'old-event') lines = [lines[0].replace('Eidolon', 'Hermes')]
    if (scenario === 'stale') lines = [lines[0].replace('1970-01-01T00:00:00.000Z', '1969-12-31T23:59:59.999Z')]
    if (scenario === 'unrelated') lines = [lines[0].replace('[boot]', '[error]'), 'error mentions [boot] Resolving Eidolon backend']
    let result
    try {
      result = { result: { value: await vm.runInNewContext(params.expression, { document: { title: scenario === 'old-title' ? 'Hermes' : 'Eidolon', body }, location: { href: scenario === 'initial-about' && samples < 3 ? 'about:blank' : (scenario === 'wrong-url' ? 'file:///other/' : prefix) + 'dist/index.html' }, window: { hermesDesktop: scenario === 'missing-preload' ? undefined : { getRecentLogs: async () => scenario === 'logs-stall' ? await never() : ({ path: scenario === 'wrong-log-path' ? '/other/desktop.log' : '/owned/home/.eidolon/logs/desktop.log', lines }) } } }) } }
    } catch (error) { result = { exceptionDetails: { text: error.message } } }
    queueMicrotask(() => this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ id, result }) })))
  }
  close() { console.log(JSON.stringify({ fixtureSamples: samples, fixtureElapsed: clock })); if (scenario !== 'close-stall') queueMicrotask(() => this.dispatchEvent(new Event('close'))) }
}
