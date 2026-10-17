import type { Locator, Page } from '@playwright/test'

import { requestReadPositionPreserved } from './request-read-position'
import { expect, test } from './test'

/** Measure the focused control in its workspace scrollport, not the header. */
export async function requestReadGeometry(target: Locator) {
  return target.evaluate(element => {
    const rect = element.getBoundingClientRect()
    const workspace = element.closest('.eid-workspace')!
    const bounds = workspace.getBoundingClientRect()

    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, focused: document.activeElement === element,
      devicePixelRatio, workspace: { x: bounds.x, y: bounds.y, height: bounds.height,
        scrollTop: workspace.scrollTop, scrollLeft: workspace.scrollLeft, scrollHeight: workspace.scrollHeight } }
  })
}

export async function expectRequestReadPosition(target: Locator, before: Awaited<ReturnType<typeof requestReadGeometry>>, label: string) {
  const after = await requestReadGeometry(target)
  await test.info().attach(`${label}-read-position`, { contentType: 'application/json', body: JSON.stringify({ before, after }, null, 2) })

  expect(requestReadPositionPreserved(before, after), `${label}: retain local position within one native physical pixel`).toBe(true)
  await expect(target).toBeInViewport({ ratio: 1 })
}

/** Sample the trusted activation before React opens details, rather than before capture/scroll settling. */
export async function openRequestAtNativeDeparture(page: Page, origin: Locator, preparedTop: number) {
  await expect(origin).toBeFocused()
  await origin.evaluate(element => {
    element.removeAttribute('data-inspection-departure')
    element.addEventListener('click', event => {
      if (!event.isTrusted) { return }
      const workspace = element.closest('.eid-workspace')!
      const rect = element.getBoundingClientRect()
      element.setAttribute('data-inspection-departure', JSON.stringify({
        top: workspace.scrollTop, left: workspace.scrollLeft, trusted: event.isTrusted,
        detail: (event as MouseEvent).detail, origin: { top: rect.top, bottom: rect.bottom, height: rect.height },
        viewport: { width: innerWidth, height: innerHeight, zoom: devicePixelRatio }
      }))
    }, { once: true, capture: true })
  })
  await page.keyboard.press('Enter')
  const value = await origin.getAttribute('data-inspection-departure')
  expect(value, 'Trusted request activation must establish the return baseline').not.toBeNull()
  const departure = JSON.parse(value!) as { top: number; left: number; trusted: boolean }
  expect(departure.trusted).toBe(true)

  return { preparedTop, ...departure }
}

export async function expectRequestScrollReturn(workspace: Locator, origin: Locator, departure: Awaited<ReturnType<typeof openRequestAtNativeDeparture>>, label: string) {
  try {
    await expect.poll(() => workspace.evaluate(element => element.scrollTop)).toBeCloseTo(departure.top, 0)
    await expect.poll(() => workspace.evaluate(element => element.scrollLeft)).toBeCloseTo(departure.left, 0)
  } finally {
    const returned = await origin.evaluate(element => {
      const workspace = element.closest('.eid-workspace')!
      const rect = element.getBoundingClientRect()

      return { top: workspace.scrollTop, left: workspace.scrollLeft, contentHeight: workspace.scrollHeight,
        height: workspace.clientHeight, focused: element === document.activeElement,
        origin: { top: rect.top, bottom: rect.bottom, height: rect.height } }
    })

    await test.info().attach(`${label}-inspection-return`, { contentType: 'application/json', body: JSON.stringify({ departure, returned }, null, 2) })
  }
}
