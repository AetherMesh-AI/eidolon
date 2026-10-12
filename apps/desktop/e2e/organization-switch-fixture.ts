/** A real secondary gateway in a temporary home. No installed runtime or real credentials. */
import { execFileSync, spawn } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
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

export async function startOrganizationSwitchGateway(sandbox: Sandbox, mockUrl: string, updateBehind = 0) {
  if (!existsSync(switchPython)) {
    throw new Error('Repository Python environment is required')
  }

  const home = path.join(sandbox.root, 'organization-switch-remote')
  mkdirSync(home, { recursive: true })
  writeMockProviderConfig(home, mockUrl, undefined, 'approvals:\n  mode: manual')
  writeEnvFile(home)
  const marker = path.join(sandbox.root, 'organization-switch-scheduler-starts')
  const recoveryComplete = `${marker}.recovery-complete`
  const updateChecks = `${marker}.update-checks`
  const updateAvailability = `${marker}.update-availability`
  writeFileSync(updateAvailability, String(updateBehind))
  const token = 'organization-switch-fixture-token'
  const env = { ...buildAppEnv(sandbox), HERMES_HOME: home, HERMES_DASHBOARD_SESSION_TOKEN: token }

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
# Only availability is scripted. The authenticated route, notification UI and
# update actions remain real; dedicated updater coverage opts into a release.
import eidolon_cli.banner as banner
update_marker=sys.argv[4]
update_availability=Path(sys.argv[5])
def isolated_update_check(*args, **kwargs):
 with Path(update_marker).open('a') as f: f.write('check\\n')
 return int(update_availability.read_text())
banner.check_for_updates=isolated_update_check
import eidolon_cli.organization_service as service_module
from eidolon_cli.organization_service import OrganizationService
recover=service_module.start_existing_services
# Capture the path before the CLI rewrites argv.
recovery_path=sys.argv[3]
def completed_recovery():
 try:
  return recover()
 finally:
  Path(recovery_path).write_text('complete')
service_module.start_existing_services=completed_recovery
original=OrganizationService.start
marker=sys.argv[1]
port=sys.argv[2]
def observed_start(self):
 with Path(marker).open('a') as f: f.write('start\\n')
 return original(self)
OrganizationService.start=observed_start
# Passive evidence only: authentication and update handlers remain unchanged.
import eidolon_cli.web_server as web_server
update_posts=Path(update_marker+'.posts')
@web_server.app.middleware('http')
async def observe_update_posts(request, call_next):
 if request.method == 'POST' and request.url.path in ('/api/eidolon/update', '/api/hermes/update'):
  with update_posts.open('a') as f: f.write('post\\n')
 return await call_next(request)
sys.argv=['eidolon','serve','--host','127.0.0.1','--port',port,'--skip-build']
from eidolon_cli.main import main
main()
`,
      marker,
      String(port),
      recoveryComplete,
      updateChecks,
      updateAvailability
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

      if (ready && existsSync(recoveryComplete)) {
        break
      }

      await new Promise(resolve => setTimeout(resolve, 250))
    }

    if (!ready || !existsSync(recoveryComplete)) {
      throw new Error(`Fixture gateway startup timed out: ${log}`)
    }

    // Startup recovery legitimately starts pre-existing ledgers. Seed only after
    // the empty gateway is ready so observation meets queued work with no scheduler.
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

    return {
      url,
      home,
      token,
      close,
      setUpdateBehind: (behind: 0 | 1) => writeFileSync(updateAvailability, String(behind)),
      updatePosts: () => (existsSync(`${updateChecks}.posts`) ? readFileSync(`${updateChecks}.posts`, 'utf8').trim().split('\n').length : 0),
      updateChecks: () => (existsSync(updateChecks) ? readFileSync(updateChecks, 'utf8').trim().split('\n').length : 0),
      ledger: () => fixtureLedger(home, env),
      starts: () => (existsSync(marker) ? readFileSync(marker, 'utf8').trim().split('\n').length : 0)
    }
  } catch (error) {
    await close()
    throw error
  }
}
