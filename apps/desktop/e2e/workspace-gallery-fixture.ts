import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { writeFileSync } from 'node:fs'

import type { Locator } from '@playwright/test'

import { setupMockBackend, waitForAppReady } from './fixtures'
import { verifyKeyboardFocus } from './focus-visibility'
import { snapshot } from './organization-visual-fixture'
import { RealSessionBuilder } from './real-session-builder'
import { expect, test } from './test'

export const galleryLocales = [
  { locale: 'en', dark: 'Dark', light: 'Light' },
  { locale: 'ja', dark: 'ダーク', light: 'ライト' },
  { locale: 'zh', dark: '暗色', light: '明亮' },
  { locale: 'zh-hant', dark: '深色', light: '明亮' },
  { locale: 'ar', dark: 'داكن', light: 'فاتح' },
  { locale: 'ru', dark: 'Тёмная', light: 'Светлая' }
] as const

export async function setupGallery(locale: string, seedHistory = false) {
  const fixture = await setupMockBackend({ extraDisplayConfig: `  language: ${locale}` })
  try {
    const { page, mock } = fixture
    await waitForAppReady(fixture)
    let sessionId: string | undefined
  
    if (seedHistory) {
      const builder = await RealSessionBuilder.start(fixture.sandbox.hermesHome)
      try {
        sessionId = (await builder.createSession({ title: 'Synthetic gallery history', turns: ['Synthetic history for UI review. No external work is requested.'] })).sessionId
      } finally {
        await builder.close()
      }
      expect(mock.receivedPrompts).toHaveLength(1)
    }
  
    const methods: string[] = []
    const state = { snapshot: structuredClone(snapshot) }
    state.snapshot.activity[1].text = 'سجل تجريبي للمراجعة · Synthetic record'
    await page.routeWebSocket(/.*/, socket => {
      const server = socket.connectToServer()
      socket.onMessage(message => {
        const request = JSON.parse(message.toString()) as { id?: number; method?: string }
        if (request.method?.startsWith('organization.')) { methods.push(request.method) }
        if (request.method === 'organization.snapshot' || request.method === 'organization.outcomes') {
          socket.send(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: request.method === 'organization.snapshot' ? state.snapshot : state.snapshot.outcomes }))
        } else { server.send(message) }
      })
    })
    await page.reload()
    await waitForAppReady(fixture)
    await expect(page.locator('html')).toHaveAttribute('lang', locale)
    await expect(page.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr')
    const captures: object[] = []
    const focusChecks: object[] = []
    const thumbChecks: object[] = []
    return { ...fixture, locale, sessionId, methods, state, captures, focusChecks, thumbChecks, seedProviderCalls: seedHistory ? 1 : 0 }
  } catch (error) {
    await fixture.cleanup()
    throw error
  }
}

export type GalleryFixture = Awaited<ReturnType<typeof setupGallery>>

export async function resizeGallery(fixture: GalleryFixture, width: number, height: number, zoom = 1) {
  await fixture.page.evaluate(() => (window as any).hermesDesktop.zoom.setPercent(100))
  await fixture.app.evaluate(({ BrowserWindow }, size) => {
    const window = BrowserWindow.getAllWindows()[0]
    window.unmaximize()
    window.setMinimumSize(600, 600)
    window.setContentSize(size.width, size.height)
  }, { width, height })
  await expect.poll(() => fixture.page.evaluate(() => innerWidth)).toBe(width)
  await fixture.page.evaluate(value => (window as any).hermesDesktop.zoom.setPercent(value * 100), zoom)
  await expect.poll(() => fixture.page.evaluate(() => devicePixelRatio)).toBe(zoom)
}

export async function setGalleryAppearance(fixture: GalleryFixture, mode: 'dark' | 'light', label: string) {
  await resizeGallery(fixture, 1536, 961)
  await fixture.page.evaluate(() => { location.hash = '/settings?tab=config:appearance' })
  await fixture.page.getByRole('button').filter({ has: fixture.page.getByText('Eidolon', { exact: true }) }).click()
  await fixture.page.getByRole('button', { name: label, exact: true }).click()
  await expect(fixture.page.locator('html')).toHaveAttribute('data-hermes-theme', 'eidolon')
  await expect.poll(() => fixture.page.locator('html').evaluate(element => element.classList.contains('dark'))).toBe(mode === 'dark')
}

export async function verifyGalleryControls(fixture: GalleryFixture, scope: Locator, label: string) {
  const controls: Locator[] = []
  for (const target of await scope.locator('button, a[href], input:not([type="hidden"]), select, textarea, summary').all()) {
    if (await target.isVisible() && await target.isEnabled() && await target.getAttribute('tabindex') !== '-1') { controls.push(target) }
  }
  // First and last controls exercise both ends of each real page's scroll owner.
  for (const index of [...new Set([0, controls.length - 1])].filter(index => index >= 0 && index < controls.length)) {
    fixture.focusChecks.push(await verifyKeyboardFocus(fixture.page, controls[index], `${label} control ${index}`))
    await expect(controls[index]).toBeInViewport({ ratio: 1 })
  }
  for (const target of await scope.locator('[data-slot="switch"]').all()) {
    if (!await target.isVisible()) { continue }
    const track = await target.boundingBox()
    const thumb = await target.locator('[data-slot="switch-thumb"]').boundingBox()
    expect(track).not.toBeNull()
    expect(thumb).not.toBeNull()
    expect(thumb!.x).toBeGreaterThanOrEqual(track!.x)
    expect(thumb!.x + thumb!.width).toBeLessThanOrEqual(track!.x + track!.width)
    expect(thumb!.y).toBeGreaterThanOrEqual(track!.y)
    expect(thumb!.y + thumb!.height).toBeLessThanOrEqual(track!.y + track!.height)
    fixture.thumbChecks.push({ label, checked: await target.getAttribute('aria-checked'), track, thumb })
  }
  expect(await scope.evaluate(element => element.scrollWidth - element.clientWidth), label).toBeLessThanOrEqual(1)
}

export async function verifyNotificationStates(fixture: GalleryFixture, label: string) {
  const { page } = fixture
  await page.evaluate(() => { location.hash = '/settings' })
  const scope = page.locator('.eid-settings-cards > [data-section="notifications"]')
  const switches = scope.getByRole('switch')
  await expect(switches).toHaveCount(8)
  const master = switches.first()
  const kind = switches.nth(1)
  await expect(master).toBeChecked()
  const initialKind = await kind.isChecked()
  for (const checked of [false, true]) {
    await kind.setChecked(checked)
    await expect(kind).toBeChecked({ checked })
    fixture.focusChecks.push(await verifyKeyboardFocus(page, kind, `${label} enabled ${checked}`))
    await verifyGalleryControls(fixture, scope, `${label} enabled ${checked}`)
    const track = await kind.boundingBox()
    const thumb = await kind.locator('[data-slot="switch-thumb"]').boundingBox()
    const inlineEnd = fixture.locale === 'ar' ? thumb!.x + thumb!.width / 2 < track!.x + track!.width / 2 : thumb!.x + thumb!.width / 2 > track!.x + track!.width / 2
    expect(inlineEnd, `${label} thumb represents checked state`).toBe(checked)
    fixture.focusChecks.push(await verifyKeyboardFocus(page, kind, `${label} captured ${checked}`))
    await captureGallery(fixture, `${label}-enabled-${checked}`, scope)
  }
  await master.setChecked(false)
  for (const target of await switches.all()) { await expect(target).not.toBeChecked() }
  for (const target of (await switches.all()).slice(1)) { await expect(target).toBeDisabled() }
  await verifyGalleryControls(fixture, scope, `${label} disabled`)
  await master.focus()
  await page.keyboard.press('Tab')
  expect(await switches.evaluateAll(elements => elements.some(element => element.matches(':disabled') && element === document.activeElement))).toBe(false)
  await captureGallery(fixture, `${label}-disabled`, scope)
  await master.setChecked(true)
  await kind.setChecked(initialKind)
  for (const target of await switches.all()) { await expect(target).toBeEnabled() }
}

export async function captureGallery(fixture: GalleryFixture, name: string, scope: Locator) {
  const { page, app } = fixture
  await expect(scope).toBeVisible()
  expect(await scope.evaluate(element => element.scrollWidth - element.clientWidth), name).toBeLessThanOrEqual(1)
  await page.evaluate(async () => { await document.fonts.ready; await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))) })
  const viewport = await page.evaluate(() => ({ width: innerWidth, height: innerHeight, devicePixelRatio }))
  const bytes = Buffer.from(await app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')), 'base64')
  const pixelSize = { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) }
  expect(pixelSize).toEqual({ width: viewport.width * viewport.devicePixelRatio, height: viewport.height * viewport.devicePixelRatio })
  writeFileSync(test.info().outputPath(`${name}.png`), bytes)
  fixture.captures.push({ name: `${name}.png`, route: page.url(), locale: fixture.locale, viewport, pixelSize,
    theme: await page.locator('html').getAttribute('data-hermes-theme'), mode: await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme),
    synthetic: true, sha256: createHash('sha256').update(bytes).digest('hex') })
}

export function writeGalleryEvidence(fixture: GalleryFixture, group: string) {
  expect(fixture.mock.receivedPrompts).toHaveLength(fixture.seedProviderCalls)
  expect(fixture.methods.every(method => ['organization.descriptor', 'organization.snapshot', 'organization.outcomes', 'organization.attention'].includes(method))).toBe(true)
  writeFileSync(test.info().outputPath(`workspace-${group}-gallery-evidence.json`), JSON.stringify({
    sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
    sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
    scenario: `synthetic-workspace-${group}-gallery`, locale: fixture.locale,
    seedProviderCalls: fixture.seedProviderCalls, galleryProviderCalls: fixture.mock.receivedPrompts.length - fixture.seedProviderCalls,
    organizationMethods: [...new Set(fixture.methods)], captures: fixture.captures, focusChecks: fixture.focusChecks, thumbChecks: fixture.thumbChecks
  }, null, 2))
}
