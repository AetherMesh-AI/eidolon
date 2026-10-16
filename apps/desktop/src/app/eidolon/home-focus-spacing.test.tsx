import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { cleanup, render } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'

const stylesheet = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'eidolon.css'), 'utf8')

afterEach(cleanup)

it('reserves Home intake keyboard scroll clearance without affecting other workspaces', () => {
  const { container } = render(<><style>{stylesheet}</style>
    <main className="eid-workspace" data-testid="home"><details className="eid-home-intake" open>
      <summary>Describe a request</summary><textarea aria-label="Request" /><button>Submit request</button>
    </details></main>
    <main className="eid-workspace" data-testid="other"><button>Other action</button></main>
  </>)

  expect(getComputedStyle(container.querySelector('[data-testid="home"]')!).scrollPadding).toBe('7px')

  for (const control of container.querySelectorAll('.eid-home-intake :is(summary, textarea, button)')) {
    expect(getComputedStyle(control).scrollMargin).toBe('7px')
  }

  expect(getComputedStyle(container.querySelector('[data-testid="other"]')!).scrollPadding).not.toBe('7px')
  expect(getComputedStyle(container.querySelector('[data-testid="other"] button')!).scrollMargin).not.toBe('7px')
})
