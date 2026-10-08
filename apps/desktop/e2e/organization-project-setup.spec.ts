/** Native guided configuration drafts never alter the fixture's granted authority. */
import * as fs from 'node:fs'
import { createRequire } from 'node:module'
import * as os from 'node:os'
import * as path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

const yaml = createRequire(import.meta.url)('js-yaml') as { load(value: string): unknown; dump(value: unknown): string }
let fixture: MockBackendFixture | undefined
let root: string | undefined

test.setTimeout(180_000)
test.afterEach(async () => {
  await fixture?.cleanup()
  fixture = undefined
  if (root) {fs.rmSync(root, { recursive: true, force: true }); root = undefined}
})

test('prepares a bounded project draft with cancel and copy but never activates it', async () => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-project-draft-'))
  fixture = await setupMockBackend({ extraConfig: yaml.dump({ organization: {
    read_roots: [root],
    project_grants: [{ id: 'unit-tests', files: ['root0/test_app.py'], execution: { root: 'root0' } }],
    projects: []
  } }) })
  await waitForAppReady(fixture)
  const { page, mock, sandbox } = fixture
  const configPath = path.join(sandbox.hermesHome, 'config.yaml')
  const original = fs.readFileSync(configPath, 'utf8')
  const calls = mock.receivedPrompts.length
  const navigation = page.getByRole('complementary', { name: 'Eidolon navigation' })
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
