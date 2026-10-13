import { expect, test } from './test'
import { captureGallery, type GalleryFixture, galleryLocales, resizeGallery, setGalleryAppearance, setupGallery, verifyGalleryControls, writeGalleryEvidence } from './workspace-gallery-fixture'

let fixture: GalleryFixture | undefined
test.setTimeout(300_000)
test.afterEach(async () => { await fixture?.cleanup(); fixture = undefined })

const destinations = [
  ['requests', '/requests', '.eid-workspace'],
  ['activity', '/activity', '.eid-workspace'],
  ['skills', '/skills', '.eid-secondary-surface'],
  ['tools', '/skills?tab=toolsets', '.eid-secondary-surface'],
  ['mcp', '/skills?tab=mcp', '.eid-secondary-surface'],
  ['messaging', '/messaging', '.eid-secondary-surface'],
  ['execution-profiles', '/profiles', '.eid-secondary-surface'],
  ['import-history', '/session-import', '.eid-secondary-surface'],
  ['legacy-history', '/legacy-organization', '.eid-workspace']
] as const

for (const copy of galleryLocales) {
  test(`secondary pages retain headings, keyboard access and synthetic history in ${copy.locale}`, async () => {
    fixture = await setupGallery(copy.locale, true)
    const { page } = fixture
    for (const mode of ['dark', 'light'] as const) {
      await setGalleryAppearance(fixture, mode, copy[mode])
      for (const zoom of [1, 2]) {
        await resizeGallery(fixture, zoom === 1 ? 1536 : 1280, zoom === 1 ? 961 : 720, zoom)
        for (const [name, route, selector] of destinations) {
          await page.evaluate(value => { location.hash = value }, route)
          const scope = page.locator(selector).last()
          const heading = scope.getByRole('heading', { level: 1 })
          await expect(heading).toHaveCount(1)
          await expect(heading).toBeVisible()
          await expect(heading).toHaveCSS('font-family', /Georgia/)
          await expect(scope.locator('[data-slot="skeleton"]')).toHaveCount(0)
          const label = `secondary-${name}-${copy.locale}-${mode}-${zoom * 100}`
          await captureGallery(fixture, label, scope)
          await verifyGalleryControls(fixture, scope, label)
          if (zoom === 2) { await captureGallery(fixture, `${label}-focus`, scope) }
        }
        await page.evaluate(id => { location.hash = `/${id}` }, fixture.sessionId!)
        const history = page.locator('.eid-rail-history')
        await expect(history).toHaveAttribute('open', '')
        await expect(history).toContainText('Synthetic history for UI review.')
        await expect(page.locator('[data-slot="aui_thread-viewport"]')).toContainText('Synthetic history for UI review. No external work is requested.')
        await captureGallery(fixture, `secondary-expanded-history-${copy.locale}-${mode}-${zoom * 100}`, history)
      }
    }
    writeGalleryEvidence(fixture, 'secondary')
  })
}
