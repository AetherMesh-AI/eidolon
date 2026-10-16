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
      if (test.info().status !== test.info().expectedStatus) {
        const png = await fixture.app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64'))
        writeFileSync(test.info().outputPath('responsive-failure-en-native.png'), Buffer.from(png, 'base64'))
      }
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

async function compactNavigation(current: GalleryFixture, suffix: string, root: Locator, draft: Locator) {
  const { page } = current
  const trigger = page.getByRole('button', { name: 'Navigation', exact: true })
  const header = page.locator('[data-slot="workspace-header"]')
  if (await page.evaluate(() => innerWidth >= 640)) {
    await expect(trigger).toHaveCount(0)
    await expect(page.getByRole('complementary', { name: 'Eidolon navigation' })).toBeVisible()
    return
  }
  const retainedDraft = await draft.elementHandle()
  const before = await draft.inputValue()
  await expect(trigger).toHaveAttribute('aria-expanded', 'false')
  await keyboardControl(current, trigger, `${suffix} Navigation trigger`)
  await captureGallery(current, `responsive-navigation-trigger-${suffix}`, header)
  await page.keyboard.press('Enter')
  const overlay = page.locator(`#${await trigger.getAttribute('aria-controls')}`)
  await expect(overlay).toBeVisible()
  await expect(trigger).toHaveAttribute('aria-expanded', 'true')
  const selected = overlay.getByRole('link', { name: 'Settings', exact: true })
  await expect(selected).toHaveAttribute('aria-current', 'page')
  await expect(selected).toBeFocused()
  for (const name of ['Home', 'Messages', 'Work Overview', 'Needs You', 'Organization', 'Deliverables', 'Settings']) {
    await expect(overlay.getByRole('link', { name, exact: true })).toHaveCount(1)
  }
  await keyboardControl(current, selected, `${suffix} current navigation destination`)
  await captureGallery(current, `responsive-navigation-open-${suffix}`, overlay)
  await page.keyboard.press('Escape')
  await expect(overlay).toHaveCount(0)
  await expect(trigger).toHaveAttribute('aria-expanded', 'false')
  await expect(trigger).toBeFocused()
  await expect(draft).toHaveValue(before)
  expect(await retainedDraft!.evaluate(element => element.isConnected)).toBe(true)
  await trigger.click()
  const close = overlay.getByRole('button', { name: 'Close', exact: true })
  await keyboardControl(current, close, `${suffix} navigation Close`)
  await captureGallery(current, `responsive-navigation-close-focus-${suffix}`, overlay)
  await page.keyboard.press('Enter')
  await expect(overlay).toHaveCount(0)
  await expect(trigger).toBeFocused()
  await expect(draft).toHaveValue(before)
  await expect(root.locator('[data-pane="detail"]')).toBeVisible()
  await captureGallery(current, `responsive-navigation-restored-draft-${suffix}`, root)

  // A mouse-opened rail must navigate by the same named destinations, then
  // collapse itself without hiding the route or trapping keyboard focus.
  await trigger.click()
  await overlay.getByRole('link', { name: 'Work Overview', exact: true }).click()
  await expect(page).toHaveURL(/#\/work$/)
  await expect(overlay).toHaveCount(0)
  await expect(trigger).toBeFocused()
  await trigger.click()
  await expect(overlay.getByRole('link', { name: 'Work Overview', exact: true })).toHaveAttribute('aria-current', 'page')
  await overlay.getByRole('link', { name: 'Objectives', exact: true }).click()
  await expect(page).toHaveURL(/#\/objectives$/)
  await expect(overlay).toHaveCount(0)
  await captureGallery(current, `responsive-navigation-destination-${suffix}`, page.locator('.eid-workspace-body'))
  await trigger.click()
  await overlay.getByRole('link', { name: 'Settings', exact: true }).click()
  await expect(page).toHaveURL(/#\/settings$/)
  await expect(overlay).toHaveCount(0)
  await expect(trigger).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(overlay.getByRole('link', { name: 'Settings', exact: true })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(overlay).toHaveCount(0)
  await expect(page).toHaveURL(/#\/settings$/)
  await expect(trigger).toBeFocused()
  await captureGallery(current, `responsive-navigation-settings-escape-${suffix}`, header)
}

async function importHeadingClear(root: Locator) {
  const heading = root.locator('.eid-workspace-page-heading h1')
  const title = await heading.boundingBox()
  expect(title).not.toBeNull()
  for (const control of await root.getByRole('button', { name: /^(Refresh|Close)$/ }).all()) {
    const box = await control.boundingBox()
    if (!box) continue
    const overlaps = title!.x < box.x + box.width && title!.x + title!.width > box.x && title!.y < box.y + box.height && title!.y + title!.height > box.y
    expect(overlaps, 'Import heading stays clear of Refresh and Close').toBe(false)
  }
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
      await compactNavigation(current, suffix, root, field)

      await page.evaluate(() => { location.hash = '/profiles' })
      root = page.locator('.eid-responsive-detail')
      const profileRow = root.locator('[data-panel-row="default"]').getByRole('button').first()
      const editor = root.locator('.cm-content[contenteditable="true"]')
      await expect(editor).toContainText('Synthetic profile')
      // CodeMirror focuses on mount. Compact layout may correctly preserve that
      // selected detail, so return through Back before explicitly selecting a row.
      await expect.poll(() => root.evaluate(element =>
        element.getAttribute('data-compact') === String(element.getBoundingClientRect().width <= 760)
      )).toBe(true)
      const profileBack = root.getByRole('button', { name: 'Back', exact: true })
      if (await profileBack.isVisible()) {
        await profileBack.click()
        await expect(root.locator('[data-pane="detail"]')).toBeHidden()
        await expect(root.locator('[data-pane="list"]')).toBeFocused()
      }
      await expect(profileRow).toBeVisible()
      await profileRow.focus()
      await page.keyboard.press('Enter')
      await expect(editor).toBeVisible()
      await editor.fill('# Unsaved synthetic profile draft')
      await resizeGallery(current, 1536, 961)
      await resizeGallery(current, width, height, zoom)
      await expect(editor).toHaveText('# Unsaved synthetic profile draft')
      await keyboardControl(current, root.getByRole('button', { name: 'Save SOUL.md', exact: true }), `${suffix} profile save`, 'next')
      await captureGallery(current, `responsive-profile-save-focus-${suffix}`, root)
      await compactBack(current, root, profileRow)
      await expect(editor).toHaveText('# Unsaved synthetic profile draft')
      await positionGalleryAtStart(root)
      if (width === 400) await expect(root.getByRole('button', { name: 'Save SOUL.md', exact: true })).toBeInViewport({ ratio: 1 })
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
          await captureGallery(current, `responsive-import-retry-focus-${suffix}`, root)
          const disabledImport = root.locator('footer button')
          await disabledImport.scrollIntoViewIfNeeded()
          await expect(disabledImport).toBeInViewport({ ratio: 1 })
          await expect(disabledImport).toBeDisabled()
        } else {
          await expect(root.locator('[data-pane="detail"]')).toContainText('Preserve this selection while resizing.')
          await keyboardControl(current, root.locator('footer button'), `${suffix} ${state} explicit import/open`)
          await captureGallery(current, `responsive-import-${state}-footer-focus-${suffix}`, root)
          if (state === 'imported') await expect(root.locator('footer button')).toContainText('Open')
        }
        await resizeGallery(current, 1536, 961)
        await resizeGallery(current, width, height, zoom)
        await expect(root.locator('.session-import-preview-heading h2')).toHaveText(`Synthetic ${state} conversation`)
        await compactBack(current, root, row)
        await positionGalleryAtStart(root)
        await importHeadingClear(page.locator('.eid-secondary-surface'))
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
    // 1280px at 200% is exactly the dock breakpoint. Also exercise an actually
    // collapsed rail at 200%, keeping both sides of the boundary covered.
    await resizeGallery(current, 1200, 720, 2)
    await page.evaluate(() => { location.hash = '/messaging' })
    const compactRoot = page.locator('.eid-responsive-detail')
    await compactRoot.locator('[data-pane="list"]').getByRole('button').filter({ hasText: 'Synthetic messaging' }).click()
    const compactDraft = compactRoot.getByLabel('Workspace label', { exact: true })
    await compactDraft.fill('Retained navigation draft at 200%')
    await compactNavigation(current, `en-${mode}-1200x720-200`, compactRoot, compactDraft)
    const ipc = await current.app.evaluate(() => (globalThis as any).__eidolonWorkspaceFixture)
    expect(ipc.reads).toContain('/api/messaging/platforms')
    expect(ipc.reads.some((path: string) => path.endsWith('/soul'))).toBe(true)
    expect(ipc.mutations).toEqual([])
    writeGalleryEvidence(current, 'responsive')
  })
}
