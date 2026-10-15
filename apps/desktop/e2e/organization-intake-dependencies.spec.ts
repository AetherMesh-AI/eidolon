/** Native exact-owner prerequisite admission; synthetic localhost inference only. */
import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { buildAppEnv, type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

let fixture: MockBackendFixture | undefined
const root = path.resolve(import.meta.dirname, '../../..')
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
test.setTimeout(180_000)
test.afterEach(async () => { await fixture?.cleanup(); fixture = undefined })

for (const stale of [false, true]) {
  // eslint-disable-next-line no-empty-pattern -- synthetic fixture lifecycle is owned here
  test(`accepted prerequisite intake ${stale ? 'rejects drift' : 'pins exactly once'} through native RPC`, async ({}, info) => {
    let run!: (script: string) => string
    let predecessor = ''
    fixture = await setupMockBackend({
      isolateModelCatalog: true,
      modelContextLength: 128000,
      mockServer: { requestIntakeReply: raw => {
        const { organization } = JSON.parse(raw)
        expect(organization.dependencyCandidates.map((item: { objectiveId: string }) => item.objectiveId)).toContain(predecessor)

        if (stale) {run(`
import os,sqlite3
from pathlib import Path
with sqlite3.connect(Path(os.environ['HERMES_HOME'])/'organization'/'state.db') as c:
 c.execute("UPDATE objective_deliverables SET content=content || 'changed after inference snapshot'")
`)}

        return JSON.stringify({ snapshotId: organization.snapshotId, routeId: 'manager', projectIds: [],
          deliveryMode: 'managed_artifact', priority: 'P3', rationale: 'Routine dependent report.',
          evidenceIds: ['owner-request'], acceptanceCriteria: ['Deliver the follow-on report.'], assumptions: [],
          duplicateCandidateIds: [], dependencyCandidateIds: [predecessor], question: null })
      } },
      beforeLaunch: async sandbox => {
        run = script => execFileSync(python, ['-c', script], { cwd: root, env: buildAppEnv(sandbox), encoding: 'utf8' })
        predecessor = run(`
import os
from pathlib import Path
from tests.eidolon_cli.test_organization_dependency_receipts import accepted
from eidolon_cli.organization_metadata import save_metadata
home=Path(os.environ['HERMES_HOME'])
s,ident,_=accepted(home/'organization')
save_metadata(s.path,display_name='Dependency Studio',owner_display_name='Owner',expected_organization_id=None,expected_revision=None,idempotency_key='dependency-studio')
# Only new objectives are atomically paused; predecessor uses real typed acceptance.
with s._write() as c:
 c.execute('CREATE TRIGGER native_dependency_pause AFTER INSERT ON objectives BEGIN INSERT INTO objective_dispatch VALUES (NEW.id,1,1); END')
print(ident)
`).trim()
      }
    })
    await waitForAppReady(fixture)
    const { page, mock } = fixture
    const request = `Use objective ${predecessor} to prepare the follow-on report.`
    await page.getByRole('complementary', { name: 'Eidolon navigation', exact: true }).getByRole('link', { name: 'Home', exact: true }).click()
    await page.getByRole('button', { name: 'Describe a request', exact: true }).click()
    const intake = page.getByRole('region', { name: 'Describe a request', exact: true })
    await intake.getByLabel('Your request', { exact: true }).fill(request)
    await intake.getByRole('button', { name: 'Submit request', exact: true }).click()

    const read = () => JSON.parse(run(`
import os,json,sqlite3
from pathlib import Path
with sqlite3.connect((Path(os.environ['HERMES_HOME'])/'organization'/'state.db').as_uri()+'?mode=ro',uri=True) as c:
 c.row_factory=sqlite3.Row
 print(json.dumps({'intakes':[dict(r) for r in c.execute('SELECT * FROM organization_request_intakes')],
 'edges':[dict(r) for r in c.execute('SELECT * FROM organization_objective_dependencies')],
 'newObjectives':[dict(r) for r in c.execute('SELECT o.id,o.priority,a.manager_id,a.executive_id,d.paused FROM objectives o JOIN objective_assignments a ON a.objective_id=o.id JOIN objective_dispatch d ON d.objective_id=o.id WHERE d.paused=1')],
 'newAttempts':c.execute('SELECT coalesce(sum(r.attempts),0) FROM requests r JOIN objective_dispatch d ON d.objective_id=r.objective_id WHERE d.paused=1').fetchone()[0]}))
`)) as { intakes: Record<string, unknown>[]; edges: Array<{ predecessor_id: string; successor_id: string; record: string }>; newObjectives: Record<string, unknown>[]; newAttempts: number }

    await expect.poll(() => read().intakes[0]?.status, { timeout: 60_000 }).toBe(stale ? 'failed' : 'admitted')
    const retained = read()
    expect(retained.intakes).toHaveLength(1)
    expect(retained.intakes[0]).toMatchObject({ input_tokens: 10, output_tokens: 20 })
    expect(retained.newAttempts).toBe(0)
    expect(mock.receivedPrompts).toHaveLength(1)
    expect(retained.edges).toHaveLength(stale ? 0 : 1)
    expect(retained.newObjectives).toHaveLength(stale ? 0 : 1)

    if (!stale) {
      expect(retained.newObjectives[0]).toMatchObject({ manager_id: 'manager', executive_id: 'executive', priority: 3, paused: 1 })
      expect(retained.edges[0]).toMatchObject({ predecessor_id: predecessor, successor_id: retained.intakes[0].objective_id })
      expect(JSON.parse(retained.edges[0].record).predecessor.receipt).toMatchObject({ objectiveId: predecessor, liveSourceVerified: false })
    }

    await page.reload()
    await waitForAppReady(fixture)
    await page.getByRole('complementary', { name: 'Eidolon navigation', exact: true }).getByRole('link', { name: 'Home', exact: true }).click()
    await page.getByRole('button', { name: 'Describe a request', exact: true }).click()
    await intake.getByText('Recent requests', { exact: true }).click()
    await intake.getByRole('button', { name: new RegExp(`Use objective ${predecessor}`) }).click()
    await expect(intake.getByLabel('Your request', { exact: true })).toHaveValue(request)
    expect(read()).toEqual(retained)
    expect(mock.receivedPrompts).toHaveLength(1)
    await info.attach('accepted-dependency-admission', { contentType: 'application/json', body: JSON.stringify({ stale, predecessor, retained }) })
  })
}
