// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ownerMarkerPosition } from './organization-owner-marker-position'
const rect = (left: number, top: number, width: number, height: number) => ({
  left, top, width, height, right: left + width, bottom: top + height,
  x: left, y: top, toJSON() { return { ...this, toJSON: undefined } }
})
const target = rect(200, 200, 20, 20)
function box(selector: string, bounds: ReturnType<typeof rect>) {
  vi.spyOn(document.querySelector(selector)!, 'getBoundingClientRect').mockReturnValue(bounds as DOMRect)
}
afterEach(() => { document.body.innerHTML = ''; vi.restoreAllMocks() })
describe('owner marker placement in real dialog DOM', () => {
  it('ignores viewport-sized negative-tabindex dialog and focus scope', () => {
    document.body.innerHTML = '<div tabindex="-1"><div role="dialog" tabindex="-1"><label for="enroll">Enroll</label><button id="enroll" role="checkbox" tabindex="0">Team</button></div></div>'
    box('body > div', rect(0, 0, innerWidth, innerHeight))
    box('[role=dialog]', rect(0, 0, innerWidth, innerHeight))
    box('label', rect(220, 200, 150, 20)); box('button', target)
    expect(ownerMarkerPosition(target)).toEqual({ left: 4, top: 4 })
  })
  it('avoids labels and negative-tabindex actionable controls', () => {
    document.body.innerHTML = '<label>Upper left</label><button tabindex="-1">Upper right</button>'
    box('label', rect(0, 0, 100, 40))
    box('button', rect(innerWidth - 100, 0, 100, 40))
    expect(ownerMarkerPosition(target)).toEqual({ left: 4, top: innerHeight - 12 })
  })
  it('always avoids the exact target and its ring even for generic containers', () => {
    expect(ownerMarkerPosition(rect(0, 0, 100, 40))).toEqual({ left: innerWidth - 28, top: 4 })
    expect(ownerMarkerPosition(rect(0, 0, innerWidth, innerHeight))).toBeUndefined()
  })
  it('fails closed when all corners contain actionable content', () => {
    document.body.innerHTML = '<button>Content</button>'
    box('button', rect(0, 0, innerWidth, innerHeight))
    expect(ownerMarkerPosition(target)).toBeUndefined()
  })
  it('excludes focusable custom controls and ignores hidden content', () => {
    document.body.innerHTML = '<div tabindex="0">Custom</div><label style="display:none">Hidden</label>'
    box('[tabindex]', rect(0, 0, 100, 40)); box('label', rect(0, 0, innerWidth, innerHeight))
    expect(ownerMarkerPosition(target)).toEqual({ left: innerWidth - 28, top: 4 })
  })
})
