import { readFileSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join } from 'node:path'

import { waitForAppReady } from './fixtures'
import { verifyKeyboardFocus } from './focus-visibility'
import { expect, test } from './test'
import {
  captureGallery,
  type GalleryFixture,
  persistGalleryEvidence,
  resizeGallery,
  setGalleryAppearance,
  setupGallery
} from './workspace-gallery-fixture'

const require = createRequire(import.meta.url)

const { capabilityNames } = require('./workspace-populated-bootstrap.cjs') as {
  capabilityNames: { server: string }
}

const sizes = [
  { width: 1536, height: 961, zoom: 1 },
  { width: 1280, height: 720, zoom: 2 },
  { width: 400, height: 620, zoom: 1 }
]

const draft = JSON.stringify(
  {
    mcpServers: {
      [capabilityNames.server]: {
        command: 'synthetic-not-executable',
        args: ['--fixture-only', 'مرحبا بالعالم', 'synthetic-long-argument-'.repeat(16)],
        enabled: false
      }
    }
  },
  null,
  2
)

let fixture: GalleryFixture | undefined
test.setTimeout(300_000)
test.afterEach(async () => {
  if (!fixture) {
    return
  }

  try {
    persistGalleryEvidence(fixture, 'code-direction')
    const ipc = await fixture.app.evaluate(() => (globalThis as any).__eidolonWorkspaceFixture)
    writeFileSync(
      test.info().outputPath('code-direction-native-evidence.json'),
      JSON.stringify(
        {
          ipc,
          rpc: fixture.capabilityMethods,
          geometryContext: test.info().title.startsWith('Arabic file source')
            ? 'Restored sandbox file in one tabbed zone; actual Arabic Source control, 200% and 400px.'
            : 'MCP capability pane; 100%, 200% and 400px. Appearance navigation remounts the route.'
        },
        null,
        2
      )
    )
    expect(ipc.mutations).toEqual([])
    expect(fixture.capabilityMethods).toEqual([])
    expect(fixture.methods.every(method => fixture!.allowedOrganizationReads.has(method))).toBe(true)
    expect(fixture.mock.receivedPrompts).toHaveLength(0)
  } finally {
    await fixture.cleanup()
    fixture = undefined
  }
})

for (const mode of ['dark', 'light'] as const) {
  test(`Arabic file source keeps native text, copy and gutter geometry in ${mode}`, async () => {
    const current = (fixture = await setupGallery('ar', false, true, {
      blockCapabilityMutations: true,
      blockOrganizationMutations: true
    }))

    const { page, app, sandbox } = current
    const filePath = join(sandbox.root, 'synthetic-direction.md')
    const source = `# مصدر تجريبي\n\n\`\`\`json\n${draft}\n\`\`\``
    writeFileSync(filePath, source)
    await setGalleryAppearance(current, mode, mode === 'dark' ? 'داكن' : 'فاتح')
    // Settings fronts the workspace and hides its tab strip. Return to chat
    // before seeding the layout, and wait for its normal navigation persistence
    // so reloading cannot restore a previously remembered full-page route.
    await page.evaluate(() => {
      location.hash = '/'
    })
    await expect.poll(() => page.evaluate(() => location.hash)).toBe('#/')
    await waitForAppReady(current)
    await expect(page.locator('textarea:visible, [contenteditable="true"]:visible').first()).toBeVisible()
    await expect
      .poll(() => page.evaluate(() => localStorage.getItem('hermes.desktop.lastRoute.profile.default')))
      .toBe('/')
    // Restore a supported preview tab in an isolated, single-zone layout.
    // The real native file reader, mode switcher and SourceView own rendering.
    const paneId = `preview-tile:file:${filePath}`
    await page.evaluate(
      ({ filePath, paneId }) => {
        const id = `file:${filePath}`
        localStorage.setItem(
          'hermes.desktop.previewTabs.v2',
          JSON.stringify([
            {
              id,
              target: {
                kind: 'file',
                label: 'synthetic-direction.md',
                path: filePath,
                source: filePath,
                url: filePath,
                previewKind: 'text',
                language: 'markdown'
              }
            }
          ])
        )
        localStorage.setItem('hermes.desktop.rightRailActiveTab', id)
        localStorage.setItem(
          'hermes.desktop.layoutTree.v2',
          JSON.stringify({
            type: 'group',
            id: 'code-direction-source',
            panes: ['workspace', paneId, 'sessions', 'review', 'files', 'terminal'],
            active: paneId,
            tabStrip: 'always'
          })
        )
      },
      { filePath, paneId }
    )
    await page.reload()
    await waitForAppReady(current, 60_000, '[data-tree-group="code-direction-source"]')
    await expect.poll(() => page.evaluate(() => location.hash)).toBe('#/')
    // Fresh-draft startup fronts chat once; let it settle before selecting the file.
    await expect(page.locator('textarea:visible, [contenteditable="true"]:visible').first()).toBeVisible()
    const fileTab = page.locator(`[data-tree-tab=${JSON.stringify(paneId)}]`)
    await expect(fileTab).toBeVisible()
    await fileTab.click()
    const root = page.locator('[data-tree-group="code-direction-source"]')
    const sourceButton = root.getByRole('button', { name: 'المصدر', exact: true })
    await expect(sourceButton).toBeVisible({ timeout: 30_000 })
    await sourceButton.click()
    const code = root.locator('.preview-source-code')
    await expect(code.locator('pre.shiki')).toHaveCount(1)
    await expect.poll(() => code.textContent()).toBe(source)
    const originalCode = await code.elementHandle()
    const scroller = code.locator('xpath=ancestor::*[@dir="ltr"][1]')

    for (const size of sizes.slice(1)) {
      const label = `source-direction-ar-${mode}-${size.width}-${size.zoom * 100}`
      await resizeGallery(current, size.width, size.height, size.zoom)
      expect(await code.evaluate((element, original) => element === original, originalCode)).toBe(true)
      await expect(sourceButton).toHaveCSS('direction', 'rtl')
      await expect(code).toHaveCSS('direction', 'ltr')
      await expect(scroller).toHaveCSS('direction', 'ltr')
      await expect.poll(() => code.textContent()).toBe(source)
      await scroller.evaluate(element => {
        element.scrollLeft = 0
        element.scrollTop = 0
      })

      const geometry = await code.evaluate(element => ({
        codeLeft: element.getBoundingClientRect().left,
        gutterRight: element.previousElementSibling!.getBoundingClientRect().right
      }))

      expect(geometry.gutterRight).toBeLessThanOrEqual(geometry.codeLeft + 1)
      await code.click({ position: { x: 5, y: 5 } })
      await code.evaluate(element => {
        const selection = element.ownerDocument.getSelection()!
        const range = element.ownerDocument.createRange()
        range.selectNodeContents(element)
        selection.removeAllRanges()
        selection.addRange(range)
      })
      await app.evaluate(({ clipboard }, sentinel) => clipboard.writeText(sentinel), `${label}: awaiting native copy`)
      await page.keyboard.press('Control+c')
      await expect.poll(() => app.evaluate(({ clipboard }) => clipboard.readText())).toBe(source)
      await scroller.evaluate(element => {
        element.scrollLeft = element.scrollWidth
      })
      expect(await scroller.evaluate(element => element.scrollLeft)).toBeGreaterThan(0)
      await scroller.evaluate(element => {
        element.scrollLeft = 0
        element.scrollTop = 0
      })
      await code.evaluate(element => element.ownerDocument.getSelection()?.removeAllRanges())
      current.focusChecks.push(await verifyKeyboardFocus(page, sourceButton, `${label} Source`))
      await captureGallery(current, label, root)
    }

    expect(readFileSync(filePath, 'utf8')).toBe(source)
  })
}

test('Arabic MCP keeps code LTR, native copy and caret stable through zoom and narrow layout', async () => {
  const current = (fixture = await setupGallery('ar', false, true, {
    blockCapabilityMutations: true,
    blockOrganizationMutations: true
  }))

  await current.app.evaluate(() => {
    ;(globalThis as any).__eidolonWorkspaceFixture.capabilities = { mode: 'populated' }
  })
  const { page, app } = current

  for (const mode of ['dark', 'light'] as const) {
    await setGalleryAppearance(current, mode, mode === 'dark' ? 'داكن' : 'فاتح')
    await page.evaluate(() => {
      location.hash = '/skills?tab=mcp'
    })
    const root = page.locator('.eid-mcp-workspace')
    const editor = root.locator('.cm-content[contenteditable="true"]')
    await expect(editor).toContainText(capabilityNames.server)
    await root.getByRole('button', { name: new RegExp(`^${capabilityNames.server}$`, 'i') }).click()
    await editor.fill(draft)
    const originalEditor = await editor.elementHandle()
    const save = root.getByRole('button', { name: 'حفظ', exact: true })

    for (const size of sizes) {
      const label = `code-direction-ar-${mode}-${size.width}-${size.zoom * 100}`
      await editor.focus()
      await page.keyboard.press('Control+End')
      await page.keyboard.press('Shift+Home')
      await expect.poll(() => page.evaluate(() => getSelection()?.toString())).toBe('}')
      await resizeGallery(current, size.width, size.height, size.zoom)
      expect(await editor.evaluate((element, original) => element === original, originalEditor)).toBe(true)
      await expect(editor).toBeFocused()
      await expect.poll(() => page.evaluate(() => getSelection()?.toString())).toBe('}')
      await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
      await expect(save).toHaveCSS('direction', 'rtl')
      await expect(editor).toHaveCSS('direction', 'ltr')
      await expect(root.locator('.cm-scroller')).toHaveCSS('direction', 'ltr')
      await expect(save).toBeEnabled()

      await page.keyboard.press('Control+a')
      await app.evaluate(({ clipboard }, sentinel) => clipboard.writeText(sentinel), `${label}: awaiting native copy`)
      await page.keyboard.press('Control+c')
      await expect.poll(() => app.evaluate(({ clipboard }) => clipboard.readText())).toBe(draft)
      await page.keyboard.press('Control+Home')
      await expect(editor.locator('.cm-line').first()).toHaveText('{')
      const cursor = root.locator('.cm-cursor').first()
      await expect(cursor).toBeAttached()

      const lineStart = await editor
        .locator('.cm-line')
        .first()
        .evaluate(element => {
          const node = element.ownerDocument.createTreeWalker(element, NodeFilter.SHOW_TEXT).nextNode()!
          const range = element.ownerDocument.createRange()
          range.setStart(node, 0)
          range.collapse(true)

          return range.getBoundingClientRect().left
        })

      // Drawn cursors update on CodeMirror's next measurement frame.
      await expect
        .poll(async () =>
          Math.abs((await cursor.evaluate(element => element.getBoundingClientRect().left)) - lineStart)
        )
        .toBeLessThanOrEqual(1)
      const before = await cursor.evaluate(element => element.getBoundingClientRect().left)
      await page.keyboard.press('ArrowRight')
      await expect
        .poll(() => cursor.evaluate(element => element.getBoundingClientRect().left))
        .toBeGreaterThan(before + 1)
      const gutter = await root.locator('.cm-gutters').boundingBox()
      const content = await editor.boundingBox()
      expect(gutter!.x + gutter!.width).toBeLessThanOrEqual(content!.x + 1)

      const scroller = root.locator('.cm-scroller')
      await scroller.evaluate(element => {
        element.scrollLeft = element.scrollWidth
      })
      expect(await scroller.evaluate(element => element.scrollLeft)).toBeGreaterThan(0)
      await scroller.evaluate(element => {
        element.scrollLeft = 0
      })
      current.focusChecks.push(await verifyKeyboardFocus(page, save, `${label} Save`, 'previous'))
      await page.keyboard.press('Tab')
      await expect(editor).toBeFocused()
      await page.keyboard.press('Control+Home')
      await captureGallery(current, label, root)
      await page.keyboard.press('Escape')
      await page.keyboard.press('Shift+Tab')
      await expect(save).toBeFocused()
    }
  }
})
