/** A real secondary gateway in a temporary home. No installed runtime or real credentials. */
import { execFileSync, spawn } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync } from 'node:fs'
import { createServer } from 'node:net'
import path from 'node:path'

import { buildAppEnv, type Sandbox, writeEnvFile, writeMockProviderConfig } from './fixtures'

const root = path.resolve(import.meta.dirname, '../../..')
export const switchPython = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
export const queuedSwitchTitle = 'Queued work must remain stopped during source observation'

export function fixtureLedger(home: string, env: Record<string, string>) {
  return execFileSync(
    switchPython,
    [
      '-c',
      `
import sqlite3,sys
from pathlib import Path
p=Path(sys.argv[1])/'organization'/'state.db'
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as c:
 print('\\n'.join(c.iterdump()))
`,
      home
    ],
    { cwd: root, env, encoding: 'utf8' }
  )
}

export async function startOrganizationSwitchGateway(sandbox: Sandbox, mockUrl: string) {
  if (!existsSync(switchPython)) {
    throw new Error('Repository Python environment is required')
  }

  const home = path.join(sandbox.root, 'organization-switch-remote')
  mkdirSync(home, { recursive: true })
  writeMockProviderConfig(home, mockUrl, undefined, 'approvals:\n  mode: manual')
  writeEnvFile(home)
  const marker = path.join(sandbox.root, 'organization-switch-scheduler-starts')
  const token = 'organization-switch-fixture-token'
  const env = { ...buildAppEnv(sandbox), HERMES_HOME: home, HERMES_DASHBOARD_SESSION_TOKEN: token }
  execFileSync(
    switchPython,
    [
      '-c',
      `
import sys
from pathlib import Path
from eidolon_cli.organization_store import OrganizationStore
s=OrganizationStore(Path(sys.argv[1])/'organization'/'state.db')
s.create_objective(sys.argv[2],idempotency_key='native-stopped-queue')
`,
      home,
      queuedSwitchTitle
    ],
    { cwd: root, env }
  )

  const port = await new Promise<number>((resolve, reject) => {
    const server = createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()

      if (!address || typeof address === 'string') {
        server.close()
        reject(new Error('No loopback port'))

        return
      }

      server.close(() => resolve(address.port))
    })
  })

  const url = `http://127.0.0.1:${port}`

  const child = spawn(
    switchPython,
    [
      '-c',
      `
import sys
from pathlib import Path
from eidolon_cli.organization_service import OrganizationService
original=OrganizationService.start
marker=sys.argv[1]
port=sys.argv[2]
def observed_start(self):
 with Path(marker).open('a') as f: f.write('start\\n')
 return original(self)
OrganizationService.start=observed_start
sys.argv=['eidolon','serve','--host','127.0.0.1','--port',port,'--skip-build']
from eidolon_cli.main import main
main()
`,
      marker,
      String(port)
    ],
    { cwd: root, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] }
  )

  let spawnError: Error | undefined
  child.once('error', error => {
    spawnError = error
  })
  let log = ''
  child.stdout.on('data', chunk => {
    log = (log + String(chunk)).slice(-12000)
  })
  child.stderr.on('data', chunk => {
    log = (log + String(chunk)).slice(-12000)
  })

  const close = async () => {
    if (child.exitCode === null && child.signalCode === null && child.pid) {
      const ended = new Promise<void>(resolve => child.once('exit', () => resolve()))
      process.kill(-child.pid, 'SIGTERM')
      await Promise.race([ended, new Promise<void>(resolve => setTimeout(resolve, 5000))])

      if (child.exitCode === null && child.signalCode === null) {
        process.kill(-child.pid, 'SIGKILL')
        await ended
      }
    }
  }

  try {
    const deadline = Date.now() + 90_000
    let ready = false

    while (Date.now() < deadline) {
      if (spawnError) {
        throw spawnError
      }

      if (child.exitCode !== null || child.signalCode !== null) {
        throw new Error(`Fixture gateway exited: ${log}`)
      }

      try {
        ready = (
          await fetch(`${url}/api/status`, {
            headers: { 'X-Hermes-Session-Token': token },
            signal: AbortSignal.timeout(2000)
          })
        ).ok
      } catch {
        /* bounded startup polling */
      }

      if (ready) {
        break
      }

      await new Promise(resolve => setTimeout(resolve, 250))
    }

    if (!ready) {
      throw new Error(`Fixture gateway startup timed out: ${log}`)
    }

    return {
      url,
      home,
      token,
      close,
      ledger: () => fixtureLedger(home, env),
      starts: () => (existsSync(marker) ? readFileSync(marker, 'utf8').trim().split('\n').length : 0)
    }
  } catch (error) {
    await close()
    throw error
  }
}
