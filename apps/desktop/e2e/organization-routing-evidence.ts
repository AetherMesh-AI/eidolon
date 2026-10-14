/** Real HTTP routing, ledger reservations and native audit pixels must agree. */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'

import { type ElectronApplication, expect, type Locator, type Page, type TestInfo } from '@playwright/test'

export interface NativeModelDispatch {
  id: string
  requestId: string
  routeId: string
  mode: string
  selectedProvider: string
  adapterProvider: string
  model: string
  reasoning: string
  pricing: { inputUsdPerMillion: string; outputUsdPerMillion: string }
}

export interface NativeRoutingRequest {
  id: string
  routeDispatch?: NativeModelDispatch | null
}

export interface NativeRoutingAudit {
  requestId: string
  modelDispatches: NativeModelDispatch[]
  modelCalls: Array<{ id: string; dispatchId: string | null; provider: string; model: string; input_limit: number; output_limit: number; reserved_cost_usd: string | null }>
}

function nanodollars(value: string) {
  expect(value).toMatch(/^\d+(?:\.\d{1,9})?$/)
  const [whole, fraction = ''] = value.split('.')

  return BigInt(whole) * 1_000_000_000n + BigInt(fraction.padEnd(9, '0'))
}

export function assertEconomyDispatch(audit: NativeRoutingAudit, request: NativeRoutingRequest) {
  expect(audit.requestId).toBe(request.id)
  expect(audit.modelDispatches).toHaveLength(1)
  const dispatch = audit.modelDispatches[0]
  expect(dispatch).toMatchObject({ requestId: request.id, routeId: 'economy', mode: 'auto', selectedProvider: 'custom', adapterProvider: 'custom', model: 'mock-economy', reasoning: 'none' })
  expect(dispatch.id).toBe(request.routeDispatch?.id)
  expect(nanodollars(dispatch.pricing.inputUsdPerMillion)).toBe(1_000_000_000n)
  expect(nanodollars(dispatch.pricing.outputUsdPerMillion)).toBe(2_000_000_000n)
  expect(audit.modelCalls.length).toBeGreaterThan(0)

  for (const call of audit.modelCalls) {
    expect(call).toMatchObject({ dispatchId: dispatch.id, provider: dispatch.selectedProvider, model: dispatch.model })
    expect(call.input_limit).toBeGreaterThan(0)
    expect(call.output_limit).toBeGreaterThan(0)
    expect(call.output_limit).toBeLessThanOrEqual(2048)
    const numerator = BigInt(call.input_limit) * nanodollars(dispatch.pricing.inputUsdPerMillion) + BigInt(call.output_limit) * nanodollars(dispatch.pricing.outputUsdPerMillion)
    expect(call.reserved_cost_usd).not.toBeNull()
    expect(nanodollars(call.reserved_cost_usd!)).toBe((numerator + 999_999n) / 1_000_000n)
  }
}

export async function captureRoutingAudit(page: Page, app: ElectronApplication, region: Locator, testInfo: TestInfo, audit: NativeRoutingAudit) {
  const captures: Array<{ zoom: number; file: string; sha256: string }> = []

  const setZoom = async (percent: number) => {
    await page.evaluate(value => {
      const desktop = window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }
      desktop.hermesDesktop.zoom.setPercent(value)
    }, percent)
    await expect.poll(() => page.evaluate(async () => {
      const desktop = window as unknown as { hermesDesktop: { zoom: { get(): Promise<{ percent: number }> } } }

      return (await desktop.hermesDesktop.zoom.get()).percent
    })).toBe(percent)
  }

  for (const zoom of [100, 200]) {
    await setZoom(zoom)
    const parts = region.locator('dd, p, time')
    await expect(parts).toHaveCount(17)

    for (let index = 0; index < await parts.count(); index += 1) {
      const part = parts.nth(index)
      await part.evaluate(element => element.scrollIntoView({ block: 'center', inline: 'nearest' }))
      await expect.poll(() => part.evaluate(element => {
        const rect = element.getBoundingClientRect()
        const label = element.tagName === 'DD' ? element.previousElementSibling?.getBoundingClientRect() : null
        const point = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)

        return rect.left >= 0 && rect.right <= window.innerWidth + 1 && rect.top >= 0 && rect.bottom <= window.innerHeight + 1 &&
          (!label || label.top >= 0) && element.scrollWidth <= element.clientWidth + 1 && !!point && (point === element || element.contains(point))
      })).toBe(true)
      const file = `model-routing-${zoom}-part-${index + 1}.png`
      const nativeWindow = await app.browserWindow(page)
      await writeFile(testInfo.outputPath(file), Buffer.from(await nativeWindow.evaluate(async window => (await window.webContents.capturePage()).toPNG().toString('base64')), 'base64'))
      captures.push({ zoom, file, sha256: createHash('sha256').update(await readFile(testInfo.outputPath(file))).digest('hex') })
    }
  }

  await setZoom(100)
  await testInfo.attach('model-routing-native-evidence', {
    body: JSON.stringify({ sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(), sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(), scenario: 'actual-loopback-provider-fixed-auto-routing', audit, captures }, null, 2),
    contentType: 'application/json'
  })
}
