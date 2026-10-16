import { verifyKeyboardFocus } from './focus-visibility'
import { expect, test } from './test'
import { captureGallery, checkGalleryDestination, type GalleryFixture, galleryLocales, persistGalleryEvidence, positionGalleryAtStart, resizeGallery, setGalleryAppearance, setupGallery, verifyGalleryControls, writeGalleryEvidence } from './workspace-gallery-fixture'

let fixture: GalleryFixture | undefined
test.setTimeout(300_000)
test.afterEach(async () => {
  try { if (fixture) { persistGalleryEvidence(fixture, 'secondary') } }
  finally { await fixture?.cleanup(); fixture = undefined }
})

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
    const current = fixture = await setupGallery(copy.locale, true)
    const { page } = fixture
    for (const mode of ['dark', 'light'] as const) {
      await setGalleryAppearance(fixture, mode, copy[mode])
      // Check every translated header control, including the narrow Navigation
      // toggle, with real Electron zoom rather than a renderer CSS transform.
      for (const [width, height, zoom] of [[1280, 720, 1.25], [1280, 720, 2], [760, 900, 2]]) {
        await resizeGallery(current, width, height, zoom)
        const header = page.locator('[data-slot="workspace-header"]')
        await expect.poll(() => page.evaluate(() => innerWidth)).toBe(Math.round(width / zoom))
        const narrow = width / zoom < 640
        await expect(header).toHaveCount(1)
        await expect(header.locator('button[data-size="icon"]')).toHaveCount(1)
        for (const selector of ['.eid-workspace-organization-trigger', '.eid-workspace-profile-trigger', 'button[data-size="icon"]', '.eid-workspace-search-trigger', '.eid-workspace-actions > button[data-size="lg"]']) {
          const control = header.locator(selector)
          await expect(control).toHaveCount(1)
          await expect(control).toBeVisible()
          await expect(control).toBeEnabled()
        }
        const navigation = header.locator('.eid-workspace-navigation-trigger')
        await expect(navigation).toHaveCount(narrow ? 1 : 0)
        if (narrow) {
          await expect(navigation).toHaveAttribute('aria-expanded', 'false')
          await expect(navigation).toHaveAttribute('aria-controls', /.+/)
        }
        // Five core controls plus this fixture's pending-intervention work indicator.
        await expect(header.getByRole('button')).toHaveCount(narrow ? 7 : 6)
        const label = `secondary-header-${copy.locale}-${mode}-${width}-${zoom * 100}`
        await expect(header).toBeInViewport({ ratio: 1 })
        expect(await header.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
        for (const icon of await header.locator('button[data-size="icon"]').all()) {
          const box = await icon.boundingBox()
          expect(box!.width, 'translated header icon minimum hit width').toBeGreaterThanOrEqual(36)
          expect(box!.height, 'translated header icon minimum hit height').toBeGreaterThanOrEqual(36)
        }
        for (const button of await header.getByRole('button').all()) {
          current.focusChecks.push(await verifyKeyboardFocus(page, button, `${label} ${await button.getAttribute('aria-label')}`))
        }
        for (const text of await header.locator('.eid-workspace-organization-name, .eid-workspace-identity button span').all()) {
          if (await text.isVisible()) {
            expect(await text.evaluate(element => ({ clipped: element.scrollWidth > element.clientWidth + 1,
              ellipsis: getComputedStyle(element).textOverflow === 'ellipsis' }))).toEqual({ clipped: false, ellipsis: false })
          }
        }
        await captureGallery(current, label, header)
      }
      for (const zoom of [1, 2]) {
        await resizeGallery(fixture, zoom === 1 ? 1536 : 1280, zoom === 1 ? 961 : 720, zoom)
        for (const [name, route, selector] of destinations) {
          await checkGalleryDestination(fixture, `${name}-${mode}-${zoom}`, async () => {
            await page.evaluate(value => { location.hash = value }, route)
            const scope = page.locator(selector).last()
            const heading = scope.getByRole('heading', { level: 1 })
            await expect(heading).toHaveCount(1)
            await expect(heading).toBeVisible()
            await expect(heading).toHaveCSS('font-family', /Georgia/)
            await expect(scope.locator('[data-slot="skeleton"]')).toHaveCount(0)
            // Skills uses PageLoader status surfaces rather than skeleton slots.
            await expect(scope.locator('[role="status"][aria-label]:has([role="presentation"])')).toHaveCount(0)
            if (name === 'activity') {
              const rows = scope.locator('.eid-activity-row')
              await expect(rows).toHaveCount(current.state.snapshot.activity.length)
              for (const row of await rows.all()) {
                const avatar = await row.locator('.eid-avatar').boundingBox()
                const content = await row.locator('.eid-activity-copy').boundingBox()
                expect(avatar).not.toBeNull()
                expect(content).not.toBeNull()
                expect(avatar!.width).toBe(avatar!.height)
                expect(content!.width).toBeGreaterThan(avatar!.width)
              }
            }
            const label = `secondary-${name}-${copy.locale}-${mode}-${zoom * 100}`
            await positionGalleryAtStart(scope)
            await captureGallery(current, label, scope)
            await verifyGalleryControls(current, scope, label)
            if (zoom === 2) { await captureGallery(current, `${label}-focus`, scope) }
          })
        }
        await checkGalleryDestination(current, `history-${mode}-${zoom}`, async () => {
          await page.evaluate(id => { location.hash = `/${id}` }, current.sessionId!)
          const history = page.locator('.eid-rail-history')
          await expect(history).toHaveAttribute('open', '')
          await expect(history).toContainText('Synthetic gallery history')
          await expect(page.locator('[data-slot="aui_thread-viewport"]')).toContainText('Synthetic history for UI review. No external work is requested.')
          await captureGallery(current, `secondary-expanded-history-${copy.locale}-${mode}-${zoom * 100}`, history)
        })
      }
    }
    writeGalleryEvidence(fixture, 'secondary')
  })
}
