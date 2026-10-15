/** Real owner transport/SQLite with an explicit test-only dispatch gate. */
import { execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { buildAppEnv, createSandbox, launchDesktop, type MockBackendFixture, waitForAppReady, writeEnvFile, writeMockProviderConfig } from './fixtures'
import { seedIsolatedModelCatalog } from './isolated-model-catalog'
import { startMockServer } from './mock-server'
const root = path.resolve(import.meta.dirname, '../../..')
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
export type GoalLedger = Record<string, Array<Record<string, unknown>>> & { attempts: number; modelCalls: number; objectiveCount: number }
export async function setupGoalNativeFixture() {
  const sandbox = createSandbox('goal-owner-proof')
  const mock = await startMockServer()
  writeMockProviderConfig(sandbox.hermesHome, mock.url, undefined, seedIsolatedModelCatalog(sandbox.hermesHome, 'approvals:\n  mode: manual'))
  writeEnvFile(sandbox.hermesHome)
  const observer = path.join(sandbox.root, 'dispatch-gate'); mkdirSync(observer)
  writeFileSync(path.join(observer, 'sitecustomize.py'), `
import os,sys
if 'serve' in sys.argv:
 from pathlib import Path
 import eidolon_cli.organization_service as service
 original=service.OrganizationService.__init__
 def stopped_dispatch(self,*args,**kwargs):
  kwargs['can_dispatch']=lambda:False
  original(self,*args,**kwargs)
 service.OrganizationService.__init__=stopped_dispatch
 original_recovery=service.start_existing_services
 def recovered():
  result=original_recovery()
  from eidolon_constants import get_eidolon_home
  (Path(get_eidolon_home())/'.goal-fixture-backend-pid').write_text(str(os.getpid()))
  return result
 service.start_existing_services=recovered
`)
  const env = buildAppEnv(sandbox, { PYTHONPATH: observer })
  const command = (mode: 'seed' | 'inspect') => JSON.parse(execFileSync(python,
    ['-m', 'scripts.ci.organization_goal_native_fixture', mode, sandbox.hermesHome], { cwd: root, env: buildAppEnv(sandbox), encoding: 'utf8' }))
  let current: Awaited<ReturnType<typeof launchDesktop>> | undefined
  const cleanup = async () => { try { await current?.app.close() } finally { await mock.close(); sandbox.cleanup() } }
  try {
    const seed = command('seed') as { objectiveId: string }
    current = await launchDesktop(env)
    const fixture: MockBackendFixture = { ...current, mock, mockUrl: mock.url, sandbox, cleanup }
    await waitForAppReady(fixture)
    return { fixture, seed, ledger: () => command('inspect') as GoalLedger,
      backendPid: () => readFileSync(path.join(sandbox.hermesHome, '.goal-fixture-backend-pid'), 'utf8'),
      restart: async () => { await current!.app.close(); current = await launchDesktop(env); Object.assign(fixture, current); await waitForAppReady(fixture) } }
  } catch (error) { await cleanup(); throw error }
}
