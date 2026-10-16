/** Real scheduler/executor and owner gateway, using one synthetic in-memory value. */
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import { buildAppEnv, createSandbox, launchDesktop, type MockBackendFixture, waitForAppReady,
  writeEnvFile, writeMockProviderConfig } from './fixtures'
import { seedIsolatedModelCatalog } from './isolated-model-catalog'
import { startHandoffNativeModel } from './organization-credential-handoff-native-model'

const root = path.resolve(import.meta.dirname, '../../..')
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')

export interface HandoffNativeLedger {
  entries: Array<{ id: string; name: string; revision: number; version: number }>
  request: { id: string; type: string; status: string; execution_count: number }
  modelCalls: number
  receipts: Array<{ operationId: string; action: string; entryId: string; revision?: number; version?: number }>
  nativeStorageExercised: false
}

export async function setupHandoffNativeFixture() {
  const sandbox = createSandbox('credential-handoff-proof')
  const rendezvous = randomUUID()
  const model = await startHandoffNativeModel(rendezvous)
  writeMockProviderConfig(sandbox.hermesHome, model.mock.url, undefined,
    seedIsolatedModelCatalog(sandbox.hermesHome, 'approvals:\n  mode: manual\nagent:\n  environment_probe: false\ncompression:\n  enabled: false'), 128000, false)
  writeEnvFile(sandbox.hermesHome)
  writeFileSync(path.join(sandbox.hermesHome, '.credential-handoff-rendezvous'), rendezvous)
  const observer = path.join(sandbox.root, 'credential-handoff-bootstrap')
  mkdirSync(observer)
  writeFileSync(path.join(observer, 'sitecustomize.py'), `
import sys
if 'serve' in sys.argv:
 from scripts.ci.organization_credential_handoff_native_fixture import install
 install()
`)
  const env = buildAppEnv(sandbox, { PYTHONPATH: observer, EIDOLON_NATIVE_CREDENTIAL_HANDOFF_FIXTURE: '1' })

  const command = (mode: 'seed' | 'inspect') => JSON.parse(execFileSync(python,
    ['-m', 'scripts.ci.organization_credential_handoff_native_fixture', mode, sandbox.hermesHome],
    { cwd: root, env: buildAppEnv(sandbox), encoding: 'utf8' }))

  let current: Awaited<ReturnType<typeof launchDesktop>> | undefined

  const cleanup = async () => {
    try {
      // Abort a held model first. Cleanup must never release new work into execution.
      await model.mock.close()
    } finally {
      try { await current?.app.close() } finally { sandbox.cleanup() }
    }
  }

  try {
    const seed = command('seed') as { organizationId: string; ownerId: string; identityId: string; entryId: string; requestId: string }
    current = await launchDesktop(env)
    const fixture: MockBackendFixture = { ...current, mock: model.mock, mockUrl: model.mock.url, sandbox, cleanup }
    await waitForAppReady(fixture)

    return { fixture, seed, model, ledger: () => command('inspect') as HandoffNativeLedger,
      start: async () => {
        writeFileSync(path.join(sandbox.hermesHome, '.credential-handoff-start'), rendezvous)
        await model.mock.waitForHeldCompletion()
      },
      databaseText: () => readdirSync(path.join(sandbox.hermesHome, 'organization'))
        .filter(name => /\.db(?:-wal|-shm)?$/.test(name))
        .map(name => readFileSync(path.join(sandbox.hermesHome, 'organization', name)).toString('latin1')).join('') }
  } catch (error) {
    await cleanup()
    throw error
  }
}
