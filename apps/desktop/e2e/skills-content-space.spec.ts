import { writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'

import type { Locator } from '@playwright/test'

import { verifyKeyboardFocus } from './focus-visibility'
import { allowErrorBanners, expect, test } from './test'
import {
  captureGallery,
  type GalleryFixture,
  persistGalleryEvidence,
  resizeGallery,
  setupGallery
} from './workspace-gallery-fixture'

const require = createRequire(import.meta.url)

const { capabilityNames: names } = require('./workspace-populated-bootstrap.cjs') as {
  capabilityNames: { skill: string; tool: string; server: string; official: string }
}

const sizes = [
  { width: 1536, height: 961, zoom: 1 },
  { width: 1280, height: 720, zoom: 1.25 },
  { width: 1280, height: 720, zoom: 2 },
  { width: 400, height: 620, zoom: 1 }
]

let fixture: GalleryFixture | undefined

test.setTimeout(600_000)
test.afterEach(async () => {
  if (!fixture) {
    return
  }

  try {
    persistGalleryEvidence(fixture, 'capabilities-content')
    const ipc = await fixture.app.evaluate(() => (globalThis as any).__eidolonWorkspaceFixture)
    writeFileSync(
      test.info().outputPath('capabilities-native-evidence.json'),
      JSON.stringify({ ipc, rpc: fixture.capabilityMethods }, null, 2)
    )
    expect(ipc.mutations).toEqual([])
    expect(fixture.capabilityMethods).toEqual([])
    expect(fixture.mock.receivedPrompts).toHaveLength(0)
  } finally {
    await fixture.cleanup()
    fixture = undefined
  }
})

async function setMode(current: GalleryFixture, mode: 'populated' | 'empty' | 'error') {
  await current.app.evaluate((_electron, value) => {
    ;(globalThis as any).__eidolonWorkspaceFixture.capabilities = { mode: value }
  }, mode)
}

async function start(mode: 'populated' | 'empty' | 'error') {
  const current = (fixture = await setupGallery('en', false, true, { blockCapabilityMutations: true }))
  await setMode(current, mode)
  // The iframe uses its real integration origin with fixture-only HTML. The
  // test never contacts the public Hub or offers an install interaction.
  await current.page.route('https://hermes-agent.nousresearch.com/**', route =>
    route.fulfill({
      contentType: 'text/html',
      body: '<!doctype html><html><body><p>Synthetic Skills Hub</p><button>Read synthetic hub</button></body></html>'
    })
  )

  return current
}

async function openTab(current: GalleryFixture, tab: 'skills' | 'toolsets' | 'mcp') {
  await current.page.evaluate(value => {
    location.hash = `/skills?tab=${value}`
  }, tab)
  await expect(current.page.getByRole('heading', { level: 1, name: 'Capabilities', exact: true })).toBeVisible()
}

async function showList(root: Locator) {
  await root.waitFor()
  // Native resize and React's ResizeObserver settle independently.
  await assertPaneGeometry(root)
  const back = root.getByRole('button', { name: 'Back', exact: true })

  if (await back.isVisible()) {
    await back.click()
  }
}

async function assertPaneGeometry(root: Locator) {
  const width = await root.evaluate(element => element.getBoundingClientRect().width)
  await expect(root).toHaveAttribute('data-compact', String(width <= 760))
  expect(await root.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)

  if (width <= 760) {
    await expect(root.locator(':scope > [data-pane]:visible')).toHaveCount(1)
    await expect(root.locator(':scope > [data-pane][hidden]')).toHaveAttribute('inert', '')
  }
}

async function assertUnclippedText(text: Locator) {
  const bounds = await text.evaluate(element => {
    const style = getComputedStyle(element)

    return {
      scroll: element.scrollWidth,
      client: element.clientWidth,
      whiteSpace: style.whiteSpace,
      overflow: style.overflow,
      textOverflow: style.textOverflow
    }
  })

  expect(bounds.whiteSpace).not.toBe('nowrap')
  expect(bounds.textOverflow).not.toBe('ellipsis')
  expect(bounds.scroll - bounds.client).toBeLessThanOrEqual(1)
}

async function readVerticalReachability(target: Locator) {
  return target.evaluate(element => {
    const box = element.getBoundingClientRect()
    const clips = [{ name: 'viewport', top: 0, bottom: window.innerHeight, scrollTop: window.scrollY }]
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      if (!/(auto|scroll|hidden|clip)/.test(getComputedStyle(parent).overflowY)) continue
      const bounds = parent.getBoundingClientRect()
      clips.push({ name: `${parent.tagName}.${parent.className}`,
        top: bounds.top + parent.clientTop,
        bottom: bounds.top + parent.clientTop + parent.clientHeight,
        scrollTop: parent.scrollTop })
    }
    return { top: box.top, bottom: box.bottom, height: box.height, clips }
  })
}

function assertVerticallyReachable(bounds: Awaited<ReturnType<typeof readVerticalReachability>>) {
  expect(bounds.height).toBeGreaterThan(0)
  for (const clip of bounds.clips) {
    expect(bounds.top, `top inside ${clip.name}`).toBeGreaterThanOrEqual(clip.top - 1)
    expect(bounds.bottom, `bottom inside ${clip.name}`).toBeLessThanOrEqual(clip.bottom + 1)
  }
}

async function backRetains(current: GalleryFixture, root: Locator, row: Locator, scrollBefore: number, beforeAction = scrollBefore) {
  if ((await root.getAttribute('data-compact')) !== 'true') {
    return
  }

  const list = root.locator('[data-pane="list"]')
  const back = root.getByRole('button', { name: 'Back', exact: true })
  current.focusChecks.push(await verifyKeyboardFocus(current.page, back, 'Capabilities Back'))
  await back.click()
  await expect(row).toBeFocused()
  await expect(root.locator('[data-pane="detail"]')).toHaveAttribute('inert', '')
  const returned = await list.locator('.eid-detail-scroll').evaluate(element => element.scrollTop)
  await test.info().attach('capability-scroll-retention', {
    body: JSON.stringify({ beforeAction, departure: scrollBefore, returned }), contentType: 'application/json'
  })
  expect(returned).toBeCloseTo(scrollBefore, 0)
}

test('Skills and Tools retain long identities, Hub, saved wide split and compact focus', async () => {
  const current = await start('populated')
  const { page } = current
  await openTab(current, 'skills')
  const hub = page.locator('.eid-hub-picker')
  const iframe = hub.locator('iframe')
  await iframe.waitFor({ state: 'attached' })
  const originalFrame = await iframe.elementHandle()
  let savedWidth: number | undefined

  for (const size of sizes) {
    const label = `${size.width}x${size.height}-${size.zoom * 100}`
    await resizeGallery(current, size.width, size.height, size.zoom)

    for (const [tab, title] of [
      ['skills', names.skill],
      ['toolsets', names.tool]
    ] as const) {
      await openTab(current, tab)
      const root = page.locator('.eid-capabilities-detail')
      await root.waitFor()
      await showList(root)
      await assertPaneGeometry(root)
      const row = root.getByRole('button', { name: new RegExp(title, 'i') })
      await row.scrollIntoViewIfNeeded()
      const text = row.locator('span.truncate').first()
      await assertUnclippedText(text)
      const listScroll = root.locator('[data-pane="list"] .eid-detail-scroll')
      const beforeAction = await listScroll.evaluate(element => element.scrollTop)
      // Playwright's trusted click may scroll/focus the row after its initial
      // scrollIntoView. Retention starts when the person actually leaves the
      // list, so capture that position before the row's navigation handler.
      const departure = await row.evaluateHandle(element => {
        const scroll = element.closest('[data-pane="list"]')!.querySelector('.eid-detail-scroll')!
        const record = { scrollTop: -1, trusted: false }
        element.addEventListener('click', event => {
          record.scrollTop = scroll.scrollTop
          record.trusted = event.isTrusted
        }, { capture: true, once: true })
        return record
      })
      await row.click()
      const departed = await departure.jsonValue()
      await departure.dispose()
      expect(departed.trusted).toBe(true)
      expect(departed.scrollTop).toBeGreaterThanOrEqual(0)
      const before = departed.scrollTop
      await test.info().attach(`capability-${tab}-departure-${label}`, {
        body: JSON.stringify({ beforeAction, ...departed }), contentType: 'application/json'
      })
      const heading = root.getByRole('heading', { name: title, exact: true })
      await expect(heading).toBeVisible()
      await assertUnclippedText(heading)
      const beforeHeadingScroll = await readVerticalReachability(heading)
      let afterHeadingScroll = beforeHeadingScroll
      let backBounds: Awaited<ReturnType<typeof readVerticalReachability>> | null = null
      try {
        // Reveal content through its real scroll owners before judging clipping.
        // A title taller than its clipping viewport must still fail this proof.
        await heading.scrollIntoViewIfNeeded()
      } finally {
        afterHeadingScroll = await readVerticalReachability(heading)
        const back = root.getByRole('button', { name: 'Back', exact: true })
        backBounds = await back.isVisible() ? await readVerticalReachability(back) : null
        await test.info().attach(`capability-${tab}-heading-reachability-${label}`, {
          body: JSON.stringify({ before: beforeHeadingScroll, after: afterHeadingScroll, back: backBounds }),
          contentType: 'application/json'
        })
        await captureGallery(current, `capability-${tab}-detail-${label}`, root)
      }
      assertVerticallyReachable(afterHeadingScroll)
      if (backBounds) assertVerticallyReachable(backBounds)
      if (tab === 'toolsets') {
        const caption = root.locator('[data-pane="detail"] main > div').last()
        await expect(caption).toContainText('Changes apply to new sessions')
        await caption.scrollIntoViewIfNeeded()
        const captionBounds = await readVerticalReachability(caption)
        await test.info().attach(`capability-tools-caption-${label}`, {
          body: JSON.stringify(captionBounds), contentType: 'application/json'
        })
        assertVerticallyReachable(captionBounds)
        await captureGallery(current, `capability-toolsets-caption-${label}`, root)
      }
      await backRetains(current, root, row, before, beforeAction)

      if ((await root.getAttribute('data-compact')) === 'false' && tab === 'skills' && savedWidth === undefined) {
        const sash = root.locator('[data-master-detail-sash]')
        const box = (await sash.boundingBox())!
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
        await page.mouse.down()
        await page.mouse.move(box.x + 40, box.y + box.height / 2)
        await page.mouse.up()
        savedWidth = await page.evaluate(
          () => JSON.parse(localStorage.getItem('hermes.desktop.paneStates.v1')!)['capabilities-split'].widthOverride
        )
        expect(savedWidth).toBeGreaterThan(0)
      }

      if (tab === 'skills') {
        await expect(hub).toBeVisible()

        const bounds = await hub.evaluate(element => {
          const frame = element.getBoundingClientRect(),
            parent = element.parentElement!.getBoundingClientRect()

          return { top: frame.top, bottom: frame.bottom, parentTop: parent.top, parentBottom: parent.bottom }
        })

        expect(bounds.top).toBeGreaterThanOrEqual(bounds.parentTop)
        expect(bounds.bottom).toBeLessThanOrEqual(bounds.parentBottom + 1)
        // Exercise real pointer hit testing inside the retained cross-origin
        // frame at every native size/zoom, not only the surrounding controls.
        const hubRead = hub.frameLocator('iframe').getByRole('button', { name: 'Read synthetic hub' })
        const readHubGeometry = async () => {
          const controlBounds = await hubRead.evaluate(element => {
            const box = element.getBoundingClientRect()
            return { x: box.x, y: box.y, width: box.width, height: box.height }
          })
          return iframe.evaluate((element, control) => {
            const rect = (node: Element) => {
              const box = node.getBoundingClientRect()
              return { x: box.x, y: box.y, width: box.width, height: box.height }
            }
            const workspace = element.closest('.eid-capabilities-workspace')!
            const frame = rect(element)
            const hit = document.elementFromPoint(frame.x + control.x + control.width / 2, frame.y + control.y + control.height / 2)
            return { workspace: rect(workspace), scrollTop: workspace.scrollTop,
              compact: workspace.querySelector('.eid-capabilities-detail')?.getAttribute('data-compact'),
              hidden: element.closest('.eid-hub-picker')?.classList.contains('hidden'), frame, control, parentHit: hit === element ? 'iframe' : hit?.tagName + '.' + hit?.className,
              viewport: rect(element.parentElement!), body: rect(element.closest('.eid-hub-body')!), hub: rect(element.closest('.eid-hub-picker')!) }
          }, controlBounds)
        }
        // Record before any scrolling so a failed action still has geometry.
        const beforeHubAction = await readHubGeometry()
        await test.info().attach(`capability-hub-before-${label}`, {
          body: JSON.stringify(beforeHubAction), contentType: 'application/json'
        })
        try {
          // Reveal the host through the workspace scroller before asking the
          // offscreen child frame to run its own animation-frame stability check.
          await iframe.scrollIntoViewIfNeeded()
          await expect(iframe).toBeInViewport()
          const hubGeometry = await readHubGeometry()
          expect(hubGeometry.viewport.height).toBeGreaterThanOrEqual(80)
          expect(hubGeometry.frame.height).toBeGreaterThanOrEqual(80)
          await hubRead.click()
          await expect(hubRead).toBeFocused()
          await expect(iframe).toBeFocused()
          await captureGallery(current, `capability-hub-clicked-${label}`, page.locator('.eid-capabilities-workspace'))
        } finally {
          await test.info().attach(`capability-hub-geometry-${label}`, {
            body: JSON.stringify({ before: beforeHubAction, after: await readHubGeometry() }), contentType: 'application/json'
          })
        }
        expect(await iframe.evaluate(element => getComputedStyle(element).transform)).toBe('none')
        const toggle = hub.getByRole('button', { expanded: true })
        current.focusChecks.push(await verifyKeyboardFocus(page, toggle, `${label} Hub toggle`))
        const expandedWidth = await root.evaluate(element => element.getBoundingClientRect().width)
        await toggle.click()
        await expect(iframe).toBeHidden()
        expect(await root.evaluate(element => element.getBoundingClientRect().width)).toBeCloseTo(expandedWidth, 0)
        await hub.getByRole('button', { expanded: false }).click()
        expect(await root.evaluate(element => element.getBoundingClientRect().width)).toBeCloseTo(expandedWidth, 0)
        expect(await iframe.evaluate((element, original) => element === original, originalFrame)).toBe(true)
        await captureGallery(
          current,
          `capability-skills-list-hub-${label}`,
          page.locator('.eid-capabilities-workspace')
        )
      } else {
        await expect(hub).toBeHidden()
        expect(await originalFrame!.evaluate(element => element.isConnected)).toBe(true)
      }
    }
  }

  await resizeGallery(current, 1536, 961)
  await openTab(current, 'skills')
  const root = page.locator('.eid-capabilities-detail')
  await expect(root.locator('[data-master-detail-sash]')).toBeVisible()
  expect(
    await page.evaluate(
      () => JSON.parse(localStorage.getItem('hermes.desktop.paneStates.v1')!)['capabilities-split'].widthOverride
    )
  ).toBe(savedWidth)
  expect(
    await root.locator('[data-pane="list"]').evaluate(element => element.getBoundingClientRect().width)
  ).toBeCloseTo(savedWidth!, 0)
  const selected = root.getByRole('button', { name: new RegExp(names.skill, 'i') })
  await selected.click()
  const hubToggle = hub.getByRole('button', { expanded: true })
  await hubToggle.focus()
  await resizeGallery(current, 400, 620)
  await expect(hubToggle).toBeFocused()
  await expect(hub).toBeVisible()
  await expect(root.locator('[data-pane="list"]')).toBeVisible()
  await resizeGallery(current, 1536, 961)
  await selected.click()
  await hub.frameLocator('iframe').getByRole('button', { name: 'Read synthetic hub' }).click()
  await resizeGallery(current, 400, 620)
  await expect(hub).toBeVisible()
  await expect(iframe).toBeFocused()
  expect(await iframe.evaluate((element, original) => element === original, originalFrame)).toBe(true)
})

test('MCP keeps one dirty document and selected identity through compact Back and resize without writes', async () => {
  const current = await start('populated')
  await openTab(current, 'mcp')
  const root = current.page.locator('.eid-mcp-workspace')

  // One logical line permits complete text equality without interpreting
  // CodeMirror's virtualized multi-line DOM.
  const draft = JSON.stringify({
    mcpServers: { [names.server]: { command: 'synthetic-not-executable', args: ['--unsaved-draft'], enabled: false } }
  })

  const editor = root.locator('.cm-content[contenteditable="true"]')
  // Config seeds (and remounts) the editor once; retain the seeded node.
  await expect(editor).toContainText(names.server)
  const originalEditor = await editor.elementHandle()
  // Exercise a genuinely scrolled fleet before replacing its unsaved text.
  await resizeGallery(current, 400, 620)
  await showList(root)
  const tail = root.getByRole('button', { name: /^synthetic-server-23$/i })
  await tail.scrollIntoViewIfNeeded()
  const fleetScroll = await root.locator('[data-pane="list"] .eid-detail-scroll').evaluate(element => element.scrollTop)
  expect(fleetScroll).toBeGreaterThan(0)
  await tail.click()
  await backRetains(current, root, tail, fleetScroll)

  for (const size of sizes) {
    const label = `${size.width}x${size.height}-${size.zoom * 100}`
    await resizeGallery(current, size.width, size.height, size.zoom)
    await showList(root)
    await assertPaneGeometry(root)
    const row = root.getByRole('button', { name: new RegExp(`^${names.server}$`, 'i') })
    await row.scrollIntoViewIfNeeded()
    const before = await root.locator('[data-pane="list"] .eid-detail-scroll').evaluate(element => element.scrollTop)
    await row.click()
    await editor.fill(draft)
    await showList(root)
    const originalRow = await row.elementHandle()
    await row.click()
    // Compact navigation keeps the selected row mounted but makes its list
    // inert/hidden; accessible-role queries correctly exclude it after click.
    const retainedRow = root.getByRole('button', { name: new RegExp(`^${names.server}$`, 'i'), includeHidden: true })
    await expect(retainedRow).toHaveAttribute('aria-current', 'true')
    expect(await retainedRow.evaluate((element, original) => element === original, originalRow)).toBe(true)
    await expect(root.locator('[data-pane="detail"]')).toBeVisible()
    if ((await root.getAttribute('data-compact')) === 'true') {
      await expect(root.locator('[data-pane="list"]')).toBeHidden()
      await expect(root.locator('[data-pane="list"]')).toHaveAttribute('inert', '')
    }
    await expect(editor.locator('.cm-line')).toHaveText([draft])
    await assertUnclippedText(root.getByRole('heading', { name: new RegExp(names.server, 'i') }))
    const save = root.getByRole('button', { name: 'Save', exact: true })
    await expect(save).toBeEnabled()
    // The next Tab stop is CodeMirror, where Shift+Tab intentionally outdents.
    // Approach from the toolbar to exercise native focus without editing text.
    current.focusChecks.push(await verifyKeyboardFocus(current.page, save, `${label} MCP Save`, 'previous'))
    await current.page.keyboard.press('Tab')
    await expect(editor).toBeFocused()
    // CodeMirror's Escape-then-Tab escape hatch must also return to the toolbar.
    await current.page.keyboard.press('Escape')
    await current.page.keyboard.press('Shift+Tab')
    await expect(save).toBeFocused()
    await expect(editor.locator('.cm-line')).toHaveText([draft])
    await captureGallery(current, `capability-mcp-draft-${label}`, root)
    await backRetains(current, root, row, before)

    if ((await root.getAttribute('data-compact')) === 'true') {
      await row.click()
    }

    await resizeGallery(current, 1536, 961)
    await editor.focus()
    await resizeGallery(current, size.width, size.height, size.zoom)
    await expect(editor).toBeFocused()
    await expect(editor.locator('.cm-line')).toHaveText([draft])
    expect(await editor.evaluate((element, original) => element === original, originalEditor)).toBe(true)
  }
})

test('empty Skills, Tools and MCP remain readable and actionable at every content width', async () => {
  const current = await start('empty')

  for (const size of sizes) {
    await resizeGallery(current, size.width, size.height, size.zoom)

    for (const tab of ['skills', 'toolsets', 'mcp'] as const) {
      await openTab(current, tab)
      const workspace = current.page.locator('.eid-capabilities-workspace')

      if (tab === 'mcp') {
        await showList(workspace.locator('.eid-mcp-workspace'))
        const create = workspace.getByRole('button', { name: 'New server', exact: true })
        await create.scrollIntoViewIfNeeded()
        await expect(create).toBeInViewport({ ratio: 1 })
        current.focusChecks.push(await verifyKeyboardFocus(current.page, create, 'Empty MCP New server'))
      } else {
        const empty = workspace.getByText(`No ${tab === 'skills' ? 'skills' : 'tools'} found`, { exact: true })
        await empty.scrollIntoViewIfNeeded()
        await expect(empty).toBeInViewport({ ratio: 1 })
      }

      await captureGallery(current, `capability-${tab}-empty-${size.width}-${size.zoom * 100}`, workspace)
    }
  }
})

test('failed capability reads keep retry controls available at every content width', async () => {
  allowErrorBanners()
  const current = await start('error')
  await openTab(current, 'skills')
  const workspace = current.page.locator('.eid-capabilities-workspace')
  const refresh = workspace.getByRole('button', { name: 'Refresh skills', exact: true })
  await refresh.waitFor({ state: 'visible' })

  for (const size of sizes) {
    await resizeGallery(current, size.width, size.height, size.zoom)
    await refresh.scrollIntoViewIfNeeded()
    current.focusChecks.push(await verifyKeyboardFocus(current.page, refresh, 'Skills read-error retry'))
    await captureGallery(current, `capability-skills-error-${size.width}-${size.zoom * 100}`, workspace)
  }

  // A previously unused profile gets a fresh config query rather than stale
  // ambient config from shell startup, exercising the genuine error state.
  await workspace.getByRole('combobox').click()
  await current.page.getByRole('option', { name: 'synthetic-scope', exact: true }).click()
  await openTab(current, 'mcp')
  const retry = workspace.getByRole('button', { name: 'Reload MCP', exact: true })
  await retry.waitFor({ state: 'visible' })

  for (const size of sizes) {
    await resizeGallery(current, size.width, size.height, size.zoom)
    await retry.scrollIntoViewIfNeeded()
    current.focusChecks.push(await verifyKeyboardFocus(current.page, retry, 'MCP read-error retry'))
    await captureGallery(current, `capability-mcp-error-${size.width}-${size.zoom * 100}`, workspace)
  }

  await setMode(current, 'populated')
  await retry.click()
  await expect(workspace.locator('.eid-mcp-workspace')).toBeVisible()
  await showList(workspace.locator('.eid-mcp-workspace'))
  await expect(workspace.getByRole('button', { name: new RegExp(`^${names.server}$`, 'i') })).toBeVisible()
})
