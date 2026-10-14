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

    for (const [index, target] of targets.entries()) {
      await expect(target.locator).toHaveCount(1)
      await target.locator.evaluate(element => element.scrollIntoView({ block: 'center', inline: 'nearest' }))
      await expect.poll(() => target.locator.evaluate(element => {
        const rect = element.getBoundingClientRect()
        const label = element.closest('label')?.getBoundingClientRect()
        const point = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)

        return rect.width > 0 && rect.height > 0 && rect.left >= 0 && rect.right <= window.innerWidth + 1 &&
          rect.top >= 0 && rect.bottom <= window.innerHeight + 1 &&
          (!label || (label.top >= 0 && label.bottom <= window.innerHeight + 1 && label.left >= 0 && label.right <= window.innerWidth + 1)) &&
          element.scrollWidth <= element.clientWidth + 1 && !!point && (point === element || element.contains(point))
      })).toBe(true)

      if (await target.locator.evaluate(element => element.matches('input,select,button,[role=checkbox]'))) {
        focus.push({ zoom, ...(await verifyKeyboardFocus(page, target.locator, target.name)) })
      }

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
