import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { cleanup, render } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'

const stylesheet = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'eidolon.css'), 'utf8')

afterEach(cleanup)

it.each(['ltr', 'rtl'])('reserves full outline clearance in objective tabs without changing graph or other tabs (%s)', dir => {
  const { container } = render(<><style>{stylesheet}</style>
    <div className="eid-page" dir={dir}><header className="eid-objective-hero" />
      <div className="eid-tabs" data-testid="objective-tabs" role="tablist"><button role="tab">Work</button></div>
      <div className="eid-tabs" data-testid="graph-tabs"><button>Graph</button></div>
    </div>
    <div className="eid-tabs" data-testid="other-tabs" role="tablist"><button role="tab">Other</button></div>
  </>)

  const style = getComputedStyle(container.querySelector('[data-testid=objective-tabs]')!)

  for (const value of [style.paddingTop, style.paddingRight, style.paddingBottom, style.paddingLeft]) {
    expect(parseFloat(value)).toBeGreaterThan(6)
  }

  expect(parseFloat(style.scrollPadding)).toBeGreaterThan(6)
  expect(style.overflowX).toBe('auto')

  for (const id of ['graph-tabs', 'other-tabs']) {
    const other = getComputedStyle(container.querySelector(`[data-testid=${id}]`)!)
    expect(parseFloat(other.padding) || 0).toBe(0)
    expect(other.overflowX).toBe('auto')
  }
})
