import { writeFileSync } from 'node:fs'

import type { Locator } from '@playwright/test'

import { verifyKeyboardFocus } from './focus-visibility'
import { expect, test } from './test'
import {
  captureGallery,
  type GalleryFixture,
  persistGalleryEvidence,
  resizeGallery,
  setupGallery
} from './workspace-gallery-fixture'

const sizes = [
  { width: 1536, height: 961, zoom: 1 },
  { width: 1280, height: 720, zoom: 1.25 },
  { width: 1280, height: 720, zoom: 2 },
  { width: 400, height: 620, zoom: 1 }
]
let fixture: GalleryFixture | undefined

test.setTimeout(600_000)
test.afterEach(async () => {
  if (!fixture) return
  try {
    persistGalleryEvidence(fixture, 'content-space')
    const ipc = await fixture.app.evaluate(() => (globalThis as any).__eidolonWorkspaceFixture)
    writeFileSync(test.info().outputPath('content-space-native-evidence.json'), JSON.stringify(ipc, null, 2))
    expect(ipc.mutations).toEqual([])
    expect(fixture.foreignMethods).not.toContain('session.foreign.import')
    expect(fixture.mock.receivedPrompts).toHaveLength(0)
  } finally {
    await fixture.cleanup()
    fixture = undefined
  }
})

async function expectLongDraft(editor: Locator) {
  // CodeMirror virtualizes long documents; prove both retained ends through
  // real caret navigation rather than expecting unmounted lines in the DOM.
  await editor.press('ControlOrMeta+End')
  await expect(editor).toContainText('Draft line 80')
  await editor.press('ControlOrMeta+Home')
  await expect(editor).toContainText('# Unsaved instructions')
}

test('profile editor has a real bounded viewport and import content has one scroll owner', async () => {
  const current = (fixture = await setupGallery('en', false, true, { longPreview: true }))
  const { page } = current
  const draft =
    '# Unsaved instructions\n\n' + Array.from({ length: 80 }, (_, index) => `Draft line ${index + 1}`).join('\n')
  for (const { width, height, zoom } of sizes) {
    const label = `${width}x${height}-${zoom * 100}`
    await resizeGallery(current, width, height, zoom)
    await page.evaluate(() => {
      location.hash = '/profiles'
    })
    const root = page.locator('.eid-responsive-detail')
    const row = root.locator('[data-panel-row="default"]').getByRole('button').first()
    const back = root.getByRole('button', { name: 'Back', exact: true })
    await root.locator('.cm-content[contenteditable="true"]').waitFor({ state: 'attached' })
    if (await back.isVisible()) await back.click()
    await row.click()
    const editor = root.locator('.cm-content[contenteditable="true"]')
    await editor.fill(draft)
    const save = root.getByRole('button', { name: 'Save SOUL.md', exact: true })
    current.focusChecks.push(await verifyKeyboardFocus(page, save, `${label} adjacent profile Save`, 'next'))
    const geometry = await root.evaluate(element => {
      const host = element.querySelector('.profile-soul-editor')!
      const frame = host.firstElementChild!
      const save = [...element.querySelectorAll('button')].find(button => button.textContent?.includes('Save SOUL.md'))!
      const h = host.getBoundingClientRect(),
        f = frame.getBoundingClientRect(),
        b = save.getBoundingClientRect()
      return { host: h.height, frame: f.height, gap: b.top - h.bottom }
    })
    expect(geometry.host).toBeGreaterThanOrEqual(128)
    expect(geometry.host).toBeLessThanOrEqual(256)
    expect(Math.abs(geometry.frame - geometry.host)).toBeLessThanOrEqual(1)
    expect(geometry.gap).toBeGreaterThanOrEqual(0)
    expect(geometry.gap).toBeLessThanOrEqual(24)
    await captureGallery(current, `content-profile-editor-${label}`, root)
    await resizeGallery(current, 1536, 961)
    await resizeGallery(current, width, height, zoom)
    await expectLongDraft(editor)
    if (await back.isVisible()) {
      await back.click()
      await expect(row).toBeFocused()
      await row.click()
      await expectLongDraft(editor)
    }

    await page.evaluate(() => {
      location.hash = '/session-import'
    })
    const importRoot = page.locator('.eid-responsive-detail')
    const previewRow = importRoot.getByRole('button').filter({ hasText: 'Synthetic preview conversation' })
    await previewRow.click()
    const preview = importRoot.locator('.session-import-preview')
    const detail = importRoot.locator('.session-import-detail')
    const paragraph = preview.getByText('Readable imported message.', { exact: true })
    await paragraph.scrollIntoViewIfNeeded()
    await expect(paragraph).toBeInViewport({ ratio: 1 })
    expect(await preview.evaluate(element => getComputedStyle(element).overflowY)).toBe('visible')
    expect(await detail.evaluate(element => element.clientHeight)).toBeGreaterThanOrEqual(96)
    await captureGallery(current, `content-import-readable-${label}`, importRoot)
    const continueButton = importRoot.locator('footer button')
    current.focusChecks.push(await verifyKeyboardFocus(page, continueButton, `${label} import footer`))
    await expect(continueButton).toBeInViewport({ ratio: 1 })
    if (await importRoot.getByRole('button', { name: 'Back', exact: true }).isVisible()) {
      await importRoot.getByRole('button', { name: 'Back', exact: true }).click()
      await expect(previewRow).toBeFocused()
    }
    await importRoot.getByRole('button').filter({ hasText: 'Synthetic error conversation' }).click()
    const retry = importRoot.getByRole('button', { name: 'Retry', exact: true })
    current.focusChecks.push(await verifyKeyboardFocus(page, retry, `${label} import retry`))
    await expect(retry).toBeInViewport({ ratio: 1 })
    await expect(importRoot.locator('footer button')).toBeDisabled()
    await captureGallery(current, `content-import-error-${label}`, importRoot)
  }
})

test('empty import copy is readable at every supported content width', async () => {
  const current = (fixture = await setupGallery('en', false, true, { emptyHistory: true }))
  for (const { width, height, zoom } of sizes) {
    await resizeGallery(current, width, height, zoom)
    await current.page.evaluate(() => {
      location.hash = '/session-import'
    })
    const list = current.page.locator('.session-import-list')
    const emptyCopy = list.getByText('No conversations found', { exact: true })
    await emptyCopy.scrollIntoViewIfNeeded()
    await expect(emptyCopy).toBeInViewport({ ratio: 1 })
    const help = list.getByText('Claude Code and Codex sessions on this backend will appear here.', { exact: true })
    await help.scrollIntoViewIfNeeded()
    await expect(help).toBeInViewport({ ratio: 1 })
    expect(await list.evaluate(element => element.clientHeight)).toBeGreaterThanOrEqual(96)
    await captureGallery(current, `content-import-empty-${width}x${height}-${zoom * 100}`, list)
  }
})
