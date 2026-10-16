/** Actual owner gateway and SQLite; synthetic values never reach an OS credential store. */
import { execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
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

const root = path.resolve(import.meta.dirname, '../../..')
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')

export interface CredentialNativeSeed {
  organizationId: string
  ownerId: string
  agentId: string
  identityId: string
}
export interface CredentialNativeLedger {
  catalog: {
    entries: Array<{ id: string; name: string; description: string; status: string; revision: number; version: number }>
    grants: Array<{ actor: string; scope: string; revision: number; permissions: string[]; bindings: unknown[] }>
    tombstones: Array<{ id: string; status: string; revision: number }>
  }
  receiptCount: number
  auditCount: number
  modelCalls: number
  attempts: number
}

export async function setupCredentialsNativeFixture() {
  const sandbox = createSandbox('credential-owner-proof')
  const mock = await startMockServer()
  writeMockProviderConfig(sandbox.hermesHome, mock.url, undefined,
    seedIsolatedModelCatalog(sandbox.hermesHome, 'approvals:\n  mode: manual'))
  writeEnvFile(sandbox.hermesHome)
  const observer = path.join(sandbox.root, 'credential-fixture-bootstrap')
  mkdirSync(observer)
  writeFileSync(path.join(observer, 'sitecustomize.py'), `
import sys
if 'serve' in sys.argv:
 from scripts.ci.organization_credentials_native_fixture import install
 install()
`)
  const env = buildAppEnv(sandbox, { PYTHONPATH: observer, EIDOLON_NATIVE_CREDENTIAL_FIXTURE: '1' })

  const command = (mode: 'seed' | 'inspect') => JSON.parse(execFileSync(python,
    ['-m', 'scripts.ci.organization_credentials_native_fixture', mode, sandbox.hermesHome],
    { cwd: root, env: buildAppEnv(sandbox, { EIDOLON_NATIVE_CREDENTIAL_FIXTURE: '1' }), encoding: 'utf8' }))

  let current: Awaited<ReturnType<typeof launchDesktop>> | undefined

  const cleanup = async () => {
    try {
      await current?.app.close()
    } finally {
      await mock.close()
      sandbox.cleanup()
    }
  }

  try {
    const seed = command('seed') as CredentialNativeSeed
    current = await launchDesktop(env)
    const fixture: MockBackendFixture = { ...current, mock, mockUrl: mock.url, sandbox, cleanup }
    await waitForAppReady(fixture)

    return { fixture, seed, ledger: () => command('inspect') as CredentialNativeLedger,
      backendPid: () => {
        const pid = Number(readFileSync(path.join(sandbox.hermesHome, '.credential-fixture-backend-pid'), 'utf8'))

        if (!Number.isSafeInteger(pid) || pid <= 0) {
          throw new Error('Invalid synthetic credential backend PID marker')
        }

        return pid
      },
      restart: async () => {
        await current!.app.close()
        current = await launchDesktop(env)
        Object.assign(fixture, current)
        await waitForAppReady(fixture)
      } }
  } catch (error) {
    await cleanup()
    throw error
  }
}
