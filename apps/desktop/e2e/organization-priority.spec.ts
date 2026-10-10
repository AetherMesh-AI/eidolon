/** Actual Electron owner UI → authenticated RPC → durable priority transaction. No provider turns. */
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
test('owner priority saves survive reload without resuming blocked work or replacing its identity', async ({}, testInfo) => {
  fixture = await setupMockBackend()
  await waitForAppReady(fixture)
  const { page, sandbox, mock } = fixture
  const env = buildAppEnv(sandbox)
  const run = (script: string) => execFileSync(python, ['-c', script], { cwd: root, env, encoding: 'utf8' })

  const id = run(`
import os
from pathlib import Path
from eidolon_cli.organization_store import OrganizationStore
s=OrganizationStore(Path(os.environ['HERMES_HOME'])/'organization'/'state.db')
with s._write() as c:
 ident=s._create_objective(c,'Reprioritize the retained objective',priority='P5',idempotency_key='native-priority',delivery_mode='managed_artifact')
 r=c.execute('SELECT * FROM requests WHERE objective_id=?',(ident,)).fetchone()
 s._pending(c,r,'Scripted pending intervention: no provider execution authorized by this fixture.')
print(ident)
`).trim()

  const retained = () => JSON.parse(run(`
import os,json,sqlite3
from pathlib import Path
with sqlite3.connect((Path(os.environ['HERMES_HOME'])/'organization'/'state.db').as_uri()+'?mode=ro',uri=True) as c:
 c.row_factory=sqlite3.Row
 o=dict(c.execute('SELECT * FROM objectives WHERE id=?',('${id}',)).fetchone())
 print(json.dumps({'admission':{k:v for k,v in o.items() if k!='priority'},'budgets':[dict(r) for r in c.execute('SELECT * FROM objective_budgets')], 'requests':[{k:r[k] for k in ['id','status','token','lease','attempts','created','available']} for r in c.execute('SELECT * FROM requests')]}))
`)) as unknown

  const before = retained()
  const changes: Record<string, unknown>[] = []
  let loseNext = false
  const lostReplies = new Set<number>()
  await page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as { id: number; method?: string; params?: Record<string, unknown> }

      if (request.method === 'organization.changePriority') {
        changes.push(request.params ?? {})

        if (loseNext) {lostReplies.add(request.id); loseNext = false}
      }

      server.send(message)
    })
    server.onMessage(message => {
      const response = JSON.parse(message.toString()) as { id?: number }

      if (response.id !== undefined && lostReplies.delete(response.id)) {
        // Archive after the real authenticated write commits, before the lost
        // reply triggers the adapter's immediate reconciliation read.
        expect(response).toHaveProperty('result.receipt.priority', 'P3')
        run(`
import os
from pathlib import Path
from eidolon_cli.organization_store import OrganizationStore
s=OrganizationStore(Path(os.environ['HERMES_HOME'])/'organization'/'state.db')
s.cancel('${id}')
s.set_objective_archived('${id}',True,expected_revision=0,idempotency_key='native-priority-archive')
assert s.snapshot()['objectives']==[]
`)
        socket.send(JSON.stringify({ jsonrpc: '2.0', id: response.id, error: { code: -32000, message: 'Scripted lost reply after commit' } }))
      } else {socket.send(message)}
    })
  })
  await page.reload()
  await waitForAppReady(fixture)
  await page.evaluate(value => { location.hash = `/objectives/${value}` }, id)
  const form = page.getByRole('region', { name: 'Objective priority', exact: true })
  await expect(form.getByText('Current priority: P5 · Priority revision: 0', { exact: true })).toBeVisible()
  expect(changes).toHaveLength(0)
  await form.getByRole('combobox', { name: 'New priority' }).selectOption('P1')
  await form.getByRole('button', { name: 'Save priority', exact: true }).click()
  await expect(form.getByText('Saved P5 → P1 (revision 1).', { exact: true })).toBeVisible()
  await expect(form.getByText('Current priority: P1 · Priority revision: 1', { exact: true })).toBeVisible()
  await page.reload()
  // Reload retains the objective route, whose ready state has no chat composer.
  await expect(form.getByText('Current priority: P1 · Priority revision: 1', { exact: true })).toBeVisible()
  await form.getByRole('combobox', { name: 'New priority' }).selectOption('P4')
  await form.getByRole('button', { name: 'Save priority', exact: true }).click()
  await expect(form.getByText('Saved P1 → P4 (revision 2).', { exact: true })).toBeVisible()
  expect(changes).toHaveLength(2)
  expect(changes.map(item => [item.id, item.priority, item.expectedRevision])).toEqual([[id, 'P1', 0], [id, 'P4', 1]])
  expect(retained()).toEqual(before)
  expect(mock.receivedPrompts).toHaveLength(0)
  await page.screenshot({ path: testInfo.outputPath('priority-owner-saved.png') })
  const initialChanges = structuredClone(changes)
  loseNext = true
  await form.getByRole('combobox', { name: 'New priority' }).selectOption('P3')
  await form.getByRole('button', { name: 'Save priority', exact: true }).click()
  await expect(form.getByRole('alert')).toContainText('Scripted lost reply after commit')
  await expect(page.getByText('Archived history', { exact: true })).toBeVisible()
  await expect(form.getByRole('combobox', { name: 'New priority' })).toHaveCount(0)
  await form.getByRole('button', { name: 'Retry priority save', exact: true }).click()
  await expect(form.getByText('Saved P4 → P3 (revision 3).', { exact: true })).toBeVisible()
  expect(changes).toHaveLength(4)
  expect(changes[2]).toEqual(changes[3])
  await expect(form.getByRole('button', { name: 'Retry priority save', exact: true })).toHaveCount(0)
  await expect(form.getByRole('button', { name: 'Save priority', exact: true })).toHaveCount(0)
  expect(mock.receivedPrompts).toHaveLength(0)
  await page.screenshot({ path: testInfo.outputPath('priority-archived-recovery.png') })
  await testInfo.attach('priority-save-evidence', { body: JSON.stringify({ id, changes: initialChanges, retainedStateUnchanged: true, providerCalls: 0, archivedRecovery: { changes: changes.slice(2), activeObjectiveAbsent: true } }), contentType: 'application/json' })
})
