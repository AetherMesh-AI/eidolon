import { writeFileSync, readdirSync, openSync, closeSync, fstatSync, readSync } from 'node:fs'
import path from 'node:path'
import { expect, test } from './test'
import { captureGallery, checkGalleryDestination, type GalleryFixture, galleryLocales, persistGalleryEvidence, positionGalleryAtStart, resizeGallery, setGalleryAppearance, setupGallery, verifyGalleryControls, verifyNotificationStates, writeGalleryEvidence } from './workspace-gallery-fixture'

let fixture: GalleryFixture | undefined
test.setTimeout(300_000)
test.afterEach(async () => {
  try { if (fixture) {
    persistGalleryEvidence(fixture, 'settings')
    const events = await fixture.app.evaluate(() => (globalThis as any).__settingsHydrationDiagnostic)
    const logCounts: Record<string, number> = {}
    const directory = path.join(fixture.sandbox.hermesHome, 'logs')
    try {
      for (const name of readdirSync(directory).filter(name => name.endsWith('.log')).slice(0, 12)) {
        const fd = openSync(path.join(directory, name), 'r')
        let text = ''
        try {
          const size = fstatSync(fd).size
          const bytes = Buffer.alloc(Math.min(size, 262144))
          const count = readSync(fd, bytes, 0, bytes.length, Math.max(0, size - bytes.length))
          text = bytes.subarray(0, count).toString('utf8')
        } finally { closeSync(fd) }
        for (const category of ['timeout', 'error', 'backend', 'model/options', 'model/info', 'model/auxiliary', 'model/moa']) {
          logCounts[category] = (logCounts[category] ?? 0) + text.toLowerCase().split(category).length - 1
        }
      }
    } catch { logCounts.unavailable = 1 }
    writeFileSync(test.info().outputPath('hydration-timing.json'), JSON.stringify({ locale: fixture.locale, events, logCounts }, null, 2))
    await fixture.app.context().tracing.stop({ path: test.info().outputPath('browser-trace.zip') })
  } }
  finally { await fixture?.cleanup(); fixture = undefined }
})

const destinations = [
  'config:model', 'config:chat', 'config:appearance', 'config:workspace', 'config:safety', 'config:browser',
  'config:memory', 'config:voice', 'config:advanced', 'notifications', 'billing', 'providers', 'gateway',
  'keybinds', 'keys', 'plugins', 'sessions', 'about',
  'providers&pview=keys', 'providers&pview=custom-endpoints', 'keys&kview=settings'
] as const

for (const copy of galleryLocales) {
  test(`Settings destinations retain keyboard access and switch containment in ${copy.locale}`, async () => {
    const current = fixture = await setupGallery(copy.locale)
    const { page } = fixture
    for (const mode of ['dark', 'light'] as const) {
      await setGalleryAppearance(fixture, mode, copy[mode])
      for (const zoom of [1, 2]) {
        await resizeGallery(fixture, zoom === 1 ? 1536 : 1280, zoom === 1 ? 961 : 720, zoom)
        if (copy.locale === 'en' || copy.locale === 'ar') {
          await verifyNotificationStates(fixture, `switch-states-${copy.locale}-${mode}-${zoom * 100}`)
        }
        for (const destination of destinations) {
          await checkGalleryDestination(fixture, `${destination}-${mode}-${zoom}`, async () => {
            await page.evaluate(value => { location.hash = `/settings?tab=${value}` }, destination)
            const scope = page.locator('.eid-settings-page')
            await expect(scope.locator('.eid-settings-main > header h2')).toHaveText(/\S/)
            const editor = scope.locator('.eid-settings-controls')
            await expect(editor).toBeVisible()
            await expect(editor.locator('[data-slot="skeleton"]')).toHaveCount(0)
            await expect.poll(() => editor.innerText()).toMatch(/\S/)
            const name = `sections-${destination.replaceAll(/[^a-z]+/g, '-')}-${copy.locale}-${mode}-${zoom * 100}`
            await positionGalleryAtStart(scope)
            await captureGallery(current, name, scope)
            await verifyGalleryControls(current, editor, name)
            if (zoom === 2) { await captureGallery(current, `${name}-focus`, scope) }
          })
        }
      }
    }
    writeGalleryEvidence(fixture, 'settings')
  })
}
