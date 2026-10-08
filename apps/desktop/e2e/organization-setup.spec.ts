/** Real Electron + profile gateway setup reads; no objectives or model calls. */
import * as fs from 'node:fs'
import { createRequire } from 'node:module'
import * as path from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

const yaml = createRequire(import.meta.url)('js-yaml') as { load(value: string): unknown; dump(value: unknown): string }
let fixture: MockBackendFixture | undefined

test.setTimeout(180_000)

test.afterEach(async () => {
  await fixture?.cleanup()
  fixture = undefined
})

test('fresh setup guides existing settings and refreshes known blockers without executing work', async () => {
  fixture = await setupMockBackend()
  await waitForAppReady(fixture)
  const { page, mock, sandbox } = fixture

  const navigation = page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })

  await navigation.getByRole('link', { name: 'Command', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Organization setup', exact: true })
  await expect(panel.getByText('Provider execution not checked', { exact: true })).toBeVisible()
  await panel.getByText('Review roster, repository and background configuration', { exact: true }).click()
  await expect(panel.getByText('Executive, manager and worker roles reported', { exact: true })).toBeVisible()
  await expect(panel.getByText(/Opt-in does not prove a gateway is running/)).toBeVisible()
  const configPath = path.join(sandbox.hermesHome, 'config.yaml')
  const initialConfig = fs.readFileSync(configPath, 'utf8')
  const initialCalls = mock.receivedPrompts.length

  await panel.getByRole('link', { name: 'Review profile model', exact: true }).click()
  await expect(page).toHaveURL(/\/settings\?tab=config:model/)
  await page.getByRole('button', { name: 'Close settings', exact: true }).click()
  await expect(panel).toBeVisible()
  await panel.getByRole('link', { name: 'Review provider accounts', exact: true }).click()
  await expect(page).toHaveURL(/\/settings\?tab=providers/)
  await page.getByRole('button', { name: 'Close settings', exact: true }).click()
  await expect(panel).toBeVisible()
  expect(fs.readFileSync(configPath, 'utf8')).toBe(initialConfig)
  expect(mock.receivedPrompts.length).toBe(initialCalls)

  // Only the fixture's temporary configuration changes. No login or native
  // Codex subprocess is launched by the read-only setup projection.
  const config = yaml.load(initialConfig) as { model: Record<string, unknown> }
  config.model.provider = 'openai-codex'
  config.model.openai_runtime = 'codex_app_server'
  const blockedConfig = yaml.dump(config)
  fs.writeFileSync(configPath, blockedConfig)
  await expect(panel.getByText('Configured transport needs review', { exact: true })).toBeVisible({ timeout: 30_000 })
  await expect(panel.getByText(/This profile selects Codex app-server/)).toBeVisible()
  await page.getByRole('textbox', { name: 'Objective', exact: true }).fill('Draft retained for owner review')
  await expect(page.getByRole('button', { name: 'Create objective', exact: true })).toBeEnabled()
  await panel.getByRole('link', { name: 'Review organization roster', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Organization', exact: true })).toBeVisible()
  await expect(
    page
      .getByRole('region', { name: 'Organization setup', exact: true })
      .getByText('Configured transport needs review', { exact: true })
  ).toBeVisible()
  await page.getByRole('region', { name: 'Organization setup', exact: true }).screenshot({
    path: test.info().outputPath('organization-setup-known-blocker.png')
  })
  expect(fs.readFileSync(configPath, 'utf8')).toBe(blockedConfig)
  expect(mock.receivedPrompts.length).toBe(initialCalls)
})
