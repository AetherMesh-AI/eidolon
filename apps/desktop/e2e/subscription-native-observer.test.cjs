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
  const deadlines = new Map()
  let now = 0
  let timerId = 0,
    detached = 0
  const observe = vm.runInNewContext(compiled, {
    createHash,
    structuredClone,
    setTimeout: (callback, delay) => {
      const id = ++timerId
      timers.set(id, callback)
      deadlines.set(id, now + delay)
      delays.push(delay)
      return id
    },
    clearTimeout: id => {
      timers.delete(id)
      deadlines.delete(id)
    }
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
    send: (id, rpcMethod = method, socket, params = {}) =>
      frame(
        'Sent',
        { id, method: rpcMethod, params: { expectedOrganizationId: secret, sourceRevision: secret, field: 'jobs', ...params } },
        socket
      ),
    receive: (id, payload = { result: { suggestion: secret, field: 'jobs' } }, socket) =>
      frame('Received', { id, ...payload }, socket),
    malformed: () =>
      handlers['Network.webSocketFrameReceived']({
        requestId: 'synthetic-socket',
        response: { opcode: 1, payloadData: 'not-json' }
      }),
    advance: duration => {
      now += duration
      for (const [id, deadline] of deadlines) {
        if (deadline <= now && timers.has(id)) timers.get(id)()
      }
    },
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

const boundaryMatches = parsed.statements.filter(
  node => ts.isFunctionDeclaration(node) && node.name?.text === 'boundaryDiagnostic'
)
assert.equal(boundaryMatches.length, 1)
const boundaryCompiled = ts.transpileModule(`(${boundaryMatches[0].getText(parsed)})`, {
  compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.CommonJS }
}).outputText
const sanitizeBoundary = rows => JSON.parse(JSON.stringify(vm.runInNewContext(boundaryCompiled)(rows)))

test('boundary sanitizer exports only closed recheck position and direction enums', () => {
  const positions = [
    'parent',
    'ancestor_2',
    'ancestor_3',
    'ancestor_4',
    'ancestor_5',
    'ancestor_6',
    'ancestor_7',
    'ancestor_8_or_higher',
    'none'
  ]
  for (const position of positions) {
    for (const direction of ['increase', 'decrease', 'unchanged', 'unknown']) {
      const [row] = sanitizeBoundary([
        {
          stage: 'filesystem_identity',
          event: 'identity_mismatch',
          identity: {
            comparison: 'initial_recheck',
            targetKind: 'directory',
            targetScope: 'ancestor',
            ancestorPosition: position,
            linksDirection: direction,
            changedFields: { links: true },
            path: secret,
            inode: 123,
            rawStat: secret
          }
        }
      ])
      assert.equal(row.identity.comparison, 'initial_recheck')
      assert.equal(row.identity.ancestorPosition, position)
      assert.equal(row.identity.linksDirection, direction)
      assert.equal(row.identity.changedFields.links, true)
      assert.ok(!JSON.stringify(row).includes(secret))
      assert.deepEqual(Object.keys(row.identity).sort(), [
        'ancestorPosition',
        'changedFields',
        'comparison',
        'linksDirection',
        'targetKind',
        'targetScope'
      ])
    }
  }
})

test('boundary sanitizer rejects unknown labels and retains row/count bounds', () => {
  for (const value of [secret, 2, true, null, {}, ['parent']]) {
    const input = {
      sequence: -1,
      lane: 33,
      line: 10001,
      event: 'identity_mismatch',
      identity: {
        comparison: value,
        ancestorPosition: value,
        linksDirection: value,
        changedFields: { links: value, changeTime: value, secret }
      }
    }
    const rows = sanitizeBoundary(Array(600).fill(input))
    assert.equal(rows.length, 128)
    assert.equal(rows[0].identity.comparison, 'other')
    assert.equal(rows[0].identity.ancestorPosition, 'none')
    assert.equal(rows[0].identity.linksDirection, 'unknown')
    assert.equal(rows[0].sequence, null)
    assert.equal(rows[0].lane, null)
    assert.equal(rows[0].line, null)
    assert.ok(!JSON.stringify(rows).includes(secret))
  }
})

const setupPaths = {
  inspect: ['teamSetup', 'setupSubscriptionStatus', 'setupSubscriptionPolicy'],
  begin: ['beginSetupSubscription', 'teamSetup', 'setupSubscriptionStatus', 'setupSubscriptionPolicy'],
  migration: [
    'teamSetup', 'setupSubscriptionStatus', 'setupSubscriptionPolicy',
    'beginSetupSubscriptionHostMigration', 'teamSetup', 'setupSubscriptionStatus', 'setupSubscriptionPolicy'
  ],
  cancel: ['cancelSetupSubscription', 'teamSetup', 'setupSubscriptionStatus', 'setupSubscriptionPolicy'],
  recover: ['recoverSetupSubscriptionOperation', 'teamSetup', 'setupSubscriptionStatus', 'setupSubscriptionPolicy'],
  settingsDisconnect: [
    'teamSetup', 'setupSubscriptionStatus', 'prepareSetupSubscriptionOperation',
    'executeSetupSubscriptionOperation', 'teamSetup', 'setupSubscriptionStatus'
  ],
  policy: ['applySetupSubscriptionPolicy', 'teamSetup', 'setupSubscriptionStatus', 'setupSubscriptionPolicy'],
  models: ['teamSetup', 'setupSubscriptionStatus', 'setupSubscriptionPolicy', 'setupSubscriptionModels', 'teamSetup'],
  disconnect: [
    'prepareSetupSubscriptionOperation',
    'executeSetupSubscriptionOperation',
    'teamSetup',
    'setupSubscriptionStatus',
    'setupSubscriptionPolicy'
  ]
}
const setupReply = name =>
  name === 'setupSubscriptionPolicy'
    ? { result: { configuration: { authorizationEpoch: secret, connectionId: secret } } }
    : name === 'setupSubscriptionModels'
      ? { result: { authorizationEpoch: secret, connectionId: secret, sourceRevision: secret } }
      : { result: {} }

for (const kind of Object.keys(setupPaths)) {
  test(`${kind} waits for its exact final read past 5s without retrying or extending a 15s read deadline`, async () => {
    const h = await harness()
    let actions = 0,
      settled = false,
      rejected
    const observed = h.observer.performSetupAction(kind, async () => {
      actions++
      setupPaths[kind].forEach((name, index) => {
        h.send(index + 1, 'organization.' + name)
        if (index < setupPaths[kind].length - 1) h.receive(index + 1, setupReply(name))
      })
    })
    observed.then(() => {
      settled = true
    }, error => { rejected = error })
    h.advance(6000)
    h.receive(99)
    h.receive(setupPaths[kind].length, undefined, 'wrong-socket')
    await flush()
    if (rejected) throw rejected
    assert.equal(settled, false)
    assert.equal(h.observer.read().pending, 1)
    h.receive(setupPaths[kind].length)
    const result = await observed
    assert.equal(actions, 1)
    assert.equal(result.steps.length, setupPaths[kind].length)
    assert.deepEqual(
      Array.from(result.steps, row => row.method),
      setupPaths[kind].map(name => 'organization.' + name)
    )
    assert.ok(result.steps.every(row => row.phase === 'result'))
    assert.deepEqual(h.delays, setupPaths[kind].map(() => 15000))
    assert.equal(h.timers.size, 0)
    assert.ok(!JSON.stringify(result).includes(secret))
    if (kind === 'models') {
      assert.equal(result.steps[3].shape.catalogEpochMatchesPolicy, true)
      assert.equal(result.steps[3].shape.catalogConnectionMatchesPolicy, true)
    }
    await h.observer.close()
    assert.deepEqual(h.commands, ['Network.enable'])
  })

  test(`${kind} never-settled terminal read refuses at the production deadline`, async () => {
    const h = await harness()
    let actions = 0
    const observed = h.observer.performSetupAction(kind, async () => {
      actions++
      setupPaths[kind].forEach((name, index) => {
        h.send(index + 1, 'organization.' + name)
        if (index < setupPaths[kind].length - 1) h.receive(index + 1, setupReply(name))
      })
    })
    h.advance(14999)
    assert.equal(h.timers.size, 1)
    h.advance(1)
    await assert.rejects(observed, /subscription_operation_timeout/)
    h.receive(setupPaths[kind].length)
    assert.equal(actions, 1)
    assert.equal(h.timers.size, 0)
    await h.observer.close()
    assert.deepEqual(h.commands, ['Network.enable'])
  })
}


for (const kind of Object.keys(setupPaths)) {
  test(`${kind} gives each completed step a fresh 15s window without resetting it on a delayed send`, async () => {
    const h = await harness()
    let actions = 0, settled = false
    const observed = h.observer.performSetupAction(kind, async () => { actions++ })
    observed.then(() => { settled = true }, () => { settled = true })
    for (const [index, name] of setupPaths[kind].entries()) {
      h.advance(7500)
      h.send(index + 1, 'organization.' + name)
      h.advance(7499)
      await flush()
      assert.equal(settled, false)
      assert.equal(h.timers.size, 1)
      if (index < setupPaths[kind].length - 1) h.receive(index + 1, setupReply(name))
    }
    h.advance(1)
    await assert.rejects(observed, /subscription_operation_timeout/)
    assert.equal(actions, 1)
    assert.deepEqual(h.delays, setupPaths[kind].map(() => 15000))
    assert.equal(h.timers.size, 0)
    await h.observer.close()
    assert.deepEqual(h.commands, ['Network.enable'])
  })
}

for (const kind of Object.keys(setupPaths)) {
for (const failure of [
  'wrong-first',
  'out-of-order',
  'duplicate',
  'same-method-new-id',
  'reused-completed-id',
  'cross-socket-next-step',
  'rpc-error',
  'malformed',
  'close'
]) {
  test(`${kind} sequence refuses ${failure} without a second action or exported private data`, async () => {
    const h = await harness()
    let actions = 0
    const observed = h.observer.performSetupAction(kind, async () => {
      actions++
      h.send(1, failure === 'wrong-first' ? 'organization.' + setupPaths[kind][1] : 'organization.' + setupPaths[kind][0])
      if (failure === 'out-of-order') {
        h.receive(1)
        h.send(2, 'organization.' + setupPaths[kind][2])
      }
      if (failure === 'duplicate') h.send(1, 'organization.' + setupPaths[kind][0])
      if (failure === 'same-method-new-id') h.send(2, 'organization.' + setupPaths[kind][0])
      if (failure === 'reused-completed-id') {
        h.receive(1)
        h.send(1, 'organization.' + setupPaths[kind][1])
        h.receive(1)
      }
      if (failure === 'cross-socket-next-step') {
        h.receive(1)
        h.send(2, 'organization.' + setupPaths[kind][1], 'different-socket')
        h.receive(2, undefined, 'different-socket')
      }
      if (failure === 'rpc-error')
        h.receive(1, { error: { code: 5076, message: secret, data: { reason: 'unavailable', secret } } })
      if (failure === 'malformed') h.malformed()
    })
    if (failure === 'close') await h.observer.close()
    await assert.rejects(observed, /subscription_(operation_correlation_lost|setup_rpc_refused|operation_cancelled)/)
    assert.equal(actions, 1)
    assert.equal(h.timers.size, 0)
    assert.ok(!JSON.stringify(h.observer.read()).includes(secret))
    if (failure !== 'close') await h.observer.close()
    assert.deepEqual(h.commands, ['Network.enable'])
  })
}

}

test('setup action allowlist, old-pending and shared inference guard reject before invoking action', async () => {
  const h = await harness()
  let actions = 0
  await assert.rejects(
    h.observer.performSetupAction('unknown', async () => {
      actions++
    }),
    /wait_unavailable/
  )
  h.send(1, 'organization.teamSetup')
  await assert.rejects(
    h.observer.performSetupAction('models', async () => {
      actions++
    }),
    /wait_unavailable/
  )
  h.receive(1)
  const inference = h.observer.performOperation(method, async () => {
    h.send(2)
  })
  await assert.rejects(
    h.observer.performSetupAction('models', async () => {
      actions++
    }),
    /wait_unavailable/
  )
  h.receive(2)
  await inference
  assert.equal(actions, 0)
  await h.observer.close()
})

test('status/readiness evidence keeps refusals and equality results while redacting raw IDs and unknown labels', async () => {
  const h = await harness()
  let result
  const observed = h.observer.performSetupAction('models', async () => {
    for (const [index, name] of setupPaths.models.entries()) {
      h.send(index + 1, 'organization.' + name)
      if (name === 'setupSubscriptionStatus') {
        h.receive(index + 1, {
          result: {
            hostIdentityState: 'migration_required',
            canMigrateHostIdentity: false,
            cleanupPending: true,
            canCancel: true,
            connectionRevision: 3,
            operation: { state: 'uncertain', revision: 3, ticket: secret },
            attemptId: secret,
            authorizationEpoch: secret
          }
        })
      } else if (name === 'setupSubscriptionModels') {
        h.receive(index + 1, {
          result: { authorizationEpoch: 'different-private-epoch', connectionId: 'different-private-binding' }
        })
      } else if (index === 4) {
        h.receive(index + 1, {
          result: {
            readiness: {
              status: 'blocked',
              blockers: ['subscription_connection_required', secret],
              modelAvailability: secret
            }
          }
        })
      } else h.receive(index + 1, setupReply(name))
    }
  })
  result = await observed
  assert.equal(result.steps[1].shape.canMigrateHostIdentity, false)
  assert.equal(result.steps[1].shape.operationState, 'uncertain')
  assert.equal(result.steps[1].shape.operationRevisionMatchesConnection, true)
  assert.equal(result.steps[3].shape.catalogEpochMatchesPolicy, false)
  assert.equal(result.steps[3].shape.catalogConnectionMatchesPolicy, false)
  assert.equal(result.steps[4].shape.readinessStatus, 'blocked')
  assert.deepEqual(Array.from(result.steps[4].shape.readinessBlockers), ['subscription_connection_required', 'other'])
  assert.equal(result.steps[4].shape.modelAvailability, 'other')
  for (const privateValue of [secret, 'different-private-epoch', 'different-private-binding']) {
    assert.ok(!JSON.stringify(result).includes(privateValue))
    assert.ok(!JSON.stringify(h.observer.read()).includes(privateValue))
  }
  h.send(10, 'organization.setupSubscriptionStatus')
  h.receive(10, {
    result: {
      hostIdentityState: secret,
      canMigrateHostIdentity: secret,
      cleanupPending: secret,
      operation: { state: secret },
      readiness: { blockers: Array(40).fill(secret) }
    }
  })
  const shape = h.observer.read().rpc.at(-1).shape
  assert.equal(shape.hostIdentityState, 'other')
  assert.equal(shape.canMigrateHostIdentity, null)
  assert.equal(shape.cleanupPending, null)
  assert.equal(shape.operationState, 'other')
  assert.equal(shape.readinessBlockers.length, 16)
  assert.ok(shape.readinessBlockers.every(value => value === 'other'))
  assert.ok(!JSON.stringify(h.observer.read()).includes(secret))
  await h.observer.close()
})

// Exercise the actual journey barrier's post-response assertions as well as
// the transport waiter. A terminal response alone never proves readiness.
const journey = readFileSync(path.join(__dirname, 'subscription-native-journey.spec.ts'), 'utf8')
const journeyParsed = ts.createSourceFile('journey.ts', journey, ts.ScriptTarget.Latest, true)
const modelHelpers = journeyParsed.statements.filter(node => ts.isFunctionDeclaration(node) && node.name?.text === 'completeModelLoad')
assert.equal(modelHelpers.length, 1)
const modelHelper = ts.transpileModule(`(${modelHelpers[0].getText(journeyParsed)})`, {
  compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.CommonJS }
}).outputText
const matchSubset = (value, subset) => {
  for (const [key, expected] of Object.entries(subset)) {
    if (expected && typeof expected === 'object' && !Array.isArray(expected)) matchSubset(value[key], expected)
    else assert.equal(JSON.stringify(value[key]), JSON.stringify(expected))
  }
}
for (const scenario of ['ready', 'stale-policy-review', 'epoch-mismatch', 'binding-mismatch', 'readiness-refused', 'source-mismatch', 'source-changed', 'revision-mismatch', 'rpc-error']) {
  test(`journey model barrier retains ${scenario} readiness/refusal semantics`, async () => {
    const rows = Array.from({ length: 5 }, () => ({ phase: 'result', shape: {} }))
    rows[1].shape = { connectionState: 'active', cleanupPending: false }
    rows[3].shape = { sourceMatches: true, connectionRevisionMatchesRequest: true, catalogEpochMatchesPolicy: true, catalogConnectionMatchesPolicy: true }
    rows[4].shape = { state: 'available', organizationMatches: true, sourceMatchesAction: true, readinessStatus: 'unchecked', readinessBlockers: [], modelAvailability: 'cached' }
    if (scenario === 'epoch-mismatch' || scenario === 'stale-policy-review') rows[3].shape.catalogEpochMatchesPolicy = false
    if (scenario === 'binding-mismatch') rows[3].shape.catalogConnectionMatchesPolicy = false
    if (scenario === 'readiness-refused' || scenario === 'stale-policy-review') rows[4].shape.readinessStatus = 'blocked'
    if (scenario === 'stale-policy-review') { rows[4].shape.policyVersionThree = true; rows[4].shape.readinessBlockers = ['subscription_models_required'] }
    if (scenario === 'source-mismatch') rows[3].shape.sourceMatches = false
    if (scenario === 'source-changed') rows[4].shape.sourceMatchesAction = false
    if (scenario === 'revision-mismatch') rows[3].shape.connectionRevisionMatchesRequest = false
    if (scenario === 'rpc-error') rows[2].phase = 'error'
    let clicks = 0
    const helper = vm.runInNewContext(modelHelper, {
      fixture: { performSetupAction: async (kind, action) => {
        assert.equal(kind, 'models'); await action(); return { steps: rows }
      } },
      expect: value => ({
        toHaveLength: count => assert.equal(value.length, count),
        toBe: expected => assert.equal(value, expected),
        toMatchObject: subset => matchSubset(value, subset)
      })
    })
    const action = helper({ getByRole: () => ({ click: async () => { clicks++ } }) }, scenario === 'stale-policy-review' ? 'stale-policy' : 'ready')
    if (['ready', 'stale-policy-review'].includes(scenario)) await action
    else await assert.rejects(action)
    assert.equal(clicks, 1)
  })
}

test('model sequence reports source drift without exporting either source', async () => {
  const h = await harness()
  const observed = h.observer.performSetupAction('models', async () => {
    setupPaths.models.forEach((name, index) => {
      h.send(index + 1, 'organization.' + name)
      if (index === 0) h.receive(index + 1, { result: { sourceRevision: secret } })
      else if (index === 4) h.receive(index + 1, { result: { sourceRevision: 'different-private-source' } })
      else h.receive(index + 1, setupReply(name))
    })
  })
  const result = await observed
  assert.equal(result.steps[0].shape.sourceMatchesAction, true)
  assert.equal(result.steps[3].shape.sourceMatchesAction, true)
  assert.equal(result.steps[4].shape.sourceMatchesAction, false)
  assert.ok(!JSON.stringify(result).includes(secret))
  assert.ok(!JSON.stringify(result).includes('different-private-source'))
  await h.observer.close()
})

// Policy writes legitimately advance generation/source. Compare the receipt to
// the old request and subsequent reads to the committed generation/new source.
const nextSource = 'new-private-source-after-policy-mutation'
const policyParams = { expectedGeneration: 4, idempotencyKey: secret, previewVersion: secret }
const policyReceipt = () => ({
  capabilityVersion: 1, historical: false, sourceRevision: nextSource,
  receipt: {
    version: 1, action: 'retireSetupInheritance', organizationId: secret,
    idempotencyKey: secret, sourceRevision: secret, previewDigest: secret,
    priorGeneration: 4, appliedGeneration: 5
  }
})
const policyReplies = () => [
  policyReceipt(),
  {
    state: 'available', organizationId: secret, sourceRevision: nextSource,
    generationAllowance: { policyVersion: 3, mode: 'subscription_calls' },
    readiness: { status: 'blocked', blockers: ['subscription_models_required'] }
  },
  { connectionState: 'active', hostIdentityState: 'current', cleanupPending: false },
  { state: 'available', organizationId: secret, sourceRevision: nextSource, policyGeneration: 5, policyVersion: 3 }
]
const policyHelpers = journeyParsed.statements.filter(node => ts.isFunctionDeclaration(node) && node.name?.text === 'completePolicyApply')
assert.equal(policyHelpers.length, 1)
const policyHelper = ts.transpileModule(`(${policyHelpers[0].getText(journeyParsed)})`, {
  compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.CommonJS }
}).outputText
const policyHelperWith = fixture => vm.runInNewContext(policyHelper, {
  fixture,
  expect: value => ({
    toHaveLength: count => assert.equal(value.length, count),
    toBe: expected => assert.equal(value, expected),
    toMatchObject: subset => matchSubset(value, subset)
  })
})

for (const scenario of [
  'generation-and-source-advance', 'no-turnover-source', 'pre-migration-legacy', 'post-migration-current',
  'legacy-cannot-satisfy-current', 'current-cannot-satisfy-legacy', 'stale-receipt-source', 'stale-prior-generation',
  'stale-applied-generation', 'wrong-idempotency-key', 'wrong-preview', 'wrong-receipt-organization',
  'historical-receipt', 'missing-receipt', 'team-source-drift', 'policy-source-drift',
  'stale-policy-generation', 'old-policy-version', 'wrong-policy-organization',
  'disconnected', 'cleanup-pending', 'readiness-refused'
]) {
  test(`policy journey barrier preserves ${scenario} semantics and exports no private values`, async () => {
    const h = await harness(), replies = policyReplies()
    const identity = ['pre-migration-legacy', 'current-cannot-satisfy-legacy'].includes(scenario) ? 'migration_required' : 'current'
    if (['pre-migration-legacy', 'legacy-cannot-satisfy-current'].includes(scenario)) replies[2].hostIdentityState = 'migration_required'
    if (scenario === 'no-turnover-source') delete replies[0].sourceRevision
    if (scenario === 'stale-receipt-source') replies[0].receipt.sourceRevision = nextSource
    if (scenario === 'stale-prior-generation') replies[0].receipt.priorGeneration = 3
    if (scenario === 'stale-applied-generation') replies[0].receipt.appliedGeneration = 4
    if (scenario === 'wrong-idempotency-key') replies[0].receipt.idempotencyKey = nextSource
    if (scenario === 'wrong-preview') replies[0].receipt.previewDigest = nextSource
    if (scenario === 'wrong-receipt-organization') replies[0].receipt.organizationId = nextSource
    if (scenario === 'historical-receipt') replies[0].historical = true
    if (scenario === 'missing-receipt') delete replies[0].receipt
    if (scenario === 'team-source-drift') replies[1].sourceRevision = secret
    if (scenario === 'policy-source-drift') replies[3].sourceRevision = secret
    if (scenario === 'stale-policy-generation') replies[3].policyGeneration = 4
    if (scenario === 'old-policy-version') replies[3].policyVersion = 2
    if (scenario === 'wrong-policy-organization') replies[3].organizationId = nextSource
    if (scenario === 'disconnected') replies[2].connectionState = 'disconnected'
    if (scenario === 'cleanup-pending') replies[2].cleanupPending = true
    if (scenario === 'readiness-refused') replies[1].readiness.blockers = ['connection_binding_changed']
    let clicks = 0
    const helper = policyHelperWith(h.observer)
    const action = helper({ click: async () => {
      clicks++
      setupPaths.policy.forEach((name, index) => {
        h.send(index + 1, 'organization.' + name, undefined,
          index === 0 ? policyParams : { sourceRevision: undefined })
        h.receive(index + 1, { result: replies[index] })
      })
    } }, identity)
    if (['generation-and-source-advance', 'no-turnover-source', 'pre-migration-legacy', 'post-migration-current'].includes(scenario)) await action
    else await assert.rejects(action)
    assert.equal(clicks, 1)
    assert.equal(h.timers.size, 0)
    for (const value of [secret, nextSource]) assert.ok(!JSON.stringify(h.observer.read()).includes(value))
    await h.observer.close()
  })
}

for (const kind of Object.keys(setupPaths)) {
for (let missing = 0; missing < setupPaths[kind].length; missing++) {
  for (const failure of ['missing-send', 'missing-reply', 'error', 'malformed-envelope', 'empty-envelope', 'error-fallback-teamSetup']) {
    test(`${kind} step ${missing + 1} ${failure} fails without resending or extending its deadline`, async () => {
      const h = await harness()
      const replies = kind === 'policy' ? policyReplies().map(result => ({ result })) : setupPaths[kind].map(setupReply)
      let clicks = 0, settled = false
      const action = h.observer.performSetupAction(kind, async () => {
        clicks++
        for (let index = 0; index <= missing; index++) {
          if (index === missing && failure === 'missing-send') break
          h.send(index + 1, 'organization.' + setupPaths[kind][index], undefined,
            kind === 'policy' && index === 0 ? policyParams : {})
          if (index < missing) h.receive(index + 1, replies[index])
          else if (failure === 'error' || failure === 'error-fallback-teamSetup') {
            h.receive(index + 1, { error: { code: 5076, message: secret, data: { reason: 'unavailable' } } })
            if (failure === 'error-fallback-teamSetup') {
              h.send(100, 'organization.teamSetup')
              h.receive(100, { result: { state: 'available', organizationId: secret, sourceRevision: secret } })
              for (let remaining = index + 1; remaining < setupPaths[kind].length; remaining++) {
                h.send(101 + remaining, 'organization.' + setupPaths[kind][remaining])
                h.receive(101 + remaining, replies[remaining])
              }
            }
          } else if (failure === 'malformed-envelope') h.receive(index + 1, { ...replies[index], error: {} })
          else if (failure === 'empty-envelope') h.receive(index + 1, {})
        }
      })
      action.then(() => { settled = true }, () => { settled = true })
      if (failure.startsWith('missing')) {
        // Unrelated and already-completed responses cannot fill the missing
        // send/reply or restart the next step's existing renderer deadline.
        h.advance(6000)
        h.receive(999)
        if (missing > 0) h.receive(missing, replies[missing - 1])
        h.receive(missing + 1, replies[missing], 'unrelated-socket')
        if (failure === 'missing-send') h.receive(missing + 1, replies[missing])
        await flush()
        assert.equal(settled, false)
        h.advance(8999)
        await flush()
        assert.equal(settled, false)
        assert.equal(h.timers.size, 1)
        h.advance(1)
      }
      await assert.rejects(action, /subscription_(operation_timeout|setup_rpc_refused|operation_correlation_lost)/)
      assert.equal(clicks, 1)
      assert.equal(h.timers.size, 0)
      assert.ok(h.delays.every(delay => delay === 15000))
      assert.equal(h.delays.length, missing + 1)
      assert.ok(!JSON.stringify(h.observer.read()).includes(secret))
      await h.observer.close()
      assert.deepEqual(h.commands, ['Network.enable'])
    })
  }
}
}

for (const kind of Object.keys(setupPaths)) {
  test(`${kind} refuses prior pending requests and a concurrent inference before its action`, async () => {
    const h = await harness()
    let actions = 0
    h.send(1, 'organization.teamSetup')
    await assert.rejects(h.observer.performSetupAction(kind, async () => { actions++ }), /wait_unavailable/)
    h.receive(1)
    const inference = h.observer.performOperation(method, async () => { h.send(2) })
    await assert.rejects(h.observer.performSetupAction(kind, async () => { actions++ }), /wait_unavailable/)
    h.receive(2)
    await inference
    await h.observer.close()
    await assert.rejects(h.observer.performSetupAction(kind, async () => { actions++ }), /wait_unavailable/)
    assert.equal(actions, 0)
    assert.equal(h.timers.size, 0)
    assert.deepEqual(h.commands, ['Network.enable'])
  })
}

test('both actual policy helpers await completion before the original saved-copy assertion', () => {
  for (const name of ['activate', 'reviewMigrationPolicy']) {
    const helper = journeyParsed.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name).getText(journeyParsed)
    assert.match(helper, /await completePolicyApply\(apply, /)
    assert.ok(helper.indexOf('await completePolicyApply(apply, ') < helper.indexOf("'Policy saved. Load account models again before Generate or Improve.'"))
    assert.doesNotMatch(helper, /await apply\.click\(\)/)
    assert.doesNotMatch(helper, /toBeVisible\(\{/)
  }
})

test('actual policy helper leaves UI assertion untouched until its delayed terminal read completes once', async () => {
  const h = await harness(), replies = policyReplies()
  let clicks = 0, uiAssertions = 0
  const helper = policyHelperWith(h.observer)
  const completed = helper({ click: async () => {
    clicks++
    setupPaths.policy.forEach((name, index) => {
      h.send(index + 1, 'organization.' + name, undefined, index === 0 ? policyParams : { sourceRevision: undefined })
      if (index < 3) h.receive(index + 1, { result: replies[index] })
    })
  } }, 'current').then(() => { uiAssertions++ })
  h.advance(6000)
  h.receive(99, { result: replies[3] })
  h.receive(4, { result: replies[3] }, 'unrelated-socket')
  await flush()
  assert.equal(uiAssertions, 0)
  assert.equal(h.observer.read().pending, 1)
  h.receive(4, { result: replies[3] })
  await completed
  assert.equal(uiAssertions, 1)
  assert.equal(clicks, 1)
  assert.deepEqual(h.delays, [15000, 15000, 15000, 15000])
  assert.equal(h.timers.size, 0)
  await h.observer.close()
})


test('migration policy callers name the exact identity on each side of migration', () => {
  const scenario = journeyParsed.getText()
  assert.equal((scenario.match(/await reviewMigrationPolicy\(connection, 'migration_required'\)/g) ?? []).length, 1)
  assert.equal((scenario.match(/await reviewMigrationPolicy\(connection, 'current', true\)/g) ?? []).length, 1)
  const activate = journeyParsed.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'activate').getText(journeyParsed)
  assert.match(activate, /await completePolicyApply\(apply, 'current'\)/)
})

// Run each connection contract through both actual layers: the CDP observer
// computes the sanitized evidence, then the journey helper checks that evidence.
const connectionHelpers = journeyParsed.statements.filter(
  node => ts.isFunctionDeclaration(node) && node.name?.text === 'completeConnectionAction'
)
assert.equal(connectionHelpers.length, 1)
const connectionHelper = ts.transpileModule(`(${connectionHelpers[0].getText(journeyParsed)})`, {
  compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.CommonJS }
}).outputText
const connectionHelperWith = fixture => vm.runInNewContext(connectionHelper, {
  fixture,
  expect: value => ({
    toHaveLength: count => assert.equal(value.length, count),
    toBe: expected => assert.equal(value, expected),
    toMatchObject: subset => matchSubset(value, subset)
  })
})
const connectionScenarios = {
  'inspect-new': 'inspect',
  'inspect-legacy': 'inspect',
  'inspect-cancelled': 'inspect',
  'begin-connected': 'begin',
  'begin-publication-refused': 'begin',
  'migration-held': 'migration',
  'migration-connected': 'migration',
  'cancel-migration': 'cancel',
  'recover-publication': 'recover',
  'settings-disconnect': 'settingsDisconnect'
}
const connectionSource = 'd'.repeat(64)
const connectionTicket = () => ({
  version: 1,
  sequence: '17',
  nonce: 'a'.repeat(64),
  kind: 'revoke',
  bindingDigest: 'b'.repeat(64),
  authorizationEpoch: '11111111-2222-4333-8444-555555555555',
  priorStoreRevision: 7,
  sourceRevision: connectionSource
})
const connectionReplies = scenario => {
  const kind = connectionScenarios[scenario]
  const status = (overrides = {}) => ({
    connectionState: 'absent', hostIdentityState: 'new', stage: 'idle',
    cleanupPending: false, canCancel: false, canMigrateHostIdentity: false,
    connectionRevision: 7, operation: { state: 'none', revision: 7 }, ...overrides
  })
  let terminal = status()
  if (scenario === 'inspect-legacy') terminal = status({ connectionState: 'active', hostIdentityState: 'migration_required' })
  if (['inspect-cancelled', 'cancel-migration'].includes(scenario)) terminal = status({
    connectionState: 'disconnected', hostIdentityState: 'migration_required', stage: 'cancelled',
    attemptId: secret, canMigrateHostIdentity: true
  })
  if (['begin-connected', 'migration-connected'].includes(scenario)) terminal = status({
    connectionState: 'active', hostIdentityState: 'current', stage: 'connected', attemptId: secret
  })
  if (scenario === 'begin-publication-refused') terminal = status({
    connectionState: 'disconnected', hostIdentityState: 'current', stage: 'publication_uncertain',
    attemptId: secret, cleanupPending: true, operation: { state: 'recovery_required', revision: 7 }
  })
  if (scenario === 'migration-held') terminal = status({
    connectionState: 'disconnected', hostIdentityState: 'migration_required', stage: 'pending',
    attemptId: secret, canCancel: true
  })
  if (scenario === 'recover-publication') terminal = status({
    connectionState: 'disconnected', hostIdentityState: 'current', stage: 'locally_retired'
  })
  if (scenario === 'settings-disconnect') terminal = status({
    connectionState: 'disconnected', hostIdentityState: 'current', operation: { state: 'admitted', revision: 7 }
  })
  const replies = setupPaths[kind].map(name => {
    if (name === 'teamSetup' || name === 'setupSubscriptionPolicy') return {
      state: scenario === 'settings-disconnect' ? 'configured' : 'available',
      organizationId: secret, sourceRevision: scenario === 'settings-disconnect' ? connectionSource : secret
    }
    if (name === 'setupSubscriptionStatus') return structuredClone(terminal)
    if (name === 'beginSetupSubscription' || name === 'beginSetupSubscriptionHostMigration') return {
      stage: scenario === 'migration-held' ? 'pending' : scenario === 'begin-publication-refused' ? 'publication_uncertain' : 'connected',
      attemptId: secret
    }
    if (name === 'cancelSetupSubscription') return { stage: 'cancelled', attemptId: secret }
    if (name === 'recoverSetupSubscriptionOperation') return { state: 'local_retired', receipt: { remoteRevocation: 'unconfirmed' } }
    if (name === 'prepareSetupSubscriptionOperation') return { state: 'prepared', verification: 'verified_current', ticket: connectionTicket() }
    if (name === 'executeSetupSubscriptionOperation') return { state: 'admitted', ticket: connectionTicket(), receipt: { remoteRevocation: 'unconfirmed' } }
    throw new Error('Unexpected synthetic connection method')
  })
  const params = replies.map(() => scenario === 'settings-disconnect' ? { sourceRevision: connectionSource } : {})
  if (kind === 'migration') {
    replies[1] = status({ connectionState: 'disconnected', hostIdentityState: 'migration_required',
      canMigrateHostIdentity: true, operation: { state: 'completed', revision: 7 } })
    if (scenario === 'migration-held') {
      delete replies[1].canCancel // Valid first preflight: no durable attempt yet.
    } else {
      replies[1].stage = 'cancelled'
      replies[1].attemptId = secret
    }
    params[3] = { expectedRevision: 7 }
  }
  if (kind === 'cancel') params[0] = { attemptId: secret }
  if (kind === 'settingsDisconnect') {
    replies[1] = status({ connectionState: 'active', hostIdentityState: 'current' })
    params[2] = { kind: 'revoke', expectedRevision: 7, sourceRevision: connectionSource }
    params[3] = { ticket: connectionTicket(), sourceRevision: connectionSource }
  }
  return { kind, replies, params, terminal: kind === 'settingsDisconnect' ? 5 : replies.length - 2 }
}
const assertConnectionPrivacy = h => {
  for (const privateValue of [secret, nextSource, connectionSource, 'a'.repeat(64), 'b'.repeat(64), 'c'.repeat(64), connectionTicket().authorizationEpoch]) {
    assert.ok(!JSON.stringify(h.observer.read()).includes(privateValue))
  }
  assert.deepEqual(h.commands, ['Network.enable'])
}

for (const scenario of Object.keys(connectionScenarios)) {
  test(`actual connection helper ${scenario} waits past 5s for its exact terminal reply and acts once`, async () => {
    const h = await harness(), data = connectionReplies(scenario)
    let clicks = 0, uiAssertions = 0
    const completed = connectionHelperWith(h.observer)({ click: async () => {
      clicks++
      setupPaths[data.kind].forEach((name, index) => {
        h.send(index + 1, 'organization.' + name, undefined, data.params[index])
        if (index < data.replies.length - 1) h.receive(index + 1, { result: data.replies[index] })
      })
    } }, scenario).then(() => { uiAssertions++ })
    h.advance(6000)
    h.receive(999, { result: data.replies.at(-1) })
    h.receive(data.replies.length, { result: data.replies.at(-1) }, 'unrelated-socket')
    await flush()
    assert.equal(uiAssertions, 0)
    assert.equal(h.observer.read().pending, 1)
    h.receive(data.replies.length, { result: data.replies.at(-1) })
    await completed
    assert.equal(uiAssertions, 1)
    assert.equal(clicks, 1)
    assert.equal(h.timers.size, 0)
    assert.deepEqual(h.delays, data.replies.map(() => 15000))
    if (data.kind === 'migration') {
      const preflight = h.observer.read().rpc.filter(row => row.method === 'organization.setupSubscriptionStatus')[0]
      assert.equal(preflight.shape.canCancel, scenario === 'migration-held' ? null : false)
      assert.equal(preflight.shape.canCancelValid, true)
    }
    assertConnectionPrivacy(h)
    await h.observer.close()
  })
}

const assertConnectionRejected = async (scenario, mutate) => {
  const h = await harness(), data = connectionReplies(scenario)
  mutate(data)
  let clicks = 0, uiAssertions = 0
  const completed = connectionHelperWith(h.observer)({ click: async () => {
    clicks++
    setupPaths[data.kind].forEach((name, index) => {
      h.send(index + 1, 'organization.' + name, undefined, data.params[index])
      h.receive(index + 1, data.envelopes?.[index] ?? { result: data.replies[index] })
    })
  } }, scenario).then(() => { uiAssertions++ })
  await assert.rejects(completed)
  assert.equal(clicks, 1)
  assert.equal(uiAssertions, 0)
  assert.equal(h.timers.size, 0)
  assertConnectionPrivacy(h)
  const evidence = h.observer.read()
  await h.observer.close()
  return evidence
}

for (const scenario of Object.keys(connectionScenarios)) {
  const negatives = {
    'rpc-refusal': data => { data.envelopes = { 0: { error: { code: 5076, message: secret, data: { reason: 'unavailable' } } } } },
    'non-object-reply': data => { data.replies[data.terminal] = [] },
    'wrong-organization': data => { data.replies[setupPaths[data.kind].indexOf('teamSetup')].organizationId = nextSource },
    'team-refusal': data => { data.replies[setupPaths[data.kind].indexOf('teamSetup')].state = 'blocked' },
    'invalid-revision': data => { data.replies[data.terminal].connectionRevision = -1 },
    'missing-revision': data => { delete data.replies[data.terminal].connectionRevision },
    'stale-operation-revision': data => { data.replies[data.terminal].operation.revision = 6 },
    'cleanup-state-mismatch': data => { data.replies[data.terminal].cleanupPending = !data.replies[data.terminal].cleanupPending },
    'unknown-identity': data => { data.replies[data.terminal].hostIdentityState = secret },
    'null-terminal-cancelability': data => { data.replies[data.terminal].canCancel = null },
    'malformed-terminal-cancelability': data => { data.replies[data.terminal].canCancel = secret },
    'connection-state-mismatch': data => { data.replies[data.terminal].connectionState = data.replies[data.terminal].connectionState === 'active' ? 'disconnected' : 'active' }
  }
  if (scenario !== 'settings-disconnect') {
    negatives['policy-source-drift'] = data => { data.replies.at(-1).sourceRevision = nextSource }
    negatives['policy-refusal'] = data => { data.replies.at(-1).state = 'blocked' }
    negatives['policy-organization-mismatch'] = data => { data.replies.at(-1).organizationId = nextSource }
    negatives['terminal-stage-mismatch'] = data => { data.replies[data.terminal].stage = 'access_denied' }
  } else negatives['terminal-team-source-drift'] = data => { data.replies[4].sourceRevision = nextSource }
  for (const [failure, mutate] of Object.entries(negatives)) {
    test(`actual connection helper ${scenario} refuses ${failure} before original UI assertions`, async () => {
      await assertConnectionRejected(scenario, mutate)
    })
  }
}

for (const scenario of ['begin-connected', 'begin-publication-refused', 'cancel-migration', 'recover-publication']) {
  for (const failure of ['wrong-initial-request-source', 'missing-initial-request-source', 'uniform-post-action-source-drift']) {
    test(`actual connection helper ${scenario} refuses ${failure}`, async () => {
      await assertConnectionRejected(scenario, data => {
        if (failure === 'uniform-post-action-source-drift') {
          for (const [index, name] of setupPaths[data.kind].entries()) {
            if (name === 'teamSetup' || name === 'setupSubscriptionPolicy') data.replies[index].sourceRevision = nextSource
          }
        } else data.params[0].sourceRevision = failure === 'missing-initial-request-source' ? undefined : nextSource
      })
    })
  }
}

for (const [scenario, kind] of Object.entries(connectionScenarios)) {
  for (const [index, name] of setupPaths[kind].entries()) {
    for (const failure of ['changed', 'missing']) {
      test(`actual connection helper ${scenario} refuses ${failure} request organization at step ${index + 1}`, async () => {
        const evidence = await assertConnectionRejected(scenario, data => {
          data.params[index].expectedOrganizationId = failure === 'changed' ? nextSource : undefined
          if (failure === 'changed') data.replies[index].organizationId = nextSource
          else delete data.replies[index].organizationId
        })
        assert.equal(evidence.rpc[index].method, 'organization.' + name)
        if (failure === 'missing') assert.equal(evidence.rpc[index].shape.requestOrganizationMatchesAction, null)
        else {
          // A changed first request becomes the anchor, so the next request
          // must fail correlation. Later changed requests fail directly.
          const mismatch = index === 0 ? 1 : index
          assert.equal(evidence.rpc[mismatch].shape.requestOrganizationMatchesAction, false)
          assert.equal(evidence.rpc[index].shape.organizationMatches, true)
        }
      })
    }
  }
}

for (const [expected, received] of [
  ['inspect-new', 'inspect-legacy'], ['inspect-new', 'inspect-cancelled'],
  ['inspect-legacy', 'inspect-new'], ['inspect-legacy', 'inspect-cancelled'],
  ['inspect-cancelled', 'inspect-new'], ['inspect-cancelled', 'inspect-legacy'],
  ['begin-connected', 'begin-publication-refused'], ['begin-publication-refused', 'begin-connected'],
  ['migration-held', 'migration-connected'], ['migration-connected', 'migration-held']
]) {
  test(`actual connection helper ${expected} cannot accept the complete ${received} state`, async () => {
    await assertConnectionRejected(expected, data => { data.replies = connectionReplies(received).replies })
  })
}

for (const scenario of ['migration-held', 'migration-connected']) {
  for (const [failure, mutate] of Object.entries({
    'ineligible-preflight': data => { data.replies[1].canMigrateHostIdentity = false },
    'wrong-preflight-identity': data => { data.replies[1].hostIdentityState = 'current' },
    'wrong-preflight-stage': data => { data.replies[1].stage = scenario === 'migration-held' ? 'cancelled' : 'idle' },
    'wrong-preflight-attempt-presence': data => {
      if (scenario === 'migration-held') data.replies[1].attemptId = secret
      else delete data.replies[1].attemptId
    },
    'invalid-preflight-revision': data => { data.replies[1].connectionRevision = -1 },
    'stale-preflight-operation-revision': data => { data.replies[1].operation.revision = 6 },
    'cancellable-preflight': data => { data.replies[1].canCancel = true },
    'null-preflight-cancelability': data => { data.replies[1].canCancel = null },
    'malformed-preflight-cancelability': data => { data.replies[1].canCancel = secret },
    'wrong-request-source': data => { data.params[3].sourceRevision = nextSource },
    'missing-request-source': data => { data.params[3].sourceRevision = undefined },
    'unfinished-preflight-operation': data => { data.replies[1].operation.state = 'admitted' },
    'stale-request-revision': data => { data.params[3].expectedRevision = 6 },
    'missing-request-revision': data => { delete data.params[3].expectedRevision },
    'missing-begin-attempt': data => { delete data.replies[3].attemptId },
    'unknown-begin-stage': data => { data.replies[3].stage = secret },
    'post-migration-source-drift': data => { data.replies[4].sourceRevision = nextSource },
    'wrong-cancel-eligibility': data => { data.replies[data.terminal].canCancel = !data.replies[data.terminal].canCancel },
    'wrong-migration-eligibility': data => { data.replies[data.terminal].canMigrateHostIdentity = true }
  })) {
    test(`actual connection helper ${scenario} refuses ${failure}`, async () => {
      await assertConnectionRejected(scenario, mutate)
    })
  }
}

test('actual connection helper migration-connected refuses omitted cancellation state after Cancel', async () => {
  await assertConnectionRejected('migration-connected', data => { delete data.replies[1].canCancel })
})

for (const scenario of ['begin-connected', 'begin-publication-refused']) {
  for (const [failure, mutate] of Object.entries({
    'missing-begin-attempt': data => { delete data.replies[0].attemptId },
    'unknown-begin-stage': data => { data.replies[0].stage = secret },
    'wrong-cancel-eligibility': data => { data.replies[data.terminal].canCancel = true },
    'wrong-migration-eligibility': data => { data.replies[data.terminal].canMigrateHostIdentity = true }
  })) {
    test(`actual connection helper ${scenario} refuses ${failure}`, async () => {
      await assertConnectionRejected(scenario, mutate)
    })
  }
}

for (const [failure, mutate] of Object.entries({
  'mismatched-cancel-attempt': data => { data.replies[0].attemptId = nextSource },
  'missing-cancel-request-attempt': data => { delete data.params[0].attemptId },
  'missing-cancel-reply-attempt': data => { delete data.replies[0].attemptId },
  'uncancelled-action-stage': data => { data.replies[0].stage = 'pending' },
  'missing-passive-attempt': data => { delete data.replies[data.terminal].attemptId },
  'still-cancellable': data => { data.replies[data.terminal].canCancel = true },
  'ineligible-for-new-migration': data => { data.replies[data.terminal].canMigrateHostIdentity = false }
})) {
  test(`actual connection helper cancel-migration refuses ${failure}`, async () => {
    await assertConnectionRejected('cancel-migration', mutate)
  })
}

for (const [failure, mutate] of Object.entries({
  'non-retired-recovery-result': data => { data.replies[0].state = 'completed' },
  'wrong-revocation-receipt': data => { data.replies[0].receipt.remoteRevocation = 'confirmed' },
  'missing-revocation-receipt': data => { delete data.replies[0].receipt },
  'remaining-operation': data => { data.replies[data.terminal].operation.state = 'recovery_required' },
  'still-cancellable': data => { data.replies[data.terminal].canCancel = true },
  'migration-eligible': data => { data.replies[data.terminal].canMigrateHostIdentity = true }
})) {
  test(`actual connection helper recover-publication refuses ${failure}`, async () => {
    await assertConnectionRejected('recover-publication', mutate)
  })
}

for (const [failure, mutate] of Object.entries({
  'inactive-preflight': data => { data.replies[1].connectionState = 'disconnected' },
  'legacy-preflight': data => { data.replies[1].hostIdentityState = 'migration_required' },
  'cleanup-preflight': data => { data.replies[1].cleanupPending = true },
  'wrong-preparation-state': data => { data.replies[2].state = 'uncertain' },
  'wrong-preparation-verification': data => { data.replies[2].verification = 'stale' },
  'missing-ticket': data => { delete data.replies[2].ticket },
  'extra-ticket-field': data => { data.replies[2].ticket.privateValue = secret },
  'missing-ticket-field': data => { delete data.replies[2].ticket.nonce },
  'wrong-ticket-version': data => { data.replies[2].ticket.version = 2 },
  'invalid-ticket-sequence': data => { data.replies[2].ticket.sequence = '01' },
  'above-int64-ticket-sequence': data => { data.replies[2].ticket.sequence = '9223372036854775808' },
  'invalid-ticket-nonce': data => { data.replies[2].ticket.nonce = secret },
  'invalid-ticket-binding': data => { data.replies[2].ticket.bindingDigest = secret },
  'invalid-ticket-epoch': data => { data.replies[2].ticket.authorizationEpoch = secret },
  'non-v4-ticket-epoch': data => { data.replies[2].ticket.authorizationEpoch = '11111111-2222-1333-8444-555555555555' },
  'invalid-variant-ticket-epoch': data => { data.replies[2].ticket.authorizationEpoch = '11111111-2222-4333-4444-555555555555' },
  'wrong-ticket-kind': data => { data.replies[2].ticket.kind = 'refresh' },
  'wrong-request-kind': data => { data.params[2].kind = 'refresh' },
  'wrong-ticket-source': data => { data.replies[2].ticket.sourceRevision = nextSource },
  'wrong-ticket-revision': data => { data.replies[2].ticket.priorStoreRevision = 6 },
  'wrong-request-revision': data => { data.params[2].expectedRevision = 6 },
  'wrong-request-source': data => { data.params[2].sourceRevision = nextSource },
  'wrong-request-and-ticket-source': data => { data.params[2].sourceRevision = nextSource; data.replies[2].ticket.sourceRevision = nextSource },
  'matching-but-nonhex-source': data => {
    data.params.forEach(params => { params.sourceRevision = secret })
    for (const [index, name] of setupPaths[data.kind].entries()) {
      if (name === 'teamSetup') data.replies[index].sourceRevision = secret
    }
    data.replies[2].ticket.sourceRevision = secret
    data.params[3].ticket.sourceRevision = secret
    data.replies[3].ticket.sourceRevision = secret
  },
  'missing-request-source': data => { data.params[2].sourceRevision = undefined },
  'missing-request-revision': data => { delete data.params[2].expectedRevision },
  'wrong-execution-request-source': data => { data.params[3].sourceRevision = nextSource },
  'missing-execution-request-source': data => { data.params[3].sourceRevision = undefined },
  'wrong-execution-ticket': data => { data.params[3].ticket.nonce = 'c'.repeat(64) },
  'missing-execution-ticket': data => { delete data.params[3].ticket },
  'altered-execution-reply-ticket': data => { data.replies[3].ticket.nonce = 'c'.repeat(64) },
  'missing-execution-reply-ticket': data => { delete data.replies[3].ticket },
  'altered-execution-request-and-reply-ticket': data => { data.params[3].ticket.nonce = 'c'.repeat(64); data.replies[3].ticket.nonce = 'c'.repeat(64) },
  'wrong-execution-state': data => { data.replies[3].state = 'completed' },
  'wrong-execution-revocation': data => { data.replies[3].receipt.remoteRevocation = 'confirmed' },
  'missing-execution-receipt': data => { delete data.replies[3].receipt },
  'wrong-passive-operation': data => { data.replies[data.terminal].operation.state = 'completed' }
})) {
  test(`actual connection helper settings-disconnect refuses ${failure}`, async () => {
    const evidence = await assertConnectionRejected('settings-disconnect', mutate)
    if (['above-int64-ticket-sequence', 'non-v4-ticket-epoch', 'invalid-variant-ticket-epoch', 'matching-but-nonhex-source'].includes(failure)) {
      const prepared = evidence.rpc.find(row => row.method === 'organization.prepareSetupSubscriptionOperation')
      assert.equal(prepared.shape.preparedTicketValid, false)
      assert.equal(prepared.shape.requestSourceMatchesAction, true)
      assert.equal(prepared.shape.requestRevisionMatchesAction, true)
    }
    if (['wrong-execution-request-source', 'missing-execution-request-source'].includes(failure)) {
      const executed = evidence.rpc.find(row => row.method === 'organization.executeSetupSubscriptionOperation')
      assert.equal(executed.shape.executeTicketMatchesPrepared, true)
      assert.equal(executed.shape.requestSourceMatchesAction, failure === 'wrong-execution-request-source' ? false : null)
    }
  })
}

// Inspect real call expressions, rather than matching synthetic helper text.
test('every connection action call site selects its explicit contract before the original UI checks', () => {
  const scenarios = []
  const visit = node => {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 'completeConnectionAction') {
      assert.ok(ts.isAwaitExpression(node.parent))
      assert.equal(node.arguments.length, 2)
      assert.ok(ts.isStringLiteral(node.arguments[1]))
      scenarios.push(node.arguments[1].text)
    }
    ts.forEachChild(node, visit)
  }
  visit(journeyParsed)
  const counts = Object.fromEntries(Object.keys(connectionScenarios).map(scenario => [scenario, scenarios.filter(value => value === scenario).length]))
  assert.deepEqual(counts, {
    'inspect-new': 3, 'inspect-legacy': 1, 'inspect-cancelled': 1,
    'begin-connected': 3, 'begin-publication-refused': 2,
    'migration-held': 1, 'migration-connected': 1, 'cancel-migration': 1,
    'recover-publication': 2, 'settings-disconnect': 1
  })
  assert.equal(scenarios.length, 16)
  assert.doesNotMatch(journey, /await (begin|recover)\.click\(\)/)
  assert.doesNotMatch(journey, /getByRole\('button', \{ name: '(Set up ChatGPT|Continue with ChatGPT|Disconnect setup connection|Cancel sign-in|Update identity and reconnect)'[^\n]+\.click\(\)/)
  assert.doesNotMatch(connectionHelpers[0].getText(journeyParsed), /\.click\([^)]*\)[\s\S]*\.click\(/)
})
