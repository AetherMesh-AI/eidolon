/** Native review, ledger activation and restart never rewrite the fixture's granted authority. */
import * as fs from 'node:fs'
import { createRequire } from 'node:module'
import * as os from 'node:os'
import * as path from 'node:path'

import { buildAppEnv, launchDesktop, type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

const yaml = createRequire(import.meta.url)('js-yaml') as { load(value: string): unknown; dump(value: unknown): string }
let fixture: MockBackendFixture | undefined
let root: string | undefined

test.setTimeout(360_000)
test.afterEach(async () => {
  // setupMockBackend's cleanup captures its first process; a restart replaces
  // the public handles, so close the current process before removing its home.
  await fixture?.app.close().catch(() => undefined)
  await fixture?.cleanup()
  fixture = undefined

  if (root) {
    fs.rmSync(root, { recursive: true, force: true })
    root = undefined
  }
})

test('prepares a bounded project draft with cancel and copy but never activates it', async () => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-project-draft-'))
  fixture = await setupMockBackend({
    extraConfig: yaml.dump({
      organization: {
        read_roots: [root],
        project_grants: [{ id: 'unit-tests', files: ['root0/test_app.py'], execution: { root: 'root0' } }],
        projects: []
      }
    })
  })
  await waitForAppReady(fixture)
  const { page, mock, sandbox } = fixture
  const configPath = path.join(sandbox.hermesHome, 'config.yaml')
  const original = fs.readFileSync(configPath, 'utf8')
  const calls = mock.receivedPrompts.length

  const navigation = page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })

  await navigation.getByRole('link', { name: 'Organization', exact: true }).click()
  await page.getByRole('button', { name: 'Set up repositories', exact: true }).click()
  const panel = page.getByRole('complementary', { name: 'Agent details', exact: true })
  await expect(panel.getByText('No repositories configured', { exact: true })).toBeVisible()
  await panel.getByRole('textbox', { name: 'Repository ID', exact: true }).fill('abandoned')
  await panel.getByRole('button', { name: 'Cancel changes', exact: true }).click()
  await expect(panel).toHaveCount(0)
  expect(fs.readFileSync(configPath, 'utf8')).toBe(original)

  await page.getByRole('button', { name: 'Set up repositories', exact: true }).click()
  await expect(panel.getByRole('textbox', { name: 'Repository ID', exact: true })).toHaveValue('')
  await panel.getByRole('textbox', { name: 'Repository ID', exact: true }).fill('frontend')
  await panel.getByRole('combobox', { name: 'Configured root', exact: true }).selectOption('root0')
  await panel.getByRole('combobox', { name: 'Execution recipe', exact: true }).selectOption('unit-tests')
  await panel.getByRole('combobox', { name: 'Team', exact: true }).selectOption('general')
  await panel.getByRole('button', { name: 'Prepare repository configuration', exact: true }).click()
  await expect(panel.getByText('Configuration prepared. Nothing has been saved.', { exact: true })).toBeVisible()
  const output = panel.getByRole('textbox', { name: 'Repository configuration YAML', exact: true })
  expect(yaml.load(await output.inputValue())).toEqual([
    { id: 'frontend', root: 'root0', recipe: 'unit-tests', team: 'general' }
  ])
  await panel.getByRole('button', { name: 'Copy', exact: true }).click()
  await expect(panel.getByRole('button', { name: 'Copied', exact: true })).toBeVisible()
  expect(await fixture.app.evaluate(({ clipboard }) => clipboard.readText())).toBe(await output.inputValue())
  await panel.screenshot({ path: test.info().outputPath('project-configuration-draft.png') })
  expect(fs.readFileSync(configPath, 'utf8')).toBe(original)
  expect(mock.receivedPrompts.length).toBe(calls)
  await panel.getByRole('textbox', { name: 'Repository ID', exact: true }).fill('next-project')
  await expect(output).toHaveCount(0)
  await panel.getByRole('button', { name: 'Refresh setup', exact: true }).click()
  await expect(panel.getByRole('textbox', { name: 'Repository ID', exact: true })).toHaveValue('next-project')
  await expect(panel.getByText('No repositories configured', { exact: true })).toBeVisible()
  expect(fs.readFileSync(configPath, 'utf8')).toBe(original)
  expect(mock.receivedPrompts.length).toBe(calls)
})

async function restartFixture() {
  if (!fixture) {
    throw new Error('The isolated fixture has not been started')
  }

  await fixture.app.close()
  const launched = await launchDesktop(buildAppEnv(fixture.sandbox))
  fixture.app = launched.app
  fixture.page = launched.page
  await waitForAppReady(fixture, 120_000)
}

async function openSetup() {
  const page = fixture!.page

  const navigation = page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })

  await navigation.getByRole('link', { name: 'Organization', exact: true }).click()
  await page.getByRole('button', { name: 'Set up repositories', exact: true }).click()

  return page.getByRole('complementary', { name: 'Agent details', exact: true })
}

async function projectChoice() {
  const page = fixture!.page
  await page.getByRole('button', { name: 'Close agent details', exact: true }).click()
  await page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })
    .getByRole('link', { name: 'Command', exact: true })
    .click()
  await page.getByRole('combobox', { name: 'Delivery scope', exact: true }).selectOption('source_project')

  return page.getByRole('checkbox', { name: 'frontend · root0 · general', exact: true })
}

// eslint-disable-next-line no-empty-pattern -- native Electron and backend lifecycles belong to the fixture
test('activates a reviewed ledger identity across restart and fails closed on recipe revocation', async ({}, testInfo) => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-project-registration-'))
  fixture = await setupMockBackend({
    extraConfig: yaml.dump({
      organization: {
        read_roots: [root],
        project_grants: [{ id: 'unit-tests', files: ['root0/test_app.py'], execution: { root: 'root0' } }],
        projects: []
      }
    })
  })
  await waitForAppReady(fixture)
  const { mock, sandbox } = fixture
  const configPath = path.join(sandbox.hermesHome, 'config.yaml')
  const original = fs.readFileSync(configPath, 'utf8')
  const calls = mock.receivedPrompts.length
  const identity = 'frontend · root0 · unit-tests · general'
  let panel = await openSetup()
  await panel.getByRole('textbox', { name: 'Repository ID', exact: true }).fill('frontend')
  await panel.getByRole('combobox', { name: 'Configured root', exact: true }).selectOption('root0')
  await panel.getByRole('combobox', { name: 'Execution recipe', exact: true }).selectOption('unit-tests')
  await panel.getByRole('combobox', { name: 'Team', exact: true }).selectOption('general')
  await panel.getByRole('button', { name: 'Prepare repository configuration', exact: true }).click()
  await expect(panel.getByText('Configuration prepared. Nothing has been saved.', { exact: true })).toBeVisible()
  const output = panel.getByRole('textbox', { name: 'Repository configuration YAML', exact: true })
  const exported = await output.inputValue()
  await panel.getByRole('button', { name: 'Review repository activation', exact: true }).click()
  let review = fixture.page.getByRole('dialog', { name: 'Review repository activation', exact: true })
  await expect(review).toContainText('this profile’s organization ledger')
  await expect(review).toContainText('does not edit YAML, add permissions or start work')

  for (const key of ['Enter', 'Space']) {
    if (key === 'Space') {
      await panel.getByRole('button', { name: 'Review repository activation', exact: true }).click()
      review = fixture.page.getByRole('dialog', { name: 'Review repository activation', exact: true })
    }

    // Real Chromium key activation must activate the focused Cancel button,
    // never the dialog's initially focused Save action.
    const cancel = review.getByRole('button', { name: 'Cancel', exact: true })
    await cancel.focus()
    await cancel.press(key)
    await expect(review).toHaveCount(0)
    await expect(panel.getByText('No repositories configured', { exact: true })).toBeVisible()
    expect(fs.readFileSync(configPath, 'utf8')).toBe(original)
    expect(mock.receivedPrompts.length).toBe(calls)
  }

  await panel.getByRole('button', { name: 'Review repository activation', exact: true }).click()
  review = fixture.page.getByRole('dialog', { name: 'Review repository activation', exact: true })
  await review.getByRole('button', { name: 'Save and activate repository', exact: true }).click()
  await expect(
    panel.getByText('Repository saved in this profile’s organization ledger.', { exact: true })
  ).toBeVisible()
  await expect(
    panel.getByRole('list', { name: 'Configured repositories', exact: true }).getByRole('listitem')
  ).toHaveText([identity])
  await expect(
    panel.getByRole('list', { name: 'Saved in this profile’s ledger', exact: true }).getByRole('listitem')
  ).toHaveText([identity])
  await expect(output).toHaveValue(exported)
  await panel.getByRole('button', { name: 'Copy', exact: true }).click()
  await expect(panel.getByRole('button', { name: 'Copied', exact: true })).toBeVisible()
  expect(await fixture.app.evaluate(({ clipboard }) => clipboard.readText())).toBe(exported)
  expect(fs.readFileSync(configPath, 'utf8')).toBe(original)
  expect(mock.receivedPrompts.length).toBe(calls)
  await panel.screenshot({ path: testInfo.outputPath('01-reviewed-project-ledger-save.png') })
  await expect(await projectChoice()).toBeVisible()

  await restartFixture()
  panel = await openSetup()
  await expect(
    panel.getByRole('list', { name: 'Configured repositories', exact: true }).getByRole('listitem')
  ).toHaveText([identity])
  await expect(
    panel.getByRole('list', { name: 'Saved in this profile’s ledger', exact: true }).getByRole('listitem')
  ).toHaveText([identity])
  expect(fs.readFileSync(configPath, 'utf8')).toBe(original)
  expect(mock.receivedPrompts.length).toBe(calls)
  await panel.screenshot({ path: testInfo.outputPath('02-project-persists-after-native-restart.png') })
  await expect(await projectChoice()).toBeVisible()

  // Only the temporary test profile is edited. The ledger must not restore
  // a revoked recipe, and its retained identity must not become new authority.
  await fixture.app.close()
  const revoked = yaml.load(original) as { organization: { project_grants: unknown[] } }
  revoked.organization.project_grants = []
  const revokedYaml = yaml.dump(revoked)
  fs.writeFileSync(configPath, revokedYaml, 'utf8')
  const relaunched = await launchDesktop(buildAppEnv(sandbox))
  fixture.app = relaunched.app
  fixture.page = relaunched.page
  await waitForAppReady(fixture, 120_000)
  panel = await openSetup()
  await expect(panel.getByText('No repositories configured', { exact: true })).toBeVisible()
  await expect(
    panel.getByRole('list', { name: 'Saved in this profile’s ledger', exact: true }).getByRole('listitem')
  ).toHaveText([identity])
  await expect(panel.getByRole('list', { name: 'Repository conflicts', exact: true })).toBeVisible()
  await expect(panel.getByRole('status').filter({ hasText: /restart/i })).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Prepare repository configuration', exact: true })).toBeDisabled()
  expect(fs.readFileSync(configPath, 'utf8')).toBe(revokedYaml)
  expect(mock.receivedPrompts.length).toBe(calls)
  await panel.screenshot({ path: testInfo.outputPath('03-revoked-recipe-retains-inactive-identity.png') })
  await expect(await projectChoice()).toHaveCount(0)

  // Reinstating the same reviewed grants, then restarting, recovers the one
  // original identity without a second registration or any YAML rewrite.
  await fixture.app.close()
  fs.writeFileSync(configPath, original, 'utf8')
  const restored = await launchDesktop(buildAppEnv(sandbox))
  fixture.app = restored.app
  fixture.page = restored.page
  await waitForAppReady(fixture, 120_000)
  panel = await openSetup()
  await expect(
    panel.getByRole('list', { name: 'Configured repositories', exact: true }).getByRole('listitem')
  ).toHaveText([identity])
  await expect(panel.getByRole('list', { name: 'Repository conflicts', exact: true })).toHaveCount(0)
  expect(fs.readFileSync(configPath, 'utf8')).toBe(original)
  expect(mock.receivedPrompts.length).toBe(calls)
  await panel.screenshot({ path: testInfo.outputPath('04-original-grants-recover-one-identity.png') })
  await testInfo.attach('project-registration-proof', {
    body: JSON.stringify(
      {
        identity,
        nativeRestarts: 3,
        yamlUnchangedByRegistration: true,
        sourcePermissionsNotRestoredByLedger: true,
        inferenceCalls: mock.receivedPrompts.length - calls,
        exportedIdentity: yaml.load(exported)
      },
      null,
      2
    ),
    contentType: 'application/json'
  })
})
