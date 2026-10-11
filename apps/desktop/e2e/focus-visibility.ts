import type { Locator, Page } from '@playwright/test'

import { expect } from './test'

/** Measure the actual outline against the painted ancestor surfaces in Chromium. */
export async function verifyKeyboardFocus(page: Page, target: Locator, label: string) {
  await target.focus()
  await page.keyboard.press('Tab')
  await page.keyboard.press('Shift+Tab')
  await expect(target).toBeFocused()
  const evidence = await target.evaluate(element => {
    const style = getComputedStyle(element)
    const backgrounds: string[] = []
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      backgrounds.push(getComputedStyle(parent).backgroundColor)
    }
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 1
    const context = canvas.getContext('2d')!
    context.fillStyle = '#fff'
    context.fillRect(0, 0, 1, 1)
    for (const color of backgrounds.reverse()) {
      context.fillStyle = color
      context.fillRect(0, 0, 1, 1)
    }
    const background = [...context.getImageData(0, 0, 1, 1).data]
    context.fillStyle = style.outlineColor
    context.fillRect(0, 0, 1, 1)
    const outline = [...context.getImageData(0, 0, 1, 1).data]
    const luminance = (color: number[]) => {
      const linear = color.slice(0, 3).map(channel => {
        const value = channel / 255
        return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4
      })
      return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]
    }
    const [low, high] = [luminance(background), luminance(outline)].sort((a, b) => a - b)
    return { visible: element.matches(':focus-visible'), style: style.outlineStyle, width: Number.parseFloat(style.outlineWidth), offset: Number.parseFloat(style.outlineOffset), color: style.outlineColor, background, outline, contrast: (high + .05) / (low + .05) }
  })
  expect(evidence.visible, label).toBe(true)
  expect(evidence.style, label).toBe('solid')
  expect(evidence.width, label).toBeGreaterThanOrEqual(2)
  expect(evidence.offset, label).toBeGreaterThanOrEqual(2)
  expect(evidence.contrast, label).toBeGreaterThanOrEqual(3)
  return { label, ...evidence }
}
