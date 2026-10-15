/** Real renderer and owner RPC; only the operating-system picker is substituted. */
import { execFileSync } from 'node:child_process'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

import { buildAppEnv, launchDesktop, type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { switchPython } from './organization-switch-fixture'
import { expect, test } from './test'

let fixture: MockBackendFixture | undefined
let root: string | undefined
const repo = path.resolve(import.meta.dirname, '../../..')
test.setTimeout(240_000)
test.afterEach(async () => {
  await fixture?.app.close().catch(() => undefined)
  await fixture?.cleanup()
  fixture = undefined

  if (root) { fs.rmSync(root, { recursive: true, force: true }); root = undefined }
})

async function openSetup() {
  const page = fixture!.page
  await page.getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })
    .getByRole('link', { name: 'Organization', exact: true }).click()
  await page.getByRole('button', { name: 'Set up project folders', exact: true }).click()

  return page.getByRole('complementary', { name: 'Agent details', exact: true })
}

test('owner selects two empty folders, approves access, and retains stable projects after restart', async () => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-reviewed-folders-'))
  const folders = ['notes', 'research'].map(name => path.join(root!, name))
  folders.forEach(folder => fs.mkdirSync(folder))
  fixture = await setupMockBackend({
    extraConfig: 'organization:\n  read_roots: []\n  projects: []\n  project_grants: []',
    beforeLaunch: async sandbox => {
      execFileSync(switchPython, ['-c', `
from pathlib import Path
from eidolon_cli.organization_metadata import save_metadata
from eidolon_cli.organization_store import OrganizationStore
import sys
ledger = Path(sys.argv[1])/'organization'/'state.db'
save_metadata(ledger, display_name='Folder Studio', owner_display_name='Owner', expected_organization_id=None, expected_revision=None, idempotency_key='fixture-owner')
OrganizationStore(ledger)
`, sandbox.hermesHome], { cwd: repo, env: buildAppEnv(sandbox) })
    }
  })
  await waitForAppReady(fixture)
  const config = path.join(fixture.sandbox.hermesHome, 'config.yaml')
  const before = fs.readFileSync(config, 'utf8')
  const calls = fixture.mock.receivedPrompts.length
  let panel = await openSetup()

  for (const [index, folder] of folders.entries()) {
    await fixture.app.evaluate(({ dialog }, selected) => {
      dialog.showOpenDialog = (async () => ({ canceled: false, filePaths: [selected] })) as typeof dialog.showOpenDialog
    }, folder)
    const section = panel.getByRole('region', { name: 'Choose a new project folder', exact: true })
    await section.getByRole('button', { name: 'Choose a new project folder', exact: true }).click()
    await expect(section.getByText(folder, { exact: true })).toBeVisible()
    const id = index === 0 ? 'notes' : 'research'
    await section.getByRole('textbox', { name: 'Project ID', exact: true }).fill(id)
    await section.getByRole('button', { name: 'Prepare project folder configuration', exact: true }).click()
    const approve = section.getByRole('button', { name: 'Approve this folder access and save project', exact: true })
    await expect(approve).toBeVisible()
    await expect(panel.getByRole('list', { name: 'Configured project folders', exact: true }).getByText(`${id} · root${index}`, { exact: false })).toHaveCount(0)
    expect(fs.readFileSync(config, 'utf8')).toBe(before)
    expect(fs.readdirSync(folder)).toEqual([])
    await section.screenshot({ path: test.info().outputPath(`folder-${index}-review.png`) })
    await approve.click()
    await expect(section.getByRole('status')).toHaveText('Project saved in this profile’s organization ledger.')
    await expect(panel.getByRole('list', { name: 'Configured project folders', exact: true }).getByRole('listitem')).toHaveCount(index + 1)
    expect(fixture.mock.receivedPrompts.length).toBe(calls)
  }

  await fixture.app.close()
  const launched = await launchDesktop(buildAppEnv(fixture.sandbox))
  fixture.app = launched.app; fixture.page = launched.page
  await waitForAppReady(fixture)
  panel = await openSetup()
  const projects = panel.getByRole('list', { name: 'Configured project folders', exact: true })
  await expect(projects.getByRole('listitem')).toHaveCount(2)
  await expect(projects).toContainText('notes · root0')
  await expect(projects).toContainText('research · root1')
  await panel.screenshot({ path: test.info().outputPath('folders-after-restart.png') })
  expect(fs.readFileSync(config, 'utf8')).toBe(before)
  folders.forEach(folder => expect(fs.readdirSync(folder)).toEqual([]))
  expect(fixture.mock.receivedPrompts.length).toBe(calls)
})
