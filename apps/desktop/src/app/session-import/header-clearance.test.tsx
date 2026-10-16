import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { cleanup, render } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'

const directory = dirname(fileURLToPath(import.meta.url))
const paneCss = readFileSync(join(directory, '../responsive-master-detail.css'), 'utf8')
const headingCss = readFileSync(join(directory, '../workspace-page-heading.css'), 'utf8')

afterEach(cleanup)

it('preserves the physical-right Import chrome gutter when compact container padding narrows', () => {
  const { container } = render(<style>{paneCss}{headingCss}</style>)
  const rules = Array.from(container.querySelector('style')!.sheet!.cssRules)
  // jsdom does not lay out container queries. Inspect the actual authored
  // declaration order here; the native journey still measures painted overlap.
  const compact = rules.find(rule => rule.cssText.startsWith('@container session-import')) as CSSGroupingRule
  expect(compact).toBeDefined()
  const header = Array.from(compact.cssRules).find(rule =>
    'selectorText' in rule && rule.selectorText === '.session-import > .eid-workspace-page-heading'
  ) as CSSStyleRule
  expect(header.style.getPropertyValue('padding-inline')).toBe('1rem')
  expect(header.style.getPropertyValue('padding-right')).toBe('2rem')
  expect(header.style.cssText.indexOf('padding-right')).toBeGreaterThan(header.style.cssText.indexOf('padding-inline'))
  const title = rules.flatMap(rule => 'cssRules' in rule ? Array.from((rule as CSSGroupingRule).cssRules) : []).find(rule =>
    'selectorText' in rule && rule.selectorText === '.session-import > header.eid-workspace-page-heading > h1'
  ) as CSSStyleRule
  expect(title.style.getPropertyValue('margin-right')).toBe('48px')
})
