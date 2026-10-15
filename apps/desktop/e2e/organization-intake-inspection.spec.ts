/** Native owner opt-in, real bounded source reads, and one durable localhost intake call. */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import { buildAppEnv, type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

interface InspectionItem {
  id: string
  projectId: string
  path: string
  excerpt: string
  sourceSha256: string
  sourceBytes: number
  untrusted: boolean
  verifiedCurrentBlocker: boolean
  acceptanceEvidence: boolean
}
interface IntakePrompt {
  evidence: Array<{ id: string }>
  organization: {
    snapshotId: string
    freshInspection: { status: string; items: InspectionItem[]; authoritySha256: string }
  }
}
interface Ledger {
  policies: Array<{ revision: number; policy_json: string }>
  inspections: Array<{ intake_id: string; status: string; authority_sha256: string; result_json: string }>
  intakes: Array<{ id: string; status: string; input_limit: number; output_limit: number; context_json: string }>
  objectives: Array<{ id: string; manager_id: string; executive_id: string; paused: number }>
  staff: Record<string, unknown>[]
  attempts: number
  toolReceipts: number
}

let fixture: MockBackendFixture | undefined
const root = path.resolve(import.meta.dirname, '../../..')
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
test.setTimeout(180_000)
test.afterEach(async () => { await fixture?.cleanup(); fixture = undefined })

// eslint-disable-next-line no-empty-pattern -- fixture lifecycle is owned by this spec
test('owner-reviewed source inspection is fresh, budgeted once and never replayed on reload', async ({}, testInfo) => {
  let run!: (script: string) => string
  fixture = await setupMockBackend({
    isolateModelCatalog: true,
    modelContextLength: 128000,
    extraConfig: 'approvals:\n  mode: manual',
    mockServer: { requestIntakeReply: raw => {
      const { organization } = JSON.parse(raw) as IntakePrompt

      return JSON.stringify({
        snapshotId: organization.snapshotId,
        routeId: 'manager', projectIds: ['alpha'], deliveryMode: 'managed_artifact', priority: 'P3',
        rationale: 'Summarize the observed synthetic project documentation.',
        evidenceIds: ['owner-request', ...organization.freshInspection.items.map(item => item.id)],
        acceptanceCriteria: ['Explain the project described in the observed README.'], assumptions: [],
        duplicateCandidateIds: [], dependencyCandidateIds: [], question: null
      })
    } },
    beforeLaunch: async sandbox => {
      run = script => execFileSync(python, ['-c', script], { cwd: root, env: buildAppEnv(sandbox), encoding: 'utf8' })
      run(`
import os,yaml
from pathlib import Path
from eidolon_cli.organization_config import load_profile_configuration
from eidolon_cli.organization_metadata import save_metadata
from eidolon_cli.organization_store import OrganizationStore
home=Path(os.environ['HERMES_HOME'])
project=home.parent/'synthetic-project'
project.mkdir()
(project/'README.md').write_text('Initial synthetic context before owner review.',encoding='utf-8')
config_path=home/'config.yaml'
config=yaml.safe_load(config_path.read_text(encoding='utf-8'))
config['organization']={'team':'engineering','capabilities':['work.inspect'],'tool_grants':['read_file'],
 'read_roots':[str(project)],'projects':[{'id':'alpha','root':'root0','recipe':None,'team':'engineering'}],
 'roster':[{'id':'reader','name':'Synthetic reader','team':'engineering',
 'capabilities':['work.inspect'],'tool_grants':['read_file']}]}
config_path.write_text(yaml.safe_dump(config),encoding='utf-8')
settings,_=load_profile_configuration(home)
p=home/'organization'/'state.db'
s=OrganizationStore(p,settings)
save_metadata(p,display_name='Inspection Studio',owner_display_name='Owner',expected_organization_id=None,expected_revision=None,idempotency_key='native-inspection-metadata')
with s._write() as c:
 c.execute("UPDATE staff_state SET active=1 WHERE agent_id='reader'")
 # Fixture-only atomic dispatch fence: production admission still runs, but
 # every admitted objective is paused before the scheduler can claim it.
 c.execute('CREATE TRIGGER native_inspection_pause AFTER INSERT ON objectives BEGIN INSERT INTO objective_dispatch VALUES (NEW.id,1,1); END')
`)
    }
  })
  await waitForAppReady(fixture)
  const { page, mock, sandbox } = fixture
  const configPath = path.join(sandbox.hermesHome, 'config.yaml')
  const originalConfig = readFileSync(configPath, 'utf8')
  const project = path.join(sandbox.root, 'synthetic-project')
  const readme = path.join(project, 'README.md')

  const read = () => JSON.parse(run(`
import os,json,sqlite3
from pathlib import Path
p=Path(os.environ['HERMES_HOME'])/'organization'/'state.db'
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as c:
 c.row_factory=sqlite3.Row
 c.execute('BEGIN')
 print(json.dumps({'policies':[dict(r) for r in c.execute('SELECT * FROM organization_intake_inspection_policies ORDER BY revision')],
 'inspections':[dict(r) for r in c.execute('SELECT * FROM organization_intake_inspections ORDER BY intake_id')],
 'intakes':[dict(r) for r in c.execute('SELECT * FROM organization_request_intakes ORDER BY id')],
 'objectives':[dict(r) for r in c.execute('SELECT o.id,a.manager_id,a.executive_id,d.paused FROM objectives o JOIN objective_assignments a ON a.objective_id=o.id JOIN objective_dispatch d ON d.objective_id=o.id ORDER BY o.id')],
 'staff':[dict(r) for r in c.execute('SELECT * FROM staff_state ORDER BY agent_id')],
 'attempts':c.execute('SELECT coalesce(sum(attempts),0) FROM requests').fetchone()[0],
 'toolReceipts':c.execute('SELECT count(*) FROM tool_receipts').fetchone()[0]}))
`)) as Ledger

  const initial = read()
  expect(initial).toMatchObject({ policies: [], inspections: [], intakes: [], objectives: [], attempts: 0, toolReceipts: 0 })
  await page.getByRole('complementary', { name: 'Eidolon navigation', exact: true }).getByRole('link', { name: 'Home', exact: true }).click()
  await page.getByRole('button', { name: 'Describe a request', exact: true }).click()
  const intake = page.getByRole('region', { name: 'Describe a request', exact: true })
  const controls = intake.getByRole('region', { name: 'Fresh intake context', exact: true })
  await controls.locator('summary').click()
  await controls.getByRole('button', { name: 'Read inspection settings', exact: true }).click()
  await expect(controls.getByRole('checkbox', { name: 'Inspect context for future requests', exact: true })).not.toBeChecked()
  expect(read()).toEqual(initial)
  expect(mock.receivedPrompts).toHaveLength(0)

  await controls.getByRole('checkbox', { name: 'Inspect context for future requests', exact: true }).check()
  await controls.getByLabel('Designated worker', { exact: true }).selectOption('reader')
  await controls.getByRole('checkbox', { name: 'alpha · root0', exact: true }).check()
  await controls.getByLabel('Explicit context paths (one per line, up to four)', { exact: true }).fill('root0/README.md')
  const review = page.getByRole('dialog', { name: 'Enable fresh context inspection?', exact: true })
  await controls.getByRole('button', { name: 'Review inspection change', exact: true }).click()
  await expect(review).toContainText('Synthetic reader · reader')
  await expect(review).toContainText('alpha · root0')
  await expect(review).toContainText('root0/README.md')
  expect(read()).toEqual(initial)
  await review.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(review).toHaveCount(0)
  expect(read()).toEqual(initial)
  expect(mock.receivedPrompts).toHaveLength(0)

  await controls.getByRole('button', { name: 'Review inspection change', exact: true }).click()
  await review.getByRole('button', { name: 'Confirm inspection change', exact: true }).click()
  await expect(controls.getByRole('status')).toHaveText('Inspection settings saved.')
  const enabled = read()
  expect(enabled.policies).toHaveLength(1)
  expect(JSON.parse(enabled.policies[0].policy_json)).toMatchObject({
    enabled: true, revision: 1, workerId: 'reader', projectIds: ['alpha'], paths: ['root0/README.md']
  })
  expect({ ...enabled, policies: [] }).toEqual(initial)
  expect(mock.receivedPrompts).toHaveLength(0)
  expect(readFileSync(configPath, 'utf8')).toBe(originalConfig)

  // Changing bytes after confirmation proves intake reads the current source,
  // rather than reusing context captured while reading or reviewing settings.
  const freshText = 'Alpha is a synthetic offline documentation project.\nFresh context written after owner confirmation.\n'
  writeFileSync(readme, freshText, 'utf8')
  const sourceHash = createHash('sha256').update(freshText).digest('hex')
  const sourceBytes = Buffer.byteLength(freshText)
  const request = 'Summarize the alpha project documentation'
  await intake.getByLabel('Your request', { exact: true }).fill(request)
  await intake.getByRole('button', { name: 'Submit request', exact: true }).click()
  await expect.poll(() => read().intakes[0]?.status, { timeout: 60_000 }).toBe('admitted')
  const retained = read()
  expect(retained.intakes).toHaveLength(1)
  expect(retained.intakes[0]).toMatchObject({ dispatched: 1, input_tokens: 10, output_tokens: 20 })
  expect(retained.intakes[0].input_limit).toBeGreaterThan(0)
  expect(retained.intakes[0].input_limit).toBeLessThanOrEqual(32768)
  expect(retained.intakes[0].output_limit).toBeGreaterThan(0)
  expect(retained.intakes[0].output_limit).toBeLessThanOrEqual(8000)
  expect(retained.objectives).toHaveLength(1)
  expect(retained.objectives[0]).toMatchObject({ manager_id: 'manager', executive_id: 'executive', paused: 1 })
  expect(retained.staff).toEqual(initial.staff)
  expect(retained.policies).toEqual(enabled.policies)
  expect(retained.attempts).toBe(0)
  expect(retained.toolReceipts).toBe(0)
  expect(mock.receivedPrompts).toHaveLength(1)

  const prompt = JSON.parse(mock.receivedPrompts[0]) as IntakePrompt
  const observed = prompt.organization.freshInspection
  expect(observed.status).toBe('observed')
  expect(observed.items).toHaveLength(1)
  expect(observed.items[0]).toMatchObject({
    projectId: 'alpha', path: 'root0/README.md', excerpt: freshText,
    sourceSha256: sourceHash, sourceBytes, untrusted: true,
    verifiedCurrentBlocker: false, acceptanceEvidence: false
  })
  expect(prompt.evidence.map(item => item.id)).toEqual(['owner-request', observed.items[0].id])
  expect(JSON.stringify(prompt)).not.toContain(project)
  expect(JSON.parse(retained.intakes[0].context_json).freshInspection).toEqual(observed)
  expect(retained.inspections).toHaveLength(1)
  expect(retained.inspections[0]).toMatchObject({
    intake_id: retained.intakes[0].id, status: 'completed', authority_sha256: observed.authoritySha256
  })
  expect(JSON.parse(retained.inspections[0].result_json).receipt).toMatchObject({
    authoritySha256: observed.authoritySha256, readBytes: sourceBytes * 2,
    files: [{ path: 'root0/README.md', sha256: sourceHash, bytes: sourceBytes }]
  })
  expect(readFileSync(readme, 'utf8')).toBe(freshText)

  // Reload and explicit history/settings reads must not inspect changed bytes
  // again, reconfigure the delegation, or resend the retained provider call.
  const laterText = 'Changed after admission; this must not replace the retained observation.\n'
  writeFileSync(readme, laterText, 'utf8')
  await page.reload()
  await waitForAppReady(fixture)
  await page.getByRole('complementary', { name: 'Eidolon navigation', exact: true }).getByRole('link', { name: 'Home', exact: true }).click()
  await page.getByRole('button', { name: 'Describe a request', exact: true }).click()
  await controls.locator('summary').click()
  await controls.getByRole('button', { name: 'Read inspection settings', exact: true }).click()
  await expect(controls.getByRole('checkbox', { name: 'Inspect context for future requests', exact: true })).toBeChecked()
  await intake.getByText('Recent requests', { exact: true }).click()
  await intake.getByRole('button', { name: 'Refresh recent requests', exact: true }).click()
  await intake.getByRole('button', { name: new RegExp(request) }).click()
  await expect(intake.getByLabel('Your request', { exact: true })).toHaveValue(request)
  await expect(intake.getByRole('link', { name: 'Open objective', exact: true })).toBeVisible()
  expect(read()).toEqual(retained)
  expect(mock.receivedPrompts).toHaveLength(1)
  expect(readFileSync(configPath, 'utf8')).toBe(originalConfig)
  expect(readFileSync(readme, 'utf8')).toBe(laterText)
  await intake.screenshot({ path: testInfo.outputPath('intake-inspection-retained.png') })
})
