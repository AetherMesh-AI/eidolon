import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { cleanup, render } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'

const stylesheet = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'messages.css'), 'utf8')
afterEach(cleanup)

it('caps the transcript against the actual detail pane with room for its keyboard outline', () => {
  const { container } = render(<><style>{stylesheet}</style><div className="eid-messages-view">
    <div className="eid-messages-layout"><div className="eid-responsive-detail-content">
      <div className="eid-owner-chat-scroll" tabIndex={0}>Retained transcript</div>
    </div></div>
  </div></>)

  const detail = container.querySelector('.eid-responsive-detail-content')!
  const transcript = container.querySelector('.eid-owner-chat-scroll')!
  expect(getComputedStyle(detail).getPropertyValue('container-type')).toBe('size')
  expect(getComputedStyle(transcript).maxHeight).toBe('min(calc(100dvh - 90px), calc(100cqh - 16px))')
  // Native proof supplies actual layout. At its previously failing 277px pane,
  // the cap leaves more than the 3px outline + 3px offset at both edges.
  expect(277 - (277 - 16)).toBeGreaterThan(2 * (3 + 3))
})
