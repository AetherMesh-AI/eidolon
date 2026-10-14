/** Native pixels for owner policy forms, with exact source and receipt provenance. */
import { execFileSync } from 'node:child_process'
import { createHash, randomBytes } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'

import { type ElectronApplication, expect, type Locator, type Page, type TestInfo } from '@playwright/test'

import { verifyKeyboardFocus } from './focus-visibility'
import { captureCausalOwnerFrame } from './organization-native-owner-frame'
import { ownerMarkerPosition } from './organization-owner-marker-position'

export interface OwnerPolicyCaptureTarget {
  name: string
  locator: Locator
}

export async function captureOwnerPolicy(
  page: Page,
  app: ElectronApplication,
  targets: OwnerPolicyCaptureTarget[],
  scenario: string,
  testInfo: TestInfo,
  receipt: unknown
) {
  expect(targets.length).toBeGreaterThan(0)
  expect(new Set(targets.map(target => target.name)).size).toBe(targets.length)
  const focus: unknown[] = []
  const captures: Array<{ zoom: number; target: string; file: string; sha256: string }> = []

  for (const zoom of [100, 200]) {
    await page.evaluate(value => {
      const desktop = window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }
      desktop.hermesDesktop.zoom.setPercent(value)
    }, zoom)
    await expect.poll(() => page.evaluate(async () => {
      const desktop = window as unknown as { hermesDesktop: { zoom: { get(): Promise<{ percent: number }> } } }

      return (await desktop.hermesDesktop.zoom.get()).percent
    })).toBe(zoom)
    // Zoom IPC acknowledgement can precede renderer relayout. Settle the
    // renderer before choosing scroll coordinates at the new zoom level.
    await page.evaluate(async () => {
      await document.fonts.ready
      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
    })

    for (const [index, target] of targets.entries()) {
      await expect(target.locator).toHaveCount(1)
      await target.locator.evaluate(element => element.scrollIntoView({ block: 'center', inline: 'nearest' }))

      const targetIsVisible = () => target.locator.evaluate(element => {
        const rect = element.getBoundingClientRect()
        const label = element.closest('label')?.getBoundingClientRect()
        const point = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)

        return rect.width > 0 && rect.height > 0 && rect.left >= 0 && rect.right <= window.innerWidth + 1 &&
          rect.top >= 0 && rect.bottom <= window.innerHeight + 1 &&
          (!label || (label.top >= 0 && label.bottom <= window.innerHeight + 1 && label.left >= 0 && label.right <= window.innerWidth + 1)) &&
          element.scrollWidth <= element.clientWidth + 1 && !!point && (point === element || element.contains(point))
      })

      const assertVisible = async () => {
        try {
          await expect.poll(targetIsVisible).toBe(true)
        } catch (error) {
          // Keep the strict guard. Failure diagnostics need native pixels too:
          // Playwright screenshots can use mismatched bounds at Electron zoom.
          const geometry = await target.locator.evaluate(element => {
            const rect = element.getBoundingClientRect()
            const label = element.closest('label')
            const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)
            const parents = []

            for (let parent = element.parentElement; parent; parent = parent.parentElement) {
              const style = getComputedStyle(parent)
              parents.push({ tag: parent.tagName, className: parent.className,
                rect: parent.getBoundingClientRect().toJSON(), clientWidth: parent.clientWidth,
                scrollWidth: parent.scrollWidth, scrollLeft: parent.scrollLeft, scrollTop: parent.scrollTop,
                direction: style.direction, overflowX: style.overflowX, overflowY: style.overflowY })
            }

            return { rect: rect.toJSON(), label: label?.getBoundingClientRect().toJSON(),
              viewport: { width: innerWidth, height: innerHeight, devicePixelRatio },
              clientWidth: element.clientWidth, scrollWidth: element.scrollWidth,
              hit: hit ? { tag: hit.tagName, className: hit.className, isTarget: hit === element || element.contains(hit) } : null,
              parents }
          })
          const nativeWindow = await app.browserWindow(page)
          const encoded = await nativeWindow.evaluate(async native => (await native.webContents.capturePage()).toPNG().toString('base64'))
          const file = `${scenario}-${zoom}-${index + 1}-visibility-failure.png`
          const bytes = Buffer.from(encoded, 'base64')
          await writeFile(testInfo.outputPath(file), bytes)
          await testInfo.attach(`${scenario}-${zoom}-${target.name}-visibility-failure`, {
            contentType: 'application/json', body: JSON.stringify({ scenario, zoom, target: target.name,
              sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
              sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
              file, sha256: createHash('sha256').update(bytes).digest('hex'), geometry }, null, 2)
          })
          throw error
        }
      }

      await assertVisible()

      if (await target.locator.evaluate(element => element.matches('input,select'))) {
        expect(await target.locator.evaluate(element => element.getBoundingClientRect().height),
          `${target.name}: single-line control must not stretch beside a tall fieldset`).toBeLessThanOrEqual(64)
      }

      if (await target.locator.evaluate(element => element.getAttribute('role') === 'checkbox')) {
        await target.locator.evaluate(async element => {
          (element as HTMLElement).blur()
          await Promise.all(element.getAnimations().filter(animation =>
            Number.isFinite(Number(animation.effect?.getComputedTiming().endTime))
          ).map(animation => animation.finished.catch(() => undefined)))
        })

        const contrast = await target.locator.evaluate(element => {
          const style = getComputedStyle(element)
          const canvas = document.createElement('canvas')
          canvas.width = canvas.height = 1
          const context = canvas.getContext('2d')!
          const surfaces: string[] = []

          for (let parent = element.parentElement; parent; parent = parent.parentElement) {
            surfaces.unshift(getComputedStyle(parent).backgroundColor)
          }

          const paint = (colors: string[]) => {
            context.fillStyle = '#fff'
            context.fillRect(0, 0, 1, 1)

            for (const color of colors) {
              context.fillStyle = color
              context.fillRect(0, 0, 1, 1)
            }

            return [...context.getImageData(0, 0, 1, 1).data]
          }

          const luminance = (color: number[]) => {
            const linear = color.slice(0, 3).map(channel => {
              const value = channel / 255

              return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4
            })

            return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]
          }

          const ratio = (a: number[], b: number[]) => {
            const [low, high] = [luminance(a), luminance(b)].sort((a, b) => a - b)

            return (high + .05) / (low + .05)
          }

          const background = paint(surfaces)
          const fill = paint([...surfaces, style.backgroundColor])
          const boundary = paint([...surfaces, style.backgroundColor, style.borderTopColor])
          const checked = element.getAttribute('aria-checked') === 'true'
          const indicator = element.querySelector('[data-slot=checkbox-indicator]')
          const mark = paint([...surfaces, style.backgroundColor, indicator ? getComputedStyle(indicator).color : style.color])

          return { checked, boundary: ratio(background, boundary), mark: ratio(fill, mark), borderWidth: Number.parseFloat(style.borderTopWidth), indicatorPresent: !!indicator }
        })

        expect(contrast.borderWidth, `${target.name}: visible checkbox boundary`).toBeGreaterThanOrEqual(1)
        expect(contrast.boundary, `${target.name}: boundary contrast`).toBeGreaterThanOrEqual(3)

        if (contrast.checked) {
          expect(contrast.indicatorPresent).toBe(true)
          expect(contrast.mark, `${target.name}: checked mark contrast`).toBeGreaterThanOrEqual(3)
        }

        focus.push({ zoom, target: target.name, checkboxContrast: contrast })
      }

      const keyboardTarget = await target.locator.evaluate(element => element.matches('input,select,button,[role=checkbox]'))
      const keyboardEvidence = keyboardTarget ? await verifyKeyboardFocus(page, target.locator, target.name) : null
      if (keyboardEvidence) focus.push({ zoom, ...keyboardEvidence })

      // Layout can precede the native compositor. Settle rendered frames
      // after keyboard scrolling as well as programmatic scrolling.
      await assertVisible()
      await page.evaluate(async () => {
        await document.fonts.ready
        await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
      })
      await assertVisible()

      const file = `${scenario}-${zoom}-${index + 1}.png`
      const nativeWindow = await app.browserWindow(page)
      const focusState = () => target.locator.evaluate(element => ({
        active: document.activeElement === element, visible: element.matches(':focus-visible'),
        documentFocused: document.hasFocus(), rect: element.getBoundingClientRect().toJSON(),
        viewport: { width: innerWidth, height: innerHeight }
      }))
      const beforeCapture = await focusState()
      const markerPosition = await page.evaluate(ownerMarkerPosition, beforeCapture.rect)
      expect(markerPosition, 'an empty viewport corner for the test-only causal marker').toBeTruthy()
      const nonce = randomBytes(9)
      const marker = { id: `owner-frame-${nonce.toString('hex')}`, ...markerPosition!,
        colors: [0, 3, 6].map(offset => [...nonce.subarray(offset, offset + 3)].map(channel => 40 + channel % 180)) }
      const capture = await nativeWindow.evaluate(captureCausalOwnerFrame, { state: beforeCapture, keyboard: keyboardEvidence, marker })
      const firstFile = `${scenario}-${zoom}-${index + 1}-first-callback.png`
      let firstSha256: string | null = null
      if (capture.first) {
        const firstBytes = Buffer.from(capture.first.encoded, 'base64')
        await writeFile(testInfo.outputPath(firstFile), firstBytes)
        firstSha256 = createHash('sha256').update(firstBytes).digest('hex')
      }
      let sha256: string | null = null
      if (capture.causal) {
        await writeFile(testInfo.outputPath(file), Buffer.from(capture.causal.encoded, 'base64'))
        sha256 = createHash('sha256').update(await readFile(testInfo.outputPath(file))).digest('hex')
        captures.push({ zoom, target: target.name, file, sha256 })
      }
      const { encoded: _causalEncoded, ...causalMetadata } = capture.causal ?? {}
      const { encoded: _firstEncoded, ...firstMetadata } = capture.first ?? {}
      await testInfo.attach(`${scenario}-${zoom}-${target.name}-frame-provenance`, {
        contentType: 'application/json', body: JSON.stringify({ error: capture.error, beforeCapture,
          windowFocused: capture.windowFocused, contentsFocused: capture.contentsFocused,
          sourceDirty: execFileSync('git', ['status', '--porcelain'], { encoding: 'utf8' }).trim().length > 0,
          sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
          sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
          first: capture.first ? { ...firstMetadata, file: firstFile, sha256: firstSha256 } : null,
          frames: capture.frames, causalSequence: capture.causal?.sequence ?? null,
          causal: capture.causal ? { ...causalMetadata, file, sha256 } : null,
          marker: { ...marker, width: 24, height: 8, disclosure: 'Test-only nonce pixels alter this empty viewport corner; cleanup is attempted in finally and failures are reported.' }
        }, null, 2)
      })
      // Preserve both PNGs and capture metadata BEFORE any renderer query or
      // assertion: a detached/stalled renderer must not erase native evidence.
      let afterCapture: Awaited<ReturnType<typeof focusState>> | null = null
      let afterCaptureError: string | null = null
      let stateTimer: ReturnType<typeof setTimeout> | undefined
      try {
        afterCapture = await Promise.race([focusState(), new Promise<never>((_, reject) => {
          stateTimer = setTimeout(() => reject(new Error('Post-capture focus inspection timed out after 5 seconds')), 5_000)
        })])
      } catch (cause) { afterCaptureError = String(cause) }
      finally { clearTimeout(stateTimer) }
      await testInfo.attach(`${scenario}-${zoom}-${target.name}-capture-state`, {
        contentType: 'application/json', body: JSON.stringify({ file, sha256, beforeCapture, afterCapture, afterCaptureError,
          captureError: capture.error, windowFocused: capture.windowFocused, contentsFocused: capture.contentsFocused,
          sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
          sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
          sourceDirty: execFileSync('git', ['status', '--porcelain'], { encoding: 'utf8' }).trim().length > 0,
          expectedOutline: keyboardEvidence?.outline, expectedOutlineWidth: keyboardEvidence?.width,
          expectedOutlineOffset: keyboardEvidence?.offset, ...causalMetadata }, null, 2)
      })
      expect(capture.error, 'native frame must contain the post-focus causal marker').toBeNull()
      expect(capture.causal).not.toBeNull()
      expect(afterCaptureError).toBeNull()
      expect(afterCapture).not.toBeNull()
      if (!capture.causal || !afterCapture) throw new Error('Required native capture or focus evidence unavailable')
      const nativeCapture = capture.causal
      expect(afterCapture.rect, 'test-only marker must not change target geometry').toEqual(beforeCapture.rect)
      expect(afterCapture.viewport).toEqual(beforeCapture.viewport)
      expect(nativeCapture.size.width).toBeGreaterThan(0)
      expect(nativeCapture.size.height).toBeGreaterThan(0)
      expect(nativeCapture.bitmapBytes).toBe(nativeCapture.size.width * nativeCapture.size.height * 4)
      if (keyboardEvidence) {
        // Assert the FIRST marker-bearing image, even if its ring is absent. Never retry a failed ring.
        expect(beforeCapture.active && beforeCapture.visible, `${target.name}: focus immediately before capture`).toBe(true)
        expect(afterCapture.active && afterCapture.visible, `${target.name}: focus immediately after capture`).toBe(true)
        expect(afterCapture.rect).toEqual(beforeCapture.rect)
        expect(nativeCapture.samples).toHaveLength(4)
        for (const sample of nativeCapture.samples) {
          expect(sample.rgb.some(rgb => rgb.length === 3 && rgb.every((channel, index) =>
            Math.abs(channel - keyboardEvidence.outline[index]) <= 8)),
          `${target.name}: painted ${sample.side} keyboard outline in first causal native capture`).toBe(true)
        }
      }
    }
  }

  await page.evaluate(() => {
    const desktop = window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }
    desktop.hermesDesktop.zoom.setPercent(100)
  })
  await testInfo.attach(`${scenario}-native-evidence`, {
    contentType: 'application/json',
    body: JSON.stringify({
      sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
      sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
      sourceDirty: execFileSync('git', ['status', '--porcelain'], { encoding: 'utf8' }).trim().length > 0,
      scenario,
      receipt,
      captures,
      focus
    }, null, 2)
  })
}
