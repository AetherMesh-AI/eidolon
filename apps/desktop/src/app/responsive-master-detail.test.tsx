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

function Fixture({ backLabel }: { backLabel?: string }) {
  const [open, setOpen] = useState(false)

  return (
    <ResponsiveMasterDetail backLabel={backLabel} detailOpen={open} onDetailOpenChange={setOpen}>
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

it('supports a contextual Back label without changing selection or lifecycle', () => {
  observeWidth()
  render(<Fixture backLabel="Back to members" />)
  act(() => resize(400))
  const row = screen.getByRole('button', { name: 'Choose platform' })
  row.focus()
  fireEvent.click(row)
  const draft = screen.getByRole('textbox', { name: 'Draft' })
  fireEvent.change(draft, { target: { value: 'Contextual draft' } })
  fireEvent.click(screen.getByRole('button', { name: 'Back to members' }))
  expect(window.document.activeElement).toBe(row)
  fireEvent.click(row)
  expect(screen.getByRole('textbox', { name: 'Draft' })).toBe(draft)
  expect((draft as HTMLTextAreaElement).value).toBe('Contextual draft')
})

it.each(['pressed', 'current'] as const)(
  'returns deep-linked detail focus to an aria-%s row without a prior click',
  state => {
    observeWidth()

    function SelectedFixture() {
      const [open, setOpen] = useState(true)

      return (
        <ResponsiveMasterDetail detailOpen={open} onDetailOpenChange={setOpen}>
          <button
            aria-current={state === 'current' ? true : undefined}
            aria-pressed={state === 'pressed' ? true : undefined}
          >
            Selected member
          </button>
          <main>
            <input aria-label="Selected draft" />
          </main>
        </ResponsiveMasterDetail>
      )
    }

    render(<SelectedFixture />)
    act(() => resize(400))
    const back = screen.getByRole('button', { name: 'Back' })
    back.focus()
    fireEvent.click(back)
    expect(window.document.activeElement).toBe(screen.getByRole('button', { name: 'Selected member' }))
  }
)

it('publishes initial measurement readiness with the compact layout and keeps it through later resizes', () => {
  observeWidth()
  const { container } = render(<Fixture />)
  const layout = container.querySelector('.eid-responsive-detail')!
  expect(layout.getAttribute('data-layout-ready')).toBe('false')
  act(() => resize(0))
  expect(layout.getAttribute('data-layout-ready')).toBe('false')
  act(() => resize(500))
  expect(layout.getAttribute('data-layout-ready')).toBe('true')
  expect(layout.getAttribute('data-compact')).toBe('true')
  act(() => resize(1000))
  expect(layout.getAttribute('data-layout-ready')).toBe('true')
  expect(layout.getAttribute('data-compact')).toBe('false')
})

it('does not wait for an unavailable resize observer', () => {
  vi.stubGlobal('ResizeObserver', undefined)
  const { container } = render(<Fixture />)
  expect(container.querySelector('.eid-responsive-detail')?.getAttribute('data-layout-ready')).toBe('true')
})
