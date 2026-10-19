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
      settled = false
    const observed = h.observer.performSetupAction(kind, async () => {
      actions++
      setupPaths[kind].forEach((name, index) => {
        h.send(index + 1, 'organization.' + name)
        if (index < 4) h.receive(index + 1, setupReply(name))
      })
    })
    observed.then(() => {
      settled = true
    })
    h.advance(6000)
    h.receive(99)
    h.receive(5, undefined, 'wrong-socket')
    await flush()
    assert.equal(settled, false)
    assert.equal(h.observer.read().pending, 1)
    h.receive(5)
    const result = await observed
    assert.equal(actions, 1)
    assert.equal(result.steps.length, 5)
    assert.deepEqual(
      Array.from(result.steps, row => row.method),
      setupPaths[kind].map(name => 'organization.' + name)
    )
    assert.ok(result.steps.every(row => row.phase === 'result'))
    assert.deepEqual(h.delays, [15000, 15000, 15000, 15000, 15000])
    assert.equal(h.timers.size, 0)
    assert.ok(!JSON.stringify(result).includes(secret))
    if (kind === 'models') {
      assert.equal(result.steps[3].shape.catalogEpochMatchesPolicy, true)
      assert.equal(result.steps[3].shape.catalogConnectionMatchesPolicy, true)
    }
    await h.observer.close()
  })

  test(`${kind} never-settled terminal read refuses at the production deadline`, async () => {
    const h = await harness()
    let actions = 0
    const observed = h.observer.performSetupAction(kind, async () => {
      actions++
      setupPaths[kind].forEach((name, index) => {
        h.send(index + 1, 'organization.' + name)
        if (index < 4) h.receive(index + 1, setupReply(name))
      })
    })
    h.advance(14999)
    assert.equal(h.timers.size, 1)
    h.advance(1)
    await assert.rejects(observed, /subscription_operation_timeout/)
    h.receive(5)
    assert.equal(actions, 1)
    assert.equal(h.timers.size, 0)
    await h.observer.close()
  })
}

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
  test(`setup sequence refuses ${failure} without a second action or exported private data`, async () => {
    const h = await harness()
    let actions = 0
    const observed = h.observer.performSetupAction('models', async () => {
      actions++
      h.send(1, failure === 'wrong-first' ? 'organization.setupSubscriptionStatus' : 'organization.teamSetup')
      if (failure === 'out-of-order') {
        h.receive(1)
        h.send(2, 'organization.setupSubscriptionPolicy')
      }
      if (failure === 'duplicate') h.send(1, 'organization.teamSetup')
      if (failure === 'same-method-new-id') h.send(2, 'organization.teamSetup')
      if (failure === 'reused-completed-id') {
        h.receive(1)
        h.send(1, 'organization.setupSubscriptionStatus')
        h.receive(1)
      }
      if (failure === 'cross-socket-next-step') {
        h.receive(1)
        h.send(2, 'organization.setupSubscriptionStatus', 'different-socket')
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
  })
}

test('setup action allowlist, old-pending and shared inference guard reject before invoking action', async () => {
  const h = await harness()
  let actions = 0
  await assert.rejects(
    h.observer.performSetupAction('begin', async () => {
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
