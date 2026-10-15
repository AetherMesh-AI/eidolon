/** Real renderer/RPC/single-send localhost inference and durable intake receipts. */
import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { buildAppEnv, type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

let fixture: MockBackendFixture | undefined
const root = path.resolve(import.meta.dirname, '../../..')
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
test.setTimeout(180_000)
test.afterEach(async () => { await fixture?.cleanup(); fixture = undefined })

for (const scenario of ['question', 'admit', 'stale'] as const) {
  // eslint-disable-next-line no-empty-pattern -- fixture lifecycle is owned by this spec
  test(`plain-language intake ${scenario} retains one charged call across reload`, async ({}, testInfo) => {
    let run!: (script: string) => string
    fixture = await setupMockBackend({
      isolateModelCatalog: true,
      modelContextLength: 128000,
      mockServer: { requestIntakeReply: raw => {
        const prompt = JSON.parse(raw)
        const organization = prompt.organization

        return JSON.stringify({
          snapshotId: scenario === 'stale' ? '0'.repeat(64) : organization.snapshotId,
          routeId: 'manager', projectIds: [], deliveryMode: 'managed_artifact', priority: 'P3',
          rationale: 'Routine synthetic status report.', evidenceIds: ['owner-request'],
          acceptanceCriteria: ['Deliver the requested status report.'], assumptions: [],
          duplicateCandidateIds: [], dependencyCandidateIds: [],
          question: scenario === 'question' ? { text: 'Which reporting period should this cover?',
            neededFor: 'The reporting period is absent from the request and supplied context.',
            checkedEvidenceIds: ['owner-request'] } : null
        })
      } },
      beforeLaunch: async sandbox => {
        run = script => execFileSync(python, ['-c', script], { cwd: root, env: buildAppEnv(sandbox), encoding: 'utf8' })
        run(`
import os
from pathlib import Path
from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_metadata import save_metadata
p=Path(os.environ['HERMES_HOME'])/'organization'/'state.db'
s=OrganizationStore(p)
save_metadata(p,display_name='Intake Studio',owner_display_name='Owner',expected_organization_id=None,expected_revision=None,idempotency_key='native-intake-metadata')
# Synthetic fixture fence: create every admitted objective with the ordinary
# paused dispatch state in the SAME transaction, before the scheduler can claim.
# This is not a production default or a stub of admission/claim behavior.
with s._write() as c:
 c.execute('CREATE TRIGGER native_intake_pause AFTER INSERT ON objectives BEGIN INSERT INTO objective_dispatch VALUES (NEW.id,1,1); END')
`)
      }
    })
    await waitForAppReady(fixture)
    const { page, mock } = fixture
    await page.evaluate(() => { location.hash = '/command-center' })
    const intake = page.getByRole('region', { name: 'Describe a request', exact: true })
    await expect(intake).toBeVisible()
    await intake.getByLabel('Your request', { exact: true }).fill('Prepare a status report')
    await intake.getByLabel('Owner priority (optional)', { exact: true }).selectOption('P2')
    await intake.getByRole('button', { name: 'Submit request', exact: true }).click()

    const read = () => JSON.parse(run(`
import os,json,sqlite3
from pathlib import Path
p=Path(os.environ['HERMES_HOME'])/'organization'/'state.db'
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as c:
 c.row_factory=sqlite3.Row
 print(json.dumps({'intakes':[dict(r) for r in c.execute('SELECT * FROM organization_request_intakes')],
 'objectives':[dict(r) for r in c.execute('SELECT o.id,o.priority,a.manager_id,a.executive_id,d.paused FROM objectives o JOIN objective_assignments a ON a.objective_id=o.id JOIN objective_dispatch d ON d.objective_id=o.id')],
 'attempts':c.execute('SELECT coalesce(sum(attempts),0) FROM requests').fetchone()[0]}))
`)) as { intakes: Record<string, unknown>[]; objectives: Record<string, unknown>[]; attempts: number }

    const expected = scenario === 'question' ? 'needs_answer' : scenario === 'admit' ? 'admitted' : 'failed'
    await expect.poll(() => read().intakes[0]?.status, { timeout: 60_000 }).toBe(expected)
    const retained = read()
    expect(retained.intakes).toHaveLength(1)
    expect(retained.intakes[0]).toMatchObject({ input_tokens: 10, output_tokens: 20 })
    expect(Number(retained.intakes[0].input_limit)).toBeGreaterThan(0)
    expect(Number(retained.intakes[0].output_limit)).toBeGreaterThan(0)
    expect(retained.attempts).toBe(0)
    expect(mock.receivedPrompts).toHaveLength(1)

    if (scenario === 'admit') {
      expect(retained.objectives).toHaveLength(1)
      expect(retained.objectives[0]).toMatchObject({ manager_id: 'manager', executive_id: 'executive', priority: 4, paused: 1 })
    } else {
      expect(retained.objectives).toHaveLength(0)
    }

    if (scenario === 'question') {
      await expect(intake.getByText('Which reporting period should this cover?', { exact: true })).toBeVisible()
    }

    await page.reload()
    await waitForAppReady(fixture)
    await page.evaluate(() => { location.hash = '/command-center' })
    await intake.getByText('Recent requests', { exact: true }).click()
    await intake.getByRole('button', { name: 'Refresh recent requests', exact: true }).click()
    await intake.getByRole('button', { name: /Prepare a status report/ }).click()
    await expect(intake.getByLabel('Your request', { exact: true })).toHaveValue('Prepare a status report')

    if (scenario === 'question') {
      await expect(intake.getByText('Which reporting period should this cover?', { exact: true })).toBeVisible()
    }

    expect(read()).toEqual(retained)
    expect(mock.receivedPrompts).toHaveLength(1)
    await intake.screenshot({ path: testInfo.outputPath(`intake-${scenario}.png`) })
  })
}
