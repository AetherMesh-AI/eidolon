import { STARTUP_REQUEST_TIMEOUT_MS } from '../src/api/request-timeouts'

import { acknowledgeExpectedErrorBanner, expect, test } from './test'
import { captureGallery, type GalleryFixture, persistGalleryEvidence, positionGalleryAtStart, resizeGallery, setupGallery, verifyGalleryControls, writeGalleryEvidence } from './workspace-gallery-fixture'

let fixture: GalleryFixture | undefined
test.setTimeout(240_000)
test.afterEach(async () => {
  try { if (fixture) { persistGalleryEvidence(fixture, 'navigation-journey') } }
  finally { await fixture?.cleanup(); fixture = undefined }
})

test('one owner journey retains recipient, work context, request and deliverable provenance', async () => {
  const current = fixture = await setupGallery('en')
  const { page } = current
  const rail = page.getByRole('complementary', { name: 'Eidolon navigation' })
  const workspace = page.locator('.eid-workspace').last()
  await resizeGallery(current, 1536, 961)
  await page.evaluate(() => { location.hash = '/home' })
  await expect(rail.getByRole('link', { name: 'Needs You', exact: true })).toBeVisible()
  await expect(rail.getByRole('button', { name: 'More workspace options' })).toHaveCount(0)
  await captureGallery(current, 'navigation-home', workspace)
  await rail.getByRole('link', { name: 'Messages', exact: true }).click()
  await page.getByRole('button', { name: /Avery/ }).click()
  const recipient = page.getByRole('button', { name: /Avery/ })
  await expect(recipient).toHaveAttribute('aria-pressed', 'true')
  const messageUrl = page.url()
  await page.reload()
  await expect(recipient).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('region', { name: 'Linked objectives' }).getByRole('link', { name: 'Prepare the autumn release brief' }).click()
  await expect(page.getByRole('heading', { name: 'Prepare the autumn release brief', exact: true, level: 1 })).toBeVisible()
  await expect(rail.getByRole('link', { name: 'Work Overview', exact: true })).toHaveAttribute('aria-current', 'page')
  await page.locator('a.eid-back').click()
  await expect(page).toHaveURL(messageUrl)
  await expect(recipient).toHaveAttribute('aria-pressed', 'true')
  await captureGallery(current, 'navigation-message-return', page.locator('.eid-messages-view'))
  await rail.getByRole('link', { name: 'Work Overview', exact: true }).click()
  await page.goBack()
  await expect(recipient).toHaveAttribute('aria-pressed', 'true')
  await page.goForward()
  await expect(rail.getByRole('link', { name: 'Objectives', exact: true })).toBeVisible()
  await captureGallery(current, 'navigation-work', workspace)
  await rail.getByRole('link', { name: 'Needs You', exact: true }).click()
  await captureGallery(current, 'navigation-needs-you', workspace)
  await rail.getByRole('link', { name: 'Home', exact: true }).click()
  await page.getByRole('link', { name: /Review request/ }).first().click()
  const request = page.getByRole('complementary', { name: 'Request details' })
  await expect(request).toBeVisible()
  await expect(request).toContainText('Review the proposed owner journey before the next iteration.')
  await captureGallery(current, 'navigation-focused-request', workspace)
  await request.getByRole('button', { name: 'Close request details' }).click()
  await expect(request).toHaveCount(0)
  expect(new URL(page.url()).hash).not.toContain('request=')
  await page.locator('a.eid-back').click()
  await expect(page.getByRole('heading', { name: 'Your organization', exact: true, level: 1 })).toBeVisible()
  await rail.getByRole('link', { name: 'Deliverables', exact: true }).click()
  await expect(page.getByRole('navigation', { name: 'Artifact sources' }).getByRole('link', { name: /Organization/ })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByText('Synthetic reviewed scope and provider boundary summary.', { exact: true })).toBeVisible()
  await captureGallery(current, 'navigation-deliverables', page.locator('.eid-deliverables'))
  await rail.getByRole('link', { name: 'Settings', exact: true }).click()
  await expect(rail.getByRole('link', { name: 'Skills', exact: true })).toBeVisible()
  const settings = page.locator('.eid-settings-page')
  const model = settings.locator('[data-section="config:model"] .eid-settings-card-body')
  // Model info/options share the product's bounded startup-read contract.
  // Wait for actual load completion, then require usable controls as before.
  await expect(model).toBeVisible()
  await expect(model.locator('[data-slot="model-settings-skeleton"]')).toHaveCount(0, { timeout: STARTUP_REQUEST_TIMEOUT_MS })
  await expect(model.getByRole('combobox').first()).toBeEnabled()
  await expect(model.locator('[data-slot="skeleton"], [data-slot="model-settings-skeleton"]')).toHaveCount(0)
  await captureGallery(current, 'navigation-settings', settings)
  await rail.getByRole('link', { name: 'Organization', exact: true }).click()
  await captureGallery(current, 'navigation-organization', workspace)
  await page.getByRole('button', { name: 'Inspect Avery', exact: true }).click()
  await captureGallery(current, 'navigation-agent-profile', page.getByRole('complementary', { name: 'Agent details' }))
  await page.getByRole('button', { name: 'Close agent details', exact: true }).click()
  await resizeGallery(current, 1280, 720, 2)
  await positionGalleryAtStart(rail)
  await verifyGalleryControls(current, rail, 'navigation-rail-200')
  await captureGallery(current, 'navigation-rail-200', rail)
  writeGalleryEvidence(current, 'navigation-journey')
})

test('empty workspace pages stay navigable and a failed observation has visible recovery', async () => {
  const current = fixture = await setupGallery('en')
  const { page } = current
  Object.assign(current.state.snapshot, { objectives: [], agents: [], tasks: [], requests: [], activity: [], knowledge: [], conversations: [] })
  Object.assign(current.state.snapshot.outcomes, { items: [], total: 0, unread: 0 })
  Object.assign(current.state.snapshot.runtime, { rosterCount: 0, workingCount: 0 })
  await page.reload()
  await resizeGallery(current, 1280, 800)
  await expect(page.getByRole('button', { name: 'Message organization', exact: true })).toBeEnabled()
  const rail = page.getByRole('complementary', { name: 'Eidolon navigation' })
  for (const label of ['Home', 'Messages', 'Work Overview', 'Needs You', 'Organization', 'Deliverables']) {
    await rail.getByRole('link', { name: label, exact: true }).click()
    const surface = label === 'Deliverables' ? page.locator('.eid-deliverables') : page.locator('.eid-workspace').last()
    await expect(surface.getByRole('heading', { level: 1 })).toHaveCount(1)
    await expect(surface.getByText('Prepare the autumn release brief', { exact: true })).toHaveCount(0)
    await positionGalleryAtStart(surface)
    await captureGallery(current, `empty-${label.toLowerCase().replaceAll(' ', '-')}`, surface)
    await verifyGalleryControls(current, surface, `empty-${label}`)
  }
  await rail.getByRole('link', { name: 'Home', exact: true }).click()
  const observationError = 'Synthetic observation unavailable for recovery inspection.'
  current.state.observationError = observationError
  await page.reload()
  const status = page.locator('.eid-workspace .eid-runtime-status').first()
  await expect(status.getByRole('alert')).toContainText(current.state.observationError)
  await expect(status.getByRole('button', { name: /Retry/ })).toBeEnabled()
  await captureGallery(current, 'observation-error-recovery', page.locator('.eid-workspace').last())
  current.state.observationError = null
  await status.getByRole('button', { name: /Retry/ }).click()
  await expect(status).toHaveCount(0)
  await acknowledgeExpectedErrorBanner(page, observationError)
  await expect(page.getByRole('button', { name: 'Message organization', exact: true })).toBeEnabled()
  await expect(rail.getByRole('link', { name: 'Home', exact: true })).toHaveAttribute('aria-current', 'page')
  await captureGallery(current, 'observation-recovered', page.locator('.eid-workspace').last())
  writeGalleryEvidence(current, 'navigation-empty-recovery')
})

test('objective entry and Back preserve owner list context at zoom and narrow widths', async () => {
  const current = fixture = await setupGallery('en')
  const { page } = current
  const workspace = page.locator('.eid-workspace').last()
  const longTitle = 'Document provider boundaries with reviewed ownership, retained evidence and clear next steps. '.repeat(8).trim()
  current.state.snapshot.objectives[2].title = longTitle
  for (const objective of current.state.snapshot.objectives.slice(0, 2)) {
    objective.description = 'Retained synthetic context for checking list restoration without losing your place. '.repeat(25)
  }
  await page.reload()
  for (const { width, height, zoom } of [
    { width: 1280, height: 800, zoom: 1 },
    { width: 1280, height: 800, zoom: 1.25 },
    { width: 1280, height: 800, zoom: 2 },
    { width: 400, height: 620, zoom: 1 }
  ]) {
    const label = `objective-continuity-${width}-${zoom * 100}`
    await resizeGallery(current, width, height, zoom)
    await page.evaluate(() => { location.hash = '/objectives' })
    const origin = workspace.getByRole('link').filter({ has: page.getByText(longTitle, { exact: true }) })
    await origin.scrollIntoViewIfNeeded()
    // Playwright may scroll a tall card again to reach its click point. Record
    // the actual departure position, the same event the workspace remembers.
    await origin.evaluate(link => {
      delete document.documentElement.dataset.continuityDepartureTop
      link.addEventListener('click', event => {
        if (event.isTrusted) {
          document.documentElement.dataset.continuityDepartureTop = String(link.closest('.eid-workspace')!.scrollTop)
        }
      }, { once: true })
    })
    await origin.click()
    const originTop = await page.evaluate(() => Number(document.documentElement.dataset.continuityDepartureTop))
    expect(originTop).toBeGreaterThan(0)
    await expect(page.getByRole('heading', { level: 1, name: longTitle, exact: true })).toBeInViewport()
    await expect.poll(() => workspace.evaluate(element => element.scrollTop)).toBe(0)
    await captureGallery(current, `${label}-entry`, workspace)
    // An explicit work action owns its own target until the user navigates away.
    await page.getByRole('button', { name: 'Inspect work and evidence', exact: true }).click()
    await expect(page.getByRole('region', { name: 'Objective work details', exact: true })).toBeFocused()
    await page.locator('a.eid-back').click()
    await expect(page).toHaveURL(/#\/objectives$/)
    await expect.poll(() => workspace.evaluate(element => element.scrollTop)).toBeCloseTo(originTop, 0)
    await expect(origin).toBeFocused()
    await captureGallery(current, `${label}-back`, workspace)
    current.state.observationError = 'Synthetic continuity observation unavailable.'
    await page.reload()
    const status = workspace.locator('.eid-runtime-status').first()
    await expect(status.getByRole('alert')).toContainText(current.state.observationError)
    await captureGallery(current, `${label}-error`, workspace)
    current.state.observationError = null
    await status.getByRole('button', { name: /Retry/ }).click()
    await expect(status).toHaveCount(0)
    await acknowledgeExpectedErrorBanner(page, 'Synthetic continuity observation unavailable.')
  }
  expect(current.mock.receivedPrompts).toHaveLength(0)
  writeGalleryEvidence(current, 'objective-continuity')
})
