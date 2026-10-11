/** Real Electron keyboard activation, temporary sessions, loopback provider only. */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'

import type { Locator } from '@playwright/test'

import { buildAppEnv, createSandbox, launchDesktop, type MockBackendFixture, waitForAppReady, writeEnvFile, writeMockProviderConfig } from './fixtures'
import { startMockServer } from './mock-server'
import { RealSessionBuilder } from './real-session-builder'
import { expect, test } from './test'

const MENU_TITLE = 'Synthetic keyboard menu session'
const HOST_TITLE = 'Synthetic keyboard archived session'
let fixture: MockBackendFixture | undefined

test.setTimeout(240_000)
test.afterEach(async () => {
  await fixture?.cleanup()
  fixture = undefined
})

test('native confirmation keyboard respects focused actions in shared host and session menu', async () => {
  const mock = await startMockServer()
  const sandbox = createSandbox('confirmation-keyboard')
  writeMockProviderConfig(sandbox.hermesHome, mock.url, undefined, '\nauxiliary:\n  title_generation:\n    enabled: false\n')
  writeEnvFile(sandbox.hermesHome)
  let menuId: string
  let hostId: string
  try {
    const builder = await RealSessionBuilder.start(sandbox.hermesHome)
    try {
      menuId = (await builder.createSession({ title: MENU_TITLE, turns: [MENU_TITLE] })).sessionId
      hostId = (await builder.createSession({ title: HOST_TITLE, turns: [HOST_TITLE] })).sessionId
    } finally {
      await builder.close()
    }
    const { app, page } = await launchDesktop(buildAppEnv(sandbox))
    fixture = { app, page, mock, mockUrl: mock.url, sandbox, cleanup: async () => {
      await app.close().catch(() => undefined)
      await mock.close()
      sandbox.cleanup()
    } }
  } catch (error) {
    await mock.close()
    sandbox.cleanup()
    throw error
  }

  const { page } = fixture
  await waitForAppReady(fixture)
  expect(mock.receivedPrompts).toHaveLength(2)
  const captures: Array<{ name: string; sha256: string }> = []
  const decisions: Array<{ surface: string; action: string; remainingRows: number }> = []
  const sourceCommit = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim()
  const sourceTree = execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim()

  function remaining(id: string): number {
    return Number(execFileSync('uv', ['run', '--active', '--no-sync', 'python', '-c',
      'import pathlib,sqlite3,sys; db=sqlite3.connect(pathlib.Path(sys.argv[1]).as_uri()+"?mode=ro",uri=True); print(db.execute("SELECT COUNT(*) FROM sessions WHERE id=?",(sys.argv[2],)).fetchone()[0])',
      join(sandbox.hermesHome, 'state.db'), id], { encoding: 'utf8' }).trim())
  }

  function writeEvidence() {
    writeFileSync(test.info().outputPath('confirmation-keyboard-evidence.json'), JSON.stringify({
      sourceCommit, sourceTree, scenario: 'synthetic temporary sessions', scriptedProviderCalls: mock.receivedPrompts.length,
      confirmationProviderCalls: mock.receivedPrompts.length - 2, decisions, captures
    }, null, 2))
  }

  async function capture(name: string) {
    const bytes = await page.screenshot({ path: test.info().outputPath(`${name}.png`), animations: 'disabled' })
    captures.push({ name, sha256: createHash('sha256').update(bytes).digest('hex') })
    writeEvidence()
  }

  async function exercise(surface: string, id: string, confirmLabel: string, open: () => Promise<void>) {
    for (const action of ['Escape', 'Cancel Enter', 'Cancel Space', 'Confirm Enter']) {
      expect(remaining(id)).toBe(1)
      await open()
      const dialog = page.getByRole('dialog')
      await expect(dialog).toHaveCount(1)
      await expect(dialog).toBeInViewport({ ratio: 1 })
      const confirm = dialog.getByRole('button', { name: confirmLabel, exact: true })
      await expect(confirm).toBeFocused()
      await expect(confirm).toBeEnabled()
      if (action.startsWith('Cancel')) {
        await page.keyboard.press('Shift+Tab')
        await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused()
      }
      await capture(`${surface}-${action.toLowerCase().replaceAll(' ', '-')}`)
      // Capture finishes menu/dialog animations; focus must still belong to the action.
      await expect(action.startsWith('Cancel') ? dialog.getByRole('button', { name: 'Cancel', exact: true }) : confirm).toBeFocused()
      // Only actual browser key activation decides the answer. No synthetic click.
      await page.keyboard.press(action === 'Escape' ? 'Escape' : action === 'Cancel Space' ? 'Space' : 'Enter')
      await expect(dialog).toHaveCount(0)
      if (surface === 'session-menu') await expect(page.locator('.eid-rail-more')).toHaveAttribute('open', '')
      const expected = action === 'Confirm Enter' ? 0 : 1
      await expect.poll(() => remaining(id)).toBe(expected)
      expect(mock.receivedPrompts).toHaveLength(2)
      decisions.push({ surface, action, remainingRows: remaining(id) })
      writeEvidence()
    }
  }

  const more = page.getByRole('complementary', { name: 'Eidolon navigation', exact: true })
    .getByRole('button', { name: 'More workspace options', exact: true })
  if (await more.locator('..').getAttribute('open') === null) await more.click()
  await expect(more.locator('..')).toHaveAttribute('open', '')
  const history = page.locator('.eid-rail-history')
  if (await history.getAttribute('open') === null) await history.locator('summary').click()
  function sessionRow(title: string): Locator {
    return page.locator('.eid-session-tree .row-hover').filter({ has: page.getByText(title, { exact: true }) })
  }
  const menuRow = sessionRow(MENU_TITLE)
  await expect(menuRow).toHaveCount(1)
  await exercise('session-menu', menuId, 'Delete', async () => {
    await menuRow.hover()
    await menuRow.getByRole('button', { name: 'Session actions', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Delete', exact: true }).click()
  })
  await expect(menuRow).toHaveCount(0)

  const hostRow = sessionRow(HOST_TITLE)
  await expect(hostRow).toHaveCount(1)
  await hostRow.hover()
  await hostRow.getByRole('button', { name: 'Session actions', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Archive', exact: true }).click()
  await expect(hostRow).toHaveCount(0)
  await page.getByRole('complementary', { name: 'Eidolon navigation', exact: true }).getByRole('link', { name: 'Settings', exact: true }).click()
  const settings = page.locator('.eid-settings-page')
  await settings.getByRole('button', { name: 'Archived Chats', exact: true }).click()
  const archived = page.locator(`#archived-session-${hostId}`)
  await expect(archived).toBeVisible()
  await exercise('shared-confirm-host', hostId, 'Delete permanently', async () => {
    await archived.getByRole('button', { name: 'Delete permanently', exact: true }).click()
  })
  await expect(archived).toHaveCount(0)
  writeEvidence()
})
