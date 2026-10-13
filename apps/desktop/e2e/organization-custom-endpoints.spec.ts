/** Real Settings rendering against an isolated runtime; no endpoint writes or probes. */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { writeFileSync } from 'node:fs'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { verifyKeyboardFocus } from './focus-visibility'
import { expect, test } from './test'

let fixture: MockBackendFixture | undefined

test.setTimeout(180_000)
test.afterEach(async () => { await fixture?.cleanup(); fixture = undefined })

for (const [locale, heading, nameLabel, modelLabel, saveLabel, testLabel, dark, light] of [
  ['en', 'Custom Endpoints', 'Name', 'Default Model', 'Save', 'Test', 'Dark', 'Light'],
  ['ja', 'カスタムエンドポイント', '名前', '既定のモデル', '保存', 'テスト', 'ダーク', 'ライト'],
  ['zh', '自定义端点', '名称', '默认模型', '保存', '测试', '暗色', '明亮'],
  ['zh-hant', '自訂端點', '名稱', '預設模型', '儲存', '測試', '深色', '明亮'],
  ['ar', 'نقاط الاتصال المخصصة', 'الاسم', 'النموذج الافتراضي', 'حفظ', 'اختبار', 'داكن', 'فاتح'],
  ['ru', 'Пользовательские эндпоинты', 'Название', 'Модель по умолчанию', 'Сохранить', 'Проверить', 'Тёмная', 'Светлая']
] as const) {
  test(`keeps localized custom endpoint drafts explicit and reachable in ${locale}`, async () => {
    fixture = await setupMockBackend({ extraDisplayConfig: `  language: ${locale}` })
    const { app, page, mock } = fixture
    await app.evaluate(() => {
      const rows: Array<{ method: string; path: string }> = []

      ;(globalThis as any).__endpointRequests = rows
      process.getBuiltinModule('diagnostics_channel').channel('http.client.request.start').subscribe(({ request }: any) => {
        const path = new URL(request.path, 'http://127.0.0.1').pathname

        if (path.startsWith('/api/providers/custom-endpoints')) {rows.push({ method: request.method, path })}
      })
    })
    await waitForAppReady(fixture)
    await expect(page.locator('html')).toHaveAttribute('lang', locale)
    await expect(page.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr')
    const settings = page.locator('.eid-settings-page')
    const captures: object[] = []
    const focusChecks: object[] = []

    for (const mode of ['dark', 'light'] as const) {
      await page.evaluate(() => (window as any).hermesDesktop.zoom.setPercent(100))
      await app.evaluate(({ BrowserWindow }) => {
        const window = BrowserWindow.getAllWindows()[0]
        window.unmaximize(); window.setMinimumSize(600, 600); window.setContentSize(1536, 961)
      })
      await page.evaluate(() => { location.hash = '/settings?tab=config:appearance' })
      await settings.getByRole('button').filter({ has: page.getByText('AetherMesh', { exact: true }) }).click()
      await settings.getByRole('button', { name: mode === 'dark' ? dark : light, exact: true }).click()
      await page.evaluate(() => { location.hash = '/settings?tab=providers&pview=custom-endpoints' })
      await expect(settings.locator('.eid-settings-main > header h2')).toHaveText(heading)
      const name = settings.getByLabel(nameLabel, { exact: true })
      const model = settings.getByLabel(modelLabel, { exact: true })
      const save = settings.getByRole('button', { name: saveLabel, exact: true })
      await expect(name).toBeVisible()
      // The fixture has an existing mock provider. Clear only the unsaved draft
      // to exercise required-field gating without deleting or altering that record.
      await name.fill('')
      await expect(save).toBeDisabled()
      await name.fill('Authored endpoint draft')
      await settings.getByPlaceholder('axet-proxy', { exact: true }).fill('retained-provider')
      await settings.getByPlaceholder('http://127.0.0.1:8081/v1', { exact: true }).fill('https://example.invalid/v1')
      await model.fill('vendor/model-original')
      await expect(save).toBeEnabled()
      await expect(settings.getByRole('button', { name: testLabel, exact: true })).toBeEnabled()
      await expect(settings.locator('input[type="password"]')).toHaveValue('')

      for (const [width, height, zoom] of [[1536, 961, 1], [1280, 720, 2]]) {
        await app.evaluate(({ BrowserWindow }, size) => BrowserWindow.getAllWindows()[0].setContentSize(size.width, size.height), { width, height })
        await page.evaluate(value => (window as any).hermesDesktop.zoom.setPercent(value * 100), zoom)
        await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(zoom)
        const target = zoom === 1 ? name : save
        await target.evaluate(element => element.scrollIntoView({ block: 'center', inline: 'nearest' }))
        focusChecks.push(await verifyKeyboardFocus(page, target, `endpoint ${locale} ${mode} ${zoom}`))
        await expect(target).toBeInViewport({ ratio: 1 })
        await expect(name).toHaveValue('Authored endpoint draft')
        await expect(model).toHaveValue('vendor/model-original')
        const overflow = await settings.evaluate(element => element.scrollWidth - element.clientWidth)
        expect(overflow).toBeLessThanOrEqual(1)
        await page.evaluate(async () => { await document.fonts.ready; await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))) })
        const bytes = Buffer.from(await app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')), 'base64')
        expect([bytes.readUInt32BE(16), bytes.readUInt32BE(20)]).toEqual([width, height])
        const captureName = `custom-endpoints-${locale}-${mode}-${zoom}.png`
        writeFileSync(test.info().outputPath(captureName), bytes)
        const appearance = await page.evaluate(() => ({ theme: document.documentElement.dataset.hermesTheme, dark: document.documentElement.classList.contains('dark'), dir: document.documentElement.dir }))
        expect(appearance).toEqual({ theme: 'nous', dark: mode === 'dark', dir: locale === 'ar' ? 'rtl' : 'ltr' })
        captures.push({ name: captureName, locale, mode, width, height, zoom, appearance, overflow, sha256: createHash('sha256').update(bytes).digest('hex') })
      }
    }

    const requests = await app.evaluate(() => (globalThis as any).__endpointRequests as Array<{ method: string; path: string }>)
    expect(requests.length).toBeGreaterThan(0)
    expect(requests.every(request => request.method === 'GET' && request.path === '/api/providers/custom-endpoints')).toBe(true)
    expect(mock.receivedPrompts).toHaveLength(0)
    writeFileSync(test.info().outputPath('custom-endpoints-evidence.json'), JSON.stringify({
      sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
      sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
      scenario: 'synthetic-custom-endpoints-read-only', locale, providerCalls: mock.receivedPrompts.length,
      requests, captures, focusChecks
    }, null, 2))
  })
}
