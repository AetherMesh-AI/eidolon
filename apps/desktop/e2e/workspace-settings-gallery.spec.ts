import { expect, test } from './test'
import { captureGallery, type GalleryFixture, galleryLocales, resizeGallery, setGalleryAppearance, setupGallery, verifyGalleryControls, verifyNotificationStates, writeGalleryEvidence } from './workspace-gallery-fixture'

let fixture: GalleryFixture | undefined
test.setTimeout(300_000)
test.afterEach(async () => { await fixture?.cleanup(); fixture = undefined })

const destinations = [
  'config:model', 'config:chat', 'config:appearance', 'config:workspace', 'config:safety', 'config:browser',
  'config:memory', 'config:voice', 'config:advanced', 'notifications', 'billing', 'providers', 'gateway',
  'keybinds', 'keys', 'plugins', 'sessions', 'about',
  'providers&pview=keys', 'providers&pview=custom-endpoints', 'keys&kview=settings'
] as const

for (const copy of galleryLocales) {
  test(`Settings destinations retain keyboard access and switch containment in ${copy.locale}`, async () => {
    fixture = await setupGallery(copy.locale)
    const { page } = fixture
    for (const mode of ['dark', 'light'] as const) {
      await setGalleryAppearance(fixture, mode, copy[mode])
      for (const zoom of [1, 2]) {
        await resizeGallery(fixture, zoom === 1 ? 1536 : 1280, zoom === 1 ? 961 : 720, zoom)
        if (copy.locale === 'en' || copy.locale === 'ar') {
          await verifyNotificationStates(fixture, `switch-states-${copy.locale}-${mode}-${zoom * 100}`)
        }
        for (const destination of destinations) {
          await page.evaluate(value => { location.hash = `/settings?tab=${value}` }, destination)
          const scope = page.locator('.eid-settings-page')
          await expect(scope.locator('.eid-settings-main > header h2')).toHaveText(/\S/)
          const editor = scope.locator('.eid-settings-controls')
          await expect(editor).toBeVisible()
          await expect(editor.locator('[data-slot="skeleton"]')).toHaveCount(0)
          await expect.poll(() => editor.innerText()).toMatch(/\S/)
          const name = `sections-${destination.replaceAll(/[^a-z]+/g, '-')}-${copy.locale}-${mode}-${zoom * 100}`
          await captureGallery(fixture, name, scope)
          await verifyGalleryControls(fixture, editor, name)
          if (zoom === 2) { await captureGallery(fixture, `${name}-focus`, scope) }
        }
      }
    }
    writeGalleryEvidence(fixture, 'settings')
  })
}
