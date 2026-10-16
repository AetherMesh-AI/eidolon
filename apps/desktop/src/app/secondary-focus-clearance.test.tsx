import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { cleanup, render } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'

const directory = dirname(fileURLToPath(import.meta.url))
const secondaryCss = readFileSync(join(directory, 'workspace-page-heading.css'), 'utf8')
const capabilityCss = readFileSync(join(directory, 'skills/skills-workspace.css'), 'utf8')

afterEach(cleanup)

it('retains a two-pixel native outline after fractional zoom rounding with room to scroll it into view', () => {
  const { container } = render(<><style>{secondaryCss}{capabilityCss}</style><div className="eid-secondary-surface">
    <div className="eid-capabilities-empty"><button>Retry</button></div>
  </div></>)
  const button = container.querySelector('button')!
  button.focus()
  const style = getComputedStyle(button)
  // Chromium rounds outline widths down to whole device pixels at fractional zoom.
  // jsdom does not apply :focus-visible; inspect the authored rule here.
  // The native gallery measures the actual painted ring and ancestor clips.
  const rules = Array.from(container.querySelector('style')!.sheet!.cssRules)
  const focus = rules.find(rule => 'selectorText' in rule && String(rule.selectorText).includes(':focus-visible')) as CSSStyleRule
  const width = parseFloat(focus.style.outline)
  const offset = parseFloat(focus.style.outlineOffset)
  expect(Math.floor(width * 1.25) / 1.25).toBeGreaterThanOrEqual(2)
  expect(offset).toBeGreaterThanOrEqual(2)
  const scroll = getComputedStyle(container.querySelector('.eid-capabilities-empty')!)
  expect(parseFloat(scroll.padding)).toBeGreaterThan(width + offset)
  expect(parseFloat(scroll.scrollPadding)).toBeGreaterThan(width + offset)
  expect(parseFloat(style.scrollMargin)).toBeGreaterThan(width + offset)
})

it('keeps an expanded Hub usable by scrolling the workspace instead of collapsing the frame', () => {
  const { container } = render(<><style>{capabilityCss}</style><div className="eid-capabilities-workspace">
    <div className="eid-capabilities-content">
      <section className="eid-hub-picker" data-open="true"><div className="eid-hub-viewport" /></section>
    </div>
  </div></>)
  const workspace = getComputedStyle(container.querySelector('.eid-capabilities-workspace')!)
  const content = getComputedStyle(container.querySelector('.eid-capabilities-content')!)
  const viewport = getComputedStyle(container.querySelector('.eid-hub-viewport')!)
  expect(workspace.overflowY).toBe('auto')
  expect(workspace.scrollbarGutter).toBe('stable')
  expect(content.flexShrink).toBe('0')
  expect(content.minHeight).toBe('28rem')
  expect(viewport.minHeight).toBe('6rem')
})

it.each(['collapsed', 'hidden'])('retires expanded Hub workspace sizing when %s', state => {
  const { container } = render(<><style>{capabilityCss}</style><div className="eid-capabilities-workspace">
    <div className="eid-capabilities-content">
      <section className={`eid-hub-picker${state === 'hidden' ? ' hidden' : ''}`} data-open={state === 'collapsed' ? 'false' : 'true'}>
        <div className="eid-hub-viewport" />
      </section>
    </div>
  </div></>)
  const workspace = getComputedStyle(container.querySelector('.eid-capabilities-workspace')!)
  expect(workspace.overflowY).toBe('auto')
  expect(workspace.scrollbarGutter).toBe('stable')
  const content = getComputedStyle(container.querySelector('.eid-capabilities-content')!)
  expect(content.minHeight).toBe('auto')
  expect(content.flexShrink).toBe('1')
})

it('gives Tools one document scroller with its caption in flow and Back outside', () => {
  const { container } = render(<><style>{capabilityCss}</style>
    <div className="eid-capabilities-tools-detail"><div className="eid-responsive-detail-content">
      <button>Back</button><main><div className="eid-detail-scroll"><h3>Wrapped heading</h3></div><div>Changes apply to new sessions.</div></main>
    </div></div>
  </>)
  const main = container.querySelector('main')!
  const style = getComputedStyle(main)
  expect(style.display).toBe('block')
  expect(style.overflowY).toBe('auto')
  expect(style.scrollbarGutter).toBe('stable')
  expect(style.scrollPadding).toBe('7px')
  expect(getComputedStyle(main.querySelector('.eid-detail-scroll')!).overflow).toBe('visible')
  expect(main.contains(container.querySelector('button'))).toBe(false)
  expect(main.lastElementChild?.textContent).toBe('Changes apply to new sessions.')
})
