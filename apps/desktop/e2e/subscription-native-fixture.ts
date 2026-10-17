/** Native gateway/auth/store/sender, synthetic TLS and vault edges only. */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { chmodSync, existsSync, mkdirSync, readdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import type { Page } from '@playwright/test'

import {
  buildAppEnv,
  createSandbox,
  launchDesktop,
  type MockBackendFixture,
  waitForAppReady,
  writeEnvFile,
  writeMockProviderConfig
} from './fixtures'
import { seedIsolatedModelCatalog } from './isolated-model-catalog'
import { startMockServer } from './mock-server'
import { switchPython } from './organization-switch-fixture'
import { expect } from './test'

const root = path.resolve(import.meta.dirname, '../../..')
export const subscriptionModel = 'synthetic-subscription-model'
export const suggestedJobs = 'Prepare clear studio drafts for owner review.'
export const suggestedGoals = 'Deliver reviewed drafts with explicit acceptance criteria.'
export const subscriptionAck =
  'I accept the listed setup-only changes. Calls use my subscription allowance, with no hard token or USD cap. Usage may continue after cancellation. Execution limits and previous charges stay unchanged.'

type Row = Record<string, unknown>
export interface SubscriptionSnapshot {
  model: Row
  metadata: Row[]
  executionPolicy: Row[]
  budget: Row[]
  generations: Row[]
  teams: Row[]
  subscriptionPolicy: Row[]
  attempts: Row[]
  dispatches: Row[]
  outcomes: Row[]
  agents: Row[]
  counts: Record<string, number>
}
export interface SubscriptionEvent extends Row {
  kind: string
  physicalNetworkSend: false
}

// Python's test-only trace has an independent closed schema. Revalidate at
// artifact export so even malformed evidence cannot copy arbitrary strings.
function boundaryDiagnostic(rows: Row[]) {
  const stages = new Set([
    'observer',
    'gateway_rpc',
    'team_rpc',
    'gateway_dispatch',
    'gateway_context',
    'gateway_service',
    'owner_scope',
    'owner_guard',
    'setup_guard',
    'catalog_load',
    'catalog_guard',
    'catalog_descriptor',
    'auth_status',
    'auth_guard',
    'auth_descriptor',
    'auth_lease',
    'auth_lease_guard',
    'store_lock',
    'store_status',
    'store_descriptor',
    'store_grant',
    'store_decrypt',
    'store_fence',
    'store_lease',
    'lease_fence',
    'lease_run',
    'lease_run_nonblocking',
    'lease_send_gate',
    'catalog_http',
    'catalog_http_send',
    'inference_transport',
    'admission_send_gate',
    'acl_descriptor',
    'darwin_acl',
    'filesystem_check',
    'filesystem_identity',
    'subscription_preflight',
    'subscription_release',
    'prepare_send',
    'complete_subscription',
    'admission_create',
    'infer_subscription',
    'accounting_wait',
    'accounting_worker',
    'failed_job',
    'dispatch_job',
    'outcome_job',
    'ledger_connection',
    'organization_write',
    'scheduler_recover',
    'scheduler_claim',
    'scheduler_continue',
    'auth_release',
    'lease_close',
    'attempt_validation',
    'usage_validation',
    'observation_validation',
    'result_validation'
  ])

  const events = new Set([
    'enter',
    'exception',
    'returned_false',
    'overflow',
    'installed',
    'existing_trace',
    'identity_mismatch'
  ])

  const classes = new Set([
    'BlockingIOError',
    'OSError',
    'ValueError',
    'TypeError',
    'AssertionError',
    'KeyError',
    'FileNotFoundError',
    'TimeoutError',
    'OperationalError',
    'OrganizationExecutionError',
    'OwnerPolicyError',
    'BoundaryError',
    'CatalogError',
    'AuthError',
    'StoreError',
    'HttpError',
    'TransportError'
  ])

  const reasons = new Set([
    'connection_unavailable',
    'recursive_use',
    'unsafe_filesystem',
    'filesystem_protection_unavailable',
    'backend_unavailable',
    'corrupt_store',
    'recovery_required',
    'storage_recovery_required',
    'operation_failed',
    'stale_revision',
    'stale_owner',
    'stale_attempt',
    'setup_unavailable',
    'owner_generation_changed',
    'lease_cancelled',
    'lease_used',
    'grant_expired',
    'invalid_grant',
    'invalid_binding',
    'scope_denied',
    'scope_not_authorized',
    'invalid_lease_result',
    'invalid_request',
    'request_failed',
    'locked',
    'corrupt',
    'unreadable',
    'source_conflict',
    'identity_conflict',
    'unconfigured',
    'cancelled',
    'stale_binding',
    'accounting_failed',
    'admission_denied',
    'connect_failed',
    'incomplete_stream',
    'invalid_headers',
    'resource_limit',
    'access_denied',
    'attempt_expired',
    'attempt_pending',
    'auth_attempt_mismatch',
    'authentication_failed',
    'browser_unavailable',
    'disconnect_required',
    'exchange_failed',
    'insufficient_scope',
    'invalid_callback',
    'invalid_identity',
    'invalid_secret',
    'native_reference_mismatch',
    'operation_history_exhausted',
    'operation_mismatch',
    'publication_failed',
    'publication_uncertain',
    'reauthorization_required',
    'refresh_pending',
    'refresh_uncertain',
    'revision_conflict',
    'revision_exhausted',
    'revision_exists',
    'ticket_conflict',
    'ticket_retired',
    'ticket_sequence_exhausted',
    'transition_pending',
    'typed_grant_requires_handoff',
    'value_too_large'
  ])

  const known = (value: unknown, allowed: Set<string>) =>
    typeof value === 'string' && allowed.has(value) ? value : 'other'

  const count = (value: unknown, maximum: number) =>
    Number.isInteger(value) && Number(value) >= 0 && Number(value) <= maximum ? value : null

  return rows.slice(-128).map(value => {
    const row = value && typeof value === 'object' && !Array.isArray(value) ? value : {}

    const identity =
      row.identity && typeof row.identity === 'object' && !Array.isArray(row.identity) ? (row.identity as Row) : {}

    const changes =
      identity.changedFields && typeof identity.changedFields === 'object' && !Array.isArray(identity.changedFields)
        ? (identity.changedFields as Row)
        : {}

    return {
      ...(row.event === 'identity_mismatch'
        ? {
            identity: {
              comparison: known(identity.comparison, new Set(['initial_fd', 'native_acl', 'final_fd', 'final_path'])),
              targetKind: known(identity.targetKind, new Set(['directory', 'regular_file', 'other'])),
              targetScope: known(identity.targetScope, new Set(['private_store', 'profile', 'ancestor', 'other'])),
              changedFields: Object.fromEntries(
                ['device', 'inode', 'mode', 'owner', 'group', 'links', 'changeTime'].map(field => [
                  field,
                  changes[field] === true
                ])
              )
            }
          }
        : {}),
      sequence: count(row.sequence, 2 ** 31 - 1),
      stage: known(row.stage, stages),
      event: known(row.event, events),
      lane: count(row.lane, 32),
      line: count(row.line, 10000),
      errorClass: known(row.errorClass, classes),
      reason: known(row.reason, reasons),
      sqlitePrimaryCode: known(
        row.sqlitePrimaryCode,
        new Set(['SQLITE_BUSY', 'SQLITE_LOCKED', 'SQLITE_CANTOPEN', 'SQLITE_READONLY', 'SQLITE_IOERR', 'SQLITE_FULL'])
      ),
      ledgerHolders: Array.isArray(row.ledgerHolders)
        ? row.ledgerHolders.slice(0, 8).map(value => {
            const holder = value && typeof value === 'object' && !Array.isArray(value) ? (value as Row) : {}

            return {
              lane: count(holder.lane, 32),
              stage: known(holder.stage, new Set(['scheduler_recover', 'scheduler_claim', 'scheduler_continue']))
            }
          })
        : [],
      existingThreadsCovered: row.existingThreadsCovered === true,
      holders: Array.isArray(row.holders)
        ? row.holders.slice(0, 8).map(value => {
            const holder = value && typeof value === 'object' ? (value as Row) : {}

            return { lane: count(holder.lane, 32), stage: known(holder.stage, stages) }
          })
        : []
    }
  })
}

// Observe production WS traffic without replacing, intercepting or replaying it.
// Only this fixed schema survives; payloads and comparison digests never leave
// the observer. Subscribe before owner actions, including on a socket opened at boot.
async function observeSubscriptionRpc(page: Page) {
  const methods = new Set(
    [
      'teamSetup',
      'improveTeamInput',
      'generateTeam',
      'previewTeam',
      'createTeam',
      'setupSubscriptionStatus',
      'beginSetupSubscription',
      'cancelSetupSubscription',
      'setupSubscriptionModels',
      'setupSubscriptionPolicy',
      'previewSetupSubscriptionPolicy',
      'applySetupSubscriptionPolicy',
      'prepareSetupSubscriptionOperation',
      'executeSetupSubscriptionOperation',
      'reconcileSetupSubscriptionOperation',
      'cancelSetupSubscriptionOperation',
      'recoverSetupSubscriptionOperation'
    ].map(name => 'organization.' + name)
  )

  const reasons = new Set([
    'unavailable',
    'rejected',
    'setup_subscription_unavailable',
    'source_conflict',
    'identity_conflict',
    'idempotency_conflict',
    'generation_running',
    'generation_uncertain',
    'generation_failed',
    'generation_in_progress_or_uncertain',
    'organization_not_empty',
    'organization_policy_paused',
    'subscription_attempt_conflict',
    'subscription_outcome_conflict',
    'subscription_completion_unavailable',
    'subscription_local_owner_required',
    'subscription_ledger_corrupt',
    'subscription_schema_unsupported',
    'subscription_platform_unsupported',
    'setup_budget_exhausted_or_blocked',
    'setup_budget_corrupt',
    'setup_budget_operation_conflict',
    'local_owner_capability_required',
    'connection_binding_changed',
    'catalog_evidence_expired',
    'catalog_evidence_required',
    'attempt_running_or_uncertain',
    'authority_or_scope_unknown',
    'enclosing_constraint_or_unknown_scope',
    'generation_conflict',
    'preview_conflict',
    'source_policy_conflict',
    'managed_configuration',
    'managed_scope_unknown',
    'unsupported_managed_scope',
    'unsupported_scope',
    'shared_scope_unsupported'
  ])

  const object = (value: unknown): Row =>
    value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Row) : {}

  const type = (value: unknown) => (value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value)

  const digest = (value: unknown) =>
    typeof value === 'string' ? createHash('sha256').update(value).digest('hex') : undefined

  const choice = (value: unknown, allowed: string[]) =>
    typeof value === 'string' && allowed.includes(value) ? value : 'other'

  const validText = (value: unknown, limit: number) =>
    typeof value === 'string' &&
    !!value.trim() &&
    value.length <= limit &&
    !Array.from(value).some(char => char.charCodeAt(0) < 32 && !'\n\r\t'.includes(char))

  const boundedInteger = (value: unknown, maximum = 1000) =>
    Number.isInteger(value) && Number(value) >= 0 && Number(value) <= maximum

  const rpc: Row[] = []
  const pending = new Map<string, { row: Row; organization?: string; source?: string; field: string }>()
  let dropped = 0
  let malformed = 0
  let closed = false
  type CompletionWaiter = {
    method: string
    key?: string
    socket?: string
    completed: boolean
    cancelled: boolean
    resolve: (row: Row) => void
    reject: (error: Error) => void
    cancel: (error: Error) => void
    timer: ReturnType<typeof setTimeout>
  }
  let completionWaiter: CompletionWaiter | undefined

  const cancel = (waiter: CompletionWaiter, error: Error) => {
    if (completionWaiter !== waiter || waiter.cancelled) {
      return
    }

    waiter.cancelled = true
    clearTimeout(waiter.timer)
    waiter.reject(error)
    waiter.cancel(error)
  }

  const rejectCorrelation = (key?: string) => {
    if (completionWaiter && (key === undefined || completionWaiter.key === key)) {
      cancel(completionWaiter, new Error('subscription_operation_correlation_lost'))
    }
  }

  const complete = (key: string, row: Row) => {
    if (completionWaiter?.key === key && !completionWaiter.completed && !completionWaiter.cancelled) {
      completionWaiter.completed = true
      completionWaiter.resolve(structuredClone(row))
    }
  }

  const session = await page.context().newCDPSession(page)

  const receive = (
    direction: 'sent' | 'received',
    event: {
      requestId: string
      response: { opcode: number; payloadData: string }
    }
  ) => {
    if (closed) {
      return
    }

    if (event.response.opcode !== 1 || event.response.payloadData.length > 1_048_576) {
      if (direction === 'received' && completionWaiter?.socket === event.requestId) {
        rejectCorrelation()
      }

      return
    }

    try {
      const frame = object(JSON.parse(event.response.payloadData))

      if (!['string', 'number'].includes(typeof frame.id) || String(frame.id).length > 128) {
        return
      }

      const key = JSON.stringify([event.requestId, frame.id])

      if (direction === 'sent') {
        if (typeof frame.method !== 'string' || !methods.has(frame.method)) {
          return
        }

        const params = object(frame.params)
        const row: Row = { method: frame.method, phase: 'sent' }
        rpc.push(row)

        if (rpc.length > 64) {
          const removed = rpc.shift()

          if (completionWaiter?.key && pending.get(completionWaiter.key)?.row === removed) {
            rejectCorrelation()
          }

          dropped++
        }

        const alreadyPending = pending.has(key)

        if (alreadyPending) {
          rejectCorrelation(key)
        }

        pending.set(key, {
          row,
          organization: digest(params.expectedOrganizationId),
          source: digest(params.sourceRevision),
          field: choice(params.field, ['jobs', 'goals'])
        })

        if (completionWaiter?.method === frame.method && completionWaiter.key === undefined && !alreadyPending) {
          completionWaiter.key = key
          completionWaiter.socket = event.requestId
        }

        if (pending.size > 128) {
          const evicted = pending.keys().next().value!
          pending.delete(evicted)
          rejectCorrelation(evicted)
          dropped++
        }

        return
      }

      const expected = pending.get(key)

      if (!expected) {
        return
      }

      pending.delete(key)

      if (Object.hasOwn(frame, 'result') === Object.hasOwn(frame, 'error')) {
        malformed++
        rejectCorrelation(key)

        return
      }

      if (frame.error !== undefined) {
        const error = object(frame.error)
        const reason = object(error.data).reason
        Object.assign(expected.row, {
          phase: 'error',
          code:
            [-32700, -32600, -32601, -32602, -32603, 4001, 5076, 5077].includes(Number(error.code)) &&
            typeof error.code === 'number'
              ? error.code
              : 'other',
          reason: typeof reason === 'string' && reasons.has(reason) ? reason : 'other'
        })
        complete(key, expected.row)

        return
      }

      const result = object(frame.result)
      const allowance = object(result.generationAllowance)
      const remaining = object(allowance.remainingByOperation)
      const usage = object(allowance.setupUsage)
      Object.assign(expected.row, {
        phase: 'result',
        shape: {
          result: type(frame.result),
          capabilityVersionOne: result.capabilityVersion === 1,
          organizationMatches:
            expected.organization === undefined ? null : digest(result.organizationId) === expected.organization,
          sourceMatches: expected.source === undefined ? null : digest(result.sourceRevision) === expected.source,
          field: choice(result.field, ['jobs', 'goals']),
          fieldMatches: result.field === expected.field,
          suggestion: type(result.suggestion),
          suggestionValid: validText(result.suggestion, 4000),
          assumptions: type(result.assumptions),
          assumptionsValid:
            Array.isArray(result.assumptions) &&
            result.assumptions.length <= 5 &&
            result.assumptions.every(value => validText(value, 300)),
          state: choice(result.state, ['available', 'blocked', 'configured', 'unconfigured', 'unsupported', 'created']),
          connectionState: choice(result.connectionState, ['absent', 'active', 'refreshing', 'disconnected']),
          allowance: type(result.generationAllowance),
          allowanceStatus: choice(allowance.status, ['available', 'exhausted', 'running', 'uncertain', 'blocked']),
          policyVersionThree: allowance.policyVersion === 3,
          subscriptionMode: allowance.mode === 'subscription_calls',
          noHardTokenGuarantee: allowance.hardTokenGuarantee === false,
          noHardUsdGuarantee: allowance.hardUsdGuarantee === false,
          timeoutValid:
            boundedInteger(allowance.requestTimeoutSeconds, 120) && Number(allowance.requestTimeoutSeconds) >= 1,
          remainingCallsValid:
            boundedInteger(allowance.remainingCalls) &&
            (allowance.status !== 'available' || Number(allowance.remainingCalls) > 0),
          operationKeysValid: Object.keys(remaining).sort().join(',') === 'generateTeam,improveTeamInput',
          operationCountsValid: ['generateTeam', 'improveTeamInput'].every(
            name => boundedInteger(remaining[name]) && Number(remaining[name]) <= Number(allowance.remainingCalls)
          ),
          usage: type(allowance.setupUsage),
          usageVersionOne: usage.version === 1,
          usageTokenStatus: choice(usage.tokenStatus, ['observed', 'unknown']),
          usageCostUnknown: usage.costStatus === 'unknown' && usage.costUsd === null,
          usageTokensValid:
            usage.tokenStatus === 'unknown'
              ? ['inputTokens', 'outputTokens', 'totalTokens'].every(name => usage[name] === null)
              : ['inputTokens', 'outputTokens', 'totalTokens'].every(
                  name => Number.isInteger(usage[name]) && Number(usage[name]) >= 0
                ) && usage.totalTokens === Number(usage.inputTokens) + Number(usage.outputTokens)
        }
      })
      complete(key, expected.row)
    } catch {
      // Never retain parser text, payload bytes or arbitrary exception details.
      malformed++

      if (direction === 'received' && completionWaiter?.socket === event.requestId) {
        rejectCorrelation()
      }
    }
  }

  session.on('Network.webSocketFrameSent', event => receive('sent', event))
  session.on('Network.webSocketFrameReceived', event => receive('received', event))
  await session.send('Network.enable')

  return {
    performOperation: async (
      method: 'organization.improveTeamInput' | 'organization.generateTeam',
      action: () => Promise<unknown>
    ): Promise<Row> => {
      if (
        closed ||
        completionWaiter ||
        !['organization.improveTeamInput', 'organization.generateTeam'].includes(method)
      ) {
        throw new Error('subscription_operation_wait_unavailable')
      }

      let waiter!: CompletionWaiter
      let rejectCancellation!: (error: Error) => void

      const cancellation = new Promise<never>((_resolve, reject) => {
        rejectCancellation = reject
      })

      const completion = new Promise<Row>((resolve, reject) => {
        waiter = {
          method,
          completed: false,
          cancelled: false,
          resolve,
          reject,
          cancel: rejectCancellation,
          // Match readTeamScope's existing renderer deadline; the 120s policy is
          // inside this RPC's preflight/accounting envelope. No request retry.
          timer: setTimeout(() => cancel(waiter, new Error('subscription_operation_timeout')), 180_000)
        }
        completionWaiter = waiter
      })

      // A timeout/cleanup may occur while the single click is still settling.
      void completion.catch(() => undefined)
      void cancellation.catch(() => undefined)

      try {
        const [, completed] = await Promise.race([Promise.all([action(), completion]), cancellation])

        return completed
      } finally {
        cancel(waiter, new Error('subscription_operation_cancelled'))

        if (completionWaiter === waiter) {
          completionWaiter = undefined
        }
      }
    },
    read: () => ({
      enabled: true,
      dropped,
      malformed,
      pending: pending.size,
      rpc: structuredClone(rpc)
    }),
    close: async () => {
      closed = true

      if (completionWaiter) {
        cancel(completionWaiter, new Error('subscription_operation_cancelled'))
      }

      pending.clear()
      await session.detach().catch(() => undefined)
    }
  }
}

function python(code: string, home: string, args: string[] = []) {
  const env = buildAppEnv(
    {
      root: path.dirname(home),
      hermesHome: home,
      userDataDir: path.join(path.dirname(home), 'electron-user-data'),
      cleanup: () => {}
    },
    {
      PYTHONDONTWRITEBYTECODE: '1',
      HOME: path.join(path.dirname(home), 'user-home')
    }
  )

  delete env.PYTHONPATH
  delete env.EIDOLON_HOME

  return execFileSync(switchPython, ['-c', code, home, ...args], {
    cwd: root,
    env,
    encoding: 'utf8'
  })
}

export async function setupNativeSubscription(
  options: {
    legacy?: boolean
    uncertain?: boolean
    recoveryFault?: 'sqlite_dispatch_once' | 'acl_publication_once'
  } = {}
) {
  const sandbox = createSandbox('subscription-native')
  // The production protected-store rejects symlink ancestors, including /var on macOS.
  sandbox.root = realpathSync(sandbox.root)
  sandbox.hermesHome = realpathSync(sandbox.hermesHome)
  sandbox.userDataDir = realpathSync(sandbox.userDataDir)
  chmodSync(sandbox.hermesHome, 0o700)
  const userHome = path.join(sandbox.root, 'user-home')
  mkdirSync(userHome, { mode: 0o700 })
  const mock = await startMockServer()
  const observer = path.join(sandbox.root, 'python-observer')
  const evidence = path.join(sandbox.root, 'evidence')
  mkdirSync(observer)
  mkdirSync(evidence)
  let launch: Awaited<ReturnType<typeof launchDesktop>> | undefined
  let rpcObserver: Awaited<ReturnType<typeof observeSubscriptionRpc>> | undefined

  const records = (prefix: string): Row[] =>
    readdirSync(evidence)
      .filter(name => name.startsWith(prefix) && name.endsWith('.jsonl'))
      .flatMap(name =>
        readFileSync(path.join(evidence, name), 'utf8')
          .split('\n')
          .filter(Boolean)
          .map(line => JSON.parse(line) as Row)
      )

  const events = () => records('subscription-events') as SubscriptionEvent[]

  const snapshot = (): SubscriptionSnapshot =>
    JSON.parse(
      python(
        'import json,sys; from tests.eidolon_cli.subscription_native_fixture import snapshot; print(json.dumps(snapshot(sys.argv[1])))',
        sandbox.hermesHome
      )
    )

  const cleanup = async () => {
    try {
      try {
        await rpcObserver?.close()
      } finally {
        await launch?.app.close()
      }
    } finally {
      try {
        await mock.close()
      } finally {
        sandbox.cleanup()
      }
    }
  }

  try {
    writeMockProviderConfig(
      sandbox.hermesHome,
      mock.url,
      undefined,
      // Disable the unrelated passive update daemon before any Python gateway import.
      seedIsolatedModelCatalog(sandbox.hermesHome, 'updates:\n  check: false\n'),
      128000
    )
    writeEnvFile(sandbox.hermesHome)

    if (options.legacy) {
      python(
        'import sys; from tests.eidolon_cli.subscription_native_fixture import seed_profile; seed_profile(sys.argv[1], legacy=True)',
        sandbox.hermesHome
      )
    }

    execFileSync(
      switchPython,
      [path.join(import.meta.dirname, 'fresh-team-provider-guard.py'), '--seed-metadata', sandbox.hermesHome],
      { cwd: root, encoding: 'utf8' }
    )
    const bootstrap = `import os,sys,runpy\nsys.path.insert(0,${JSON.stringify(root)})\ntry:\n runpy.run_path(${JSON.stringify(path.join(import.meta.dirname, 'fresh-team-provider-guard.py'))})\n from tests.eidolon_cli.subscription_native_fixture import SyntheticEdges\n subscription_fixture = SyntheticEdges(os.environ['HERMES_HOME'], evidence=${JSON.stringify(evidence)}, mode=${JSON.stringify(options.uncertain ? 'uncertain' : 'complete')}, recovery_fault=${options.recoveryFault ? JSON.stringify(options.recoveryFault) : 'None'}).install()\nexcept BaseException:\n raise SystemExit('Synthetic subscription guard could not install')\n`
    writeFileSync(path.join(observer, 'sitecustomize.py'), bootstrap)

    const env = buildAppEnv(sandbox, {
      HOME: userHome,
      PYTHONPATH: observer,
      PYTHONDONTWRITEBYTECODE: '1',
      EIDOLON_FRESH_TEAM_GUARD_DIR: evidence,
      EIDOLON_FRESH_TEAM_ROOT_HOME: sandbox.hermesHome
    })

    delete env.EIDOLON_HOME
    launch = await launchDesktop(env, 'subscription-native-network.cjs')
    rpcObserver = await observeSubscriptionRpc(launch.page)

    const fixture: MockBackendFixture = {
      ...launch,
      mock,
      mockUrl: mock.url,
      sandbox,
      cleanup
    }

    await waitForAppReady(fixture)
    // Boot is isolated on the ordinary synthetic API route. Select the unsupported
    // Codex configuration before opening setup, without inventing a Codex login.
    // The real Check configuration action below observes this source change.
    python(
      "import sys,yaml; from pathlib import Path; p=Path(sys.argv[1])/'config.yaml'; c=yaml.safe_load(p.read_text()); c['model']={'provider':'openai-codex','default':'synthetic-codex-model'}; p.write_text(yaml.safe_dump(c))",
      sandbox.hermesHome
    )
    await expect.poll(() => events().some(row => row.kind === 'installed')).toBe(true)
    expect(records('browser-windows').length).toBeGreaterThan(0)
    expect(records('browser-windows').every(row => row.guardedBeforeCreation === true)).toBe(true)

    const assertQuiet = (calls: number) => {
      expect(events().filter(row => row.kind === 'responses')).toHaveLength(calls)
      expect(events().filter(row => row.kind.startsWith('denied'))).toEqual([])
      expect(records('attempts-')).toEqual([])
      expect(records('browser-denied')).toEqual([])
      expect(records('sends-')).toEqual([])
      expect(mock.receivedPrompts).toEqual([])
    }

    assertQuiet(0)

    return {
      ...fixture,
      evidence,
      performOperation: (method: 'improveTeamInput' | 'generateTeam', action: () => Promise<unknown>) =>
        rpcObserver!.performOperation(`organization.${method}`, action),
      diagnostic: () => {
        const rows = events()
        const windows = records('browser-windows')
        const state = snapshot()

        return {
          version: 1,
          rpc: rpcObserver!.read(),
          boundaries: boundaryDiagnostic(records('subscription-boundaries')),
          eventCounts: Object.fromEntries(
            [
              'installed',
              'consent',
              'oauth-exchange',
              'jwks',
              'models',
              'responses',
              'recovery-fault',
              'recovery-checkpoint',
              'denied-tls',
              'denied-network'
            ].map(kind => [kind, rows.filter(row => row.kind === kind).length])
          ),
          responseOperations: Object.fromEntries(
            ['improve_jobs', 'improve_goals', 'generate'].map(operation => [
              operation,
              rows.filter(row => row.kind === 'responses' && row.operation === operation).length
            ])
          ),
          generationStatusCounts: Object.fromEntries(
            ['running', 'completed', 'failed', 'uncertain'].map(status => [
              status,
              state.generations.filter(row => row.status === status).length
            ])
          ),
          terminalKindCounts: Object.fromEntries(
            ['completed', 'failed', 'incomplete'].map(kind => [
              kind,
              state.outcomes.filter(row => {
                try {
                  return JSON.parse(String(row.outcome_json)).terminal_kind === kind
                } catch {
                  return false
                }
              }).length
            ])
          ),
          guards: {
            browserWindowsObserved: windows.length > 0,
            allBrowserWindowsGuarded: windows.every(row => row.guardedBeforeCreation === true),
            browserDenials: records('browser-denied').length,
            ordinaryProviderAttempts: records('attempts-').length,
            ordinaryProviderSends: records('sends-').length,
            ordinaryProviderPrompts: mock.receivedPrompts.length
          },
          privateStoreExists: existsSync(path.join(sandbox.hermesHome, '.setup-subscription-private'))
        }
      },
      events,
      snapshot,
      assertQuiet,
      installed: () =>
        readdirSync(evidence)
          .filter(name => name.startsWith('installed-') && name.endsWith('.json'))
          .map(name => JSON.parse(readFileSync(path.join(evidence, name), 'utf8')) as Row),
      hasPrivateStore: () => existsSync(path.join(sandbox.hermesHome, '.setup-subscription-private'))
    }
  } catch (error) {
    await cleanup()
    throw error
  }
}
