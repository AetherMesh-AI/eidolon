// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ownerMarkerPosition } from './organization-owner-marker-position'
import nativeLayouts from './organization-owner-marker-native-fixtures.json'
const rect = (left: number, top: number, width: number, height: number) => ({
  left, top, width, height, right: left + width, bottom: top + height,
  x: left, y: top, toJSON() { return { ...this, toJSON: undefined } }
})
const target = rect(200, 200, 20, 20)
function box(selector: string, bounds: ReturnType<typeof rect>) {
  vi.spyOn(document.querySelector(selector)!, 'getBoundingClientRect').mockReturnValue(bounds as DOMRect)
}
afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ''; vi.restoreAllMocks() })
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

// Rectangles captured in real Electron at 200% zoom in failed run38641381070,
// artifact11929819347. This replays native layout evidence, not a browser claim.
describe('recorded native marker placement regressions', () => {
  it.each(nativeLayouts)('preserves a clear corner in $scenario', layout => {
    vi.stubGlobal('innerWidth', layout.viewport.width)
    vi.stubGlobal('innerHeight', layout.viewport.height)
    for (const control of layout.controls) {
      let parent: HTMLElement = document.body
      for (const ancestor of [...layout.chains[control.chain]].reverse()) {
        const element = document.createElement('div')
        element.style.overflowX = ancestor.overflowX
        element.style.overflowY = ancestor.overflowY
        vi.spyOn(element, 'getBoundingClientRect').mockReturnValue(ancestor.rect as DOMRect)
        parent.appendChild(element); parent = element
      }
      const element = document.createElement(control.tag.toLowerCase())
      if (control.role) element.setAttribute('role', control.role)
      if (control.tabindex !== null) element.setAttribute('tabindex', control.tabindex)
      if (control.tag === 'A') element.setAttribute('href', '#')
      vi.spyOn(element, 'getBoundingClientRect').mockReturnValue(control.rect as DOMRect)
      parent.appendChild(element)
    }
    expect(ownerMarkerPosition(layout.target)).toBeDefined()
    vi.unstubAllGlobals()
  })
  it.each([
    { css: 'overflow-x:clip;overflow-y:visible', ancestor: rect(0, 100, 100, 100), child: rect(0, 0, 80, 40) },
    { css: 'overflow-y:clip;overflow-x:visible', ancestor: rect(100, 0, 100, 100), child: rect(0, 0, 40, 40) }
  ])('does not clip the visible axis ($css)', ({ css, ancestor, child }) => {
    document.body.innerHTML = `<div style="${css}"><button>Visible overflow</button></div>`
    box('div', ancestor); box('button', child)
    expect(ownerMarkerPosition(target)).toEqual({ left: innerWidth - 28, top: 4 })
  })
  it('clips nested ancestors independently on each overflow axis', () => {
    document.body.innerHTML = '<div id="outer" style="overflow-x:hidden;overflow-y:visible"><div id="inner" style="overflow-x:visible;overflow-y:auto"><label>Scrolled label</label></div></div>'
    box('#outer', rect(180, 0, 300, innerHeight))
    box('#inner', rect(0, 100, innerWidth, 180))
    box('label', rect(0, -50, innerWidth, 80))
    expect(ownerMarkerPosition(target)).toEqual({ left: 4, top: 4 })
  })
  it('keeps a partly clipped outline and exact target protected', () => {
    document.body.innerHTML = '<div style="overflow-y:hidden"><button>Clipped</button></div>'
    box('div', rect(0, 0, innerWidth, 80))
    box('button', rect(0, -25, 100, 20))
    // Border ends at -5, but its protected outline extends into the viewport.
    expect(ownerMarkerPosition(target)).toEqual({ left: innerWidth - 28, top: 4 })
    expect(ownerMarkerPosition(rect(0, 0, innerWidth, innerHeight))).toBeUndefined()
  })
})
