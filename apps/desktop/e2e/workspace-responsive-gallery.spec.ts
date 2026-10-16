import { writeFileSync } from 'node:fs'

import type { Locator } from '@playwright/test'

import { verifyKeyboardFocus } from './focus-visibility'
import { expect, test } from './test'
import { captureGallery, type GalleryFixture, gallerySizes, persistGalleryEvidence, positionGalleryAtStart, resizeGallery, setGalleryAppearance, setupGallery, writeGalleryEvidence } from './workspace-gallery-fixture'

let fixture: GalleryFixture | undefined
test.setTimeout(600_000)
test.afterEach(async () => {
  try {
    if (fixture) {
      persistGalleryEvidence(fixture, 'responsive')
      const ipc = await fixture.app.evaluate(() => (globalThis as any).__eidolonWorkspaceFixture)
      writeFileSync(test.info().outputPath('workspace-responsive-native-evidence.json'), JSON.stringify(ipc, null, 2))
      expect(ipc.mutations).toEqual([])
    }
  }
  finally { await fixture?.cleanup(); fixture = undefined }
})

async function keyboardControl(current: GalleryFixture, target: Locator, label: string, approach: 'next' | 'previous' = 'previous') {
  await expect(target).toBeEnabled()
  current.focusChecks.push(await verifyKeyboardFocus(current.page, target, label, approach))
  await expect(target).toBeInViewport({ ratio: 1 })
}

async function compactBack(current: GalleryFixture, root: Locator, row: Locator) {
  if (await root.getAttribute('data-compact') !== 'true') return
  const list = root.locator('[data-pane="list"]')
  const detail = root.locator('[data-pane="detail"]')
  await expect(list).toBeHidden()
  await expect(list).toHaveAttribute('inert', '')
  const back = detail.getByRole('button', { name: 'Back', exact: true })
  await keyboardControl(current, back, 'compact Back')
  // Traverse the native keyboard order; hidden list controls cannot reappear in it.
  for (let index = 0; index < 12; index++) {
    // CodeMirror owns Tab as an edit command. Do not modify the draft while testing navigation.
    if (await current.page.locator('.cm-content:focus').count()) break
    await current.page.keyboard.press('Tab')
    expect(await list.evaluate(element => element.contains(document.activeElement))).toBe(false)
  }
  await back.focus()
  await current.page.keyboard.press('Enter')
  await expect(detail).toBeHidden()
  await expect(detail).toHaveAttribute('inert', '')
  await expect(row).toBeFocused()
  await current.page.keyboard.press('Tab')
  expect(await detail.evaluate(element => element.contains(document.activeElement))).toBe(false)
  await row.focus()
  await current.page.keyboard.press('Enter')
  await expect(detail).toBeVisible()
}

for (const mode of ['dark', 'light'] as const) {
  test(`populated secondary drafts, selected history and critical controls survive responsive layout in ${mode}`, async () => {
    const current = fixture = await setupGallery('en', false, true)
    const { page } = current
    await setGalleryAppearance(current, mode, mode === 'dark' ? 'Dark' : 'Light')
    for (const { width, height, zoom } of gallerySizes) {
      const suffix = `en-${mode}-${width}x${height}-${zoom * 100}`
      await resizeGallery(current, width, height, zoom)
      await page.evaluate(() => { location.hash = '/messaging' })
      let root = page.locator('.eid-responsive-detail')
      const messagingRow = root.locator('[data-pane="list"]').getByRole('button').filter({ hasText: 'Synthetic messaging' })
      await messagingRow.click()
      const field = root.getByLabel('Workspace label', { exact: true })
      await field.fill('Unsaved synthetic workspace draft')
      await root.getByLabel('Destination label', { exact: true }).fill('Unsaved synthetic destination')
      await expect(root).toContainText('Synthetic connection error.')
      await keyboardControl(current, field, `${suffix} messaging field`)
      // Cross the breakpoint with focus in a live editor; no selection or draft may be lost.
      await resizeGallery(current, 1536, 961)
      await resizeGallery(current, width, height, zoom)
      await expect(field).toHaveValue('Unsaved synthetic workspace draft')
      await keyboardControl(current, root.getByRole('button', { name: 'Save changes', exact: true }), `${suffix} messaging save`)
      await keyboardControl(current, root.getByRole('switch'), `${suffix} messaging enabled`)
      await compactBack(current, root, messagingRow)
      await expect(field).toHaveValue('Unsaved synthetic workspace draft')
      await positionGalleryAtStart(root)
      await captureGallery(current, `responsive-messaging-${suffix}`, root)

      await page.evaluate(() => { location.hash = '/profiles' })
      root = page.locator('.eid-responsive-detail')
      const profileRow = root.locator('[data-panel-row="default"]').getByRole('button').first()
      await profileRow.click()
      const editor = root.locator('.cm-content[contenteditable="true"]')
      await expect(editor).toContainText('Synthetic profile')
      await editor.fill('# Unsaved synthetic profile draft')
      await resizeGallery(current, 1536, 961)
      await resizeGallery(current, width, height, zoom)
      await expect(editor).toHaveText('# Unsaved synthetic profile draft')
      await keyboardControl(current, root.getByRole('button', { name: 'Save SOUL.md', exact: true }), `${suffix} profile save`, 'next')
      await compactBack(current, root, profileRow)
      await expect(editor).toHaveText('# Unsaved synthetic profile draft')
      await positionGalleryAtStart(root)
      await captureGallery(current, `responsive-profile-${suffix}`, root)

      await page.evaluate(() => { location.hash = '/session-import' })
      root = page.locator('.eid-responsive-detail')
      for (const state of ['preview', 'imported', 'error']) {
        const row = root.locator('[data-pane="list"]').getByRole('button').filter({ hasText: `Synthetic ${state} conversation` })
        const back = root.getByRole('button', { name: 'Back', exact: true })
        if (await back.isVisible()) await back.click()
        await row.click()
        await expect(root.locator('.session-import-preview-heading h2')).toHaveText(`Synthetic ${state} conversation`)
        if (state === 'error') {
          await expect(root.locator('[data-pane="detail"]')).toContainText('Preview unavailable')
          await keyboardControl(current, root.getByRole('button', { name: 'Retry', exact: true }), `${suffix} preview retry`)
          await expect(root.locator('footer button')).toBeDisabled()
        } else {
          await expect(root.locator('[data-pane="detail"]')).toContainText('Preserve this selection while resizing.')
          await keyboardControl(current, root.locator('footer button'), `${suffix} ${state} explicit import/open`)
          if (state === 'imported') await expect(root.locator('footer button')).toContainText('Open')
        }
        await resizeGallery(current, 1536, 961)
        await resizeGallery(current, width, height, zoom)
        await expect(root.locator('.session-import-preview-heading h2')).toHaveText(`Synthetic ${state} conversation`)
        await compactBack(current, root, row)
        await positionGalleryAtStart(root)
        await captureGallery(current, `responsive-import-${state}-${suffix}`, root)
      }

      await page.evaluate(() => { location.hash = '/settings?tab=config:appearance' })
      const settings = page.locator('.eid-settings-page')
      await positionGalleryAtStart(settings)
      const first = settings.locator('.eid-settings-controls button:visible').first()
      await expect(first).toBeInViewport({ ratio: 1 })
      await keyboardControl(current, first, `${suffix} Settings first control`)
      await captureGallery(current, `responsive-settings-${suffix}`, settings)
    }
    const ipc = await current.app.evaluate(() => (globalThis as any).__eidolonWorkspaceFixture)
    expect(ipc.reads).toContain('/api/messaging/platforms')
    expect(ipc.reads.some((path: string) => path.endsWith('/soul'))).toBe(true)
    expect(ipc.mutations).toEqual([])
    writeGalleryEvidence(current, 'responsive')
  })
}
