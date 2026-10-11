/** Real native editor and profile ledger; loopback fixture, no model calls. */
import { execFileSync } from 'node:child_process'
import { join } from 'node:path'

import { setupMockBackend, waitForAppReady } from './fixtures'
import { verifyKeyboardFocus } from './focus-visibility'
import { acknowledgeExpectedErrorBanner, collectErrorBanners, expect, test } from './test'

for (const locale of ['en', 'ar'] as const) {
  test(`saves organization and owner names explicitly and retains their identities across reload in ${locale}`, async () => {
    test.setTimeout(180_000)
    const fixture = await setupMockBackend({ extraDisplayConfig: `  language: ${locale}` })
    const { app, page, sandbox, mock } = fixture
    const ledger = join(sandbox.hermesHome, 'organization', 'state.db')

    const stored = () =>
      JSON.parse(
        execFileSync(
          'python3',
          [
            '-c',
            `
import json, sqlite3, sys
from pathlib import Path
path = Path(sys.argv[1])
if not path.exists():
    print('null'); sys.exit()
conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
conn.row_factory = sqlite3.Row
if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='organization_metadata'").fetchone():
    print('null'); sys.exit()
row = conn.execute('SELECT * FROM organization_metadata').fetchone()
print(json.dumps(dict(row) if row else None))
`,
            ledger
          ],
          { encoding: 'utf8' }
        )
      ) as null | {
        organization_id: string
        owner_id: string
        revision: number
        display_name: string
        owner_display_name: string
      }

    const observedMethods: string[] = []
    let dropReply = false
    const savedRequests: Record<string, unknown>[] = []

    try {
      await page.routeWebSocket(/.*/, socket => {
        const server = socket.connectToServer()
        let droppedId: number | string | undefined
        socket.onMessage(message => {
          const request = JSON.parse(message.toString()) as {
            id?: number | string
            method?: string
            params?: Record<string, unknown>
          }

          if (request.method) {
            observedMethods.push(request.method)
          }

          if (request.method === 'organization.saveMetadata') {
            savedRequests.push(request.params!)

            if (dropReply) {
              droppedId = request.id
              dropReply = false
            }
          }

          server.send(message)
        })
        server.onMessage(message => {
          const response = JSON.parse(message.toString()) as { id?: number | string }

          if (droppedId !== undefined && response.id === droppedId) {
            return
          }

          socket.send(message)
        })
      })
      await page.reload()
      await waitForAppReady(fixture)
      const trigger = page.getByRole('button', { name: 'Edit organization and owner names', exact: true })
      await expect(trigger).toBeEnabled()
      expect(stored()).toBeNull()
      await trigger.click()
      const dialog = page.getByRole('dialog', { name: 'Organization and owner' })
      await dialog.getByLabel('Organization name', { exact: true }).fill('Cancelled draft')
      await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
      await expect(dialog).toHaveCount(0)
      await expect(trigger).toBeFocused()
      expect(stored()).toBeNull()

      await trigger.click()
      await dialog.getByLabel('Organization name', { exact: true }).fill('Research studio')
      await dialog.getByLabel('Your display name', { exact: true }).fill('Alex')
      await dialog.getByRole('button', { name: 'Save names', exact: true }).click()
      await expect(dialog).toHaveCount(0)
      await expect(trigger).toContainText('Alex')
      await expect(page.locator('.eid-workspace-organization-name')).toHaveText('Research studio')
      const first = stored()!
      expect(first.revision).toBe(1)
      expect(first.organization_id).not.toBe(first.owner_id)
      expect(first.display_name).toBe('Research studio')
      expect(first.owner_display_name).toBe('Alex')
      await page.reload()
      await waitForAppReady(fixture)
      await expect(trigger).toContainText('Alex')
      await expect(page.locator('.eid-workspace-organization-name')).toHaveText('Research studio')
      expect(stored()).toEqual(first)

      await trigger.click()
      await dialog.getByLabel('Organization name', { exact: true }).fill('Research studio revised')
      await dialog.getByLabel('Your display name', { exact: true }).fill('Alex Rivera')
      await dialog.getByRole('button', { name: 'Save names', exact: true }).click()
      await expect(dialog).toHaveCount(0)
      expect(stored()).toMatchObject({
        organization_id: first.organization_id,
        owner_id: first.owner_id,
        revision: 2,
        display_name: 'Research studio revised',
        owner_display_name: 'Alex Rivera'
      })

      // Deliver the real write to the backend, but lose its reply at the transport boundary.
      // The ledger must commit once, and reload must preserve the exact original request.
      dropReply = true
      await page.reload()
      await waitForAppReady(fixture)
      await trigger.click()
      await dialog.getByLabel('Organization name', { exact: true }).fill('Retained original Save')
      await dialog.getByRole('button', { name: 'Save names', exact: true }).click()
      await expect.poll(() => stored()?.revision).toBe(3)
      await page.reload()
      await waitForAppReady(fixture)
      await expect(trigger).toBeEnabled()
      expect(savedRequests).toHaveLength(3)
      await trigger.click()

      const uncertainMessage =
        'The Save result is unknown. Your original request is retained for this organization; no new request will be sent automatically.'

      await expect(dialog.getByRole('alert')).toHaveText(uncertainMessage)
      await expect(dialog.getByLabel('Organization name', { exact: true })).toHaveValue('Retained original Save')
      await expect(dialog.getByLabel('Organization name', { exact: true })).toBeDisabled()
      await dialog.getByRole('button', { name: 'Check original Save', exact: true }).click()
      await expect(dialog).toHaveCount(0)
      expect(savedRequests).toHaveLength(4)
      expect(savedRequests[3]).toEqual(savedRequests[2])
      expect(stored()).toMatchObject({
        organization_id: first.organization_id,
        owner_id: first.owner_id,
        revision: 3,
        display_name: 'Retained original Save'
      })
      await acknowledgeExpectedErrorBanner(page, uncertainMessage)

      const captures: object[] = []

      for (const mode of ['dark', 'light'] as const) {
        await app.evaluate(({ BrowserWindow }) => {
          const window = BrowserWindow.getAllWindows()[0]
          window.webContents.setZoomFactor(1)
          window.setContentSize(1536, 961)
        })
        await page.evaluate(() => {
          location.hash = '/settings?tab=config:appearance'
        })
        await page
          .getByRole('button')
          .filter({ has: page.getByText('AetherMesh', { exact: true }) })
          .click()
        const label = locale === 'ar' ? (mode === 'dark' ? 'داكن' : 'فاتح') : mode === 'dark' ? 'Dark' : 'Light'
        await page.getByRole('button', { name: label, exact: true }).click()
        await expect
          .poll(() => page.locator('html').evaluate(element => element.classList.contains('dark')))
          .toBe(mode === 'dark')
        await page.evaluate(() => {
          location.hash = '/home'
        })
        await expect(page.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr')

        for (const [width, height, zoom] of [
          [1536, 961, 1],
          [1280, 720, 2],
          [760, 900, 1]
        ]) {
          await app.evaluate(
            ({ BrowserWindow }, size) => {
              const window = BrowserWindow.getAllWindows()[0]
              window.unmaximize()
              window.setMinimumSize(600, 600)
              window.setContentSize(size.width, size.height)
              window.webContents.setZoomFactor(size.zoom)
            },
            { width, height, zoom }
          )
          await expect(trigger).toBeVisible()
          await trigger.click()
          await expect(dialog).toBeVisible()
          await verifyKeyboardFocus(
            page,
            dialog.getByLabel('Organization name', { exact: true }),
            `owner-editor-${locale}-${mode}-${width}-${zoom}`
          )
          const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
          expect(overflow).toBeLessThanOrEqual(1)

          const png = Buffer.from(
            await app.evaluate(async ({ BrowserWindow }) =>
              (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')
            ),
            'base64'
          )

          await test.info().attach(`organization-owner-editor-${locale}-${mode}-${width}-${height}-${zoom}`, {
            body: png,
            contentType: 'image/png'
          })
          captures.push({ locale, mode, width, height, zoom, overflow, captureMethod: 'electron.capturePage' })
          await page.keyboard.press('Escape')
          await expect(dialog).toHaveCount(0)
          await expect(trigger).toBeFocused()
        }
      }

      expect(await collectErrorBanners(page)).toEqual([])
      expect(mock.receivedPrompts).toHaveLength(0)
      expect(observedMethods.filter(method => method === 'organization.saveMetadata')).toHaveLength(4)
      expect(observedMethods).not.toContain('session.create')
      expect(observedMethods).not.toContain('chat.send')
      expect(
        observedMethods.filter(
          method =>
            method.startsWith('organization.') &&
            ![
              'organization.descriptor',
              'organization.saveMetadata',
              'organization.snapshot',
              'organization.outcomes'
            ].includes(method)
        )
      ).toEqual([])
      await test.info().attach('organization-metadata-evidence', {
        contentType: 'application/json',
        body: JSON.stringify({
          sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
          sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
          locale,
          savedRequests,
          observedMethods,
          initial: first,
          updated: stored(),
          providerCalls: mock.receivedPrompts.length,
          captures
        })
      })
    } finally {
      await fixture.cleanup()
    }
  })
}
