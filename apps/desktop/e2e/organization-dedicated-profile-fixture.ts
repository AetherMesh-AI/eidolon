/** Electron's real local-profile backends; instrumentation records starts without suppressing them. */
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
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
import { startMockServer } from './mock-server'
import { fixtureLedger, queuedSwitchTitle, switchPython } from './organization-switch-fixture'
import { expect } from './test'

export const profileA = 'observation-a'
export const profileB = 'observation-b'
export const queuedSourceTitle = 'Queued work belonging only to profile A'

export async function setupDedicatedProfiles() {
  const sandbox = createSandbox('dedicated-organization')
  const mock = await startMockServer()
  const observer = path.join(sandbox.root, 'python-observer')
  mkdirSync(observer)
  const homes = [profileA, profileB].map(name => path.join(sandbox.hermesHome, 'profiles', name))

  for (const home of [sandbox.hermesHome, ...homes]) {
    mkdirSync(home, { recursive: true })
    writeMockProviderConfig(home, mock.url, undefined, 'approvals:\n  mode: manual')
    writeEnvFile(home)
  }

  writeFileSync(
    path.join(observer, 'sitecustomize.py'),
    `
import os,sys
if 'serve' in sys.argv:
 from pathlib import Path
 import eidolon_cli.organization_service as service
 from eidolon_cli.config import get_eidolon_home
 original_start=service.OrganizationService.start
 original_recovery=service.start_existing_services
 def recorded_start(self):
  with (Path(get_eidolon_home())/'.fixture-scheduler-starts').open('a') as f: f.write('start\\n')
  return original_start(self)
 def recorded_recovery():
  result=original_recovery()
  (Path(get_eidolon_home())/'.fixture-serve-pid').write_text(str(os.getpid()))
  (Path(get_eidolon_home())/'.fixture-recovery-complete').write_text('complete')
  return result
 service.OrganizationService.start=recorded_start
 service.start_existing_services=recorded_recovery
`
  )
  const env = buildAppEnv(sandbox, { PYTHONPATH: observer })
  let launch: Awaited<ReturnType<typeof launchDesktop>> | undefined

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
    launch = await launchDesktop(env)
    const fixture: MockBackendFixture = { ...launch, mock, mockUrl: mock.url, sandbox, cleanup }
    await waitForAppReady(fixture)

    const routes = await launch.page.evaluate(
      async profiles => {
        const bridge = (
          window as unknown as {
            hermesDesktop: {
              getConnection(profile: string): Promise<{
                baseUrl: string
                profile: string
                sharedRemote?: boolean
                sharedPrimary?: boolean
              }>
            }
          }
        ).hermesDesktop

        const routes = []

        for (const profile of profiles) {
          const route = await bridge.getConnection(profile)

          if (route.profile !== profile || route.sharedRemote || route.sharedPrimary) {
            throw new Error('Fixture requires an actual dedicated local profile descriptor')
          }

          routes.push({
            baseUrl: route.baseUrl,
            profile: route.profile,
            sharedRemote: Boolean(route.sharedRemote),
            sharedPrimary: Boolean(route.sharedPrimary)
          })
        }

        return routes
      },
      [profileA, profileB]
    )

    return {
      fixture,
      routes,
      homes,
      env,
      // Seed only after the caller has installed transport interception and
      // reloaded the renderer while every organization ledger is still empty.
      seedQueuedWork: async () => {
        for (const home of homes) {
          await expect.poll(() => existsSync(path.join(home, '.fixture-recovery-complete')), { timeout: 90_000 }).toBe(true)
          execFileSync(
            switchPython,
            [
              '-c',
              `
import sys
from pathlib import Path
from eidolon_cli.organization_store import OrganizationStore
s=OrganizationStore(Path(sys.argv[1])/'organization'/'state.db')
s.create_objective(sys.argv[2],idempotency_key='native-stopped-profile-queue')
`,
              home,
              home === homes[0] ? queuedSourceTitle : queuedSwitchTitle
            ],
            { cwd: path.resolve(import.meta.dirname, '../../..'), env }
          )
        }
      },
      ledger: (index: number) => fixtureLedger(homes[index], env),
      starts: (index: number) => {
        const marker = path.join(homes[index], '.fixture-scheduler-starts')

        return existsSync(marker) ? readFileSync(marker, 'utf8').trim().split('\n').length : 0
      }
    }
  } catch (error) {
    await cleanup()
    throw error
  }
}
