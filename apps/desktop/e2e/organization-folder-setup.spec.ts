/** Real renderer and owner RPC; only the operating-system picker is substituted. */
import { execFileSync } from 'node:child_process'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

import { buildAppEnv, launchDesktop, type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { switchPython } from './organization-switch-fixture'
import { acknowledgeExpectedErrorBanner, expect, test, type Page } from './test'

let fixture: MockBackendFixture | undefined
let root: string | undefined
type ReviewReply = { outcome: 'pending' | 'success' | 'noncommit' | 'unrecognized'; reason?: string }
const reviewReplies: ReviewReply[] = []
let reviewOverflow = false
let stopReviewObservation: (() => Promise<void>) | undefined
const repo = path.resolve(import.meta.dirname, '../../..')
test.setTimeout(240_000)
test.afterEach(async () => {
  try {
    await test.info().attach('folder-review-response-kinds', {
      body: JSON.stringify({ replies: reviewReplies, overflow: reviewOverflow }), contentType: 'application/json'
    })
  } finally {
    await stopReviewObservation?.().catch(() => undefined)
    stopReviewObservation = undefined
    try {
      await fixture?.app.close().catch(() => undefined)
      await fixture?.cleanup()
    } finally {
      fixture = undefined
      if (root) { fs.rmSync(root, { recursive: true, force: true }); root = undefined }
    }
  }
})

/** Passive protocol observation, including an already-open socket. Never send,
 * replace or delay an application frame; retain only a bounded reason enum. */
async function observeReviewReplies(page: Page) {
  const observer = await page.context().newCDPSession(page)
  const pending = new Map<string, number>()
  let pendingRead: { key: string; socket: string; organizationId: string } | undefined
  let currentRead: { socket: string; organizationId: string; generation: number; reviewVersion: string } | undefined
  const reasons = new Set(['generation_conflict', 'grant_conflict', 'review_conflict', 'execution_still_stopping',
    'project_reconciliation_required', 'uncertain_outcome_review_required', 'execution_not_initialized'])
  const parse = (payload: string) => {
    try {
      return JSON.parse(payload) as { id?: unknown; method?: string;
        params?: { expectedOrganizationId?: unknown; expectedGeneration?: unknown; reviewVersion?: unknown };
        error?: { code?: unknown; data?: { reason?: unknown } };
        result?: { capabilityVersion?: unknown; organizationId?: unknown; policyGeneration?: unknown; reviewVersion?: unknown;
          scope?: { kind?: unknown; profile?: unknown }; accessRevoked?: boolean; reviewed?: boolean }
      } | null
    } catch { return null }
  }
  observer.on('Network.webSocketFrameSent', ({ requestId, response }) => {
    if (response.opcode !== 1) { return }
    const message = parse(response.payloadData)

    if (!message || !['string', 'number'].includes(typeof message.id)) { return }
    const key = `${requestId}:${typeof message.id}:${message.id}`

    if (message.method === 'organization.folderAccess' && typeof message.params?.expectedOrganizationId === 'string') {
      pendingRead = { key, socket: requestId, organizationId: message.params.expectedOrganizationId }
      currentRead = undefined
      return
    }
    if (message.method !== 'organization.reviewFolderRevocation') { return }
    if (reviewReplies.length >= 3) { reviewOverflow = true; return }
    if (!currentRead || currentRead.socket !== requestId || currentRead.organizationId !== message.params?.expectedOrganizationId ||
      currentRead.generation !== message.params.expectedGeneration || currentRead.reviewVersion !== message.params.reviewVersion) {
      reviewReplies.push({ outcome: 'unrecognized' })
      return
    }
    pending.set(key, reviewReplies.length)
    reviewReplies.push({ outcome: 'pending' })
  })
  observer.on('Network.webSocketFrameReceived', ({ requestId, response }) => {
    if (response.opcode !== 1) { return }
    const message = parse(response.payloadData)
    const key = `${requestId}:${typeof message?.id}:${message?.id}`

    if (pendingRead?.key === key) {
      const value = message?.result

      if (!message?.error && value?.capabilityVersion === 1 && value.scope?.kind === 'served-profile' && value.scope.profile === 'default' &&
        value.organizationId === pendingRead.organizationId && typeof value.policyGeneration === 'number' && typeof value.reviewVersion === 'string') {
        currentRead = { socket: pendingRead.socket, organizationId: pendingRead.organizationId,
          generation: value.policyGeneration, reviewVersion: value.reviewVersion }
      }
      pendingRead = undefined
      return
    }
    const index = pending.get(key)

    if (index === undefined) { return }
    pending.delete(key)
    const reason = message?.error?.data?.reason

    if (message?.error?.code === 5074 && typeof reason === 'string' && reasons.has(reason)) {
      reviewReplies[index] = { outcome: 'noncommit', reason }
    } else {
      reviewReplies[index] = { outcome: !message?.error && message?.result?.accessRevoked === true && message.result.reviewed === true ? 'success' : 'unrecognized' }
    }
  })
  await observer.send('Network.enable')
  stopReviewObservation = () => observer.detach()
}

async function openSetup() {
  const page = fixture!.page
  await page.getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })
    .getByRole('link', { name: 'Organization', exact: true }).click()
  await page.getByRole('button', { name: 'Set up project folders', exact: true }).click()

  return page.getByRole('complementary', { name: 'Agent details', exact: true })
}

test('owner selects two empty folders, approves access, and retains stable projects after restart', async () => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-reviewed-folders-'))
  const folders = ['notes', 'research'].map(name => path.join(root!, name))
  folders.forEach(folder => fs.mkdirSync(folder))
  fixture = await setupMockBackend({
    extraConfig: 'organization:\n  read_roots: []\n  projects: []\n  project_grants: []',
    beforeLaunch: async sandbox => {
      execFileSync(switchPython, ['-c', `
from pathlib import Path
from eidolon_cli.organization_metadata import save_metadata
from eidolon_cli.organization_store import OrganizationStore
import sys
ledger = Path(sys.argv[1])/'organization'/'state.db'
save_metadata(ledger, display_name='Folder Studio', owner_display_name='Owner', expected_organization_id=None, expected_revision=None, idempotency_key='fixture-owner')
OrganizationStore(ledger)
`, sandbox.hermesHome], { cwd: repo, env: buildAppEnv(sandbox) })
    }
  })
  await waitForAppReady(fixture)
  const config = path.join(fixture.sandbox.hermesHome, 'config.yaml')
  const before = fs.readFileSync(config, 'utf8')
  const calls = fixture.mock.receivedPrompts.length
  let panel = await openSetup()

  for (const [index, folder] of folders.entries()) {
    await fixture.app.evaluate(({ dialog }, selected) => {
      dialog.showOpenDialog = (async () => ({ canceled: false, filePaths: [selected] })) as typeof dialog.showOpenDialog
    }, folder)
    const section = panel.getByRole('region', { name: 'Choose a new project folder', exact: true })
    await section.getByRole('button', { name: 'Choose a new project folder', exact: true }).click()
    await expect(section.getByText(folder, { exact: true })).toBeVisible()
    const id = index === 0 ? 'notes' : 'research'
    await section.getByRole('textbox', { name: 'Project ID', exact: true }).fill(id)
    await section.getByRole('button', { name: 'Prepare project folder configuration', exact: true }).click()
    const approve = section.getByRole('button', { name: 'Approve this folder access and save project', exact: true })
    await expect(approve).toBeVisible()
    await expect(panel.getByRole('list', { name: 'Configured project folders', exact: true }).getByText(`${id} · root${index}`, { exact: false })).toHaveCount(0)
    expect(fs.readFileSync(config, 'utf8')).toBe(before)
    expect(fs.readdirSync(folder)).toEqual([])
    await section.screenshot({ path: test.info().outputPath(`folder-${index}-review.png`) })
    await approve.click()
    const saved = section.getByRole('status')
    const refusal = section.getByRole('alert')

    // An idle scheduler can briefly hold the same capacity fence. A refusal is
    // terminal until the owner explicitly retries the retained reviewed intent.
    for (let retry = 0; retry < 3; retry += 1) {
      await expect(saved.or(refusal)).toBeVisible()

      if (await saved.isVisible()) {
        break
      }

      await expect(refusal).toHaveText('Organization owner policy: execution_still_stopping')
      await expect(section.getByText(folder, { exact: true })).toBeVisible()
      await expect(section.getByText(`${id} · root${index} · general`, { exact: true })).toBeVisible()
      await expect(panel.getByRole('list', { name: 'Configured project folders', exact: true }).getByRole('listitem')).toHaveCount(index)
      expect(fs.readFileSync(config, 'utf8')).toBe(before)
      expect(fs.readdirSync(folder)).toEqual([])
      expect(fixture.mock.receivedPrompts.length).toBe(calls)
      await section.getByRole('button', { name: 'Retry the same reviewed registration', exact: true }).click()
    }

    await expect(section.getByRole('status')).toHaveText('Project saved in this profile’s organization ledger.')
    await expect(panel.getByRole('list', { name: 'Configured project folders', exact: true }).getByRole('listitem')).toHaveCount(index + 1)
    expect(fixture.mock.receivedPrompts.length).toBe(calls)
  }

  await fixture.app.close()
  const launched = await launchDesktop(buildAppEnv(fixture.sandbox))
  fixture.app = launched.app; fixture.page = launched.page
  await waitForAppReady(fixture)
  panel = await openSetup()
  const projects = panel.getByRole('list', { name: 'Configured project folders', exact: true })
  await expect(projects.getByRole('listitem')).toHaveCount(2)
  await expect(projects).toContainText('notes · root0')
  await expect(projects).toContainText('research · root1')
  await panel.screenshot({ path: test.info().outputPath('folders-after-restart.png') })
  expect(fs.readFileSync(config, 'utf8')).toBe(before)
  folders.forEach(folder => expect(fs.readdirSync(folder)).toEqual([]))
  expect(fixture.mock.receivedPrompts.length).toBe(calls)

  let access = panel.getByRole('region', { name: 'Folder access', exact: true })
  await expect(access.getByText('Access approved', { exact: true })).toHaveCount(2)
  await access.getByRole('button', { name: 'Revoke folder access', exact: true }).first().click()
  let confirm = fixture.page.getByRole('dialog', { name: 'Confirm folder revocation', exact: true })
  await confirm.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(access.getByText('Access approved', { exact: true })).toHaveCount(2)
  await access.getByRole('button', { name: 'Revoke folder access', exact: true }).first().click()
  confirm = fixture.page.getByRole('dialog', { name: 'Confirm folder revocation', exact: true })
  await expect(confirm).toContainText(`root0 · ${folders[0]}`)
  await confirm.getByRole('button', { name: 'Confirm folder revocation', exact: true }).click()
  await expect(access.getByText('Access revoked', { exact: true })).toBeVisible()
  await expect(access.getByText('New organization dispatch is paused.', { exact: true })).toBeVisible()
  await access.screenshot({ path: test.info().outputPath('folder-revoked-awaiting-review.png') })

  // Revoked paths may disappear; retained owner controls must still work.
  fs.rmdirSync(folders[0])
  await fixture.app.close()
  const reopened = await launchDesktop(buildAppEnv(fixture.sandbox))
  fixture.app = reopened.app; fixture.page = reopened.page
  await observeReviewReplies(fixture.page)
  await waitForAppReady(fixture)
  panel = await openSetup()
  access = panel.getByRole('region', { name: 'Folder access', exact: true })
  await expect(access.getByText('Access revoked', { exact: true })).toBeVisible()
  const resume = access.getByRole('button', { name: 'Resume unaffected work after review', exact: true })
  const revokedGrant = access.getByRole('listitem').filter({ has: fixture.page.getByText(`root0 · ${folders[0]}`, { exact: true }) })
  const identity = await revokedGrant.locator('details').textContent()
  expect(identity).toBeTruthy()
  let expectedConflict: string | undefined

  // A clear read does not reserve the backend's commit fence. Only an observed
  // definite noncommit response permits another fresh read and owner decision.
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await expect.poll(async () => {
      await access.getByRole('button', { name: 'Refresh folder access', exact: true }).click()
      return resume.isEnabled()
    }, { timeout: 30_000 }).toBe(true)
    await expect(revokedGrant.getByText('Access revoked', { exact: true })).toBeVisible()
    await expect(revokedGrant.locator('details')).toHaveText(identity!)
    await expect(access.getByText('New organization dispatch is paused.', { exact: true })).toBeVisible()
    await expect(access.getByText('Access approved', { exact: true })).toHaveCount(1)

    if (expectedConflict && await fixture.page.evaluate(text =>
      (window as unknown as { __ERROR_BANNER_GUARD__?: string[] }).__ERROR_BANNER_GUARD__?.includes(text) ?? false, expectedConflict)) {
      await acknowledgeExpectedErrorBanner(fixture.page, expectedConflict)
    }
    expect(reviewReplies).toHaveLength(attempt)
    await resume.click()
    const review = fixture.page.getByRole('dialog', { name: 'Resume unaffected work after review', exact: true })
    await expect(review).toContainText(`root0 · ${folders[0]}`)
    await review.getByRole('button', { name: 'Resume unaffected work after review', exact: true }).click()
    await expect.poll(() => reviewReplies[attempt]?.outcome ?? 'pending').not.toBe('pending')
    expect(reviewOverflow).toBe(false)
    expect(reviewReplies).toHaveLength(attempt + 1)

    if (reviewReplies[attempt].outcome === 'success') { break }
    expect(reviewReplies[attempt].outcome).toBe('noncommit')
    const refusal = access.getByRole('alert')
    await expect(refusal).toContainText('Folder access changed. Refresh, review the current grant and outcomes, then confirm again.')
    await expect(access.getByText('Review recorded. Folder access remains revoked. Check current dispatch status below.', { exact: true })).toHaveCount(0)
    await expect(access.getByRole('button', { name: 'Retry the exact request', exact: true })).toHaveCount(0)
    await expect(resume).toHaveCount(0)
    expectedConflict = await refusal.innerText()
    expect(fs.existsSync(folders[0])).toBe(false)
    expect(fs.readdirSync(folders[1])).toEqual([])
    expect(fs.readFileSync(config, 'utf8')).toBe(before)
    expect(fixture.mock.receivedPrompts.length).toBe(calls)
  }
  await expect(access.getByText('Revocation reviewed; access remains revoked', { exact: true })).toBeVisible()
  await expect(access.getByText('New organization dispatch is paused.', { exact: true })).toHaveCount(0)
  await expect(panel.getByRole('list', { name: 'Configured project folders', exact: true })).toContainText('research · root1')
  await expect(panel.getByRole('list', { name: 'Configured project folders', exact: true })).not.toContainText('notes · root0')
  await access.screenshot({ path: test.info().outputPath('folder-revocation-reviewed.png') })
  expect(fs.existsSync(folders[0])).toBe(false)
  expect(fs.readdirSync(folders[1])).toEqual([])
  expect(fs.readFileSync(config, 'utf8')).toBe(before)
  expect(fixture.mock.receivedPrompts.length).toBe(calls)
})
