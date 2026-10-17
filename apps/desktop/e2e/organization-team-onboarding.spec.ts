/** Fresh owner UI → authenticated gateway → localhost model → roster → admitted objective. */
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import {
  buildAppEnv,
  setupMockBackend,
  setupNoProvider,
  waitForAppReady,
  waitForOnboarding,
  type MockBackendFixture,
  type NoProviderFixture
} from './fixtures'
import { expect, test } from './test'
import { startMockServer, type MockServer } from './mock-server'

let fixture: MockBackendFixture | NoProviderFixture | undefined
let localProvider: MockServer | undefined
const root = path.resolve(import.meta.dirname, '../../..')
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const team = {
  executive: { name: 'Ash', purpose: 'Coordinate the studio', responsibilities: ['Delegate and review outcomes'] },
  members: [
    {
      id: 'studio-lead',
      name: 'Ember',
      role: 'Manager',
      manager_id: 'executive',
      team: 'studio',
      capabilities: ['request.plan', 'request.integrate'],
      purpose: 'Plan studio work',
      responsibilities: ['Coordinate drafts']
    },
    {
      id: 'studio-writer',
      name: 'Flint',
      role: 'Worker',
      manager_id: 'studio-lead',
      team: 'studio',
      capabilities: ['work.draft'],
      purpose: 'Write scoped drafts',
      responsibilities: ['Produce requested drafts']
    }
  ]
}
test.setTimeout(180_000)
test.afterEach(async () => {
  await fixture?.cleanup()
  fixture = undefined
  await localProvider?.close()
  localProvider = undefined
})

test('fresh home generates an editable named team and creates it only after explicit review', async () => {
  const configuredFixture = await setupMockBackend({
    modelContextLength: 128000,
    mockServer: {
      starterTeamReply: JSON.stringify(team),
      requestIntakeReply: raw => {
        const prompt = JSON.parse(raw)
        return JSON.stringify({
          snapshotId: prompt.organization.snapshotId,
          routeId: 'studio-lead',
          projectIds: [],
          deliveryMode: 'managed_artifact',
          priority: 'P3',
          rationale: 'A synthetic first request needs a reporting period.',
          evidenceIds: ['owner-request'],
          acceptanceCriteria: ['Deliver the requested studio status report.'],
          assumptions: [],
          duplicateCandidateIds: [],
          dependencyCandidateIds: [],
          question: raw.includes('Owner answer: Week ending 2026-10-16')
            ? null
            : {
                text: 'Which reporting period should this cover?',
                neededFor: 'The request does not name a reporting period.',
                checkedEvidenceIds: ['owner-request']
              }
        })
      }
    }
  })
  fixture = configuredFixture
  await waitForAppReady(configuredFixture)
  const { page, mock } = configuredFixture
  const navigation = page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })
  await expect(page).toHaveURL(/#\/home$/)
  await expect(page.locator('.eid-team-entry')).toBeVisible()
  await expect(page.locator('#eid-home-describe-request')).toBeHidden()
  await page.locator('#eid-home-new-objective').click()
  await page.locator('#eid-home-intake > summary').click()
  await expect(page.locator('#eid-home-new-objective')).toBeFocused()
  await page.screenshot({ path: test.info().outputPath('fresh-organization-home.png') })
  await page.locator('.eid-team-entry').getByRole('button', { name: 'Set up organization', exact: true }).click()
  const names = page.getByRole('dialog', { name: 'Organization and owner', exact: true })
  await names.getByLabel('Organization name', { exact: true }).fill('Starter Studio')
  await names.getByLabel('Your display name', { exact: true }).fill('Studio Owner')
  await names.getByRole('button', { name: 'Save names', exact: true }).click()
  await expect(names).toHaveCount(0)
  const dialog = page.getByRole('dialog', { name: 'Build your starter team', exact: true })
  await dialog.getByRole('textbox', { name: 'Work to delegate', exact: true }).fill('Writing and planning for a small studio')
  await dialog.getByRole('textbox', { name: 'What success looks like', exact: true }).fill('Deliver reviewed drafts')
  await dialog.getByLabel('Team naming style (optional)', { exact: true }).fill('Use Ash, Ember and Flint')
  const before = mock.receivedPrompts.length
  await dialog.getByRole('button', { name: 'Generate team · Recommended', exact: true }).click()
  await expect(dialog.getByLabel('Name', { exact: true }).first()).toHaveValue('Ash', { timeout: 60_000 })
  expect(mock.receivedPrompts.length).toBe(before + 1)
  await dialog.getByLabel('Name', { exact: true }).first().fill('Ash Studio')
  await expect(dialog.getByRole('button', { name: 'Create team', exact: true })).toHaveCount(0)
  await dialog.getByRole('button', { name: 'Review changes', exact: true }).click()
  await expect(dialog.getByRole('button', { name: 'Create team', exact: true })).toBeEnabled()
  await dialog.screenshot({ path: test.info().outputPath('starter-team-reviewed.png') })
  await dialog.getByRole('button', { name: 'Create team', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Team saved', exact: true })).toBeVisible()
  expect(mock.receivedPrompts.length).toBe(before + 1)
  const run = (script: string) =>
    execFileSync(python, ['-c', script], {
      cwd: root,
      env: buildAppEnv(configuredFixture.sandbox),
      encoding: 'utf8'
    })
  // Fence only synthetic work dispatch, after real UI creation initialized the
  // fresh organization. Admission and persisted objective creation stay real.
  run(`
import os,sqlite3
from pathlib import Path
p=Path(os.environ['HERMES_HOME'])/'organization'/'state.db'
with sqlite3.connect(p) as c:
 c.execute('CREATE TRIGGER native_first_request_pause AFTER INSERT ON objectives BEGIN INSERT INTO objective_dispatch VALUES (NEW.id,1,1); END')
`)
  await page
    .getByRole('dialog', { name: 'Team saved', exact: true })
    .getByRole('link', { name: 'Describe your first request', exact: true })
    .click()
  await expect(page.locator('#eid-assisted-intake')).toHaveAttribute('open', '')
  await expect(page.locator('#eid-manual-intake')).not.toHaveAttribute('open', '')
  await expect(page.locator('#eid-assisted-intake textarea').first()).toBeFocused()
  await expect(page.locator('#eid-assisted-intake textarea').first()).toBeInViewport()
  await expect(page.locator('#eid-home-describe-request')).toHaveAttribute('data-variant', 'default')
  await expect(page.locator('#eid-home-intake > summary')).toBeInViewport()
  await expect(page.locator('.eid-home-setup > summary')).toBeInViewport()
  await page.screenshot({ path: test.info().outputPath('first-request-continuation.png') })
  expect(mock.receivedPrompts.length).toBe(before + 1)
  const intake = page.getByRole('region', { name: 'Describe a request', exact: true })
  await intake.getByLabel('Your request', { exact: true }).fill('Prepare a studio status report')
  await intake.getByRole('button', { name: 'Submit request', exact: true }).click()
  await expect(intake.getByText('Which reporting period should this cover?', { exact: true })).toBeVisible({
    timeout: 60_000
  })
  await page.screenshot({ path: test.info().outputPath('first-request-response.png') })
  expect(mock.receivedPrompts.length).toBe(before + 2)
  await intake.getByLabel('Your answer', { exact: true }).fill('Week ending 2026-10-16')
  await intake.getByRole('button', { name: 'Submit answer', exact: true }).click()
  // Eligible proposals are admitted automatically by the existing intake
  // contract after explicit owner submission; there is no separate Accept UI.
  const openObjective = intake.getByRole('link', { name: 'Open objective', exact: true })
  await expect(openObjective).toBeVisible({ timeout: 60_000 })
  const retained = JSON.parse(
    run(`
import os,json,sqlite3
from pathlib import Path
p=Path(os.environ['HERMES_HOME'])/'organization'/'state.db'
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as c:
 c.row_factory=sqlite3.Row
 print(json.dumps({'intakes':[dict(r) for r in c.execute('SELECT id,status,parent_intake_id,objective_id FROM organization_request_intakes ORDER BY created')],
 'objectives':[dict(r) for r in c.execute('SELECT o.id,o.title,o.description,a.manager_id,d.paused FROM objectives o JOIN objective_assignments a ON a.objective_id=o.id JOIN objective_dispatch d ON d.objective_id=o.id')],
 'attempts':c.execute('SELECT coalesce(sum(attempts),0) FROM requests').fetchone()[0]}))
`)
  ) as {
    intakes: { id: string; status: string; parent_intake_id: string | null; objective_id: string | null }[]
    objectives: { id: string; title: string; description: string; manager_id: string; paused: number }[]
    attempts: number
  }
  expect(retained.intakes).toHaveLength(2)
  const parent = retained.intakes.find(record => record.status === 'answered')!
  const child = retained.intakes.find(record => record.status === 'admitted')!
  expect(parent).toBeTruthy()
  expect(child).toMatchObject({ parent_intake_id: parent.id })
  expect(retained.objectives).toEqual([{
    id: child.objective_id,
    title: 'Prepare a studio status report',
    description: 'Prepare a studio status report\n\nClarification question: Which reporting period should this cover?\nOwner answer: Week ending 2026-10-16',
    manager_id: 'studio-lead',
    paused: 1
  }])
  expect(retained.attempts).toBe(0)
  expect(mock.receivedPrompts.length).toBe(before + 3)
  await openObjective.click()
  await expect(page).toHaveURL(new RegExp(`#/objectives/${child.objective_id}(?:[?]|$)`))
  await expect(page.locator('.eid-objective-hero')).toBeInViewport()
  await expect.poll(() => page.locator('.eid-workspace').last().evaluate(element => element.scrollTop)).toBe(0)
  await expect(page.locator('.eid-objective-hero').getByRole('heading', { level: 1 })).toHaveText('Prepare a studio status report')
  await expect(page.locator('.eid-objective-hero .eid-result-text')).toHaveText(retained.objectives[0].description)
  await page.screenshot({ path: test.info().outputPath('first-request-created-objective.png') })
  await page.locator('a.eid-back').click()
  await expect(page).toHaveURL(/#\/home\?intake=assisted$/)
  const returnedObjective = page.locator('.eid-home-record').filter({ has: page.locator(`a[href*='/objectives/${child.objective_id}']`) })
  await expect(returnedObjective).toBeVisible()
  await expect(returnedObjective).toContainText('Prepare a studio status report')
  await expect(returnedObjective).toContainText('New dispatch paused')
  await page.screenshot({ path: test.info().outputPath('first-request-return-home.png') })
  await page.reload()
  await navigation.getByRole('link', { name: 'Organization', exact: true }).click()
  await expect(page.getByText('Ash Studio', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Ember', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Flint', { exact: true }).first()).toBeVisible()
  expect(mock.receivedPrompts.length).toBe(before + 3)
})

test('provider-later setup remains a coherent manual journey without any inference', async () => {
  fixture = await setupNoProvider()
  const { page } = fixture
  await waitForOnboarding(page, 90_000)
  await page.getByRole('button', { name: "I'll choose a provider later", exact: true }).click()
  await expect(page).toHaveURL(/#\/home$/)
  await page.screenshot({ path: test.info().outputPath('provider-later-home.png') })
  await page.locator('.eid-team-entry').getByRole('button', { name: 'Set up organization', exact: true }).click()
  const names = page.getByRole('dialog', { name: 'Organization and owner', exact: true })
  await names.getByLabel('Organization name', { exact: true }).fill('Offline Test Studio')
  await names.getByLabel('Your display name', { exact: true }).fill('Test Owner')
  await names.getByRole('button', { name: 'Save names', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Build your starter team', exact: true })
  await expect(dialog.getByRole('button', { name: 'Generate team · Recommended', exact: true })).toBeDisabled()
  await dialog.getByRole('button', { name: 'Model settings', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  const providerDialog = page.getByRole('dialog', { name: "Let's get you setup with Eidolon", exact: true })
  await expect(providerDialog).toBeVisible()
  await expect.poll(() => providerDialog.evaluate(element => element.contains(document.activeElement))).toBe(true)
  for (let index = 0; index < 8; index++) {
    await page.keyboard.press('Tab')
    await expect.poll(() => providerDialog.evaluate(element => element.contains(document.activeElement))).toBe(true)
  }
  await page.keyboard.press('Escape')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Generate team · Recommended', exact: true })).toBeDisabled()
  await dialog.getByText('Set up manually', { exact: true }).click()
  await dialog.getByRole('button', { name: 'Research and writing', exact: true }).click()
  await dialog.getByRole('button', { name: 'Review changes', exact: true }).click()
  await expect(dialog.getByRole('button', { name: 'Create team', exact: true })).toBeEnabled()
  await dialog.screenshot({ path: test.info().outputPath('manual-team-review.png') })
  await dialog.getByRole('button', { name: 'Create team', exact: true }).click()
  const saved = page.getByRole('dialog', { name: 'Team saved', exact: true })
  await expect(
    saved.getByText('A usable model is required before your first request. Review model settings.', { exact: true })
  ).toBeVisible()
  await saved.getByRole('link', { name: 'Describe your first request', exact: true }).click()
  await expect(page.locator('#eid-assisted-intake')).toHaveAttribute('open', '')
  await page.screenshot({ path: test.info().outputPath('manual-first-request-readiness.png') })
})

test('connecting a localhost model returns to the existing team draft with refreshed readiness', async () => {
  localProvider = await startMockServer()
  fixture = await setupNoProvider()
  const { page } = fixture
  await waitForOnboarding(page, 90_000)
  await page.getByRole('button', { name: "I'll choose a provider later", exact: true }).click()
  await page.locator('.eid-team-entry').getByRole('button', { name: 'Set up organization', exact: true }).click()
  const names = page.getByRole('dialog', { name: 'Organization and owner', exact: true })
  await names.getByLabel('Organization name', { exact: true }).fill('Local Model Studio')
  await names.getByLabel('Your display name', { exact: true }).fill('Test Owner')
  await names.getByRole('button', { name: 'Save names', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Build your starter team', exact: true })
  await expect(dialog.getByRole('button', { name: 'Generate team · Recommended', exact: true })).toBeDisabled()
  await dialog.getByRole('button', { name: 'Model settings', exact: true }).click()
  const provider = page.getByRole('dialog', { name: "Let's get you setup with Eidolon", exact: true })
  await provider.getByRole('button', { name: 'I have an API key', exact: true }).click()
  await provider.getByRole('button', { name: /Local \/ custom endpoint/ }).click()
  await provider.getByPlaceholder('http://127.0.0.1:8000/v1', { exact: true }).fill(`${localProvider.url}/v1`)
  await provider.getByRole('button', { name: 'Connect', exact: true }).click()
  await expect(provider).toHaveCount(0)
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('textbox', { name: 'Work to delegate', exact: true })).toBeEnabled()
  await dialog.getByRole('textbox', { name: 'Work to delegate', exact: true }).fill('Write studio reports')
  await dialog.getByRole('textbox', { name: 'What success looks like', exact: true }).fill('Reviewed and useful reports')
  await expect(dialog.getByRole('button', { name: 'Generate team · Recommended', exact: true })).toBeEnabled()
  await expect(dialog.getByText('Model execution has not been tested. Review readiness before requesting work.', { exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: 'Check configuration', exact: true }).click()
  await expect(dialog.getByRole('button', { name: 'Check configuration', exact: true })).toBeEnabled()
  await expect(dialog.getByRole('textbox', { name: 'Work to delegate', exact: true })).toHaveValue('Write studio reports')
  await expect(dialog.getByRole('textbox', { name: 'What success looks like', exact: true })).toHaveValue('Reviewed and useful reports')
  await expect(dialog.getByText('Model execution has not been tested. Review readiness before requesting work.', { exact: true })).toBeVisible()
  await dialog.screenshot({ path: test.info().outputPath('local-provider-unverified-team.png') })
  expect(localProvider.receivedPrompts).toHaveLength(0)
})
