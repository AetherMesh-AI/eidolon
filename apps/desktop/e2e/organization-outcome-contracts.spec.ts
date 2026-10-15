/** Real Electron owner action and SQLite judgment; synthetic pre-reviewed output, no provider calls. */
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
test('binds an explicit owner judgment to the current typed requirement without claiming final completion', async ({}, testInfo) => {
  let seed!: { id: string; contract: string; deliverable: string }
  let run!: (script: string) => string
  fixture = await setupMockBackend({ beforeLaunch: async sandbox => {
    run = (script: string) => execFileSync(python, ['-c', script], { cwd: root, env: buildAppEnv(sandbox), encoding: 'utf8' })
    seed = JSON.parse(run(`
import os,json
from pathlib import Path
from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_metadata import save_metadata,read_descriptor
from tests.eidolon_cli.test_organization_outcome_contracts import prepare
p=Path(os.environ['HERMES_HOME'])/'organization'/'state.db'
s=OrganizationStore(p)
m=read_descriptor(p).get('metadata')
if m is None:
 save_metadata(p,display_name='Outcome review',owner_display_name='Owner',expected_organization_id=None,expected_revision=None,idempotency_key='native-outcome-metadata')
o=prepare(s,'human_judgment')
c=s.claim_next()
s.finish(c,{'summary':'Synthetic reviewed recommendation','deliverable':'Choose A based on supplied facts.'})
s.set_objective_paused(o['id'],True,expected_revision=0,idempotency_key='native-outcome-hold')
o=s.snapshot()['objectives'][0]
print(json.dumps({'id':o['id'],'contract':o['acceptance']['contract']['sha256'],'deliverable':o['acceptance']['deliverableSha256']}))
`))
  } })
  await waitForAppReady(fixture)
  const { page, mock } = fixture
  const writes: Record<string, unknown>[] = []
  await page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as { method?: string; params?: Record<string, unknown> }

      if (request.method === 'organization.recordOutcomeJudgment') {writes.push(request.params ?? {})}
      server.send(message)
    })
    server.onMessage(message => socket.send(message))
  })
  await page.reload()
  await waitForAppReady(fixture)
  await page.evaluate(id => { location.hash = `/objectives/${id}` }, seed.id)
  await page.getByRole('tab', { name: 'Work', exact: true }).click()
  const form = page.getByRole('region', { name: 'One supported recommendation', exact: true })
  await expect(form).toBeVisible()
  const approve = form.getByRole('button', { name: 'Approve this requirement', exact: true })
  await expect(approve).toBeDisabled()
  await form.getByRole('textbox', { name: 'Review reason', exact: true }).fill('I reviewed the exact synthetic recommendation.')
  await form.getByRole('checkbox').check()
  await approve.click()
  await expect(form.getByRole('status')).toContainText('Review recorded.')
  expect(writes).toHaveLength(1)
  expect(writes[0]).toMatchObject({ objectiveId: seed.id, criterionId: 'recommendation', contractSha256: seed.contract,
    deliverableSha256: seed.deliverable, approved: true, confirmed: true })

  const retained = JSON.parse(run(`
import os,json,sqlite3
from pathlib import Path
p=Path(os.environ['HERMES_HOME'])/'organization'/'state.db'
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as c:
 c.row_factory=sqlite3.Row
 print(json.dumps({'judgments':[dict(r) for r in c.execute('SELECT * FROM objective_human_judgments')], 'status':c.execute('SELECT status FROM objective_control WHERE objective_id=?',('${seed.id}',)).fetchone()[0]}))
`)) as { judgments: Record<string, unknown>[]; status: string }

  expect(retained.judgments).toHaveLength(1)
  expect(retained.judgments[0]).toMatchObject({ criterion_id: 'recommendation', contract_sha256: seed.contract, deliverable_sha256: seed.deliverable, approved: 1 })
  expect(retained.status).toBe('reviewing')
  expect(mock.receivedPrompts).toHaveLength(0)
  await page.screenshot({ path: testInfo.outputPath('typed-owner-judgment.png') })
  await page.reload()
  await expect(page.getByText('I reviewed the exact synthetic recommendation.', { exact: false })).toBeVisible()
})
