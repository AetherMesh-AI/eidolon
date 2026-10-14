/** Native pixels for owner policy forms, with exact source and receipt provenance. */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'

import { type ElectronApplication, expect, type Locator, type Page, type TestInfo } from '@playwright/test'

import { verifyKeyboardFocus } from './focus-visibility'

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

      await expect.poll(targetIsVisible).toBe(true)

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

      if (await target.locator.evaluate(element => element.matches('input,select,button,[role=checkbox]'))) {
        focus.push({ zoom, ...(await verifyKeyboardFocus(page, target.locator, target.name)) })
      }

      // Layout can precede the native compositor. Settle rendered frames
      // after keyboard scrolling as well as programmatic scrolling.
      await expect.poll(targetIsVisible).toBe(true)
      await page.evaluate(async () => {
        await document.fonts.ready
        await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
      })
      await expect.poll(targetIsVisible).toBe(true)

      const file = `${scenario}-${zoom}-${index + 1}.png`
      const nativeWindow = await app.browserWindow(page)
      const encoded = await nativeWindow.evaluate(async native => (await native.webContents.capturePage()).toPNG().toString('base64'))
      await writeFile(testInfo.outputPath(file), Buffer.from(encoded, 'base64'))
      captures.push({ zoom, target: target.name, file, sha256: createHash('sha256').update(await readFile(testInfo.outputPath(file))).digest('hex') })
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
