import type { Locator, Page } from '@playwright/test'

import { expect, test } from './test'

/** Measure the actual outline against the painted ancestor surfaces in Chromium. */
export async function verifyKeyboardFocus(page: Page, target: Locator, label: string, approach: 'next' | 'previous' = 'next') {
  await target.focus()
  await page.keyboard.press(approach === 'next' ? 'Tab' : 'Shift+Tab')
  await page.keyboard.press(approach === 'next' ? 'Shift+Tab' : 'Tab')

  return expectKeyboardFocusVisible(target, label)
}

/** Assert the current focus without refocusing, scrolling, or traversing again. */
export async function expectKeyboardFocusVisible(target: Locator, label: string) {
  await expect(target).toBeFocused()
  const evidence = await measureKeyboardFocus(target)
  if (evidence.clipping.length) {
    await test.info().attach('focus-clipping-geometry', { body: JSON.stringify({ label, ...evidence }, null, 2), contentType: 'application/json' })
    // Observe after a subsequent RAF callback without repairing focus or scroll. The original
    // failing sample remains authoritative even if the later layout is clear.
    let afterFrame: Awaited<ReturnType<typeof measureKeyboardFocus>> | null = null
    let frameObserved = false
    try {
      // This timer bounds only the in-page RAF wait. Browser transport and
      // geometry evaluation retain the existing test deadline.
      frameObserved = await target.evaluate(() => new Promise<boolean>(resolve => {
        const timer = setTimeout(() => {
          cancelAnimationFrame(frame)
          resolve(false)
        }, 100)
        const frame = requestAnimationFrame(() => {
          clearTimeout(timer)
          resolve(true)
        })
      }))
      if (frameObserved) {afterFrame = await measureKeyboardFocus(target)}
      await test.info().attach('focus-clipping-next-frame', { body: JSON.stringify({ label, frameObserved, afterFrame }, null, 2), contentType: 'application/json' })
    } catch { /* Optional observation/attachment must not mask the first failure. */ }
  }
  expect(evidence.clipping, `${label}: complete outline inside clipping ancestors`).toEqual([])
  expect(evidence.visible, label).toBe(true)
  expect(evidence.style, label).toBe('solid')
  expect(evidence.width, label).toBeGreaterThanOrEqual(2)
  expect(evidence.offset, label).toBeGreaterThanOrEqual(2)
  expect(evidence.contrast, label).toBeGreaterThanOrEqual(3)
  return { label, ...evidence }
}

async function measureKeyboardFocus(target: Locator) {
  return target.evaluate(element => {
    const style = getComputedStyle(element)
    const rect = element.getBoundingClientRect()
    const outset = Number.parseFloat(style.outlineWidth) + Number.parseFloat(style.outlineOffset)
    const ring = { left: rect.left - outset, top: rect.top - outset, right: rect.right + outset, bottom: rect.bottom + outset }
    const clipping: string[] = []
    const clippingBounds: { ancestor: string, left: number, top: number, right: number, bottom: number, scrollTop: number, scrollHeight: number, clientHeight: number }[] = []
    if (ring.left < 0 || ring.top < 0 || ring.right > innerWidth || ring.bottom > innerHeight) {clipping.push('viewport')}
    const backgrounds: string[] = []
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      const ancestorStyle = getComputedStyle(parent)
      backgrounds.push(ancestorStyle.backgroundColor)
      const bounds = parent.getBoundingClientRect()
      const clips = (overflow: string) => ['auto', 'scroll', 'hidden', 'clip'].includes(overflow)
      if ((clips(ancestorStyle.overflowX) && (ring.left < bounds.left + parent.clientLeft - .5 || ring.right > bounds.left + parent.clientLeft + parent.clientWidth + .5)) ||
          (clips(ancestorStyle.overflowY) && (ring.top < bounds.top + parent.clientTop - .5 || ring.bottom > bounds.top + parent.clientTop + parent.clientHeight + .5))) {
        const ancestor = `${parent.tagName}.${parent.className}`
        clipping.push(ancestor)
        clippingBounds.push({ ancestor, left: bounds.left + parent.clientLeft, top: bounds.top + parent.clientTop, right: bounds.left + parent.clientLeft + parent.clientWidth, bottom: bounds.top + parent.clientTop + parent.clientHeight, scrollTop: parent.scrollTop, scrollHeight: parent.scrollHeight, clientHeight: parent.clientHeight })
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
    return { focused: document.activeElement === element, clipping, clippingBounds, viewport: { width: innerWidth, height: innerHeight, devicePixelRatio }, ring, visible: element.matches(':focus-visible'), style: style.outlineStyle, width: Number.parseFloat(style.outlineWidth), offset: Number.parseFloat(style.outlineOffset), color: style.outlineColor, background, outline, contrast: (high + .05) / (low + .05) }
  })
}
