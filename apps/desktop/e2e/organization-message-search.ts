import type { Page } from '@playwright/test'

import { expect } from './test'

/** Real rendered search geometry; no component or bridge substitution. */
export async function verifyMemberSearchGeometry(page: Page) {
  const geometry = await page.locator('.eid-message-search').evaluate(element => {
    const directory = element.closest('.eid-message-members')!
    const style = getComputedStyle(directory)
    const availableWidth = directory.clientWidth - Number.parseFloat(style.paddingLeft) - Number.parseFloat(style.paddingRight)
    const box = (node: Element) => node.getBoundingClientRect().toJSON()

    return { availableWidth, field: box(element), input: box(element.querySelector('input')!), overflow: element.scrollWidth - element.clientWidth }
  })

  expect(geometry.field.width, 'search fills its member column').toBeGreaterThanOrEqual(geometry.availableWidth - 1)
  expect(geometry.field.width).toBeLessThanOrEqual(geometry.availableWidth + 1)
  expect(geometry.input.width, 'input fills space beside search and clear icons').toBeGreaterThanOrEqual(geometry.field.width - 70)
  expect(geometry.overflow, 'long queries stay inside the field').toBeLessThanOrEqual(1)

  return geometry
}
