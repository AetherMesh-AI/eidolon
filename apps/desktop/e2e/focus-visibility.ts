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
    const rect = element.getBoundingClientRect()
    const outset = Number.parseFloat(style.outlineWidth) + Number.parseFloat(style.outlineOffset)
    const ring = { left: rect.left - outset, top: rect.top - outset, right: rect.right + outset, bottom: rect.bottom + outset }
    const clipping: string[] = []
    if (ring.left < 0 || ring.top < 0 || ring.right > innerWidth || ring.bottom > innerHeight) clipping.push('viewport')
    const backgrounds: string[] = []
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      const ancestorStyle = getComputedStyle(parent)
      backgrounds.push(ancestorStyle.backgroundColor)
      const bounds = parent.getBoundingClientRect()
      const clips = (overflow: string) => ['auto', 'scroll', 'hidden', 'clip'].includes(overflow)
      if ((clips(ancestorStyle.overflowX) && (ring.left < bounds.left + parent.clientLeft - .5 || ring.right > bounds.left + parent.clientLeft + parent.clientWidth + .5)) ||
          (clips(ancestorStyle.overflowY) && (ring.top < bounds.top + parent.clientTop - .5 || ring.bottom > bounds.top + parent.clientTop + parent.clientHeight + .5))) {
        clipping.push(`${parent.tagName}.${parent.className}`)
      }
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
    return { clipping, ring, visible: element.matches(':focus-visible'), style: style.outlineStyle, width: Number.parseFloat(style.outlineWidth), offset: Number.parseFloat(style.outlineOffset), color: style.outlineColor, background, outline, contrast: (high + .05) / (low + .05) }
  })
  expect(evidence.clipping, `${label}: complete outline inside clipping ancestors`).toEqual([])
  expect(evidence.visible, label).toBe(true)
  expect(evidence.style, label).toBe('solid')
  expect(evidence.width, label).toBeGreaterThanOrEqual(2)
  expect(evidence.offset, label).toBeGreaterThanOrEqual(2)
  expect(evidence.contrast, label).toBeGreaterThanOrEqual(3)
  return { label, ...evidence }
}
