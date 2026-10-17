/** Actual isolated Electron settings, temporary profile, no inference requests. */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { STARTUP_REQUEST_TIMEOUT_MS } from '../src/api/request-timeouts'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { verifyKeyboardFocus } from './focus-visibility'
import { expect, test } from './test'

let fixture: MockBackendFixture | undefined
let initialEditorReadiness: object[] = []

test.setTimeout(300_000)
test.afterEach(async () => {
  if (fixture) {
    // Keep failed readiness assertions failed, but retain bounded transport evidence.
    await expect.poll(() => fixture!.app.evaluate(() =>
      ((globalThis as any).__settingsConfigRequests ?? []).filter((row: any) => row.finishedAt == null).length
    ), { timeout: 30_000 }).toBe(0).catch(() => undefined)
    const requests = await fixture.app.evaluate(() => (globalThis as any).__settingsConfigRequests ?? [])
    await test.info().attach('settings-config-transport', { body: JSON.stringify(requests, null, 2), contentType: 'application/json' })
    const currentReadiness = await fixture.page.evaluate(() => (window as any).__settingsEditorReadiness ?? [])
    await test.info().attach('settings-editor-readiness', { body: JSON.stringify(initialEditorReadiness.length ? initialEditorReadiness : currentReadiness, null, 2), contentType: 'application/json' })
  }

  await fixture?.cleanup()
  fixture = undefined
  initialEditorReadiness = []
})

test('keeps all Settings destinations, scoped saves, dismissal and selected appearance in the workspace', async () => {
  fixture = await setupMockBackend({
    isolateModelCatalog: true,
    extraConfig: '\ncheckpoints:\n  enabled: false\nsettings_fixture_marker: retained\n'
  })
  const { app, page, mock, sandbox } = fixture
  await app.evaluate(() => {
    const rows: any[] = []

    ;(globalThis as any).__settingsConfigRequests = rows
    // Observe real Node HTTP requests without replacing IPC, responses or routing.
    process.getBuiltinModule('diagnostics_channel').channel('http.client.request.start').subscribe(({ request }: any) => {
      const url = new URL(request.path, 'http://127.0.0.1')

      if (!['/api/config', '/api/config/schema', '/api/model/info', '/api/model/options', '/api/model/auxiliary', '/api/model/moa'].includes(url.pathname)) {
        return
      }

      const row: any = { path: url.pathname, profile: url.searchParams.get('profile'), method: request.method, startedAt: Date.now() }
      rows.push(row)
      request.once('response', (response: any) => {
        row.status = response.statusCode
        response.once('end', () => { row.finishedAt = Date.now(); row.durationMs = row.finishedAt - row.startedAt })
      })
      request.once('error', (error: Error) => { row.finishedAt = Date.now(); row.error = error.message })
    })
  })
  await page.evaluate(() => {
    const rows: object[] = []

    ;(window as any).__settingsEditorReadiness = rows
    let previous = ''

    const record = () => {
      const root = document.querySelector('.eid-settings-page')

      const state = {
        mounted: !!root,
        skeletons: root?.querySelectorAll('[data-slot=skeleton]').length ?? 0,
        applyPresent: [...(root?.querySelectorAll('button') ?? [])].some(button => button.textContent?.trim() === 'Apply')
      }

      const key = JSON.stringify(state)

      if (key !== previous) { rows.push({ at: Date.now(), ...state }); previous = key }
    }

    new MutationObserver(record).observe(document.body, { childList: true, subtree: true })
    record()
  })
  await waitForAppReady(fixture)
  const navigation = page.getByRole('complementary', { name: 'Eidolon navigation', exact: true })
  await navigation.getByRole('link', { name: 'Home', exact: true }).click()
  await navigation.getByRole('link', { name: 'Settings', exact: true }).click()
  const settings = page.locator('.eid-settings-page')
  const title = settings.locator('.eid-settings-main > header h2')
  const captures: object[] = []
  const focusChecks: object[] = []
  const configPath = join(sandbox.hermesHome, 'config.yaml')
  const sourceCommit = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim()
  const sourceTree = execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim()

  async function resize(width: number, height = 900) {
    await app.evaluate(({ BrowserWindow }, value) => {
      const window = BrowserWindow.getAllWindows()[0]
      window.unmaximize()
      window.setMinimumSize(600, 600)
      window.setContentSize(value.width, value.height)
    }, { width, height })
    await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(width)
  }

  async function capture(name: string, nativeWindow = false) {
    // Matched review images are neutral-focus; keyboard checks retain their own evidence.
    if (name.startsWith('reference-')) {
      await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) {document.activeElement.blur()} })
    }

    await expect(settings.locator('[data-slot="skeleton"]')).toHaveCount(0)
    await page.evaluate(async () => {
      await document.fonts.ready
      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
    })
    await expect(navigation).toBeVisible()
    const rail = settings.locator('.eid-settings-layout > aside')

    if (await rail.isVisible()) {
      const rows = await rail.locator('button[aria-describedby]').evaluateAll(elements => elements.map(element => ({
        label: element.getAttribute('aria-label'),
        height: element.getBoundingClientRect().height,
        contentHeight: element.scrollHeight
      })))

      expect(rows.length).toBeGreaterThan(0)

      for (const row of rows) {
        expect(row.height, `${row.label} retains its full text height`).toBeGreaterThanOrEqual(row.contentHeight - 1)
      }
    }

    const health = page.getByRole('contentinfo', { name: 'System health', exact: true })
    await expect(health).toHaveCount(1)
    await expect(health).toBeInViewport({ ratio: 1 })
    const bounds = await settings.boundingBox()
    const bar = await health.boundingBox()
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(bar!.y + 1)
    expect(await settings.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)

    // Page screenshots crop Electron content at non-default zoom. Retain the
    // full native surface, and verify pixel dimensions rather than trusting DPR.
    const viewport = await page.evaluate(() => ({ width: innerWidth, height: innerHeight, devicePixelRatio }))

    const bytes = nativeWindow
      ? Buffer.from(await app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')), 'base64')
      : await page.screenshot({ path: test.info().outputPath(`${name}.png`), animations: 'disabled', caret: 'hide' })

    const pixelSize = { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) }

    if (nativeWindow) {
      expect(pixelSize).toEqual({ width: viewport.width * viewport.devicePixelRatio, height: viewport.height * viewport.devicePixelRatio })
      writeFileSync(test.info().outputPath(`${name}.png`), bytes)
    }

    const overviewCards = name.startsWith('reference-settings-overview')
      ? await settings.locator('.eid-settings-cards > section').evaluateAll(elements => elements.map(element => ({
          label: element.getAttribute('aria-label'), ...element.getBoundingClientRect().toJSON()
        })))
      : undefined

    captures.push({
      overviewCards,
      name,
      sha256: createHash('sha256').update(bytes).digest('hex'),
      route: page.url(),
      viewport,
      pixelSize,
      captureMethod: nativeWindow ? 'electron.capturePage' : 'playwright.screenshot',
      theme: await page.locator('html').getAttribute('data-hermes-theme'),
      mode: await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)
    })
    writeFileSync(
      test.info().outputPath('settings-visual-evidence.json'),
      JSON.stringify(
        {
          sourceCommit,
          sourceTree,
          scenario: 'synthetic-temporary-profile-settings',
          providerCalls: mock.receivedPrompts.length,
          captures,
          focusChecks
        },
        null,
        2
      )
    )
  }

  await resize(1536, 961)
  await expect(settings.locator('.eid-settings-cards > section')).toHaveCount(6)
  const initialModel = settings.locator('[data-section="config:model"] .eid-settings-card-body')
  // Model info/options use the product's startup-read budget. A terminal error
  // still fails the usable-editor assertions below instead of passing as loaded.
  await expect(initialModel).toBeVisible()
  await expect(initialModel.getByRole('button', { name: 'Apply', exact: true })).toBeVisible({ timeout: STARTUP_REQUEST_TIMEOUT_MS })
  await expect(initialModel.locator('[data-slot="model-settings-skeleton"]')).toHaveCount(0)
  await expect(initialModel.getByRole('combobox').first()).toBeEnabled()
  await expect(initialModel.getByRole('button', { name: 'Apply', exact: true })).toBeEnabled()
  // Preserve the initial readiness timeline before appearance tests reload the renderer.
  initialEditorReadiness = await page.evaluate(() => (window as any).__settingsEditorReadiness ?? [])
  const cards = await settings.locator('.eid-settings-card-editor').evaluateAll(elements => elements.map(element => element.getBoundingClientRect().toJSON()))
  expect(Math.abs(cards[0].top - cards[1].top)).toBeLessThanOrEqual(1)
  expect(cards[1].left).toBeGreaterThan(cards[0].right)
  // Reference first-row height is about 295px. Keep the real controls within a
  // small content allowance, preserving controls and their focus outlines.
  expect(Math.max(...cards.map(card => card.height))).toBeLessThanOrEqual(300)
  const middleCards = await settings.locator('.eid-settings-cards > :is([data-section="config:workspace"], [data-section="config:safety"])').evaluateAll(elements => elements.map(element => element.getBoundingClientRect().toJSON()))
  expect(middleCards).toHaveLength(2)
  expect(middleCards.every(card => card.height >= 275 && card.height <= 285)).toBe(true)
  expect(Math.abs(middleCards[0].top - middleCards[1].top)).toBeLessThanOrEqual(1)
  const bottomCards = await settings.locator('.eid-settings-cards > :is([data-section="providers"], [data-section="config:appearance"])').evaluateAll(elements => elements.map(element => element.getBoundingClientRect().toJSON()))
  expect(bottomCards.every(card => card.top > middleCards[0].bottom && card.bottom <= 915)).toBe(true)
  // The page owns overview scrolling, with a bounded notification list preserving
  // one switch column. Secondary editors retain their native disclosures.
  const advancedModel = settings.locator('.eid-settings-model-advanced')
  await expect(advancedModel).not.toHaveAttribute('open', '')
  const advancedSummary = advancedModel.locator('summary')
  focusChecks.push(await verifyKeyboardFocus(page, advancedSummary, 'overview advanced model disclosure'))
  await page.keyboard.press('Enter')
  await expect(advancedModel).toHaveAttribute('open', '')
  await expect(advancedModel.getByRole('button', { name: 'Reset all to main' })).toBeVisible()
  await advancedSummary.focus()
  await page.keyboard.press('Enter')
  await expect(advancedModel).not.toHaveAttribute('open', '')
  const parameters = settings.locator('.eid-settings-model-parameters')
  await expect(parameters).not.toHaveAttribute('open', '')
  focusChecks.push(await verifyKeyboardFocus(page, parameters.locator('summary'), 'overview context and fallback disclosure'))
  await page.keyboard.press('Enter')
  await expect(parameters).toHaveAttribute('open', '')
  await expect(parameters.getByText('Context Window', { exact: true })).toBeVisible()
  await expect(parameters.getByRole('button', { name: 'Add fallback', exact: true })).toBeVisible()
  await parameters.locator('summary').focus()
  await page.keyboard.press('Enter')
  await expect(parameters).not.toHaveAttribute('open', '')

  const nestedScrollers = await settings.locator('.eid-settings-card-body .settings-content > div').evaluateAll(elements =>
    elements.filter(element => element.scrollHeight > element.clientHeight + 1).length)

  expect(nestedScrollers).toBe(0)
  await expect(settings.getByRole('button', { name: 'Open Appearance', exact: true })).toBeInViewport({ ratio: 1 })
  await capture('reference-settings-overview')
  const notifications = settings.locator('.eid-settings-cards').getByRole('region', { name: 'Notifications', exact: true })
  await expect(notifications.getByRole('switch')).toHaveCount(8)
  await expect(notifications.locator('.eid-settings-toggle-description')).toHaveCount(0)
  await expect(notifications.getByText('Device-level OS alerts', { exact: true })).toBeVisible()
  await expect(notifications.getByRole('switch', { name: 'Enable notifications', exact: true })).toHaveAttribute('aria-description', 'OS notifications (not in-app toasts). Per device. Off silences every notification below.')
  const approvalSwitch = notifications.getByRole('switch', { name: 'Approval needed', exact: true })
  const beforeApproval = await approvalSwitch.getAttribute('aria-checked')
  focusChecks.push(await verifyKeyboardFocus(page, notifications.getByRole('switch', { name: 'Enable notifications', exact: true }), 'overview notification scope switch'))
  focusChecks.push(await verifyKeyboardFocus(page, approvalSwitch, 'overview notification explanation and switch'))
  await expect(notifications.getByText('Approve or reject a command', { exact: true })).toBeVisible()
  await expect(approvalSwitch).toHaveAttribute('aria-description', 'A command is waiting for you to approve or reject it.')
  await expect(approvalSwitch).toHaveAttribute('aria-checked', beforeApproval!)
  const notificationRows = await notifications.locator('.settings-row:has([role="switch"]) > div').evaluateAll(rows => rows.map(row => row.getBoundingClientRect().height))
  expect(notificationRows.every(height => height >= 34 && height <= 38)).toBe(true)
  const switchRects = await notifications.getByRole('switch').evaluateAll(elements => elements.map(element => element.getBoundingClientRect().toJSON()))
  expect(Math.max(...switchRects.map(rect => rect.right)) - Math.min(...switchRects.map(rect => rect.right))).toBeLessThanOrEqual(1)
  expect(switchRects.every((rect, index) => index === 0 || rect.top > switchRects[index - 1].bottom)).toBe(true)
  const notificationList = notifications.locator('.eid-settings-notification-kinds')
  expect(await notificationList.evaluate(element => element.scrollHeight > element.clientHeight + 1)).toBe(true)
  focusChecks.push(await verifyKeyboardFocus(page, notifications.getByRole('switch').last(), 'overview final notification kind in scroll list'))
  await expect(notifications.getByRole('switch').last()).toBeInViewport()
  const enabled = notifications.getByRole('switch').first()
  const beforeConfig = readFileSync(configPath, 'utf8')
  const beforeEnabled = await enabled.getAttribute('aria-checked')
  await enabled.click()
  await expect(enabled).toHaveAttribute('aria-checked', beforeEnabled === 'true' ? 'false' : 'true')
  await expect(approvalSwitch).toBeDisabled()
  await expect(notifications.getByText('Approve or reject a command', { exact: true })).toBeVisible()
  await expect(approvalSwitch).toHaveAttribute('aria-description', 'A command is waiting for you to approve or reject it.')
  expect(readFileSync(configPath, 'utf8')).toBe(beforeConfig)
  await enabled.click()
  await expect(enabled).toHaveAttribute('aria-checked', beforeEnabled!)
  await settings.getByRole('button', { name: 'Appearance', exact: true }).click()
  await settings.getByRole('button').filter({ has: page.getByText('AetherMesh', { exact: true }) }).click()
  await settings.getByRole('button', { name: 'Light', exact: true }).click()
  await settings.getByRole('button', { name: 'Overview', exact: true }).click()
  await expect(settings.getByRole('button', { name: 'Apply', exact: true })).toBeVisible()
  await expect(settings.getByRole('button', { name: 'Open Appearance', exact: true })).toBeInViewport({ ratio: 1 })
  await capture('reference-settings-overview-light')
  await resize(1280, 720)
  await page.evaluate(() => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(200))
  await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(2)
  const zoomProviders = settings.getByRole('button', { name: 'Open Providers', exact: true })
  await zoomProviders.scrollIntoViewIfNeeded()
  focusChecks.push(await verifyKeyboardFocus(page, zoomProviders, 'overview providers light 200 percent'))
  await expect(zoomProviders).toBeInViewport({ ratio: 1 })
  await capture('settings-overview-light-200-percent', true)
  await page.evaluate(() => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(100))
  await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(1)
  await resize(1536, 961)
  await settings.getByRole('button', { name: 'Appearance', exact: true }).click()
  await settings.getByRole('button').filter({ has: page.getByText('Eidolon', { exact: true }) }).click()
  await settings.getByRole('button', { name: 'Dark', exact: true }).click()
  await settings.getByRole('button', { name: 'Overview', exact: true }).click()
  await resize(760)
  const openProviders = settings.getByRole('button', { name: 'Open Providers', exact: true })
  await openProviders.scrollIntoViewIfNeeded()
  focusChecks.push(await verifyKeyboardFocus(page, openProviders, 'overview providers narrow'))
  await capture('settings-overview-narrow')
  await openProviders.click()
  await expect(page).toHaveURL(/tab=providers/);
  await expect(title).toHaveText('Accounts')
  await resize(1536, 961)
  await settings.getByRole('button', { name: 'Model', exact: true }).click()
  // Wait for the real editor, not the gap before lazy content mounts its skeleton.
  await expect(settings.getByRole('button', { name: 'Apply', exact: true })).toBeVisible({ timeout: 30_000 })
  await capture('reference-settings-model')
  await resize(1440)
  await settings.getByRole('button', { name: 'Model', exact: true }).focus()
  await page.keyboard.press('Tab')
  await expect(settings.getByRole('button', { name: 'Chat', exact: true })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(title).toHaveText('Chat')

  const tabs = [
    'Model',
    'Chat',
    'Appearance',
    'Workspace',
    'Safety',
    'Browser',
    'Memory & Context',
    'Voice',
    'Advanced',
    'Notifications',
    'Billing',
    'Providers',
    'Gateways',
    'Keyboard Shortcuts',
    'Tools & Keys',
    'Plugins',
    'Archived Chats',
    'About'
  ]

  for (const [index, tab] of tabs.entries()) {
    await settings.getByRole('button', { name: tab, exact: true }).click()
    await expect(title).toHaveText(tab === 'Providers' ? 'Accounts' : tab === 'Tools & Keys' ? 'Tools' : tab)
    await capture(`settings-${String(index + 1).padStart(2, '0')}-${tab.toLowerCase().replaceAll(/[^a-z]+/g, '-')}`)

    if (tab === 'Providers') {
      for (const child of ['Accounts', 'API keys', 'Custom Endpoints']) {
        await settings.locator('[data-tour="overlay-nav"]').getByRole('button', { name: child, exact: true }).click()
        await expect(title).toHaveText(child)
        await capture(`settings-providers-${child.toLowerCase().replaceAll(/[^a-z]+/g, '-')}`)
      }
    }

    if (tab === 'Tools & Keys') {
      for (const child of ['Tools', 'Settings']) {
        await settings.locator('[data-tour="overlay-nav"]').getByRole('button', { name: child, exact: true }).click()
        await expect(title).toHaveText(child)
        await capture(`settings-keys-${child.toLowerCase()}`)
      }
    }
  }

  // Settings includes the persistent primary rail, so it collapses earlier
  // than standalone overlays. Check both old and new exact boundaries.
  await settings.getByRole('button', { name: 'Appearance', exact: true }).click()

  for (const width of [759, 760, 761, 999, 1000, 1001]) {
    await resize(width)
    const rail = settings.locator('.eid-settings-layout > aside')
    const dropdown = settings.locator('.eid-settings-layout > div')

    if (width < 1000) {
      await expect(rail).toBeHidden()
      await expect(dropdown).toBeVisible()
    } else {
      await expect(rail).toBeVisible()
      await expect(dropdown).toBeHidden()
    }

    const controls = await settings.locator('.eid-settings-controls').boundingBox()
    expect(controls!.width).toBeGreaterThan(300)
    expect(controls!.height).toBeGreaterThan(240)
    await capture(`settings-breakpoint-${width}`)
  }

  await resize(999)
  const compact = settings.locator('.eid-settings-layout > div').getByRole('button', { name: 'Appearance', exact: true })
  await compact.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('menu')).toBeVisible()
  await page.keyboard.press('Home')
  await expect(page.getByRole('menuitem', { name: 'Overview', exact: true })).toBeFocused()
  await page.keyboard.press('ArrowDown')
  await expect(page.getByRole('menuitem', { name: 'Organization', exact: true })).toBeFocused()
  await page.keyboard.press('ArrowDown')
  await expect(page.getByRole('menuitem', { name: 'Model', exact: true })).toBeFocused()
  await page.keyboard.press('ArrowDown')
  await expect(page.getByRole('menuitem', { name: 'Chat', exact: true })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(title).toHaveText('Chat')
  await expect(settings.locator('.eid-settings-layout > div').getByRole('button', { name: 'Chat', exact: true })).toBeFocused()
  const focusedStyle = await settings.locator('.eid-settings-layout > div').getByRole('button', { name: 'Chat', exact: true }).evaluate(element => ({ style: getComputedStyle(element).outlineStyle, width: getComputedStyle(element).outlineWidth }))
  expect(focusedStyle.style).toBe('solid')
  expect(Number.parseFloat(focusedStyle.width)).toBeGreaterThanOrEqual(2)
  await capture('settings-compact-keyboard-chat')
  await page.keyboard.press('Enter')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('menu')).toHaveCount(0)
  await expect(settings).toBeVisible()
  await expect(settings.locator('.eid-settings-layout > div').getByRole('button', { name: 'Chat', exact: true })).toBeFocused()
  await resize(1440)
  await settings.getByRole('button', { name: 'Safety', exact: true }).click()
  const checkpoints = settings.locator('[id="setting-field-checkpoints.enabled"]').getByRole('switch')
  await expect(checkpoints).toHaveAttribute('aria-checked', 'false')
  await checkpoints.click()
  await expect.poll(() => readFileSync(configPath, 'utf8')).toMatch(/checkpoints:\n\s+enabled: true/)
  await checkpoints.click()
  await expect.poll(() => readFileSync(configPath, 'utf8')).toMatch(/checkpoints:\n\s+enabled: false/)
  expect(readFileSync(configPath, 'utf8')).toContain('settings_fixture_marker: retained')
  await settings.getByRole('button', { name: 'Appearance', exact: true }).click()
  await settings.getByRole('button', { name: 'Safety', exact: true }).click()
  await expect(checkpoints).toHaveAttribute('aria-checked', 'false')

  await settings.getByRole('button', { name: 'Reset to defaults', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(settings).toBeVisible()
  expect(readFileSync(configPath, 'utf8')).toContain('settings_fixture_marker: retained')
  await page.keyboard.press('Escape')
  await expect(settings).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Your organization', exact: true })).toBeVisible()
  await navigation.getByRole('link', { name: 'Settings', exact: true }).click()

  for (const [theme, key] of [
    ['Eidolon', 'eidolon'],
    ['Catppuccin', 'catppuccin'],
    ['AetherMesh', 'nous']
  ]) {
    for (const mode of ['Light', 'Dark']) {
      await settings.getByRole('button', { name: 'Appearance', exact: true }).click()
      await settings
        .getByRole('button')
        .filter({ has: page.getByText(theme, { exact: true }) })
        .click()
      await settings.getByRole('button', { name: mode, exact: true }).click()
      await expect(page.locator('html')).toHaveAttribute('data-hermes-theme', key)
      focusChecks.push(await verifyKeyboardFocus(page, settings.locator('.eid-settings-layout > aside').getByRole('button', { name: 'Appearance', exact: true }), `${key}-${mode}-navigation`))
      await capture(`settings-appearance-${key}-${mode.toLowerCase()}`)
      focusChecks.push(await verifyKeyboardFocus(page, settings.locator('.eid-settings-controls input:visible').first(), `${key}-${mode}-editor`))
      await capture(`settings-focus-editor-${key}-${mode.toLowerCase()}`)
      await resize(760)
      focusChecks.push(await verifyKeyboardFocus(page, settings.locator('.eid-settings-layout > div').getByRole('button', { name: 'Appearance', exact: true }), `${key}-${mode}-compact-selector`))
      await capture(`settings-appearance-${key}-${mode.toLowerCase()}-narrow`)
      await resize(1440)
      await settings.getByRole('button', { name: 'Close settings', exact: true }).click()
      await page.reload()
      await waitForAppReady(fixture)
      await expect(page.locator('html')).toHaveAttribute('data-hermes-theme', key)
      await navigation.getByRole('link', { name: 'Settings', exact: true }).click()
      await settings.getByRole('button', { name: 'Appearance', exact: true }).click()
      await expect(settings.getByRole('button', { name: mode, exact: true })).toHaveAttribute('aria-pressed', 'true')
    }
  }

  expect(mock.receivedPrompts).toHaveLength(0)
})

for (const locale of ['ja', 'ar', 'ru'] as const) {
  test(`keeps the Settings notification list keyboard reachable in ${locale} at normal and 200 percent zoom`, async () => {
    fixture = await setupMockBackend({ extraDisplayConfig: `  language: ${locale}` })
    const { app, page, mock } = fixture
    await waitForAppReady(fixture)
    await expect(page.locator('html')).toHaveAttribute('lang', locale)
    await expect(page.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr')
    await page.evaluate(() => { window.location.hash = '/settings' })
    const settings = page.locator('.eid-settings-page')
    const notifications = settings.locator('.eid-settings-cards > [data-section="notifications"]')
    const switches = notifications.getByRole('switch')
    await expect(switches).toHaveCount(8)
    const before = await switches.evaluateAll(elements => elements.map(element => element.getAttribute('aria-checked')))
    const captures: object[] = []
    const focusChecks: object[] = []
    const thumbChecks: object[] = []

    for (const zoom of [100, 200]) {
      await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1280, 720))
      await page.evaluate(percent => (window as any).hermesDesktop.zoom.setPercent(percent), zoom)
      await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(zoom / 100)
      await switches.first().focus()
      for (let index = 0; index < 8; index++) {
        if (index > 0) { await page.keyboard.press('Tab') }
        const target = switches.nth(index)
        await expect(target).toBeFocused()
        await expect(target).toHaveAttribute('aria-description', /\S/)
        focusChecks.push(await verifyKeyboardFocus(page, target, `notification ${locale} ${zoom} ${index}`))
        const track = await target.boundingBox()
        const thumb = await target.locator('[data-slot="switch-thumb"]').boundingBox()
        expect(track).not.toBeNull()
        expect(thumb).not.toBeNull()
        expect(thumb!.x).toBeGreaterThanOrEqual(track!.x)
        expect(thumb!.x + thumb!.width).toBeLessThanOrEqual(track!.x + track!.width)
        expect(thumb!.y).toBeGreaterThanOrEqual(track!.y)
        expect(thumb!.y + thumb!.height).toBeLessThanOrEqual(track!.y + track!.height)
        thumbChecks.push({ locale, zoom, index, track, thumb })
        await expect(target).toBeInViewport({ ratio: 1 })
      }
      const list = notifications.locator('.eid-settings-notification-kinds')
      expect(await list.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
      expect(await settings.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
      const rects = await switches.evaluateAll(elements => elements.map(element => element.getBoundingClientRect().toJSON()))
      expect(Math.max(...rects.map(rect => rect.right)) - Math.min(...rects.map(rect => rect.right))).toBeLessThanOrEqual(1)
      expect(rects.every((rect, index) => index === 0 || rect.top > rects[index - 1].bottom)).toBe(true)
      expect(await switches.evaluateAll(elements => elements.map(element => element.getAttribute('aria-checked')))).toEqual(before)
      const viewport = await page.evaluate(() => ({ width: innerWidth, height: innerHeight, devicePixelRatio }))
      const bytes = Buffer.from(await app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')), 'base64')
      const pixelSize = { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) }
      expect(pixelSize).toEqual({ width: viewport.width * viewport.devicePixelRatio, height: viewport.height * viewport.devicePixelRatio })
      const name = `settings-notifications-${locale}-${zoom}.png`
      writeFileSync(test.info().outputPath(name), bytes)
      captures.push({ name, locale, zoom, viewport, pixelSize, sha256: createHash('sha256').update(bytes).digest('hex') })
    }
    expect(mock.receivedPrompts).toHaveLength(0)
    writeFileSync(test.info().outputPath('settings-notification-focus-evidence.json'), JSON.stringify({
      sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
      sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
      scenario: 'synthetic-settings-notification-keyboard', locale, providerCalls: mock.receivedPrompts.length,
      captures, focusChecks, thumbChecks
    }, null, 2))
  })
}
