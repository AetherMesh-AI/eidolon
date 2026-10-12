/** Real native editor and profile ledger; loopback fixture, no model calls. */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { join } from 'node:path'

import { setupMockBackend, waitForAppReady } from './fixtures'
import { verifyKeyboardFocus } from './focus-visibility'
import { acknowledgeExpectedErrorBanner, collectErrorBanners, expect, test } from './test'

for (const locale of ['en', 'ar'] as const) {
  test(`saves organization and owner names explicitly and retains their identities across reload in ${locale}`, async () => {
    test.setTimeout(180_000)

    const copy = locale === 'ar' ? {
      edit: 'تعديل اسم المؤسسة والمالك', title: 'المؤسسة والمالك', organizationName: 'اسم المؤسسة',
      ownerName: 'اسم العرض الخاص بك', save: 'حفظ الأسماء', cancel: 'إلغاء', refresh: 'تحديث الأسماء المحفوظة',
      retry: 'التحقق من الحفظ الأصلي', storage: 'تعذّرت قراءة الأسماء المحفوظة. حدّثها للمحاولة مجدداً.',
      uncertain: 'نتيجة الحفظ غير معروفة. تم الاحتفاظ بطلبك الأصلي لهذه المؤسسة، ولن يُرسل طلب جديد تلقائياً.'
    } : {
      edit: 'Edit organization and owner names', title: 'Organization and owner', organizationName: 'Organization name',
      ownerName: 'Your display name', save: 'Save names', cancel: 'Cancel', refresh: 'Refresh saved names',
      retry: 'Check original Save', storage: 'The saved names could not be read. Refresh to try again.',
      uncertain: 'The Save result is unknown. Your original request is retained for this organization; no new request will be sent automatically.'
    }

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
    let rejectRetry = false
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

            if (rejectRetry) {
              rejectRetry = false
              socket.send(
                JSON.stringify({
                  jsonrpc: '2.0',
                  id: request.id,
                  error: { code: 4001, message: 'Injected retry transport rejection' }
                })
              )

              return
            }

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
      const trigger = page.getByRole('button', { name: copy.edit, exact: true })
      await expect(trigger).toBeEnabled()
      expect(stored()).toBeNull()
      await trigger.click()
      const dialog = page.getByRole('dialog', { name: copy.title })
      await dialog.getByLabel(copy.organizationName, { exact: true }).fill('Cancelled draft')
      await dialog.getByRole('button', { name: copy.cancel, exact: true }).click()
      await expect(dialog).toHaveCount(0)
      await expect(trigger).toBeFocused()
      expect(stored()).toBeNull()

      await trigger.click()
      await dialog.getByLabel(copy.organizationName, { exact: true }).fill('Research studio')
      await dialog.getByLabel(copy.ownerName, { exact: true }).fill('Alex')
      await dialog.getByRole('button', { name: copy.save, exact: true }).click()
      await expect(dialog).toHaveCount(0)
      await expect(trigger).toBeFocused()
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
      await dialog.getByLabel(copy.organizationName, { exact: true }).fill('Research studio revised')
      await dialog.getByLabel(copy.ownerName, { exact: true }).fill('Alex Rivera')
      await dialog.getByRole('button', { name: copy.save, exact: true }).click()
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
      await dialog.getByLabel(copy.organizationName, { exact: true }).fill('Retained original Save')
      await dialog.getByRole('button', { name: copy.save, exact: true }).click()
      await expect.poll(() => stored()?.revision).toBe(3)
      await page.reload()
      await waitForAppReady(fixture)
      await expect(trigger).toBeEnabled()
      expect(savedRequests).toHaveLength(3)
      await trigger.click()

      const uncertainMessage =
        copy.uncertain

      await expect(dialog.getByRole('alert')).toHaveText(uncertainMessage)
      await expect(dialog.getByLabel(copy.organizationName, { exact: true })).toHaveValue('Retained original Save')
      await expect(dialog.getByLabel(copy.organizationName, { exact: true })).toBeDisabled()
      rejectRetry = true
      await dialog.getByRole('button', { name: copy.retry, exact: true }).click()
      await expect(dialog).toHaveCount(0)
      expect(savedRequests).toHaveLength(4)
      expect(stored()?.revision).toBe(3)
      await page.reload()
      await waitForAppReady(fixture)
      await trigger.click()
      await expect(dialog.getByRole('alert')).toHaveText(uncertainMessage)
      await dialog.getByRole('button', { name: copy.retry, exact: true }).click()
      await expect(dialog).toHaveCount(0)
      expect(savedRequests).toHaveLength(5)
      expect(savedRequests[4]).toEqual(savedRequests[2])
      expect(savedRequests[3]).toEqual(savedRequests[2])
      expect(stored()).toMatchObject({
        organization_id: first.organization_id,
        owner_id: first.owner_id,
        revision: 3,
        display_name: 'Retained original Save'
      })
      await acknowledgeExpectedErrorBanner(page, uncertainMessage)

      // Replace only the fixture's metadata row to model restoring another ledger identity.
      // Production changes still travel exclusively through the native editor's real RPC.
      const restoreMetadata = (record: ReturnType<typeof stored>) => {
        execFileSync('python3', [
          '-c',
          `
import json, sqlite3, sys
row = json.loads(sys.argv[2])
with sqlite3.connect(sys.argv[1]) as conn:
    conn.execute('DELETE FROM organization_metadata')
    if row:
        names = list(row)
        conn.execute('INSERT INTO organization_metadata (' + ','.join(names) + ') VALUES (' + ','.join('?' for _ in names) + ')', list(row.values()))
`,
          ledger,
          JSON.stringify(record)
        ])
      }

      const originalRecord = stored()!

      const replacement = {
        ...originalRecord,
        organization_id: '33333333-3333-4333-8333-333333333333',
        owner_id: '44444444-4444-4444-8444-444444444444',
        display_name: 'Replacement organization',
        revision: 1
      }

      await trigger.click()
      await dialog.getByLabel(copy.organizationName, { exact: true }).fill('Original A draft')
      restoreMetadata(replacement)
      await dialog.getByRole('button', { name: copy.refresh, exact: true }).click()
      await expect(dialog).toHaveCount(0)
      await trigger.click()
      await expect(dialog.getByLabel(copy.organizationName, { exact: true })).toHaveValue('Replacement organization')
      await dialog.getByLabel(copy.organizationName, { exact: true }).fill('Replacement B draft')
      restoreMetadata(originalRecord)
      await dialog.getByRole('button', { name: copy.refresh, exact: true }).click()
      await expect(dialog).toHaveCount(0)
      await trigger.click()
      await expect(dialog.getByLabel(copy.organizationName, { exact: true })).toHaveValue('Original A draft')
      restoreMetadata(null)
      await dialog.getByRole('button', { name: copy.refresh, exact: true }).click()
      await expect(dialog).toHaveCount(0)
      await trigger.click()
      await expect(dialog.getByLabel(copy.organizationName, { exact: true })).toHaveValue('')
      // Removing the identity while its durable receipts remain is deliberately
      // inconsistent storage. The backend must reject it, never reinitialize it.
      const missingIdentityError = copy.storage
      await expect(dialog.getByRole('alert')).toHaveText(missingIdentityError)
      await expect(dialog.getByRole('button', { name: copy.save, exact: true })).toBeDisabled()
      await dialog.getByRole('button', { name: copy.cancel, exact: true }).click()
      restoreMetadata(originalRecord)
      await trigger.click()
      await dialog.getByRole('button', { name: copy.refresh, exact: true }).click()
      await expect(dialog).toHaveCount(0)
      expect(savedRequests).toHaveLength(5)
      await acknowledgeExpectedErrorBanner(page, missingIdentityError)

      const captures: object[] = []

      for (const mode of ['dark', 'light'] as const) {
        await page.evaluate(() =>
          (
            window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }
          ).hermesDesktop.zoom.setPercent(100)
        )
        await app.evaluate(({ BrowserWindow }) => {
          const window = BrowserWindow.getAllWindows()[0]
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
          await page.evaluate(() =>
            (
              window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }
            ).hermesDesktop.zoom.setPercent(100)
          )
          await app.evaluate(
            ({ BrowserWindow }, size) => {
              const window = BrowserWindow.getAllWindows()[0]
              window.unmaximize()
              window.setMinimumSize(600, 600)
              window.setContentSize(size.width, size.height)
            },
            { width, height }
          )
          await expect.poll(() => page.evaluate(() => innerWidth)).toBe(width)
          await page.evaluate(
            value =>
              (
                window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }
              ).hermesDesktop.zoom.setPercent(value * 100),
            zoom
          )
          await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(zoom)
          await expect.poll(() => page.evaluate(() => innerWidth)).toBe(width / zoom)
          await expect(trigger).toBeVisible()
          await trigger.click()
          await expect(dialog).toBeVisible()
          await verifyKeyboardFocus(
            page,
            dialog.getByLabel(copy.organizationName, { exact: true }),
            `owner-editor-${locale}-${mode}-${width}-${zoom}`
          )
          const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
          expect(overflow).toBeLessThanOrEqual(1)

          const viewport = await page.evaluate(() => ({
            width: innerWidth,
            height: innerHeight,
            scale: devicePixelRatio
          }))

          expect(viewport).toEqual({ width: width / zoom, height: height / zoom, scale: zoom })

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
          captures.push({
            locale,
            mode,
            width,
            height,
            zoom,
            viewport,
            overflow,
            sha256: createHash('sha256').update(png).digest('hex'),
            captureMethod: 'electron.capturePage'
          })

          if (zoom === 2) {
            const name = `Native ${mode} name at 200%`
            await dialog.getByLabel(copy.organizationName, { exact: true }).fill(name)
            const save = dialog.getByRole('button', { name: copy.save, exact: true })
            await save.scrollIntoViewIfNeeded()
            await verifyKeyboardFocus(page, save, `owner-save-${locale}-${mode}-${zoom}`)
            await expect(save).toBeInViewport({ ratio: 1 })
            await page.keyboard.press('Enter')
            await expect.poll(() => stored()?.display_name).toBe(name)
          } else {
            await page.keyboard.press('Escape')
          }

          await expect(dialog).toHaveCount(0)
          await expect(trigger).toBeFocused()
        }
      }

      expect(await collectErrorBanners(page)).toEqual([])
      expect(mock.receivedPrompts).toHaveLength(0)
      expect(observedMethods.filter(method => method === 'organization.saveMetadata')).toHaveLength(7)
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
