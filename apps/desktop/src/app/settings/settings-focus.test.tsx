import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { cleanup, render } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'

const stylesheet = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'settings.css'), 'utf8')
afterEach(cleanup)

function focusRule(container: HTMLElement, target: HTMLElement) {
  // jsdom does not resolve custom-property colors in outline shorthands.
  // Read the real parsed rule rather than substituting a test-only outline.
  const rules = Array.from(container.querySelector('style')!.sheet!.cssRules)
  const rule = rules.find(candidate => 'selectorText' in candidate && candidate.selectorText === '.eid-settings-page :is(button, a, input, textarea, select, summary):focus-visible') as CSSStyleRule
  expect(target.matches(rule.selectorText)).toBe(true)

  return rule.style
}

it('keeps the Settings keyboard outline wide enough after fractional-zoom pixel rounding', () => {
  const { container, getByRole } = render(<><style>{stylesheet}</style><main className="eid-settings-page"><button>Refresh</button></main></>)
  const refresh = getByRole('button', { name: 'Refresh' })
  refresh.focus()
  expect(refresh.matches(':focus-visible')).toBe(true)
  const style = focusRule(container, refresh)
  const width = Number.parseFloat(style.getPropertyValue('outline'))
  const offset = Number.parseFloat(style.getPropertyValue('outline-offset'))

  // Native Chromium snaps the painted edge to device pixels. The observed
  // 2px outline became 1.6 CSS pixels at 125%; keep the strict native 2px floor.
  for (const zoom of [1, 1.25, 2]) {
    expect(Math.floor(width * zoom) / zoom).toBeGreaterThanOrEqual(2)
    expect(Math.floor(offset * zoom) / zoom).toBeGreaterThanOrEqual(2)
  }

  expect(style.getPropertyValue('transition-property')).not.toMatch(/(^|,)\s*(all|outline(?:-width|-offset)?)\s*(,|$)/)
})

it('reserves scroll clearance beyond the complete Settings outline', () => {
  const { container, getByRole } = render(<><style>{stylesheet}</style><main className="eid-settings-page">
    <div className="eid-settings-layout"><aside><button>Section</button></aside>
      <section className="eid-settings-main"><div className="eid-settings-controls"><div className="settings-content"><div><button>Refresh</button></div></div></div></section>
    </div>
  </main></>)

  const refresh = getByRole('button', { name: 'Refresh' })
  refresh.focus()
  const style = focusRule(container, refresh)
  const outset = Number.parseFloat(style.getPropertyValue('outline')) + Number.parseFloat(style.getPropertyValue('outline-offset'))
  const scroller = container.querySelector('.settings-content > div')!
  const navigation = container.querySelector('aside')!

  expect(Number.parseFloat(getComputedStyle(refresh).scrollMargin)).toBeGreaterThan(outset)
  expect(Number.parseFloat(getComputedStyle(scroller).scrollPadding)).toBeGreaterThan(outset)
  expect(Number.parseFloat(getComputedStyle(scroller).paddingTop)).toBeGreaterThan(outset)
  expect(Number.parseFloat(getComputedStyle(navigation).scrollPadding)).toBeGreaterThan(outset)
  expect(Number.parseFloat(getComputedStyle(navigation).paddingTop)).toBeGreaterThan(outset)
  expect(Number.parseFloat(getComputedStyle(navigation).paddingLeft)).toBeGreaterThan(outset)
})
