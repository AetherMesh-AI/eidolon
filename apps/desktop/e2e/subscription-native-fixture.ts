/** Native gateway/auth/store/sender, synthetic TLS and vault edges only. */
import { execFileSync } from 'node:child_process'
import { chmodSync, existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, writeFileSync } from 'node:fs'
import path from 'node:path'

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

function python(code: string, home: string, args: string[] = []) {
  const env = buildAppEnv(
    {
      root: path.dirname(home),
      hermesHome: home,
      userDataDir: path.join(path.dirname(home), 'electron-user-data'),
      cleanup: () => {}
    },
    { PYTHONDONTWRITEBYTECODE: '1', HOME: path.join(path.dirname(home), 'user-home') }
  )
  delete env.PYTHONPATH
  delete env.EIDOLON_HOME
  return execFileSync(switchPython, ['-c', code, home, ...args], { cwd: root, env, encoding: 'utf8' })
}

export async function setupNativeSubscription(options: { legacy?: boolean; uncertain?: boolean } = {}) {
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
      await launch?.app.close()
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
      seedIsolatedModelCatalog(sandbox.hermesHome),
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
    const bootstrap = `import os,sys,runpy\nsys.path.insert(0,${JSON.stringify(root)})\ntry:\n runpy.run_path(${JSON.stringify(path.join(import.meta.dirname, 'fresh-team-provider-guard.py'))})\n from tests.eidolon_cli.subscription_native_fixture import SyntheticEdges\n subscription_fixture = SyntheticEdges(os.environ['HERMES_HOME'], evidence=${JSON.stringify(evidence)}, mode=${JSON.stringify(options.uncertain ? 'uncertain' : 'complete')}).install()\nexcept BaseException:\n raise SystemExit('Synthetic subscription guard could not install')\n`
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
    const fixture: MockBackendFixture = { ...launch, mock, mockUrl: mock.url, sandbox, cleanup }
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
