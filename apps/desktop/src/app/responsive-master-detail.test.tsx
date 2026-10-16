import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import { ResponsiveMasterDetail } from './responsive-master-detail'

let resize: (width: number) => void

function observeWidth() {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(callback: ResizeObserverCallback) {
        resize = width =>
          callback([{ contentRect: { width } } as ResizeObserverEntry], this as unknown as ResizeObserver)
      }
      observe() {}
      disconnect() {}
    }
  )
}

function Fixture() {
  const [open, setOpen] = useState(false)

  return (
    <ResponsiveMasterDetail detailOpen={open} onDetailOpenChange={setOpen}>
      <div>
        <button onClick={() => setOpen(true)}>Choose platform</button>
        <input aria-label="Filter" />
      </div>
      <main>
        <textarea aria-label="Draft" defaultValue="Original" />
        <button>Save</button>
      </main>
    </ResponsiveMasterDetail>
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('uses container width and preserves mounted drafts, scroll and row focus through compact navigation', () => {
  observeWidth()
  const { container } = render(<Fixture />)
  act(() => resize(500))
  const row = screen.getByRole('button', { name: 'Choose platform' })
  const list = container.querySelector<HTMLElement>('[data-pane="list"]')!
  const detail = container.querySelector<HTMLElement>('[data-pane="detail"]')!
  expect(detail.hidden).toBe(true)
  expect(detail.hasAttribute('inert')).toBe(true)
  list.scrollTop = 40
  row.focus()
  fireEvent.click(row)
  expect(list.hidden).toBe(true)
  expect(list.hasAttribute('inert')).toBe(true)
  expect(window.document.activeElement).toBe(screen.getByRole('button', { name: 'Back' }))
  const draft = screen.getByRole('textbox', { name: 'Draft' })
  fireEvent.change(draft, { target: { value: 'Keep this draft' } })
  fireEvent.click(screen.getByRole('button', { name: 'Back' }))
  expect(window.document.activeElement).toBe(row)
  expect(list.scrollTop).toBe(40)
  fireEvent.click(row)
  expect(screen.getByRole('textbox', { name: 'Draft' })).toBe(draft)
  expect((draft as HTMLTextAreaElement).value).toBe('Keep this draft')
  act(() => resize(1100))
  expect(list.hidden).toBe(false)
  expect(detail.hidden).toBe(false)
  expect(screen.queryByRole('button', { name: 'Back' })).toBeNull()
  expect(window.document.activeElement).not.toBe(container.querySelector('.eid-responsive-detail-back button'))
})

it('keeps the focused pane visible when resizing a wide workspace', () => {
  observeWidth()
  const { container } = render(<Fixture />)
  act(() => resize(1100))
  const draft = screen.getByRole('textbox', { name: 'Draft' })
  draft.focus()
  act(() => resize(500))
  expect(container.querySelector<HTMLElement>('[data-pane="detail"]')!.hidden).toBe(false)
  expect(window.document.activeElement).toBe(draft)
  act(() => resize(1100))
  const filter = screen.getByRole('textbox', { name: 'Filter' })
  filter.focus()
  act(() => resize(500))
  expect(container.querySelector<HTMLElement>('[data-pane="list"]')!.hidden).toBe(false)
  expect(window.document.activeElement).toBe(filter)
})
