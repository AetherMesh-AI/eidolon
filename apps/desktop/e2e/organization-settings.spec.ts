/** Actual isolated Electron settings, temporary profile, no inference requests. */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { verifyKeyboardFocus } from './focus-visibility'
import { expect, test } from './test'

let fixture: MockBackendFixture | undefined

test.setTimeout(300_000)
test.afterEach(async () => {
  await fixture?.cleanup()
  fixture = undefined
})

test('keeps all Settings destinations, scoped saves, dismissal and selected appearance in the workspace', async () => {
  fixture = await setupMockBackend({
    extraConfig: '\ncheckpoints:\n  enabled: false\nsettings_fixture_marker: retained\n'
  })
  const { app, page, mock, sandbox } = fixture
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

  async function capture(name: string) {
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
    const bytes = await page.screenshot({
      path: test.info().outputPath(`${name}.png`),
      animations: 'disabled',
      caret: 'hide'
    })
    captures.push({
      name,
      sha256: createHash('sha256').update(bytes).digest('hex'),
      route: page.url(),
      viewport: await page.evaluate(() => ({ width: innerWidth, height: innerHeight })),
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
  // Wait for the real editor, not the gap before lazy content mounts its skeleton.
  await expect(settings.getByRole('button', { name: 'Apply', exact: true })).toBeVisible()
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
