import { verifyKeyboardFocus } from './focus-visibility'
import { expectRequestReadPosition, expectRequestScrollReturn, openRequestAtNativeDeparture, requestReadGeometry } from './request-inspection-continuity'
import { acknowledgeExpectedErrorBanner, expect, test } from './test'
import { captureGallery, type GalleryFixture, persistGalleryEvidence, positionGalleryAtStart, resizeGallery, setupGallery, writeGalleryEvidence } from './workspace-gallery-fixture'

let fixture: GalleryFixture | undefined
test.setTimeout(240_000)
test.afterEach(async () => {
  try { if (fixture) { persistGalleryEvidence(fixture, 'needs-you-recovery') } }
  finally { await fixture?.cleanup(); fixture = undefined }
})

const sizes = [
  { width: 1280, height: 800, zoom: 1.25 },
  { width: 1280, height: 800, zoom: 2 },
  { width: 400, height: 620, zoom: 1 }
] as const

test('legacy Needs You queue distinguishes pending and failed initial observation from verified empty', async () => {
  const current = fixture = await setupGallery('en', false, false, { blockOrganizationMutations: true })
  const { page, observationControl } = current
  await page.evaluate(() => { location.hash = '/requests' })
  await resizeGallery(current, 400, 620)
  observationControl.pause()
  await page.reload()
  await expect.poll(() => observationControl.pending).toBeGreaterThan(0)
  const workspace = page.locator('.eid-workspace').last()
  await expect(workspace.getByRole('heading', { level: 1, name: 'Needs You', exact: true })).toBeVisible()
  await expect(workspace.getByText('Request state has not been verified', { exact: true })).toBeVisible()
  await expect(workspace.getByText('Nothing needs your input', { exact: true })).toHaveCount(0)
  await expect(workspace.getByRole('alert')).toHaveCount(0)
  await workspace.getByText('Request state has not been verified', { exact: true }).scrollIntoViewIfNeeded()
  await captureGallery(current, 'needs-you-initial-observation-pending-400', workspace)

  const observationError = 'Synthetic Needs You observation unavailable. Retained requests are not an empty queue.'
  current.state.observationError = observationError
  observationControl.release()
  const status = workspace.locator('.eid-runtime-status')
  await expect(status.getByRole('alert')).toHaveText(observationError)
  await expect(workspace.getByText('Request state has not been verified', { exact: true })).toBeVisible()
  await expect(workspace.getByText('Nothing needs your input', { exact: true })).toHaveCount(0)
  await expect(workspace.getByRole('button', { name: 'Submit response', exact: true })).toHaveCount(0)
  await positionGalleryAtStart(workspace)
  await captureGallery(current, 'needs-you-initial-observation-failed-400', workspace)

  observationControl.pause()
  const retry = status.getByRole('button', { name: /Retry/ })
  await retry.click()
  await expect.poll(() => observationControl.pending).toBeGreaterThan(0)
  await expect(workspace.getByText('Nothing needs your input', { exact: true })).toHaveCount(0)
  await captureGallery(current, 'needs-you-initial-observation-retrying-400', workspace)
  current.state.observationError = null
  observationControl.release()
  await expect(status).toHaveCount(0)
  await expect(workspace.getByRole('button', { name: 'Inspect request: Question', exact: true })).toHaveCount(1)
  await acknowledgeExpectedErrorBanner(page, observationError)
  await expect(page).toHaveURL(/#\/requests$/)
  await positionGalleryAtStart(workspace)
  await captureGallery(current, 'needs-you-initial-observation-recovered-400', workspace)
  writeGalleryEvidence(current, 'needs-you-initial-recovery')
})

test('legacy Needs You queue retains long-request context, draft, focus and scroll through observation recovery', async () => {
  const current = fixture = await setupGallery('en', false, false, { blockOrganizationMutations: true })
  const { page, observationControl } = current
  const original = current.state.snapshot.requests[0]
  const longOutcome = 'Owner boundary review: retain the exact request, recorded requester and objective while inspecting a long response. '.repeat(10).trim()
  const longReason = 'Synthetic review context remains readable across narrow and zoomed windows. '.repeat(12).trim()
  current.state.snapshot.requests = Array.from({ length: 4 }, (_, index) => ({
    ...original,
    id: `fixture-owner-question-${index}`,
    requestedOutcome: index === 3 ? longOutcome : `Owner boundary review ${index + 1}: ${'Retained synthetic request context. '.repeat(7)}`,
    reason: index === 3 ? longReason : 'Synthetic context before the selected request.',
    allowedResolutions: [{ action: 'answer_request', label: 'Answer request', requiresText: true, requiresEvidence: false }]
  }))
  await page.reload()
  const workspace = page.locator('.eid-workspace').last()

  for (const { width, height, zoom } of sizes) {
    const label = `needs-you-continuity-${width}-${zoom * 100}`
    await resizeGallery(current, width, height, zoom)
    await page.evaluate(() => { location.hash = '/requests' })
    const queue = workspace.locator('.eid-runtime-requests')
    const search = queue.getByRole('textbox', { name: 'Find a request…', exact: true })
    await search.fill('Owner boundary review')
    await queue.getByRole('combobox', { name: 'Team', exact: true }).selectOption('Product')
    const origin = queue.getByRole('button', { name: 'Inspect request: Question', exact: true }).filter({ hasText: longOutcome })
    await origin.scrollIntoViewIfNeeded()
    await origin.focus()
    const preparedTop = await workspace.evaluate(element => element.scrollTop)
    expect(preparedTop).toBeGreaterThan(0)
    await captureGallery(current, `${label}-long-request`, workspace)
    const departure = await openRequestAtNativeDeparture(page, origin, preparedTop)
    const details = page.getByRole('complementary', { name: 'Request details', exact: true })
    const close = details.getByRole('button', { name: 'Close request details', exact: true })
    await expect(close).toBeFocused()
    await expect(details).toContainText(longOutcome)
    await expect(details).toContainText('Morgan')
    await expect(details).toContainText('fixture-owner-question-3')
    const objectiveLink = details.getByRole('link', { name: 'Open objective', exact: true })
    const objectiveHref = await objectiveLink.getAttribute('href')
    expect(objectiveHref).toContain('/objectives/navigation')
    expect(objectiveHref).toContain('requestOwner')
    const response = details.getByRole('textbox', { name: 'Response', exact: true })
    const draft = `Unsent synthetic response for ${width}px at ${zoom * 100}%. Keep this exact text after Retry.`
    await response.fill(draft)
    await expect(details.getByRole('combobox', { name: 'Action', exact: true })).toHaveValue('answer_request')
    current.focusChecks.push(await verifyKeyboardFocus(page, close, `${label} close`, 'previous'))
    const beforeFailure = await requestReadGeometry(close)
    await captureGallery(current, `${label}-draft`, workspace)

    // Wait for the app's actual next bounded observation; no reload or fake timers
    // may replace the mounted owner-scoped form during recovery.
    observationControl.pause()
    await expect.poll(() => observationControl.pending, { timeout: 20_000 }).toBeGreaterThan(0)
    const observationError = `Synthetic Needs You continuity read failed at ${width}px/${zoom * 100}%.`
    current.state.observationError = observationError
    observationControl.release()
    const status = workspace.locator('.eid-runtime-status')
    await expect(status.getByRole('alert')).toHaveText(observationError)
    await expect(response).toHaveValue(draft)
    await expect(response).toBeDisabled()
    await expect(details.getByRole('button', { name: 'Submit response', exact: true })).toBeDisabled()
    await expect(close).toBeFocused()
    await expectRequestReadPosition(close, beforeFailure, `${label}-failure`)
    await expect(search).toHaveValue('Owner boundary review')
    await expect(queue.getByRole('combobox', { name: 'Team', exact: true })).toHaveValue('Product')
    await expect(objectiveLink).toHaveAttribute('href', objectiveHref!)
    await expect(queue.getByText('Nothing needs your input', { exact: true })).toHaveCount(0)
    await captureGallery(current, `${label}-failed-draft-retained`, workspace)

    observationControl.pause()
    const retry = status.getByRole('button', { name: /Retry/ })
    await retry.click()
    await expect.poll(() => observationControl.pending).toBeGreaterThan(0)
    await captureGallery(current, `${label}-retry-pending`, workspace)
    await close.focus()
    const beforeRecovery = await requestReadGeometry(close)
    current.state.observationError = null
    observationControl.release()
    await expect(status).toHaveCount(0)
    await expect(response).toBeEnabled()
    await expect(response).toHaveValue(draft)
    await expect(close).toBeFocused()
    await expectRequestReadPosition(close, beforeRecovery, `${label}-recovery`)
    await expect(objectiveLink).toHaveAttribute('href', objectiveHref!)
    await acknowledgeExpectedErrorBanner(page, observationError)
    await captureGallery(current, `${label}-recovered-draft`, workspace)
    await close.click()
    await expect(details).toHaveCount(0)
    await expect(origin).toBeFocused()
    await expect(search).toHaveValue('Owner boundary review')
    await expectRequestScrollReturn(workspace, origin, departure, label)
    await expect(page).toHaveURL(/#\/requests$/)
    await captureGallery(current, `${label}-return-to-request`, workspace)
  }

  writeGalleryEvidence(current, 'needs-you-continuity')
})
