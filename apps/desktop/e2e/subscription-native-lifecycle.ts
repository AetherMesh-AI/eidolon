/** Observation-only teardown fence for this fixture's captured descendants. */
import { type ChildProcess, execFile } from 'node:child_process'

type Identity = { pid: number; birth: number; uid: number }

const reasons = [
  'none',
  'invalid',
  'identity_changed',
  'inspection_denied',
  'inspection_failed',
  'limit',
  'timeout'
] as const

type Reason = (typeof reasons)[number]
type CaptureDiagnostic = { check: string; scope: string; component: string }

function captureDiagnostic(value: unknown): value is CaptureDiagnostic {
  return record(value) && keys(value, ['check', 'scope', 'component']) &&
    ['parent_link', 'snapshot', 'verify', 'duplicate', 'listed_child', 'post_walk', 'unhandled_disappearance'].includes(value.check as string) &&
    ['root', 'descendant', 'unknown'].includes(value.scope as string) &&
    ['birth', 'uid', 'pid', 'ppid', 'presence', 'identity'].includes(value.component as string)
}
type InspectionDiagnostic = { stage: string; category: string }
function inspectionDiagnostic(value: unknown): value is InspectionDiagnostic {
  return record(value) && keys(value, ['stage', 'category']) &&
    ['unspecified', 'process', 'birth', 'uid', 'parent', 'status', 'children', 'child_pid', 'listed_birth', 'observe'].includes(value.stage as string) &&
    ['permission', 'process_lookup', 'os_error', 'attribute', 'type', 'value', 'other', 'access_denied'].includes(value.category as string)
}
type Summary = {
  capturedCount: number
  initialLive: number | null
  remainingLive: number | null
  rootExited: boolean | null
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function keys(value: Record<string, unknown>, expected: string[]): boolean {
  return Object.keys(value).sort().join(',') === [...expected].sort().join(',')
}

function integer(value: unknown, maximum = Number.MAX_SAFE_INTEGER): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 && value <= maximum
}

function identity(value: unknown): value is Identity {
  return (
    record(value) &&
    keys(value, ['pid', 'birth', 'uid']) &&
    integer(value.pid) &&
    value.pid > 0 &&
    typeof value.birth === 'number' &&
    Number.isFinite(value.birth) &&
    value.birth > 0 &&
    integer(value.uid)
  )
}

function same(left: Identity, right: Identity): boolean {
  return left.pid === right.pid && left.birth === right.birth && left.uid === right.uid
}

class LifecycleFailure extends Error {
  constructor(readonly reason: Reason, diagnostic?: CaptureDiagnostic | InspectionDiagnostic) {
    super(`subscription_fixture_lifecycle_${reason}${diagnostic ? ` [${'check' in diagnostic ? `${diagnostic.check}/${diagnostic.scope}/${diagnostic.component}` : `${diagnostic.stage}/${diagnostic.category}`}]` : ''}`)
  }
}

export function createSubscriptionLifecycle(
  child: ChildProcess,
  options: { python: string; script: string; cwd: string }
) {
  let root: Identity | undefined
  let identities: Identity[] | undefined
  let phase: 'created' | 'bound' | 'captured' | 'waiting' | 'settled' | 'refused' = 'created'
  let reason: Reason = 'none'
  let summary: Summary = { capturedCount: 0, initialLive: null, remainingLive: null, rootExited: null }

  const fail = (value: Reason, diagnostic?: CaptureDiagnostic | InspectionDiagnostic): never => {
    if (phase !== 'refused') {
      reason = value
    }

    phase = 'refused'
    throw new LifecycleFailure(value, diagnostic)
  }

  const liveHandle = () => integer(child.pid) && child.pid > 0 && child.exitCode === null && child.signalCode === null

  const run = (input: Record<string, unknown>, timeout: number): Promise<Record<string, unknown>> =>
    new Promise((resolve, reject) => {
      // -I ignores inherited Python paths and user site initialization. The
      // probe imports only psutil/stdlib and never the gateway or test observer.
      const helper = execFile(
        options.python,
        ['-I', options.script],
        {
          cwd: options.cwd,
          encoding: 'utf8',
          timeout,
          killSignal: 'SIGKILL',
          maxBuffer: 16_384
        },
        (error, stdout) => {
          if (error) {
            reject(new LifecycleFailure(error.killed ? 'timeout' : 'inspection_failed'))

            return
          }

          try {
            const value: unknown = JSON.parse(stdout)

            if (!record(value)) {
              throw new LifecycleFailure('invalid')
            }

            resolve(value)
          } catch {
            reject(new LifecycleFailure('invalid'))
          }
        }
      )

      helper.stdin?.on('error', () => reject(new LifecycleFailure('inspection_failed')))

      if (!helper.stdin) {
        reject(new LifecycleFailure('inspection_failed'))
      } else {
        helper.stdin.end(JSON.stringify(input))
      }
    })

  const request = async (input: Record<string, unknown>, timeout: number) => {
    try {
      return await run(input, timeout)
    } catch (error) {
      return fail(error instanceof LifecycleFailure ? error.reason : 'inspection_failed')
    }
  }

  const refusal = (result: Record<string, unknown>, capture = false) => {
    if (capture && result.ok === false && result.reason === 'identity_changed' &&
      keys(result, ['ok', 'reason', 'diagnostic']) && captureDiagnostic(result.diagnostic)) {
      return fail('identity_changed', result.diagnostic)
    }

    if (capture && result.ok === false &&
      (result.reason === 'inspection_failed' || result.reason === 'inspection_denied') &&
      keys(result, ['ok', 'reason', 'diagnostic']) && inspectionDiagnostic(result.diagnostic)) {
      return fail(result.reason, result.diagnostic)
    }

    if (
      result.ok === false &&
      keys(result, ['ok', 'reason']) &&
      reasons.includes(result.reason as Reason) &&
      result.reason !== 'none'
    ) {
      return fail(result.reason as Reason)
    }

    return fail('invalid')
  }

  return {
    async bind() {
      if (phase !== 'created' || !liveHandle() || typeof process.getuid !== 'function') {
        return fail('identity_changed')
      }

      const pid = child.pid!
      const uid = process.getuid()
      const result = await request({ mode: 'root', pid, uid }, 2_000)

      // The original owned ChildProcess must still be alive around the birth
      // capture; a JSON PID alone never authorizes another process observation.
      if (!liveHandle() || child.pid !== pid) {
        return fail('identity_changed')
      }

      if (result.ok !== true) {
        return refusal(result)
      }

      if (
        !keys(result, ['ok', 'root']) ||
        !identity(result.root) ||
        result.root.pid !== pid ||
        result.root.uid !== uid
      ) {
        return fail('invalid')
      }

      root = result.root
      phase = 'bound'
    },
    async capture() {
      if (phase !== 'bound' || !root || !liveHandle() || child.pid !== root.pid) {
        return fail('identity_changed')
      }

      const result = await request({ mode: 'capture', root }, 2_000)

      if (!liveHandle() || child.pid !== root.pid) {
        return fail('identity_changed')
      }

      if (result.ok !== true) {
        return refusal(result, true)
      }

      if (
        !keys(result, ['ok', 'identities']) ||
        !Array.isArray(result.identities) ||
        !result.identities.length ||
        result.identities.length > 65 ||
        !result.identities.every(identity) ||
        !same(result.identities[0], root) ||
        result.identities.some(item => item.uid !== root!.uid) ||
        new Set(result.identities.map(item => item.pid)).size !== result.identities.length
      ) {
        return fail('invalid')
      }

      identities = result.identities
      summary.capturedCount = identities.length
      phase = 'captured'
    },
    async wait() {
      if (phase !== 'captured' || !identities) {
        return fail('invalid')
      }

      phase = 'waiting'
      // This one deadline includes helper startup and all process inspections.
      const result = await request({ mode: 'wait', identities, deadlineEpochMs: Date.now() + 6_000 }, 6_000)

      if (
        !keys(result, ['ok', 'reason', 'capturedCount', 'initialLive', 'remainingLive', 'rootExited']) ||
        typeof result.ok !== 'boolean' ||
        !reasons.includes(result.reason as Reason) ||
        result.capturedCount !== identities.length ||
        !(result.initialLive === null || integer(result.initialLive, identities.length)) ||
        !(result.remainingLive === null || integer(result.remainingLive, identities.length)) ||
        !(result.rootExited === null || typeof result.rootExited === 'boolean')
      ) {
        return fail('invalid')
      }

      summary = {
        capturedCount: identities.length,
        initialLive: result.initialLive as number | null,
        remainingLive: result.remainingLive as number | null,
        rootExited: result.rootExited as boolean | null
      }

      if (result.ok !== true) {
        return fail(result.reason !== 'none' ? (result.reason as Reason) : 'invalid')
      }

      if (
        result.reason !== 'none' ||
        !integer(result.initialLive, identities.length) ||
        result.remainingLive !== 0 ||
        result.rootExited !== true
      ) {
        return fail('invalid')
      }

      phase = 'settled'
      // These are captured-descendant observations, not a guarantee about
      // descendants born/reparented outside the pre-close capture window.
      identities = undefined
      root = undefined
    },
    diagnostic: () => ({ phase, reason, ...summary })
  }
}

/** Always close resources, but never remove a home after uncertain lifecycle. */
export async function cleanupSubscriptionFixture(steps: {
  capture: () => Promise<void>
  observer: () => Promise<unknown>
  app: () => Promise<unknown>
  wait: () => Promise<void>
  mock: () => Promise<unknown>
  remove: () => void
}) {
  let failed = false
  let first: unknown

  for (const close of [steps.capture, steps.observer, steps.app, steps.wait, steps.mock]) {
    try {
      await close()
    } catch (error) {
      if (!failed) {
        first = error
        failed = true
      }
    }
  }

  if (failed) {
    throw first
  }

  steps.remove()
}

/** A teardown-only failure still needs one receipt; preserve its original error. */
export async function cleanupAndReport(cleanup: () => Promise<void>, report: () => Promise<void>) {
  let failed = false
  let first: unknown

  for (const action of [cleanup, report]) {
    try {
      await action()
    } catch (error) {
      if (!failed) {
        failed = true
        first = error
      }
    }
  }

  if (failed) {
    throw first
  }
}
