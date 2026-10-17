import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join } from 'node:path'

import type { Locator } from '@playwright/test'

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

/** Select literal offsets across the mounted source chunks, including their newline boundary. */
async function selectSourceRange(chunks: Locator, from: number, to: number) {
  await chunks.evaluateAll(
    (elements, { from, to }) => {
      const owner = elements[0].ownerDocument

      const boundary = (offset: number): { node: Node; offset: number } => {
        for (const [index, element] of elements.entries()) {
          const length = element.textContent!.length

          // At a real seam, use the downstream chunk rather than inventing a byte.
          if (offset < length || index === elements.length - 1) {
            const walker = owner.createTreeWalker(element, NodeFilter.SHOW_TEXT)
            let node = walker.nextNode()

            while (node) {
              const length = node.textContent!.length

              if (offset <= length) {
                return { node, offset }
              }

              offset -= length
              node = walker.nextNode()
            }

            return { node: element, offset: element.childNodes.length }
          }

          offset -= length
        }

        throw new Error('Source selection is outside the mounted chunks')
      }

      const start = boundary(from)
      const end = boundary(to)
      const range = owner.createRange()
      range.setStart(start.node, start.offset)
      range.setEnd(end.node, end.offset)
      const selection = owner.getSelection()!
      selection.removeAllRanges()
      selection.addRange(range)
    },
    { from, to }
  )
}

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
          geometryContext: test.info().title.includes('baseline window diagnostic')
            ? 'Diagnostic only: baseline source window at 200%, recording raw DOM and native clipboard against unchanged original-source bytes. Not a supported-scope acceptance gate.'
            : test.info().title.startsWith('Arabic Copy Source')
              ? 'Supplemental explicit Copy Source: exact loaded decoded payload, including CRLF/Unicode and unmounted rows; a truncated prefix is labeled and disclosed. Keyboard activation and full focus ring at 200% and 400px. Does not claim full native-selection fidelity at a virtualized window end.'
            : test.info().title.startsWith('Arabic file source')
            ? 'Restored sandbox files in one tabbed zone; exact LF/non-ASCII full and partial copy, real chunk delimiters, terminal cases, 200% and 400px. CRLF is an explicitly existing normalized-display diagnostic, not raw-byte fidelity. Native geometry waits for completed highlighting; the loading/error fallback is covered by component behavior tests. CRLF stays raw in that fallback until Shiki normalizes its completed display.'
            : test.info().title.startsWith('Arabic preview controls')
              ? 'Independent preview header focus and unsaved Edit/Cancel proof at 200% and 400px; never Save.'
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

type SourceSample = { name: string; text: string; displayText?: string }

async function setupSourceGallery(mode: 'dark' | 'light', sourceSamples: SourceSample[]) {
  const current = (fixture = await setupGallery('ar', false, true, {
    blockCapabilityMutations: true,
    blockOrganizationMutations: true
  }))

  const { page, sandbox } = current

  const samples = sourceSamples.map(sample => ({ ...sample, path: join(sandbox.root, `synthetic-${sample.name}.md`) }))

  for (const sample of samples) {writeFileSync(sample.path, sample.text)}
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
  const paneId = `preview-tile:file:${samples[0].path}`
  await page.evaluate(
    ({ samples, paneId }) => {
      const id = `file:${samples[0].path}`
      localStorage.setItem(
        'hermes.desktop.previewTabs.v2',
        JSON.stringify(
          samples.map(sample => ({
            id: `file:${sample.path}`,
            target: {
              kind: 'file',
              label: `synthetic-${sample.name}.md`,
              path: sample.path,
              source: sample.path,
              url: sample.path,
              previewKind: 'text',
              language: 'markdown'
            }
          }))
        )
      )
      localStorage.setItem('hermes.desktop.rightRailActiveTab', id)
      localStorage.setItem(
        'hermes.desktop.layoutTree.v2',
        JSON.stringify({
          type: 'group',
          id: 'code-direction-source',
          panes: [
            'workspace',
            ...samples.map(sample => `preview-tile:file:${sample.path}`),
            'sessions',
            'review',
            'files',
            'terminal'
          ],
          active: paneId,
          tabStrip: 'always'
        })
      )
    },
    { samples: samples.map(({ name, path }) => ({ name, path })), paneId }
  )
  await page.reload()
  await waitForAppReady(current, 60_000, '[data-tree-group="code-direction-source"]')
  await expect.poll(() => page.evaluate(() => location.hash)).toBe('#/')
  // Fresh-draft startup fronts chat once; let it settle before selecting the file.
  await expect(page.locator('textarea:visible, [contenteditable="true"]:visible').first()).toBeVisible()
  const root = page.locator('[data-tree-group="code-direction-source"]')

  return { current, root, samples }
}

const mixedSource = [
  `# مصدر تجريبي\n\n\`\`\`json\n${draft}\n\`\`\``,
  ...Array.from({ length: 194 }, (_, index) => `// synthetic row ${index + 1}`),
  '',
  ''
].join('\n')

const blankSeam = Array.from({ length: 203 }, (_, index) => index === 199 ? '' : `// صف ${index + 1}: café 🧪`).join('\n')

const sourceSamples: SourceSample[] = [
  { name: 'mixed', text: mixedSource },
  { name: 'terminal', text: '\n' },
  { name: 'blank-chunks', text: '\n'.repeat(200) },
  { name: 'blank-seam', text: blankSeam },
  { name: 'no-terminal', text: '// آخر صف: café 🧪' },
  { name: 'one-terminal', text: '// آخر صف: café 🧪\n' },
  // Shiki already normalizes CRLF. This diagnostic is not a raw-byte copy promise.
  { name: 'crlf-display-normalization', text: `${blankSeam.replace(/\n/g, '\r\n')}\r\n`, displayText: `${blankSeam}\n` }
]

for (const mode of ['dark', 'light'] as const) {
  test(`Arabic Copy Source preserves the exact loaded payload in ${mode}`, async () => {
    const full = `${Array.from({ length: 1201 }, (_, index) => `// صف ${index + 1}: café 🧪`).join('\r\n')}\r\n`
    const truncated = `// جزء محمّل: café 🧪\r\n${'// synthetic source row\r\n'.repeat(24_000)}not loaded`
    const { current, root, samples } = await setupSourceGallery(mode, [
      { name: 'copy-full', text: full }, { name: 'copy-truncated', text: truncated }
    ])
    const { page, app } = current
    const copies: { sample: string; width: number; zoom: number; loadedBytes: number; mountedChunks: number; totalChunks: number; truncated: boolean }[] = []

    for (const sample of samples) {
      await page.locator(`[data-tree-tab=${JSON.stringify(`preview-tile:file:${sample.path}`)}]`).click()
      const isTruncated = sample.name === 'copy-truncated'
      if (isTruncated) {
        await root.getByRole('button', { name: 'معاينة على أي حال', exact: true }).click()
        await expect(root.getByText('عرض أول 512 KB.', { exact: true })).toBeVisible()
        await expect(root.getByRole('button', { name: 'تحرير', exact: true })).toHaveCount(0)
      }
      const loaded = Buffer.from(sample.text, 'utf8').subarray(0, 512 * 1024).toString('utf8')
      const copyLabel = isTruncated ? 'نسخ الجزء المحمّل فقط' : 'نسخ المصدر'
      const copy = root.getByRole('button', { name: copyLabel, exact: true })
      await expect(copy).toBeVisible()
      await root.getByRole('button', { name: 'المصدر', exact: true }).click()
      const chunks = root.locator('.preview-source-code:visible')
      const totalChunks = Math.ceil(loaded.split('\n').length / 200)

      for (const size of sizes.slice(1)) {
        const label = `copy-source-${sample.name}-ar-${mode}-${size.width}-${size.zoom * 100}`
        await resizeGallery(current, size.width, size.height, size.zoom)
        await expect(copy).toBeVisible()
        const mountedChunks = await chunks.count()
        expect(mountedChunks).toBeGreaterThan(0)
        expect(mountedChunks).toBeLessThan(totalChunks)
        current.focusChecks.push(await verifyKeyboardFocus(page, copy, label))
        await expect(copy).toHaveCSS('opacity', '1')
        await captureGallery(current, label, root)
        await app.evaluate(({ clipboard }, sentinel) => clipboard.writeText(sentinel), `${label}: awaiting explicit copy`)
        await page.keyboard.press('Enter')
        await expect.poll(() => app.evaluate(({ clipboard }) => clipboard.readText())).toBe(loaded)
        await expect(root.getByRole('button', { name: 'تم النسخ', exact: true })).toBeVisible()
        expect(readFileSync(sample.path, 'utf8')).toBe(sample.text)
        copies.push({ sample: sample.name, ...size, loadedBytes: Buffer.byteLength(loaded, 'utf8'), mountedChunks, totalChunks, truncated: isTruncated })
      }
    }
    await test.info().attach('explicit-source-copy-context', { body: JSON.stringify(copies), contentType: 'application/json' })
  })

  test(`Arabic file source keeps native text, copy and gutter geometry in ${mode}`, async () => {
    const { current, root, samples } = await setupSourceGallery(mode, sourceSamples)
    const { page, app } = current

    for (const sample of samples) {
      const fileTab = page.locator(`[data-tree-tab=${JSON.stringify(`preview-tile:file:${sample.path}`)}]`)
      await expect(fileTab).toBeVisible()
      await fileTab.click()
      const sourceButton = root.getByRole('button', { name: 'المصدر', exact: true })
      await expect(sourceButton).toBeVisible({ timeout: 30_000 })
      await sourceButton.click()
      const chunks = root.locator('.preview-source-code:visible')
      const observedCopies: { label: string; text: string }[] = []
      const displayText = sample.displayText ?? sample.text
      const chunkCount = Math.ceil(displayText.split('\n').length / 200)
      await expect(chunks).toHaveCount(chunkCount)
      await expect(chunks.locator('[data-source-fallback]')).toHaveCount(0)
      await expect(chunks.getByTestId('shiki-container')).toHaveCount(chunkCount)
      const code = chunks.first()
      await expect.poll(async () => (await chunks.allTextContents()).join('')).toBe(displayText)
      const originalCode = await code.elementHandle()
      const scroller = code.locator('xpath=ancestor::*[@dir="ltr"][1]')

      for (const size of sizes.slice(1)) {
        const label = `source-direction-${sample.name}-ar-${mode}-${size.width}-${size.zoom * 100}`
        await resizeGallery(current, size.width, size.height, size.zoom)
        expect(await code.evaluate((element, original) => element === original, originalCode)).toBe(true)
        await expect(sourceButton).toHaveCSS('direction', 'rtl')
        await expect(code).toHaveCSS('direction', 'ltr')
        await expect(scroller).toHaveCSS('direction', 'ltr')
        await expect.poll(async () => (await chunks.allTextContents()).join('')).toBe(displayText)
        await scroller.evaluate(element => {
          element.scrollLeft = 0
          element.scrollTop = 0
        })

        const geometry = await code.evaluate(element => ({
          codeLeft: element.getBoundingClientRect().left,
          gutterRight: element.previousElementSibling!.getBoundingClientRect().right
        }))

        expect(geometry.gutterRight).toBeLessThanOrEqual(geometry.codeLeft + 1)

        for (let index = 0; index < chunkCount; index++) {
          const rows = Math.min(200, displayText.split('\n').length - index * 200)
          const continuation = index < chunkCount - 1
          const chunk = chunks.nth(index)

          const layout = await chunk.evaluate(element => {
            const code = element.querySelector('pre.shiki code')!.getBoundingClientRect()
            const gutter = element.previousElementSibling!.getBoundingClientRect()

            return { y: code.y, height: code.height, gutterY: gutter.y, gutterHeight: gutter.height }
          })

          expect(layout.height).toBe(rows * 20)
          expect(layout.gutterHeight).toBe(rows * 20)
          expect(layout.y).toBe(layout.gutterY)
          await expect(chunk.locator('.line')).toHaveCount(rows + Number(continuation))

          if (continuation) {
            await expect(chunk.locator('.line').last()).toHaveCSS('display', 'none')
            const nextY = await chunks.nth(index + 1).locator('pre.shiki code').evaluate(element => element.getBoundingClientRect().y)
            expect(nextY - layout.y).toBe(rows * 20)
          }
        }

        if (displayText.endsWith('\n')) {
          const finalLine = await chunks.last().locator('.line').last().evaluate(element => {
            const { y, width, height } = element.getBoundingClientRect()

            return { y, width, height }
          })

          expect(finalLine.width).toBe(0)
          expect(finalLine.height).toBe(20)
          const lastGutter = await chunks.last().evaluate(element => element.previousElementSibling!.getBoundingClientRect().bottom)
          expect(lastGutter - finalLine.y).toBe(20)
        }

        await code.click({ position: { x: 5, y: 5 } })
        const chunkBoundary = displayText.split('\n').slice(0, 200).join('\n').length

        const ranges = [
          [0, displayText.length],
          [Math.max(0, displayText.length - 2), displayText.length]
        ]

        if (sample.name === 'mixed') {ranges.push([2, displayText.indexOf('{') + 1])}

        if (chunkCount > 1) {
          ranges.push([chunkBoundary, chunkBoundary + 1], [chunkBoundary - 2, Math.min(displayText.length, chunkBoundary + 2)])
        }

        for (const [proposedFrom, proposedTo] of ranges) {
          // These fixtures use precomposed text and single-codepoint emoji.
          // Keep partial selections outside surrogate pairs, as a user would.
          const from = proposedFrom > 0 && displayText.codePointAt(proposedFrom - 1)! > 0xffff ? proposedFrom - 1 : proposedFrom
          const to = proposedTo < displayText.length && displayText.codePointAt(proposedTo - 1)! > 0xffff ? proposedTo + 1 : proposedTo
          await selectSourceRange(chunks, from, to)
          await app.evaluate(({ clipboard }, sentinel) => clipboard.writeText(sentinel), `${label}: copy ${from}-${to}`)
          await page.keyboard.press('Control+c')
          await expect
            .poll(() => app.evaluate(({ clipboard }) => clipboard.readText()))
            .toBe(displayText.slice(from, to))

          if (sample.displayText !== undefined && from === 0 && to === displayText.length) {
            observedCopies.push({ label, text: await app.evaluate(({ clipboard }) => clipboard.readText()) })
          }
        }

        if (sample.name === 'mixed') {
          await scroller.evaluate(element => {
            element.scrollLeft = element.scrollWidth
          })
          expect(await scroller.evaluate(element => element.scrollLeft)).toBeGreaterThan(0)
        }

        await scroller.evaluate(element => {
          element.scrollLeft = 0
          element.scrollTop = 0
        })
        await code.evaluate(element => element.ownerDocument.getSelection()?.removeAllRanges())
        current.focusChecks.push(await verifyKeyboardFocus(page, sourceButton, `${label} Source`))
        await captureGallery(current, label, root)
      }

      if (sample.displayText !== undefined) {
        await test.info().attach('existing-crlf-display-normalization', {
          body: JSON.stringify({ rawSource: sample.text, observedDisplay: (await chunks.allTextContents()).join(''), observedCopies, rawDiskUnchanged: readFileSync(sample.path, 'utf8') === sample.text }),
          contentType: 'application/json'
        })
      }

      expect(readFileSync(sample.path, 'utf8')).toBe(sample.text)
    }
  })


  test(`Arabic file source preserves mounted seams while virtualizing in ${mode} [diagnostic opt-in]`, async () => {
    test.skip(process.env.EIDOLON_SOURCE_WINDOW_DIAGNOSTIC !== '1', 'Exploratory known native-selection window-end LF limitation; baseline red receipt retained.')
    const text = `${Array.from({ length: 1201 }, (_, index) => index % 200 === 199 ? '' : `// صف ${index + 1}: café 🧪`).join('\n')}\n`
    const { current, root, samples } = await setupSourceGallery(mode, [{ name: 'windowed', text }])
    const { page, app } = current
    await page.locator(`[data-tree-tab=${JSON.stringify(`preview-tile:file:${samples[0].path}`)}]`).click()
    await root.getByRole('button', { name: 'المصدر', exact: true }).click()
    const chunks = root.locator('.preview-source-code:visible')
    const scroller = chunks.first().locator('xpath=ancestor::*[@dir="ltr"][1]')
    const lines = text.split('\n')
    const windows: unknown[] = []

    for (const size of sizes.slice(1)) {
      await resizeGallery(current, size.width, size.height, size.zoom)

      for (const ratio of [0, 0.5, 1]) {
        await scroller.evaluate((element, ratio) => {
          element.scrollTop = (element.scrollHeight - element.clientHeight) * ratio
        }, ratio)
        await expect.poll(async () => {
          const start = await chunks.first().evaluate(element => Number(element.previousElementSibling!.firstElementChild!.textContent))

          return ratio === 0 ? start === 1 : start > 1
        }).toBe(true)

        if (ratio === 1) {
          await expect.poll(async () => chunks.last().evaluate(element => Number(element.previousElementSibling!.lastElementChild!.textContent))).toBe(lines.length)
        }

        const count = await chunks.count()
        expect(count).toBeLessThanOrEqual(6)
        expect(count).toBeLessThan(Math.ceil(lines.length / 200))
        await expect(chunks.locator('[data-source-fallback]')).toHaveCount(0)
        await expect(chunks.getByTestId('shiki-container')).toHaveCount(count)

        const bounds = await chunks.evaluateAll(elements => ({
          start: Number(elements[0].previousElementSibling!.firstElementChild!.textContent) - 1,
          end: Number(elements.at(-1)!.previousElementSibling!.lastElementChild!.textContent)
        }))

        const expected = lines.slice(bounds.start, bounds.end).join('\n') + (bounds.end < lines.length ? '\n' : '')
        await expect.poll(async () => (await chunks.allTextContents()).join('')).toBe(expected)
        expect(await scroller.evaluate(element => element.scrollHeight)).toBe(lines.length * 20)

        const layout = await chunks.evaluateAll(elements => elements.map(element => {
          const code = element.querySelector('pre.shiki code')!.getBoundingClientRect()
          const gutter = element.previousElementSibling!.getBoundingClientRect()

          return { codeHeight: code.height, gutterHeight: gutter.height, codeY: code.y, gutterY: gutter.y }
        }))

        for (const chunk of layout) {
          expect(chunk.codeHeight).toBe(chunk.gutterHeight)
          expect(chunk.codeY).toBe(chunk.gutterY)
        }

        await scroller.click({ position: { x: 100, y: 10 } })
        await selectSourceRange(chunks, 0, expected.length)
        await app.evaluate(({ clipboard }) => clipboard.writeText('awaiting mounted source copy'))
        await page.keyboard.press('Control+c')
        await expect.poll(() => app.evaluate(({ clipboard }) => clipboard.readText())).toBe(expected)
        windows.push({ mode, ...size, ratio, ...bounds, mountedChunks: count, layout })
      }
    }

    await test.info().attach('mounted-source-window-geometry', { body: JSON.stringify(windows), contentType: 'application/json' })
    expect(readFileSync(samples[0].path, 'utf8')).toBe(text)
  })

  test(`Arabic file source baseline window diagnostic in ${mode} [diagnostic opt-in]`, async () => {
    test.skip(process.env.EIDOLON_SOURCE_WINDOW_DIAGNOSTIC !== '1', 'Exploratory baseline-only native-selection comparison; never a supported-scope acceptance gate.')
    const text = `${Array.from({ length: 1201 }, (_, index) => index % 200 === 199 ? '' : `// صف ${index + 1}: café 🧪`).join('\n')}\n`
    const { current, root, samples } = await setupSourceGallery(mode, [{ name: 'windowed', text }])
    const { page, app } = current
    await page.locator(`[data-tree-tab=${JSON.stringify(`preview-tile:file:${samples[0].path}`)}]`).click()
    await root.getByRole('button', { name: 'المصدر', exact: true }).click()
    const chunks = root.locator('.preview-source-code:visible')
    // Base 822 predates the source scroller's explicit LTR direction.
    const scroller = chunks.first().locator('xpath=../..')
    await resizeGallery(current, 1280, 720, 2)
    await scroller.evaluate(element => { element.scrollTop = 0 })
    const mountedBounds = () => chunks.evaluateAll(elements => ({
      start: Number(elements[0].previousElementSibling!.firstElementChild!.textContent) - 1,
      end: Number(elements.at(-1)!.previousElementSibling!.lastElementChild!.textContent)
    }))
    await expect.poll(mountedBounds).toEqual({ start: 0, end: 600 })
    await expect(chunks.getByTestId('shiki-container')).toHaveCount(await chunks.count())
    await expect.poll(() => chunks.evaluateAll(elements => elements.every(element =>
      element.querySelector('pre.shiki code .line') !== null
    ))).toBe(true)
    const bounds = await mountedBounds()
    const lines = text.split('\n')
    const expected = lines.slice(bounds.start, bounds.end).join('\n') + (bounds.end < lines.length ? '\n' : '')
    const rawChunks = await chunks.allTextContents()
    const rawDom = rawChunks.join('')
    const layout = await chunks.evaluateAll(elements => elements.map(element => {
      const code = element.querySelector('pre.shiki code')!.getBoundingClientRect()
      const gutter = element.previousElementSibling!.getBoundingClientRect()

      return { codeHeight: code.height, gutterHeight: gutter.height, codeY: code.y, gutterY: gutter.y }
    }))
    await scroller.click({ position: { x: 100, y: 10 } })
    await selectSourceRange(chunks, 0, rawDom.length)
    const sentinel = 'awaiting diagnostic mounted source copy'
    await app.evaluate(({ clipboard }, value) => clipboard.writeText(value), sentinel)
    await page.keyboard.press('Control+c')
    await expect.poll(() => app.evaluate(({ clipboard }) => clipboard.readText())).not.toBe(sentinel)
    const actual = await app.evaluate(({ clipboard }) => clipboard.readText())
    const trailingLf = (value: string) => value.match(/\n*$/)![0].length
    writeFileSync(test.info().outputPath('baseline-window-diagnostic.json'), JSON.stringify({
      purpose: 'Diagnostic only; bind this run to base 822 plus the spec-only delta before calling it baseline evidence.',
      sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
      sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
      mode, width: 1280, zoom: 2, bounds, layout, rawChunks, rawDom, expected, actual,
      rawDomMatchesSource: rawDom === expected,
      trailingLf: { expected: trailingLf(expected), actual: trailingLf(actual) },
      rawDiskUnchanged: readFileSync(samples[0].path, 'utf8') === text
    }, null, 2))
    // Preserve the exact native clipboard contract despite the known baseline DOM mismatch.
    await expect.poll(() => app.evaluate(({ clipboard }) => clipboard.readText())).toBe(expected)
  })

  test(`Arabic preview controls keep focus and unsaved edits in ${mode}`, async () => {
    const { current, root, samples } = await setupSourceGallery(mode, [{ name: 'focus', text: mixedSource }])
    const { page, app } = current
    // Independent scenario: a failing copy case must not mask header focus proof.
    const sample = samples[0]
    await page.locator(`[data-tree-tab=${JSON.stringify(`preview-tile:file:${sample.path}`)}]`).click()
    const sourceButton = root.getByRole('button', { name: 'المصدر', exact: true })
    const renderedButton = root.getByRole('button', { name: 'المعاينة', exact: true })
    const editButton = root.getByRole('button', { name: 'تحرير', exact: true })
    const header = root.locator('.preview-mode-switcher:visible')
    await expect(sourceButton).toBeVisible()
    await sourceButton.click()
    const readHeaderBottoms = new Map<number, number>()
    const headerGeometry: { width: number; zoom: number; readBottom: number; editBottom: number }[] = []

    for (const size of sizes.slice(1)) {
      await resizeGallery(current, size.width, size.height, size.zoom)
      const bounds = await header.boundingBox()
      readHeaderBottoms.set(size.width, bounds!.y + bounds!.height)

      for (const [name, button] of [['Source', sourceButton], ['Rendered', renderedButton], ['Edit', editButton]] as const) {
        current.focusChecks.push(await verifyKeyboardFocus(page, button, `preview-${name}-ar-${mode}-${size.width}-${size.zoom * 100}`))
      }
    }

    await editButton.click()
    const editor = root.locator('.cm-content[contenteditable="true"]:visible')
    await expect(editor).toBeVisible()
    const originalEditor = await editor.elementHandle()
    const unsavedDraft = `${sample.text}\n// disposable unsaved focus check: مرحبا`
    await editor.fill(unsavedDraft)
    const save = root.getByRole('button', { name: 'حفظ', exact: true })
    const cancel = root.getByRole('button', { name: 'إلغاء', exact: true })
    await expect(save).toBeEnabled()

    for (const size of sizes.slice(1)) {
      const label = `source-edit-focus-ar-${mode}-${size.width}-${size.zoom * 100}`
      await resizeGallery(current, size.width, size.height, size.zoom)
      const editHeader = await header.boundingBox()
      const readBottom = readHeaderBottoms.get(size.width)!
      const editBottom = editHeader!.y + editHeader!.height
      expect(editBottom).toBe(readBottom)
      headerGeometry.push({ width: size.width, zoom: size.zoom, readBottom, editBottom })
      expect(await editor.evaluate((element, original) => element === original, originalEditor)).toBe(true)
      await editor.focus()
      await page.keyboard.press('Control+a')
      await app.evaluate(({ clipboard }, sentinel) => clipboard.writeText(sentinel), `${label}: awaiting draft copy`)
      await page.keyboard.press('Control+c')
      await expect.poll(() => app.evaluate(({ clipboard }) => clipboard.readText())).toBe(unsavedDraft)
      await page.keyboard.press('Control+Home')
      current.focusChecks.push(await verifyKeyboardFocus(page, cancel, `${label} Cancel`))
      current.focusChecks.push(await verifyKeyboardFocus(page, save, `${label} Save`, 'previous'))
      await captureGallery(current, label, root)
      expect(readFileSync(sample.path, 'utf8')).toBe(sample.text)
    }

    await test.info().attach(`preview-header-geometry-${mode}`, {
      body: JSON.stringify(headerGeometry),
      contentType: 'application/json'
    })
    await cancel.click()
    await expect(editor).toHaveCount(0)
    await expect.poll(async () => (await root.locator('.preview-source-code:visible').allTextContents()).join('')).toBe(sample.text)
    expect(readFileSync(sample.path, 'utf8')).toBe(sample.text)
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
