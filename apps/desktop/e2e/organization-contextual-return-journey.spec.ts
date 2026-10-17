import { writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'

import type { Locator } from '@playwright/test'

import { verifyKeyboardFocus } from './focus-visibility'
import { acknowledgeExpectedErrorBanner, expect, test } from './test'
import { captureGallery, type GalleryFixture, persistGalleryEvidence, resizeGallery, setupGallery, writeGalleryEvidence } from './workspace-gallery-fixture'

const require = createRequire(import.meta.url)
const { profile } = require('./session-artifact-recovery-bootstrap.cjs')

const sizes = [
  { width: 1280, height: 800, zoom: 1 },
  { width: 1280, height: 800, zoom: 1.25 },
  { width: 1280, height: 800, zoom: 2 },
  { width: 400, height: 620, zoom: 1 }
]

const title = 'Document provider boundaries'
const artifactId = 'fixture-deliverable'
const errorText = 'Synthetic delayed evidence read failed. ' + 'LongUnbrokenEvidenceDiagnostic'.repeat(12)
const content = 'Retained synthetic evidence. ' + 'LongUnbrokenEvidenceText'.repeat(24)
let fixture: GalleryFixture | undefined
const returns: object[] = []

test.setTimeout(300_000)
test.afterEach(async () => {
  if (!fixture) {return}

  try {
    persistGalleryEvidence(fixture, 'contextual-return')

    const boundary = await fixture.app.evaluate(() => ({
      reads: (globalThis as any).__eidolonReadFaults.evidence,
      actions: (globalThis as any).__eidolonAttachmentRecovery
    }))

    writeFileSync(test.info().outputPath('contextual-native-boundary.json'), JSON.stringify({ ...boundary, returns,
      scope: 'Synthetic default-profile evidence and blocked pending-profile selection; no completed activation or foreign-owner read is claimed.' }, null, 2))
    expect(boundary.reads.writes).toEqual([])
    expect(boundary.actions.blockedActions).toEqual([])
    expect(fixture.capabilityMethods).toEqual([])
    expect(fixture.mock.receivedPrompts).toHaveLength(0)
  } finally { await fixture.cleanup(); fixture = undefined; returns.length = 0 }
})

async function setup() {
  const current = fixture = await setupGallery('en', false, false, {
    bootstrap: 'session-artifact-recovery-bootstrap.cjs', syntheticEvidence: true, blockOrganizationMutations: true, blockCapabilityMutations: true
  })

  Object.assign(current.state.snapshot.agents[0], { identityId: 'synthetic-avery', context: {
    contextSummary: 'Retained synthetic member context. '.repeat(16),
    recentHistory: [{ requestId: 'synthetic-history', objectiveId: 'research', objectiveTitle: title, taskId: null,
      requestType: 'work.analyze', summary: 'Synthetic retained result', evidenceIds: [artifactId], createdAt: '2026-10-16T00:00:00Z' }]
  } })
  Object.assign(current.state.snapshot.activity[0], { objectiveId: 'research', text: 'Synthetic contextual event ' + 'LongUnbrokenEventTitle'.repeat(8) })
  current.evidenceControl!.state.artifacts[artifactId] = { id: artifactId, objectiveId: 'research', taskId: null, content,
    summary: 'Synthetic read-only evidence', sha256: 'synthetic-digest', createdAt: '2026-10-16T00:00:00Z' }
  // Observe delivery of this synthetic evidence on the renderer's actual socket.
  // This witness never changes messages or application state.
  await current.page.addInitScript(id => {
    const receipts: string[] = []

    ;(window as any).__contextualEvidenceReceipts = receipts
    const Original = window.WebSocket
    window.WebSocket = class extends Original {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols)
        this.addEventListener('message', event => {
          try {
            const message = JSON.parse(String(event.data))

            if ((message.result?.data ?? message.result)?.id === id) {receipts.push(id)}
          } catch { /* Non-JSON gateway frames are unrelated to this witness. */ }
        })
      }
    }
  }, artifactId)
  await current.page.reload()

  return current
}

async function tabTo(target: Locator) {
  for (let attempt = 0; attempt < 16 && !await target.evaluate(element => element === document.activeElement); attempt++) {
    await fixture!.page.keyboard.press('Tab')
  }

  await expect(target).toBeFocused()
  await expect(target).toBeInViewport({ ratio: 1 })
}

async function rememberDeparture(link: Locator) {
  await link.evaluate(element => {
    delete document.documentElement.dataset.contextDeparture
    element.addEventListener('click', event => {
      if (!event.isTrusted) {return}
      const root = element.closest('.eid-workspace')!
      const selectors = ['.eid-home-panel-body', '.eid-messages-layout > .eid-responsive-detail-list', '.eid-messages-layout > .eid-responsive-detail-content', '.eid-message-context']
      const panels = selectors.flatMap(selector => Array.from(root.querySelectorAll<HTMLElement>(selector)).map(panel => ({ selector, label: panel.getAttribute('aria-label') ?? '', top: panel.scrollTop })))
      document.documentElement.dataset.contextDeparture = JSON.stringify({ top: root.scrollTop, panels })
    }, { once: true })
  })
}

async function visitAndReturn(link: Locator, label: string) {
  const current = fixture!
  const { page } = current
  const origin = page.url()
  await rememberDeparture(link)
  await link.click()
  const position = await page.evaluate(() => JSON.parse(document.documentElement.dataset.contextDeparture!)) as { top: number; panels: { selector: string; label: string; top: number }[] }
  await expect(page.getByRole('heading', { level: 1, name: title, exact: true })).toBeInViewport()
  await captureGallery(current, `${label}-objective-entry`, page.locator('.eid-workspace').last())
  await page.locator('a.eid-back').click()
  await expect(page).toHaveURL(origin)
  await expect(link).toBeFocused()
  await expect.poll(() => page.locator('.eid-workspace').last().evaluate(element => element.scrollTop)).toBeCloseTo(position.top, 0)

  for (const panel of position.panels) {
    await expect.poll(() => page.locator(panel.selector).evaluateAll((elements, label) =>
      elements.find(element => (element.getAttribute('aria-label') ?? '') === label)?.scrollTop, panel.label)).toBeCloseTo(panel.top, 0)
  }

  await expect(link).toBeInViewport({ ratio: 1 })
  returns.push({ label, origin, position, exactOrigin: true, focusedOrigin: true })
  await captureGallery(current, `${label}-back`, page.locator('.eid-workspace').last())
}

test('contextual objectives return to accepted Home, member history and selected filtered inspectors', async () => {
  const current = await setup()
  const { page } = current

  for (const size of sizes) {
    await resizeGallery(current, size.width, size.height, size.zoom)
    const label = `context-${size.width}-${size.zoom * 100}`
    await page.evaluate(() => { location.hash = '/home' })
    const accepted = page.getByRole('heading', { name: title, exact: true }).locator('..')
    await visitAndReturn(accepted.getByRole('link', { name: 'View objective →', exact: true }), `${label}-home`)

    await page.evaluate(() => { location.hash = '/messages' })
    await page.getByRole('button', { name: /Avery/ }).click()
    const context = page.getByRole('region', { name: 'Member context', exact: true })
    await context.locator('summary').filter({ hasText: 'Identity, memory and history' }).click()
    const history = context.getByRole('region', { name: 'Recent identity history', exact: true })
    await visitAndReturn(history.getByRole('link', { name: title, exact: true }), `${label}-history`)
    expect(new URL(page.url()).hash).toContain('identity=synthetic-avery')

    await page.evaluate(() => { location.hash = '/activity' })
    await page.getByRole('searchbox', { name: 'Search activity', exact: true }).fill('Synthetic contextual event')
    const event = page.getByRole('button', { name: /Inspect event: Synthetic contextual event/ })
    await event.click()
    const inspector = page.getByRole('complementary', { name: 'Event details', exact: true })
    await visitAndReturn(inspector.getByRole('link', { name: 'Open objective', exact: true }), `${label}-activity`)
    await expect(page.getByRole('searchbox', { name: 'Search activity', exact: true })).toHaveValue('Synthetic contextual event')
    await expect(inspector).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(event).toBeFocused()
    await expect(inspector).toHaveCount(0)

    await page.evaluate(() => { location.hash = '/organization' })
    await page.getByRole('textbox', { name: 'Find an agent', exact: true }).fill('Avery')
    await page.getByRole('button', { name: 'List', exact: true }).click()
    const member = page.getByRole('button', { name: 'Inspect Avery', exact: true })
    await member.click()
    const agent = page.getByRole('complementary', { name: 'Agent details', exact: true })
    await visitAndReturn(agent.getByRole('region', { name: 'Recent identity history', exact: true }).getByRole('link', { name: title, exact: true }), `${label}-organization`)
    await expect(page.getByRole('textbox', { name: 'Find an agent', exact: true })).toHaveValue('Avery')
    await expect(page.getByRole('button', { name: 'List', exact: true })).toHaveAttribute('aria-pressed', 'true')
    await page.keyboard.press('Escape')
    await expect(member).toBeFocused()
    await expect(agent).toHaveCount(0)
  }

  expect(current.evidenceControl!.reads).toEqual([])
  writeGalleryEvidence(current, 'contextual-return')
})

async function openPreview() {
  const { page } = fixture!
  await page.evaluate(() => { location.hash = '/objectives/research' })
  await page.getByRole('button', { name: 'Inspect work and evidence', exact: true }).click()
  const origin = page.getByRole('button', { name: 'Read final deliverable', exact: true })
  await rememberDeparture(origin)
  await origin.click()
  const inspector = page.getByRole('complementary', { name: 'Artifact details', exact: true })
  await expect(inspector).toBeVisible()

  return { origin, inspector }
}

test('long evidence preview retains keyboard Retry, context and row focus at every viewport', async () => {
  const current = await setup()
  const { page } = current
  const seam = current.evidenceControl!

  for (const size of sizes) {
    await resizeGallery(current, size.width, size.height, size.zoom)
    const label = `preview-${size.width}-${size.zoom * 100}`
    seam.control.pause()
    const { origin, inspector } = await openPreview()
    await expect.poll(() => seam.control.pending).toBe(1)
    await expect(inspector.getByRole('button', { name: 'Retrieving full artifact', exact: true })).toHaveAttribute('aria-disabled', 'true')
    seam.state.error = errorText
    seam.control.release()
    await expect(inspector.getByRole('alert')).toHaveText(errorText)
    const retry = inspector.getByRole('button', { name: 'Retry artifact', exact: true })
    await tabTo(retry)
    current.focusChecks.push(await verifyKeyboardFocus(page, retry, `${label}-retry`))
    await captureGallery(current, `${label}-failed`, inspector)
    seam.control.pause()
    seam.state.error = null
    const before = seam.reads.length
    await page.keyboard.press('Enter')
    await expect.poll(() => seam.control.pending).toBe(1)
    await expect(inspector.getByRole('button', { name: 'Retrieving full artifact', exact: true })).toBeFocused()
    await page.keyboard.press('Enter')
    expect(seam.reads.length).toBe(before + 1)
    seam.control.release()
    await expect(inspector.getByText(content, { exact: true })).toBeVisible()
    await expect(inspector.getByRole('button', { name: 'Refresh artifact', exact: true })).toBeFocused()
    await acknowledgeExpectedErrorBanner(page, errorText)
    await captureGallery(current, `${label}-recovered`, inspector)
    const position = await page.evaluate(() => JSON.parse(document.documentElement.dataset.contextDeparture!)) as { top: number }
    await page.keyboard.press('Escape')
    await expect(inspector).toHaveCount(0)
    await expect(origin).toBeFocused()
    await expect.poll(() => page.locator('.eid-workspace').last().evaluate(element => element.scrollTop)).toBeCloseTo(position.top, 0)
    await captureGallery(current, `${label}-returned-row`, page.locator('.eid-workspace').last())
  }

  writeGalleryEvidence(current, 'contextual-preview')
})

for (const size of sizes) {test(`pending profile selection revokes late evidence at ${size.width}/${size.zoom * 100}%`, async () => {
  const current = await setup()
  await resizeGallery(current, size.width, size.height, size.zoom)
  const { page } = current
  const seam = current.evidenceControl!
  seam.control.pause()
  const { inspector } = await openPreview()
  await expect.poll(() => seam.control.pending).toBe(1)
  const history = page.locator('.eid-rail-history')

  if (await history.getAttribute('open') === null) {await history.locator('summary').click()}
  await page.locator('[data-slot="profile-rail"]').getByRole('button', { name: profile, exact: true }).click()
  await expect.poll(() => current.app.evaluate(() => (globalThis as any).__eidolonAttachmentRecovery.pendingProfiles.length)).toBeGreaterThan(0)
  await expect(inspector).toHaveCount(0)
  await expect(page.getByText(content, { exact: true })).toHaveCount(0)
  const before = await page.evaluate(() => (window as any).__contextualEvidenceReceipts.length)
  const connectedReplies = seam.control.pending
  seam.control.release()

  if (connectedReplies) {
    await expect.poll(() => page.evaluate(() => (window as any).__contextualEvidenceReceipts.length)).toBe(before + connectedReplies)
  }

  // A renderer task after the actual message witness orders stale UI checks.
  // If profile selection already closed the old socket, the fixture correctly
  // discards its reply instead; that is transport revocation, not late delivery.
  await page.evaluate(() => new Promise<void>(resolve => {
    const channel = new MessageChannel()

    channel.port1.onmessage = () => { channel.port1.close(); channel.port2.close(); resolve() }
    channel.port2.postMessage(null)
  }))
  await expect(inspector).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Refresh artifact', exact: true })).toHaveCount(0)
  await expect(page.getByText(content, { exact: true })).toHaveCount(0)
  returns.push({ label: 'pending-profile', pendingActivation: true, completedActivation: false, lateEvidenceHidden: true, connectedReplies, rendererEvidenceReceipts: await page.evaluate(() => (window as any).__contextualEvidenceReceipts.length), completion: connectedReplies ? 'late frame received and ignored' : 'old socket closed; held reply discarded' })
  await captureGallery(current, `contextual-pending-profile-${size.width}-${size.zoom * 100}`, page.locator('body'))
  writeGalleryEvidence(current, 'contextual-pending-profile')
})}
