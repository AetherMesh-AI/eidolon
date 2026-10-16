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
  let sessionId: string | undefined
  const fixture = await setupMockBackend({
    isolateModelCatalog: true,
    extraDisplayConfig: `  language: ${locale}`,
    beforeLaunch: seedHistory ? async (sandbox, mock) => {
      const builder = await RealSessionBuilder.start(sandbox.hermesHome)
      try {
        sessionId = (await builder.createSession({ title: 'Synthetic gallery history', turns: ['Synthetic history for UI review. No external work is requested.'] })).sessionId
      } finally {
        await builder.close()
      }
      expect(mock.receivedPrompts).toHaveLength(1)
    } : undefined
  })
  try {
    const { page } = fixture
    await waitForAppReady(fixture)
    const methods: string[] = []
    const observationReads: string[] = []
    const state = { snapshot: structuredClone(snapshot), observationError: null as string | null }
    state.snapshot.activity[1].text = 'سجل تجريبي للمراجعة · Synthetic record'
    await page.routeWebSocket(/.*/, socket => {
      const server = socket.connectToServer()
      socket.onMessage(message => {
        const request = JSON.parse(message.toString()) as { id?: number; method?: string; params?: { method?: string } }
        if (request.method?.startsWith('organization.')) { methods.push(request.method) }
        if ((request.method === 'organization.observe' || request.method === 'organization.snapshot') && state.observationError) {
          socket.send(JSON.stringify({ jsonrpc: '2.0', id: request.id, error: { code: -32000, message: state.observationError } }))
        } else if (request.method === 'organization.observe') {
          socket.send(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: { capabilityVersion: 1, state: 'available', scope: { kind: 'served-profile', profile: 'default' }, snapshot: state.snapshot } }))
        } else if (request.method === 'organization.observeRead') {
          observationReads.push(request.params?.method ?? '')
          if (request.params?.method === 'organization.outcomes') {
            socket.send(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: { capabilityVersion: 1, state: 'available', scope: { kind: 'served-profile', profile: 'default' }, data: state.snapshot.outcomes } }))
          } else { server.send(message) }
        } else if (request.method === 'organization.snapshot' || request.method === 'organization.outcomes') {
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
    const failures: { label: string; error: string }[] = []
    return { ...fixture, locale, sessionId, methods, observationReads, state, captures, focusChecks, thumbChecks, failures, seedProviderCalls: seedHistory ? 1 : 0 }
  } catch (error) {
    await fixture.cleanup()
    throw error
  }
}

export type GalleryFixture = Awaited<ReturnType<typeof setupGallery>>

/** Retain every failure while inspecting independent destinations in the same bounded run. */
export async function checkGalleryDestination(fixture: GalleryFixture, label: string, check: () => Promise<void>) {
  try { await check() } catch (error) {
    fixture.failures.push({ label, error: error instanceof Error ? error.stack ?? error.message : String(error) })
  }
}

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
  // Eidolon intentionally stays dark in both appearance preferences. Use the
  // retained AetherMesh light palette to exercise actual light surfaces.
  const theme = mode === 'dark' ? { label: 'Eidolon', id: 'eidolon' } : { label: 'AetherMesh', id: 'nous' }
  await fixture.page.getByRole('button').filter({ has: fixture.page.getByText(theme.label, { exact: true }) }).click()
  await fixture.page.getByRole('button', { name: label, exact: true }).click()
  await expect(fixture.page.locator('html')).toHaveAttribute('data-hermes-theme', theme.id)
  await expect.poll(() => fixture.page.locator('html').evaluate(element => element.classList.contains('dark'))).toBe(mode === 'dark')
}

export async function verifyGalleryControls(fixture: GalleryFixture, scope: Locator, label: string) {
  const candidates = scope.locator('button:visible, a[href]:visible, input:not([type="hidden"]):visible, select:visible, textarea:visible, summary:visible')
  const count = await candidates.count()
  const controls: { index: number; target: Locator }[] = []
  // Only the first and last eligible controls are exercised. Do not serialize
  // metadata reads for hundreds of catalog buttons that the check never uses.
  for (const direction of [1, -1]) {
    for (let index = direction === 1 ? 0 : count - 1; index >= 0 && index < count; index += direction) {
      const target = candidates.nth(index)
      if (!await target.isEnabled() || await target.getAttribute('tabindex') === '-1') { continue }
      const placeholder = await target.getAttribute('placeholder')
      const stable = placeholder ? scope.getByPlaceholder(placeholder, { exact: true }) : target
      if (!controls.some(control => control.index === index)) {
        const selected = await stable.count() === 1 ? stable : target
        // Pin this DOM node. Positional locators may silently rebind when a
        // background catalog query inserts buttons during keyboard traversal.
        const identity = `${label}-${index}`
        await selected.evaluate((element, key) => element.setAttribute('data-gallery-focus-identity', key), identity)
        const pinned = scope.locator(`[data-gallery-focus-identity=${JSON.stringify(identity)}]`)
        await expect(pinned).toHaveCount(1)
        controls.push({ index, target: pinned })
      }
      break
    }
  }
  for (const { index, target } of controls) {
    fixture.focusChecks.push(await verifyKeyboardFocus(fixture.page, target, `${label} control ${index}`, 'previous'))
    await expect(target).toBeInViewport({ ratio: 1 })
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
  let capturedBase64 = ''
  let nativeFrameAttempts = 0
  // DOM readiness does not guarantee Electron's compositor has painted the
  // native frame yet. Reject uniform frames, then save the exact NativeImage
  // that passed this check rather than taking a second, unchecked capture.
  await expect.poll(async () => {
    nativeFrameAttempts += 1
    capturedBase64 = await app.evaluate(async ({ BrowserWindow }) => {
      const frame = await BrowserWindow.getAllWindows()[0].webContents.capturePage()
      const bitmap = frame.toBitmap()
      // NativeImage exposes four bytes per pixel. Compare the three color
      // channels only; platform channel order does not affect uniformity.
      for (let offset = 4; offset + 3 < bitmap.length; offset += 4) {
        if (bitmap[offset] !== bitmap[0] || bitmap[offset + 1] !== bitmap[1] || bitmap[offset + 2] !== bitmap[2]) {
          return frame.toPNG().toString('base64')
        }
      }
      return ''
    })
    return capturedBase64.length > 0
  }, { message: `${name}: native capture must contain painted content`, timeout: 5_000 }).toBe(true)
  const bytes = Buffer.from(capturedBase64, 'base64')
  const pixelSize = { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) }
  expect(pixelSize).toEqual({ width: viewport.width * viewport.devicePixelRatio, height: viewport.height * viewport.devicePixelRatio })
  writeFileSync(test.info().outputPath(`${name}.png`), bytes)
  fixture.captures.push({ name: `${name}.png`, route: page.url(), locale: fixture.locale, viewport, pixelSize,
    theme: await page.locator('html').getAttribute('data-hermes-theme'), mode: await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme),
    synthetic: true, nativeFrameAttempts, sha256: createHash('sha256').update(bytes).digest('hex') })
}

/** Initial composition captures start at the page's beginning; focus captures keep actual keyboard scrolling. */
export async function positionGalleryAtStart(scope: Locator) {
  await scope.evaluate(element => {
    const containers = new Set<Element>([element, ...element.querySelectorAll('*')])
    for (let parent = element.parentElement; parent; parent = parent.parentElement) { containers.add(parent) }
    for (const container of containers) {
      if (container instanceof HTMLElement) { container.scrollTop = 0; container.scrollLeft = 0 }
    }
  })
}

export function persistGalleryEvidence(fixture: GalleryFixture, group: string) {
  writeFileSync(test.info().outputPath(`workspace-${group}-gallery-evidence.json`), JSON.stringify({
    sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
    sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
    scenario: `synthetic-workspace-${group}-gallery`, locale: fixture.locale,
    seedProviderCalls: fixture.seedProviderCalls, galleryProviderCalls: fixture.mock.receivedPrompts.length - fixture.seedProviderCalls,
    organizationMethods: [...new Set(fixture.methods)], observationReads: [...new Set(fixture.observationReads)], captures: fixture.captures, focusChecks: fixture.focusChecks, thumbChecks: fixture.thumbChecks, failures: fixture.failures
  }, null, 2))
}

export function writeGalleryEvidence(fixture: GalleryFixture, group: string) {
  persistGalleryEvidence(fixture, group)
  expect(fixture.mock.receivedPrompts).toHaveLength(fixture.seedProviderCalls)
  expect(fixture.methods.every(method => ['organization.descriptor', 'organization.snapshot', 'organization.outcomes', 'organization.attention', 'organization.observe', 'organization.observeRead', 'organization.intake.list'].includes(method))).toBe(true)
  expect(fixture.observationReads.every(method => ['organization.outcomes', 'organization.attention'].includes(method))).toBe(true)
  expect(fixture.failures, 'Every destination must pass its original assertions').toEqual([])
}
