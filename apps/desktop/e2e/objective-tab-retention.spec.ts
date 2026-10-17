import { writeFileSync } from 'node:fs'

import { verifyKeyboardFocus } from './focus-visibility'
import { expect, test } from './test'
import { captureGallery, type GalleryFixture, persistGalleryEvidence, resizeGallery, setupGallery, writeGalleryEvidence } from './workspace-gallery-fixture'

const sizes = [
  { width: 1536, height: 961, zoom: 1 },
  { width: 1280, height: 800, zoom: 1.25 },
  { width: 1280, height: 800, zoom: 2 },
  { width: 400, height: 620, zoom: 1 }
] as const

let fixture: GalleryFixture | undefined
const continuity: object[] = []
test.setTimeout(300_000)
test.afterEach(async () => {
  try {
    if (fixture) { persistGalleryEvidence(fixture, 'objective-tab-retention') }
    writeFileSync(test.info().outputPath('objective-tab-continuity.json'), JSON.stringify(continuity, null, 2))
  } finally { await fixture?.cleanup(); fixture = undefined; continuity.length = 0 }
})

for (const locale of ['en', 'ar'] as const) {
  test(`objective tabs retain local state and isolate hidden inspectors in ${locale}`, async () => {
    const current = fixture = await setupGallery(locale, false, false, { blockOrganizationMutations: true, blockCapabilityMutations: true })
    const { page } = current
    // All data stays in the existing read-only socket fixture. No evidence fetch,
    // credential, provider, filesystem action, or organization mutation is needed.
    Object.assign(current.state.snapshot, {
      activity: [
        { ...current.state.snapshot.activity[0], id: 'retained-release-event', objectiveId: 'release', text: 'Synthetic retained release event', description: 'Recorded local selection. No action or provider work is requested.' },
        { ...current.state.snapshot.activity[1], id: 'other-objective-event', objectiveId: 'navigation', text: 'Synthetic retained other-objective event' }
      ],
      knowledge: [
        { id: 'retained-release-evidence', objectiveId: 'release', title: 'Synthetic retained release evidence', kind: 'artifact', body: 'Local searchable fixture text. No evidence file is opened.' },
        { id: 'other-objective-evidence', objectiveId: 'navigation', title: 'Synthetic retained other-objective evidence', kind: 'artifact', body: 'Separate objective.' }
      ]
    } satisfies {
      activity: Array<typeof current.state.snapshot.activity[number] & { objectiveId: string; description?: string }>
      knowledge: Array<{ id: string; objectiveId: string; title: string; kind: 'artifact'; body: string }>
    })
    await page.reload()
    const workspace = page.locator('.eid-workspace').last()
    let originOwner = ''

    for (const size of sizes) {
      const label = `objective-tabs-${locale}-${size.width}-${size.zoom * 100}`
      await resizeGallery(current, size.width, size.height, size.zoom)
      await page.evaluate(() => { location.hash = '/messages' })
      const recipient = page.getByRole('button', { name: /Avery/ })
      await recipient.click()
      await expect(page.locator('.eid-message-members button[aria-pressed]').filter({ hasText: 'Avery' })).toHaveAttribute('aria-pressed', 'true')
      const messageUrl = page.url()
      const objectiveLink = page.locator('a[href*="/objectives/release"]').filter({ hasText: 'Prepare the autumn release brief' })
      const objectiveHref = await objectiveLink.getAttribute('href')
      expect(objectiveHref).toContain('originOwner=')
      expect(objectiveHref).toContain('from=')
      originOwner = new URLSearchParams(objectiveHref!.split('?')[1]).get('originOwner') ?? ''
      expect(originOwner).not.toBe('')
      await objectiveLink.click()
      const heading = workspace.getByRole('heading', { name: 'Prepare the autumn release brief', exact: true, level: 1 })
      await heading.scrollIntoViewIfNeeded()
      await expect(heading).toBeVisible()
      const headingGeometry = await heading.evaluate(element => {
        const box = element.getBoundingClientRect()
        const clippedBy: string[] = []
        for (let parent = element.parentElement; parent; parent = parent.parentElement) {
          const style = getComputedStyle(parent)
          const bounds = parent.getBoundingClientRect()
          const clips = (overflow: string) => ['auto', 'scroll', 'hidden', 'clip'].includes(overflow)
          if ((clips(style.overflowX) && (box.left < bounds.left + parent.clientLeft - 1 || box.right > bounds.left + parent.clientLeft + parent.clientWidth + 1)) ||
              (clips(style.overflowY) && (box.top < bounds.top + parent.clientTop - 1 || box.bottom > bounds.top + parent.clientTop + parent.clientHeight + 1))) {
            clippedBy.push(`${parent.tagName}.${parent.className}`)
          }
        }
        return { left: box.left, right: box.right, top: box.top, bottom: box.bottom, width: innerWidth, height: innerHeight, clippedBy,
          overflow: element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1 }
      })
      expect(headingGeometry.overflow).toBe(false)
      expect(headingGeometry.clippedBy).toEqual([])
      expect(headingGeometry.top).toBeGreaterThanOrEqual(0)
      expect(headingGeometry.bottom).toBeLessThanOrEqual(headingGeometry.height)
      expect(headingGeometry.left).toBeGreaterThanOrEqual(0)
      expect(headingGeometry.right).toBeLessThanOrEqual(headingGeometry.width)

      const tabs = workspace.getByRole('tab')
      await expect(tabs).toHaveCount(4)
      const work = tabs.nth(0)
      const activity = tabs.nth(1)
      const artifacts = tabs.nth(2)
      const decisions = tabs.nth(3)
      const panels = workspace.locator('[role="tabpanel"]')
      await expect(panels).toHaveCount(4)
      for (let index = 0; index < 4; index++) {
        expect(await tabs.nth(index).getAttribute('aria-controls')).toBe(await panels.nth(index).getAttribute('id'))
        expect(await panels.nth(index).getAttribute('aria-labelledby')).toBe(await tabs.nth(index).getAttribute('id'))
      }
      await work.focus()
      await page.keyboard.press(locale === 'ar' ? 'ArrowLeft' : 'ArrowRight')
      await expect(activity).toBeFocused()
      await expect(work).toHaveAttribute('aria-selected', 'true')
      await page.keyboard.press('End')
      await expect(decisions).toBeFocused()
      await expect(work).toHaveAttribute('aria-selected', 'true')
      await page.keyboard.press('Home')
      await expect(work).toBeFocused()
      current.focusChecks.push(await verifyKeyboardFocus(page, work, `${label}-work-tab`))
      await captureGallery(current, `${label}-keyboard-work`, workspace)
      await page.keyboard.press(locale === 'ar' ? 'ArrowLeft' : 'ArrowRight')
      await page.keyboard.press('Enter')
      await expect(activity).toHaveAttribute('aria-selected', 'true')
      await expect(workspace.getByRole('tabpanel')).toHaveCount(1)
      const activityPanel = panels.nth(1)
      const search = activityPanel.getByRole('searchbox')
      await expect(activityPanel.getByText('Synthetic retained release event', { exact: true })).toBeVisible()
      await expect(activityPanel.getByText('Synthetic retained other-objective event', { exact: true })).toHaveCount(0)
      await search.fill('Synthetic retained')
      await expect(activityPanel.getByText('Synthetic retained other-objective event', { exact: true })).toHaveCount(0)

      // Keep an older, visible objective inspector while a newer activity
      // inspector is hidden: Escape must choose the top *visible* surface.
      await work.click()
      await workspace.locator('.eid-objective-hero + .eid-inline').getByRole('button').click()
      const objectiveInspector = workspace.getByRole('complementary', { name: 'Objective details', exact: true })
      await expect(objectiveInspector).toBeVisible()
      await activity.click()
      const eventRow = activityPanel.getByRole('button').filter({ hasText: 'Synthetic retained release event' })
      await eventRow.click()
      const eventInspector = activityPanel.locator('aside[aria-label="Event details"]')
      await expect(eventInspector).toBeVisible()
      await work.click()
      await expect(activityPanel).toHaveAttribute('hidden', '')
      await expect(activityPanel).toHaveAttribute('inert', '')
      await expect(eventInspector).toBeHidden()
      await expect(eventInspector).toHaveCount(1)
      await work.focus()
      await page.keyboard.press('Escape')
      await expect(objectiveInspector).toHaveCount(0)
      await expect(eventInspector).toHaveCount(1)
      await work.focus()
      for (let index = 0; index < 8; index++) {
        await page.keyboard.press('Tab')
        expect(await page.evaluate(() => !!document.activeElement?.closest('[hidden], [inert]'))).toBe(false)
      }
      await activity.click()
      await expect(search).toHaveValue('Synthetic retained')
      await expect(eventInspector).toBeVisible()
      const closeEvent = eventInspector.getByRole('button', { name: 'Close event details', exact: true })
      current.focusChecks.push(await verifyKeyboardFocus(page, closeEvent, `${label}-retained-event-close`))
      await captureGallery(current, `${label}-retained-event`, workspace)
      await page.keyboard.press('Escape')
      await expect(eventInspector).toHaveCount(0)
      await expect(eventRow).toBeFocused()

      await artifacts.click()
      const artifactPanel = panels.nth(2)
      const artifactSearch = artifactPanel.getByRole('textbox')
      await expect(artifactPanel.getByRole('button').filter({ hasText: 'Synthetic retained release evidence' })).toBeVisible()
      await expect(artifactPanel.getByRole('button').filter({ hasText: 'Synthetic retained other-objective evidence' })).toHaveCount(0)
      await artifactSearch.fill('Synthetic retained')
      await expect(artifactPanel.getByRole('button').filter({ hasText: 'Synthetic retained other-objective evidence' })).toHaveCount(0)
      await decisions.focus()
      await page.keyboard.press('Space')
      await expect(decisions).toHaveAttribute('aria-selected', 'true')
      await expect(artifactPanel).toHaveAttribute('hidden', '')
      await expect(artifactPanel.locator('input')).toHaveValue('Synthetic retained')
      await artifacts.click()
      await expect(artifactSearch).toHaveValue('Synthetic retained')
      current.focusChecks.push(await verifyKeyboardFocus(page, artifactSearch, `${label}-retained-evidence-search`))
      await captureGallery(current, `${label}-retained-search`, workspace)
      const back = workspace.locator('a.eid-back')
      expect(await back.getAttribute('href')).toContain('/messages')
      await back.click()
      await expect(page).toHaveURL(messageUrl)
      await expect(page.locator('.eid-message-members button[aria-pressed]').filter({ hasText: 'Avery' })).toHaveAttribute('aria-pressed', 'true')
      continuity.push({ label, objectiveHref, messageUrl, headingGeometry, keyboard: ['directional-arrow', 'Home', 'End', 'Enter', 'Space'], retained: ['activity search', 'event selection', 'evidence search'], hiddenEscapeIsolated: true, hiddenPaneAttributesVerified: true, sampledTabFocusOutsideHidden: 8, sameOwnerMessagesReturn: true })
    }
    for (const invalid of [
      { label: 'missing-owner', from: '/messages', owner: '' },
      { label: 'foreign-owner', from: '/messages', owner: 'synthetic-other-owner' },
      { label: 'external-return', from: 'https://synthetic.invalid/external', owner: originOwner }
    ]) {
      const query = new URLSearchParams({ from: invalid.from, originOwner: invalid.owner }).toString()
      await page.evaluate(route => { location.hash = route }, `/objectives/release?${query}`)
      const back = workspace.locator('a.eid-back')
      await expect(back).toHaveAttribute('href', /#\/objectives$/)
      await back.click()
      await expect(page).toHaveURL(/#\/objectives$/)
      continuity.push({ locale, invalidReturn: invalid.label, safeFallback: '/objectives' })
    }
    expect(current.capabilityMethods).toEqual([])
    writeGalleryEvidence(current, 'objective-tab-retention')
  })
}
