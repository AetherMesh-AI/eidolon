import { verifyKeyboardFocus } from './focus-visibility'
import { galleryAttentionPage } from './organization-attention-gallery-fixture'
import { expectRequestReadPosition, expectRequestScrollReturn, openRequestAtNativeDeparture, requestReadGeometry } from './request-inspection-continuity'
import { acknowledgeExpectedErrorBanner, expect, test } from './test'
import { captureGallery, type GalleryFixture, persistGalleryEvidence, positionGalleryAtStart, resizeGallery, setupGallery, writeGalleryEvidence } from './workspace-gallery-fixture'

let fixture: GalleryFixture | undefined
test.setTimeout(240_000)
test.afterEach(async () => {
  try { if (fixture) { persistGalleryEvidence(fixture, 'current-attention-recovery') } }
  finally { await fixture?.cleanup(); fixture = undefined }
})

test('current attention inbox retains its cursor, owner and unsent long-request draft through read failure and Retry', async () => {
  const current = fixture = await setupGallery('en', false, false, { blockOrganizationMutations: true })
  const { page, attentionControl } = current
  const original = current.state.snapshot.requests[0]
  const longOutcome = 'Current inbox owner review: preserve the original request and recorded objective while reading every part of this long question. '.repeat(10).trim()
  current.state.snapshot.requests = Array.from({ length: 26 }, (_, index) => ({
    ...original,
    id: `fixture-attention-${index}`,
    attentionRevision: 1,
    requestedOutcome: index === 25 ? longOutcome : `Synthetic current-inbox question ${index + 1}.`,
    reason: index === 25 ? 'This is a synthetic long owner request with retained context. '.repeat(12).trim() : 'Synthetic bounded pagination record.',
    allowedResolutions: [{ action: 'answer_request', label: 'Answer request', requiresText: true, requiresEvidence: false }]
  }))

  const records = current.state.snapshot.requests.map(request => ({
    requestId: request.id, objectiveId: request.objectiveId, revision: 1, seen: false,
    createdAt: request.createdAt, updatedAt: request.createdAt
  }))

  current.state.attention = { items: records, error: null }
  Object.assign(current.state.snapshot, { attention: galleryAttentionPage(records) })
  await page.reload()
  const workspace = page.locator('.eid-workspace').last()

  for (const { width, height, zoom } of [
    { width: 1280, height: 800, zoom: 1.25 },
    { width: 1280, height: 800, zoom: 2 },
    { width: 400, height: 620, zoom: 1 }
  ]) {
    const label = `current-attention-${width}-${zoom * 100}`
    await page.evaluate(() => { location.hash = '/home' })
    await expect(workspace.locator('.eid-runtime-requests')).toHaveCount(0)
    await resizeGallery(current, width, height, zoom)
    attentionControl.pause()
    await page.evaluate(() => { location.hash = '/requests' })
    const inbox = workspace.getByRole('region', { name: 'Attention inbox', exact: true })
    const loading = inbox.getByRole('status', { name: 'Reading attention inbox', exact: true })
    await expect.poll(() => attentionControl.pending).toBeGreaterThan(0)
    await expect(loading).toBeVisible()
    await expect(inbox.getByRole('alert')).toHaveCount(0)
    await expect(inbox.getByText('Nothing needs your input', { exact: true })).toHaveCount(0)
    await expect(inbox.getByText('Needs You: 26 · Unread: 26', { exact: true })).toBeVisible()
    await loading.scrollIntoViewIfNeeded()
    await captureGallery(current, `${label}-initial-loading`, workspace)
    attentionControl.release()
    await expect(loading).toHaveCount(0)
    await expect(inbox.getByRole('button', { name: 'Inspect request: Question', exact: true })).toHaveCount(25)
    await inbox.getByRole('button', { name: 'Unread', exact: true }).click()
    await expect.poll(() => current.attentionReads.at(-1)?.unreadOnly).toBe(true)
    await expect(loading).toHaveCount(0)
    const pagination = inbox.getByRole('navigation', { name: 'Attention inbox pages', exact: true })
    await pagination.getByRole('button', { name: 'Next inbox page', exact: true }).scrollIntoViewIfNeeded()
    await captureGallery(current, `${label}-first-page-controls`, workspace)
    await pagination.getByRole('button', { name: 'Next inbox page', exact: true }).click()
    await expect(inbox.getByText('Inbox page 2', { exact: true })).toBeVisible()
    await expect(inbox.getByRole('button', { name: 'Inspect request: Question', exact: true })).toHaveCount(1)
    expect(current.attentionReads.at(-1)).toEqual({ before: records[24].requestId, limit: 25, unreadOnly: true })
    await expect(pagination.getByRole('button', { name: 'Next inbox page', exact: true })).toBeDisabled()
    await expect(pagination.getByRole('button', { name: 'Previous inbox page', exact: true })).toBeEnabled()
    const origin = inbox.getByRole('button', { name: 'Inspect request: Question', exact: true })
    await origin.focus()
    const preparedTop = await workspace.evaluate(element => element.scrollTop)
    await captureGallery(current, `${label}-long-second-page`, workspace)
    const departure = await openRequestAtNativeDeparture(page, origin, preparedTop)
    const details = page.getByRole('complementary', { name: 'Request details', exact: true })
    const close = details.getByRole('button', { name: 'Close request details', exact: true })
    const response = details.getByRole('textbox', { name: 'Response', exact: true })
    const objectiveLink = details.getByRole('link', { name: 'Open objective', exact: true })
    const objectiveHref = await objectiveLink.getAttribute('href')
    expect(objectiveHref).toContain('/objectives/navigation')
    expect(objectiveHref).toContain('requestOwner')
    await expect(details).toContainText('Morgan')
    await expect(details).toContainText(records[25].requestId)
    await expect(details).toContainText(longOutcome)
    const draft = `Unsent current-inbox owner response at ${width}px/${zoom * 100}%.`
    await response.fill(draft)

    attentionControl.pause()
    await inbox.getByRole('button', { name: 'Refresh', exact: true }).click()
    await expect.poll(() => attentionControl.pending).toBeGreaterThan(0)
    await expect(loading).toBeVisible()
    await expect(inbox.getByRole('button', { name: 'Mark seen', exact: true })).toBeDisabled()
    await loading.scrollIntoViewIfNeeded()
    await captureGallery(current, `${label}-refresh-loading`, workspace)
    current.focusChecks.push(await verifyKeyboardFocus(page, close, `${label} close`, 'previous'))
    const beforeFailure = await requestReadGeometry(close)
    current.state.attention!.error = `Synthetic current attention-page read failed at ${width}px/${zoom * 100}%.`
    attentionControl.release()
    const error = inbox.getByRole('alert')
    await expect(error).toContainText('Could not read attention inbox')
    await expect(error).toContainText(current.state.attention!.error)
    const expectedErrorText = (await error.textContent())!.trim()
    await expect(loading).toHaveCount(0)
    await expect(inbox.getByText('Nothing needs your input', { exact: true })).toHaveCount(0)
    await expect(inbox.getByText('Needs You: 26 · Unread: 26', { exact: true })).toBeVisible()
    await expect(inbox.getByText('Inbox page 2', { exact: true })).toBeVisible()
    await expect(inbox.getByRole('button', { name: 'Unread', exact: true })).toHaveAttribute('aria-pressed', 'true')
    await expect(response).toHaveValue(draft)
    await expect(objectiveLink).toHaveAttribute('href', objectiveHref!)
    await expect(close).toBeFocused()
    await expectRequestReadPosition(close, beforeFailure, `${label}-failure`)
    await captureGallery(current, `${label}-failed-draft-retained`, workspace)
    await error.scrollIntoViewIfNeeded()
    await captureGallery(current, `${label}-visible-error`, workspace)

    attentionControl.pause()
    await inbox.getByRole('button', { name: 'Retry inbox', exact: true }).click()
    await expect.poll(() => attentionControl.pending).toBeGreaterThan(0)
    await expect(loading).toBeVisible()
    await expect(error).toHaveCount(0)
    await expect(response).toHaveValue(draft)
    await loading.scrollIntoViewIfNeeded()
    await captureGallery(current, `${label}-retry-loading`, workspace)
    await close.focus()
    const beforeRecovery = await requestReadGeometry(close)
    current.state.attention!.error = null
    attentionControl.release()
    await expect(loading).toHaveCount(0)
    await expect(error).toHaveCount(0)
    await expect(response).toHaveValue(draft)
    await expect(close).toBeFocused()
    await expectRequestReadPosition(close, beforeRecovery, `${label}-recovery`)
    await expect(objectiveLink).toHaveAttribute('href', objectiveHref!)
    await expect(inbox.getByText('Inbox page 2', { exact: true })).toBeVisible()
    expect(current.attentionReads.at(-1)).toEqual({ before: records[24].requestId, limit: 25, unreadOnly: true })
    await acknowledgeExpectedErrorBanner(page, expectedErrorText)
    await captureGallery(current, `${label}-recovered-draft`, workspace)
    await close.click()
    await expect(details).toHaveCount(0)
    await expect(origin).toBeFocused()
    await expectRequestScrollReturn(workspace, origin, departure, label)
    await positionGalleryAtStart(workspace)
    await captureGallery(current, `${label}-recovered-second-page`, workspace)
  }

  writeGalleryEvidence(current, 'current-attention-recovery')
})
