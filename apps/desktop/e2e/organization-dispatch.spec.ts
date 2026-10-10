/** Native controls use the real profile ledger; blocked fixtures execute no providers. */
import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { buildAppEnv, type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

let fixture: MockBackendFixture | undefined
const root = path.resolve(import.meta.dirname, '../../..')
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
test.setTimeout(180_000)
test.afterEach(async () => { await fixture?.cleanup(); fixture = undefined })

// eslint-disable-next-line no-empty-pattern -- fixture lifecycle is managed by this spec
test('owner pause persists across reload, resumes explicitly, and retains an expired pause without renewing work', async ({}, testInfo) => {
  fixture = await setupMockBackend()
  await waitForAppReady(fixture)
  const { page, sandbox, mock } = fixture
  const env = buildAppEnv(sandbox)
  const run = (script: string) => execFileSync(python, ['-c', script], { cwd: root, env, encoding: 'utf8' })

  const prelude = `
import os,json
from pathlib import Path
from eidolon_cli.organization_store import OrganizationStore
s=OrganizationStore(Path(os.environ['HERMES_HOME'])/'organization'/'state.db')
`

  const ids = JSON.parse(run(prelude + `
ids=[]
with s._write() as c:
 for key in ['Paused objective','Unrelated objective']:
  ident=s._create_objective(c,key,idempotency_key=key,delivery_mode='managed_artifact')
  r=c.execute('SELECT * FROM requests WHERE objective_id=?',(ident,)).fetchone()
  s._pending(c,r,'Scripted owner question; provider calls are not authorized by this fixture.')
  ids.append(ident)
print(json.dumps(ids))
`)) as string[]

  const retained = () => JSON.parse(run(prelude + `
with s._connect() as c:
 print(json.dumps({t:[list(r) for r in c.execute('SELECT * FROM '+t)] for t in ['objectives','objective_budgets','requests','agents']}))
`)) as unknown

  const before = retained()
  const changes: Record<string, unknown>[] = []
  await page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as { method?: string; params?: Record<string, unknown> }

      if (request.method === 'organization.setPaused') {changes.push(request.params ?? {})}
      server.send(message)
    })
  })
  await page.reload()
  await waitForAppReady(fixture)
  await page.evaluate(id => { location.hash = `/objectives/${id}` }, ids[0])
  const form = page.getByRole('region', { name: 'Objective dispatch', exact: true })
  await expect(form.getByText('New dispatch enabled · Dispatch revision: 0', { exact: true })).toBeVisible()
  await form.getByRole('button', { name: 'Pause new dispatch', exact: true }).click()
  await expect(form.getByRole('status')).toHaveText('Dispatch change confirmed · Receipt revision: 1')
  await page.reload()
  await expect(form.getByText('New dispatch paused · Dispatch revision: 1', { exact: true })).toBeVisible()
  await expect(form.getByText('Already claimed stages: 0', { exact: true })).toBeVisible()
  await form.getByRole('button', { name: 'Resume dispatch', exact: true }).click()
  await expect(form.getByRole('status')).toHaveText('Dispatch change confirmed · Receipt revision: 2')
  expect(retained()).toEqual(before)
  await form.getByRole('button', { name: 'Pause new dispatch', exact: true }).click()
  await expect(form.getByRole('status')).toHaveText('Dispatch change confirmed · Receipt revision: 3')
  run(prelude + `
with s._write() as c:
 c.execute('UPDATE objective_budgets SET deadline=0 WHERE objective_id=?',('${ids[0]}',))
`)
  const expired = retained()
  await form.getByRole('button', { name: 'Resume dispatch', exact: true }).click()
  await expect(form.getByRole('alert')).toContainText('Objective deadline reached')
  await expect(form.getByText('New dispatch paused · Dispatch revision: 3', { exact: true })).toBeVisible()
  expect(retained()).toEqual(expired)
  const states = JSON.parse(run(prelude + `print(json.dumps([s._objective_view(i)['dispatchControl'] for i in ${JSON.stringify(ids)}]))`))
  expect(states).toEqual([{ paused: true, revision: 3, runningCount: 0 }, { paused: false, revision: 0, runningCount: 0 }])
  expect(changes.map(item => [item.id, item.paused, item.expectedRevision])).toEqual([[ids[0], true, 0], [ids[0], false, 1], [ids[0], true, 2], [ids[0], false, 3]])
  expect(mock.receivedPrompts).toHaveLength(0)
  await form.scrollIntoViewIfNeeded()
  await page.screenshot({ path: testInfo.outputPath('objective-dispatch-expired.png') })
  await testInfo.attach('dispatch-evidence', { body: JSON.stringify({ ids, changes, states, originalStatePreserved: true, deniedResumePreservedState: true, providerCalls: 0 }), contentType: 'application/json' })
})
