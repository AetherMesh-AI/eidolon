const assert = require('node:assert/strict')
const { createHash } = require('node:crypto')
const { readFileSync } = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const vm = require('node:vm')
const ts = require('typescript')

// Execute the actual adjacent fixture observer, not a second implementation.
const source = readFileSync(path.join(__dirname, 'subscription-native-fixture.ts'), 'utf8')
const parsed = ts.createSourceFile('subscription-native-fixture.ts', source, ts.ScriptTarget.Latest, true)
const matches = parsed.statements.filter(
  node => ts.isFunctionDeclaration(node) && node.name?.text === 'observeSubscriptionRpc'
)
assert.equal(matches.length, 1)
const compiled = ts.transpileModule(`(${matches[0].getText(parsed)})`, {
  compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.CommonJS }
}).outputText
const method = 'organization.improveTeamInput'
const secret = 'synthetic-private-value-never-exported'
const flush = async () => {
  await Promise.resolve()
  await Promise.resolve()
}

async function harness() {
  const timers = new Map(),
    delays = [],
    handlers = {},
    commands = []
  let timerId = 0,
    detached = 0
  const observe = vm.runInNewContext(compiled, {
    createHash,
    structuredClone,
    setTimeout: (callback, delay) => {
      const id = ++timerId
      timers.set(id, callback)
      delays.push(delay)
      return id
    },
    clearTimeout: id => timers.delete(id)
  })
  const session = {
    on: (name, callback) => {
      handlers[name] = callback
    },
    send: async command => {
      commands.push(command)
    },
    detach: async () => {
      detached++
    }
  }
  const observer = await observe({ context: () => ({ newCDPSession: async () => session }) })
  const frame = (direction, payload, socket = 'synthetic-socket') =>
    handlers[`Network.webSocketFrame${direction}`]({
      requestId: socket,
      response: { opcode: 1, payloadData: JSON.stringify(payload) }
    })
  return {
    observer,
    timers,
    delays,
    commands,
    detached: () => detached,
    send: (id, rpcMethod = method, socket) =>
      frame(
        'Sent',
        { id, method: rpcMethod, params: { expectedOrganizationId: secret, sourceRevision: secret, field: 'jobs' } },
        socket
      ),
    receive: (id, payload = { result: { suggestion: secret, field: 'jobs' } }, socket) =>
      frame('Received', { id, ...payload }, socket),
    malformed: () =>
      handlers['Network.webSocketFrameReceived']({
        requestId: 'synthetic-socket',
        response: { opcode: 1, payloadData: 'not-json' }
      }),
    expire: () => {
      assert.equal(timers.size, 1)
      ;[...timers.values()][0]()
    }
  }
}

test('arms before one action, resolves the exact completion, and exports only sanitized fields', async () => {
  const h = await harness()
  let actions = 0
  const row = await h.observer.performOperation(method, async () => {
    actions++
    h.send(1)
    h.receive(1)
  })
  assert.equal(actions, 1)
  assert.equal(row.phase, 'result')
  assert.equal(row.method, method)
  assert.equal(row.shape.suggestionValid, true)
  assert.ok(!JSON.stringify(row).includes(secret))
  assert.deepEqual(h.delays, [180000])
  assert.equal(h.timers.size, 0)
  h.receive(1, { error: { code: 5076, data: { reason: 'unavailable' } } })
  assert.equal(row.phase, 'result')
  assert.equal(h.observer.read().pending, 0)
  await h.observer.close()
  assert.equal(h.detached(), 1)
  assert.deepEqual(h.commands, ['Network.enable'])
})

test('old pending, wrong socket/id, other method and second matching request cannot settle or replace the bound request', async () => {
  const h = await harness()
  h.send(1)
  let settled = false
  const operation = h.observer.performOperation(method, async () => {
    h.send(2)
    h.send(3, 'organization.generateTeam')
    h.send(4)
  })
  operation.then(() => {
    settled = true
  })
  h.receive(1)
  h.receive(2, undefined, 'different-socket')
  h.receive(99)
  h.receive(3)
  h.receive(4)
  await flush()
  assert.equal(settled, false)
  h.receive(2)
  assert.equal((await operation).phase, 'result')
  assert.equal(h.timers.size, 0)
  await h.observer.close()
})

test('duplicate old pending send is not a new request to bind', async () => {
  const h = await harness()
  h.send(1)
  const operation = h.observer.performOperation(method, async () => {
    h.send(1)
    h.receive(1)
  })
  h.expire()
  await assert.rejects(operation, /subscription_operation_timeout/)
  assert.equal(h.timers.size, 0)
  await h.observer.close()
})

test('duplicate bound key rejects correlation rather than replacing the request', async () => {
  const h = await harness()
  const operation = h.observer.performOperation(method, async () => {
    h.send(1)
    h.send(1)
  })
  await assert.rejects(operation, /subscription_operation_correlation_lost/)
  assert.equal(h.timers.size, 0)
  await h.observer.close()
})

for (const [name, payload] of [
  ['neither', {}],
  ['both', { result: {}, error: {} }]
]) {
  test(`matching completion with ${name} result/error rejects safely`, async () => {
    const h = await harness()
    const operation = h.observer.performOperation(method, async () => {
      h.send(1)
      h.receive(1, payload)
    })
    await assert.rejects(operation, /subscription_operation_correlation_lost/)
    assert.equal(h.timers.size, 0)
    await h.observer.close()
  })
}

test('malformed received bytes on the bound socket reject without retaining bytes', async () => {
  const h = await harness()
  const operation = h.observer.performOperation(method, async () => {
    h.send(1)
    h.malformed()
  })
  await assert.rejects(operation, /subscription_operation_correlation_lost/)
  assert.equal(h.observer.read().malformed, 1)
  assert.equal(h.timers.size, 0)
  await h.observer.close()
})

test('eviction of the observed bound request rejects instead of silently losing correlation', async () => {
  const h = await harness()
  const operation = h.observer.performOperation(method, async () => {
    h.send(1)
    for (let id = 2; id <= 65; id++) h.send(id, 'organization.teamSetup')
  })
  await assert.rejects(operation, /subscription_operation_correlation_lost/)
  assert.equal(h.timers.size, 0)
  await h.observer.close()
})

for (const bound of [false, true]) {
  test(`timeout ${bound ? 'after binding' : 'before send'} removes the timer and ignores late responses`, async () => {
    const h = await harness()
    const operation = h.observer.performOperation(method, async () => {
      if (bound) h.send(1)
    })
    h.expire()
    await assert.rejects(operation, /subscription_operation_timeout/)
    assert.equal(h.timers.size, 0)
    const next = h.observer.performOperation(method, async () => {
      h.send(2)
      h.receive(1)
      h.receive(2)
    })
    assert.equal((await next).phase, 'result')
    assert.equal(h.timers.size, 0)
    await h.observer.close()
  })
  test(`cleanup ${bound ? 'after binding' : 'before send'} cancels observation without sending a provider cancellation`, async () => {
    const h = await harness()
    const operation = h.observer.performOperation(method, async () => {
      if (bound) h.send(1)
    })
    await h.observer.close()
    await assert.rejects(operation, /subscription_operation_cancelled/)
    assert.equal(h.timers.size, 0)
    const before = JSON.stringify(h.observer.read())
    h.send(2)
    h.receive(1)
    assert.equal(JSON.stringify(h.observer.read()), before)
    assert.deepEqual(h.commands, ['Network.enable'])
    assert.equal(h.detached(), 1)
  })
  test(`action rejection ${bound ? 'after binding' : 'before send'} cancels the waiter and preserves the action failure`, async () => {
    const h = await harness()
    await assert.rejects(
      h.observer.performOperation(method, async () => {
        if (bound) h.send(1)
        throw new Error('synthetic action failure')
      }),
      /synthetic action failure/
    )
    assert.equal(h.timers.size, 0)
    assert.equal(h.observer.read().pending, bound ? 1 : 0)
    await h.observer.close()
    assert.deepEqual(h.commands, ['Network.enable'])
  })
}

test('only one waiter can be active and closed observers never run an action', async () => {
  const h = await harness()
  let forbiddenActions = 0
  const first = h.observer.performOperation(method, async () => {
    h.send(1)
  })
  await assert.rejects(
    h.observer.performOperation(method, async () => {
      forbiddenActions++
    }),
    /subscription_operation_wait_unavailable/
  )
  h.receive(1)
  await first
  await h.observer.close()
  await assert.rejects(
    h.observer.performOperation(method, async () => {
      forbiddenActions++
    }),
    /subscription_operation_wait_unavailable/
  )
  assert.equal(forbiddenActions, 0)
  assert.equal(h.timers.size, 0)
})

test('expected unavailable completion remains a sanitized RPC error, never an implicit success', async () => {
  const h = await harness()
  const row = await h.observer.performOperation(method, async () => {
    h.send(1)
    h.receive(1, { error: { code: 5076, message: secret, data: { reason: 'unavailable', secret } } })
  })
  assert.equal(row.phase, 'error')
  assert.equal(row.code, 5076)
  assert.equal(row.reason, 'unavailable')
  assert.ok(!JSON.stringify(row).includes(secret))
  assert.equal(h.timers.size, 0)
  await h.observer.close()
})

for (const cancellation of ['timeout', 'close']) {
  test(`${cancellation} rejects promptly while the single action stays unresolved`, { timeout: 1000 }, async () => {
    const h = await harness()
    let release
    const action = new Promise(resolve => {
      release = resolve
    })
    const operation = h.observer.performOperation(method, async () => {
      h.send(1)
      await action
    })
    if (cancellation === 'timeout') h.expire()
    else await h.observer.close()
    await flush()
    assert.equal(h.timers.size, 0)
    await assert.rejects(operation, /subscription_operation_(timeout|cancelled)/)
    release()
    await flush()
    if (cancellation === 'timeout') await h.observer.close()
  })
}

test('actual observer source remains strict-type-correct against its minimal CDP interface', () => {
  const filename = '/synthetic-observer-typecheck.ts'
  const declarations = `type Row = Record<string, unknown>
    type Frame = { requestId: string; response: { opcode: number; payloadData: string } }
    interface Page { context(): { newCDPSession(page: Page): Promise<{
      on(event: string, callback: (event: Frame) => void): void;
      send(command: string): Promise<void>; detach(): Promise<void>;
    }> } }
    declare function createHash(name: string): { update(value: string): { digest(encoding: string): string } }
  `
  const code = declarations + matches[0].getText(parsed)
  const options = { strict: true, noEmit: true, skipLibCheck: true, types: [], target: ts.ScriptTarget.ES2023 }
  const host = ts.createCompilerHost(options)
  const original = host.getSourceFile.bind(host)
  host.getSourceFile = (name, ...args) =>
    name === filename ? ts.createSourceFile(name, code, ts.ScriptTarget.ES2023, true) : original(name, ...args)
  const program = ts.createProgram([filename], options, host)
  const errors = ts.getPreEmitDiagnostics(program)
  assert.equal(
    errors.length,
    0,
    errors.map(error => ts.flattenDiagnosticMessageText(error.messageText, '\n')).join('\n')
  )
})

for (const cancellation of ['timeout', 'close']) {
  test(
    `result before unresolved action keeps ${cancellation} and the single-operation guard active`,
    { timeout: 1000 },
    async () => {
      const h = await harness()
      let release,
        extraActions = 0
      const action = new Promise(resolve => {
        release = resolve
      })
      const operation = h.observer.performOperation(method, async () => {
        h.send(1)
        h.receive(1)
        await action
      })
      await flush()
      assert.equal(h.timers.size, 1)
      await assert.rejects(
        h.observer.performOperation(method, async () => {
          extraActions++
        }),
        /subscription_operation_wait_unavailable/
      )
      if (cancellation === 'timeout') h.expire()
      else await h.observer.close()
      await assert.rejects(operation, /subscription_operation_(timeout|cancelled)/)
      assert.equal(extraActions, 0)
      assert.equal(h.timers.size, 0)
      release()
      await flush()
      if (cancellation === 'timeout') await h.observer.close()
    }
  )
}
