/** Native renderer proof; faults replace only synthetic read responses at IPC. */
import { writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'

import type { Locator } from '@playwright/test'

import { verifyKeyboardFocus } from './focus-visibility'
import { acknowledgeExpectedErrorBanner, expect, test } from './test'
import {
  captureGallery,
  type GalleryFixture,
  persistGalleryEvidence,
  positionGalleryAtStart,
  resizeGallery,
  setupGallery,
  writeGalleryEvidence
} from './workspace-gallery-fixture'

const require = createRequire(import.meta.url)
const { profile, sessionId, sessionPath, listPath, sessionTitle, sessionPage, transcript } = require('./session-artifact-recovery-bootstrap.cjs')
const sizes = [
  { width: 1536, height: 961, zoom: 1 },
  { width: 1280, height: 720, zoom: 1.25 },
  { width: 1280, height: 720, zoom: 2 },
  { width: 400, height: 620, zoom: 1 }
]
type ReadResult = { kind: 'pending' | 'error' | 'success'; data?: unknown }
let fixture: GalleryFixture | undefined
const transportBarriers: object[] = []

test.setTimeout(300_000)
test.afterEach(async () => {
  if (!fixture) return
  try {
    persistGalleryEvidence(fixture, 'session-artifact-recovery')
    const ipc = await fixture.app.evaluate(() => ({
      reads: (globalThis as any).__eidolonReadFaults.evidence,
      attachments: (globalThis as any).__eidolonAttachmentRecovery
    }))
    writeFileSync(test.info().outputPath('session-artifact-native-boundary.json'), JSON.stringify({
      ...ipc, transportBarriers, synthetic: true, nativeProfileCompletion: false,
      scope: 'Session files read recovery and pending synthetic profile selection. Ambient plugin roots and legacy owner migration are synthetic no-ops; no actual file access, mutation dispatch, or backend profile activation.'
    }, null, 2))
    expect(ipc.reads.writes).toEqual([])
    expect(ipc.attachments.blockedActions).toEqual([])
    expect(ipc.attachments.ambientStubs.every((entry: { backendDispatch: boolean; result: string }) =>
      entry.backendDispatch === false && ['no-synthetic-plugin-roots', 'no-synthetic-legacy-owners'].includes(entry.result))).toBe(true)
    expect(fixture.mock.receivedPrompts).toHaveLength(0)
  } finally {
    await fixture.cleanup()
    fixture = undefined
    transportBarriers.length = 0
  }
})

async function setRead(path: string, result: ReadResult) {
  await fixture!.app.evaluate((_, value) => {
    ;(globalThis as any).__eidolonReadFaults.set(value.path, value.result)
  }, { path, result })
}
async function releaseRead(path: string, result: ReadResult) {
  return fixture!.app.evaluate((_, value) =>
    (globalThis as any).__eidolonReadFaults.release(value.path, value.result), { path, result })
}
async function heldReads(path: string) {
  return fixture!.app.evaluate((_, value) => (globalThis as any).__eidolonReadFaults.pending(value), path)
}
async function readCount(path: string) {
  return fixture!.app.evaluate((_, value) =>
    (globalThis as any).__eidolonReadFaults.evidence.reads.filter((read: { path: string }) => read.path === value).length, path)
}
async function openSessionFiles() {
  const { page } = fixture!
  await page.getByRole('navigation', { name: 'Primary', exact: true }).getByRole('link', { name: 'Deliverables', exact: true }).click()
  const scope = page.locator('.eid-deliverables')
  await expect(scope.getByRole('heading', { level: 1, name: 'Deliverables', exact: true })).toBeVisible()
  await expect(scope.getByRole('link', { name: 'Organization evidence', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(scope).toContainText('Evidence and retained context from the current-profile organization ledger.')
  await scope.getByRole('link', { name: 'Session files', exact: true }).click()
  await expect(scope.getByRole('link', { name: 'Session files', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(scope).toContainText('Files and links from chat history. Opening a source returns to its original session.')
  return scope
}
async function captureSizes(scope: Locator, state: string, focusRetry = false) {
  for (const { width, height, zoom } of sizes) {
    const name = `session-files-${state}-${width}x${height}-${zoom * 100}`
    await resizeGallery(fixture!, width, height, zoom)
    await positionGalleryAtStart(scope)
    if (width / zoom <= 800 || height / zoom <= 600) {
      await expect(scope).toHaveCSS('overflow-y', 'auto')
      await expect(scope.locator('.eid-artifact-index')).toHaveCSS('overflow-y', 'visible')
    }
    await expect(scope.getByRole('link', { name: 'Session files', exact: true })).toHaveAttribute('aria-current', 'page')
    await captureGallery(fixture!, name, scope)
    if (focusRetry) {
      const retry = scope.getByRole('button', { name: 'Retry indexing', exact: true })
      fixture!.focusChecks.push(await verifyKeyboardFocus(fixture!.page, retry, `${name} scoped Retry`, 'previous'))
      await expect(retry).toBeInViewport({ ratio: 1 })
      await captureGallery(fixture!, `${name}-retry-focus`, scope)
    }
  }
  await resizeGallery(fixture!, 1536, 961)
}
async function assertSafeReadFailure(scope: Locator) {
  await expect(scope).not.toContainText('Synthetic read unavailable')
  await expect(scope).not.toContainText('No artifacts found')
  await expect(scope.getByRole('button', { name: 'Retry indexing', exact: true })).toBeEnabled()
}

// Initial error, successful empty, and partial unread are distinct outcomes.
test('Session files keeps loading and unread transcripts distinct from confirmed empty and retries safely', async () => {
  fixture = await setupGallery('en', false, false, { bootstrap: 'session-artifact-recovery-bootstrap.cjs' })
  const { page } = fixture
  await setRead(listPath, { kind: 'pending' })
  await setRead(sessionPath, { kind: 'success', data: transcript() })
  const scope = await openSessionFiles()
  await expect.poll(() => heldReads(listPath)).toBeGreaterThan(0)
  await expect(scope.getByRole('status', { name: 'Indexing recent session artifacts', exact: true })).toBeVisible()
  await expect(scope.getByText('No artifacts found', { exact: true })).toHaveCount(0)
  const before = await readCount(listPath)
  await page.keyboard.press('r')
  await page.keyboard.press('r')
  await captureSizes(scope, 'initial-loading')
  expect(await readCount(listPath)).toBe(before)

  await setRead(listPath, { kind: 'error' })
  expect(await releaseRead(listPath, { kind: 'error' })).toBeGreaterThan(0)
  const alert = scope.getByRole('alert')
  await expect(alert).toContainText('Artifacts failed to load')
  const expectedError = (await alert.textContent())!.trim()
  await assertSafeReadFailure(scope)
  await captureSizes(scope, 'enumeration-error', true)
  await setRead(listPath, { kind: 'success', data: sessionPage([]) })
  await scope.getByRole('button', { name: 'Retry indexing', exact: true }).click()
  await expect(scope.getByText('No artifacts found', { exact: true })).toBeVisible()
  await acknowledgeExpectedErrorBanner(page, expectedError)
  await captureSizes(scope, 'confirmed-empty')

  await setRead(listPath, { kind: 'success', data: { ...sessionPage(), errors: [{ profile: 'synthetic-unread-profile', error: 'Synthetic read unavailable.' }] } })
  await setRead(sessionPath, { kind: 'error' })
  await scope.getByRole('button', { name: 'Refresh artifacts', exact: true }).click()
  await expect(scope).toContainText('Could not read 1 of 1 recent sessions.')
  await expect(scope).toContainText('Profiles that could not be read: 1.')
  await expect(scope).toContainText('Other sessions are still unavailable')
  await assertSafeReadFailure(scope)
  await captureSizes(scope, 'transcript-unread', true)
  await setRead(listPath, { kind: 'success', data: sessionPage() })
  await setRead(sessionPath, { kind: 'success', data: transcript() })
  await scope.getByRole('button', { name: 'Retry indexing', exact: true }).click()
  await expect(scope.getByRole('button', { name: 'synthetic-report.pdf', exact: true })).toBeVisible()
  await expect(scope).toContainText(sessionTitle)
  await expect(scope).not.toContainText('Could not read')
  writeGalleryEvidence(fixture, 'session-artifact-recovery')
})

test('Session files retains same-owner attachments through failed enumeration and transcript refreshes, then replaces stale data', async () => {
  fixture = await setupGallery('en', false, false, { bootstrap: 'session-artifact-recovery-bootstrap.cjs' })
  await setRead(listPath, { kind: 'success', data: sessionPage() })
  await setRead(sessionPath, { kind: 'success', data: transcript() })
  const scope = await openSessionFiles()
  const report = scope.getByRole('button', { name: 'synthetic-report.pdf', exact: true })
  await expect(report).toBeVisible()
  await expect(scope).toContainText(sessionTitle)
  await expect(scope).toContainText('/synthetic-only/synthetic-report.pdf')
  await captureSizes(scope, 'populated-source')

  await setRead(listPath, { kind: 'pending' })
  await scope.getByRole('button', { name: 'Refresh artifacts', exact: true }).click()
  await expect.poll(() => heldReads(listPath)).toBeGreaterThan(0)
  await expect(report).toBeVisible()
  await expect(scope.getByRole('button', { name: 'Refreshing artifacts', exact: true })).toBeDisabled()
  await captureGallery(fixture, 'session-files-refresh-retains-current-owner', scope)
  await setRead(listPath, { kind: 'error' })
  await releaseRead(listPath, { kind: 'error' })
  await expect(scope).toContainText('may be out of date')
  await expect(report).toBeVisible()
  await assertSafeReadFailure(scope)
  await captureSizes(scope, 'stale-enumeration', true)

  await setRead(listPath, { kind: 'success', data: sessionPage() })
  await setRead(sessionPath, { kind: 'error' })
  await scope.getByRole('button', { name: 'Retry indexing', exact: true }).click()
  await expect(scope).toContainText('Could not read 1 of 1 recent sessions.')
  await expect(scope).toContainText('may be out of date')
  await expect(report).toBeVisible()
  await assertSafeReadFailure(scope)
  await captureSizes(scope, 'stale-transcript', true)

  await setRead(sessionPath, { kind: 'success', data: transcript('synthetic-recovered') })
  await scope.getByRole('button', { name: 'Retry indexing', exact: true }).click()
  await expect(scope.getByRole('button', { name: 'synthetic-recovered.pdf', exact: true })).toBeVisible()
  await expect(report).toHaveCount(0)
  await expect(scope.getByRole('button', { name: 'Retry indexing', exact: true })).toHaveCount(0)
  await expect(scope).not.toContainText('may be out of date')
  await expect(scope).toContainText(sessionTitle)
  await captureSizes(scope, 'recovered-source')
  writeGalleryEvidence(fixture, 'session-artifact-recovery')
})

test('pending profile selection suppresses prior attachments and a late transcript cannot restore or continue their scan', async () => {
  fixture = await setupGallery('en', false, false, { bootstrap: 'session-artifact-recovery-bootstrap.cjs' })
  const { page } = fixture
  await setRead(listPath, { kind: 'success', data: sessionPage() })
  await setRead(sessionPath, { kind: 'success', data: transcript() })
  const scope = await openSessionFiles()
  await expect(scope.getByRole('button', { name: 'synthetic-report.pdf', exact: true })).toBeVisible()
  const nextPath = '/api/sessions/synthetic-must-not-read/messages'
  await setRead(listPath, { kind: 'success', data: sessionPage([sessionId, 'synthetic-must-not-read']) })
  await setRead(sessionPath, { kind: 'pending' })
  await setRead(nextPath, { kind: 'success', data: transcript('synthetic-must-not-appear', 'synthetic-must-not-read') })
  await scope.getByRole('button', { name: 'Refresh artifacts', exact: true }).click()
  await expect.poll(() => heldReads(sessionPath)).toBeGreaterThan(0)
  await expect(scope.getByRole('button', { name: 'synthetic-report.pdf', exact: true })).toBeVisible()
  const history = page.locator('.eid-rail-history')
  if (await history.getAttribute('open') === null) await history.locator('summary').click()
  await page.locator('[data-slot="profile-rail"]').getByRole('button', { name: profile, exact: true }).click()
  await expect.poll(() => fixture!.app.evaluate(() => (globalThis as any).__eidolonAttachmentRecovery.pendingProfiles.length)).toBeGreaterThan(0)
  await expect(page.getByRole('button', { name: 'synthetic-report.pdf', exact: true })).toHaveCount(0)
  // Profile selection may open a fresh draft. Reopen the same source while
  // activation is held; the attachment owner must remain revoked throughout.
  await page.evaluate(() => { location.hash = '/artifacts?source=sessions' })
  await expect(scope.getByRole('status', { name: 'Waiting for the selected source', exact: true })).toBeVisible()
  await expect(scope.getByRole('button', { name: 'synthetic-report.pdf', exact: true })).toHaveCount(0)
  await expect(scope.getByRole('button', { name: 'Refresh artifacts', exact: true })).toBeDisabled()
  const barrierPath = '/api/synthetic-attachment-transport-barrier'
  await setRead(barrierPath, { kind: 'success', data: { syntheticTransportBarrier: true } })
  const releasedReads = await releaseRead(sessionPath, { kind: 'success', data: transcript('synthetic-obsolete') })
  expect(releasedReads).toBeGreaterThan(0)
  // A later renderer receipt on the same real IPC bridge, followed by a task
  // boundary, orders the stale-read assertions after response delivery. This
  // is a transport fence, not instrumentation of the obsolete callback.
  const receipt = await page.evaluate(async path => {
    const response = await (window as any).hermesDesktop.api({ path, method: 'GET' })
    await new Promise<void>(resolve => {
      const channel = new MessageChannel()
      channel.port1.onmessage = () => { channel.port1.close(); channel.port2.close(); resolve() }
      channel.port2.postMessage(null)
    })
    return response
  }, barrierPath)
  expect(receipt).toEqual({ syntheticTransportBarrier: true })
  expect(await readCount(barrierPath)).toBe(1)
  transportBarriers.push({ path: barrierPath, releasedReads, rendererReceipt: true, rendererTask: 'MessageChannel', backendDispatch: false })
  await expect(scope).not.toContainText('synthetic-obsolete.pdf')
  await expect(scope).not.toContainText('synthetic-must-not-appear.pdf')
  expect(await readCount(nextPath)).toBe(0)
  await expect(scope.getByRole('status', { name: 'Waiting for the selected source', exact: true })).toBeVisible()
  await captureGallery(fixture, 'session-files-pending-profile-revokes-late-transcript', scope)
  writeGalleryEvidence(fixture, 'session-artifact-recovery')
})
