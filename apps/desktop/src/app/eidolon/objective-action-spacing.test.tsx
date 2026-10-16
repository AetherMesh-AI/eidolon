import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { cleanup, render } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'

const stylesheet = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'eidolon.css'), 'utf8')

afterEach(cleanup)

it('reserves action-to-copy spacing beyond the six-pixel keyboard outline outset', () => {
  const { container } = render(<><style>{stylesheet}</style><div className="eid-objective-overview">
    <header><div className="eid-progress-heading"><button>Inspect work</button></div><p>Recorded progress</p></header>
    <div className="eid-objective-progress-aside"><section className="eid-home-panel"><p>Owner requests</p><button>Review requests</button></section><section className="eid-home-panel"><p>Final acceptance</p><button>Inspect acceptance</button></section></div>
  </div></>)

  expect(parseFloat(getComputedStyle(container.querySelector('.eid-progress-heading')!).marginBottom)).toBeGreaterThan(6)

  for (const button of container.querySelectorAll('.eid-objective-progress-aside button')) {
    expect(parseFloat(getComputedStyle(button).marginTop)).toBeGreaterThan(6)
  }
})
