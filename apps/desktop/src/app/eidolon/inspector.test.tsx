import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { Inspector } from './inspector'

it('dismisses contextual details with Escape and restores the opener focus and nested owner scroll offsets', () => {
  const outerScroller = globalThis.document.createElement('section')
  const scroller = globalThis.document.createElement('section')
  const opener = globalThis.document.createElement('button')
  scroller.append(opener)
  outerScroller.append(scroller)
  globalThis.document.body.append(outerScroller)
  outerScroller.scrollTop = 128
  outerScroller.scrollLeft = 7
  scroller.scrollTop = 192.8
  scroller.scrollLeft = 12
  opener.focus()
  const close = vi.fn()
  const view = render(<Inspector kind="event" onClose={close} title="Assignment recorded"><p>Prototype event</p></Inspector>)
  expect(screen.getByRole('complementary', { name: 'Event details' })).toBeTruthy()
  expect(globalThis.document.activeElement).toBe(screen.getByRole('button', { name: 'Close event details' }))
  scroller.scrollTop = 327.2
  scroller.scrollLeft = 24
  outerScroller.scrollTop = 256
  outerScroller.scrollLeft = 14
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(close).toHaveBeenCalledOnce()
  view.unmount()
  expect(globalThis.document.activeElement).toBe(opener)
  expect(scroller.scrollTop).toBe(192.8)
  expect(scroller.scrollLeft).toBe(12)
  expect(outerScroller.scrollTop).toBe(128)
  expect(outerScroller.scrollLeft).toBe(7)
  outerScroller.remove()
})

it('does not restore a dismissed inspector into an owner context whose opener was removed', () => {
  const scroller = globalThis.document.createElement('section')
  const opener = globalThis.document.createElement('button')
  scroller.append(opener)
  globalThis.document.body.append(scroller)
  scroller.scrollTop = 45
  opener.focus()
  const view = render(<Inspector kind="request" onClose={vi.fn()} title="Recorded request"><p>Current owner</p></Inspector>)
  opener.remove()
  scroller.scrollTop = 90
  view.unmount()
  expect(globalThis.document.activeElement).not.toBe(opener)
  expect(scroller.scrollTop).toBe(90)
  scroller.remove()
})

it('does not restore focus or scroll into another owner with the same request ID', () => {
  function OwnerRequest({ owner, open }: { owner: string; open: boolean }) {
    return (
      <>
        <button data-request-id="shared-request">Request for {owner}</button>
        {open && <Inspector kind="request" onClose={vi.fn()} title="Recorded request"><p>{owner} details</p></Inspector>}
      </>
    )
  }

  const contents = (owner: string, open: boolean) => (
    <section aria-label="Owner workspace"><OwnerRequest key={owner} open={open} owner={owner} /></section>
  )

  const view = render(contents('first owner', false))
  const scroller = screen.getByRole('region', { name: 'Owner workspace' })
  const opener = screen.getByRole('button', { name: 'Request for first owner' })
  scroller.scrollTop = 45
  opener.focus()
  view.rerender(contents('first owner', true))
  expect(globalThis.document.activeElement).toBe(screen.getByRole('button', { name: 'Close request details' }))
  scroller.scrollTop = 90
  view.rerender(contents('second owner', false))
  const nextOwnerOpener = screen.getByRole('button', { name: 'Request for second owner' })
  expect(screen.getByRole('region', { name: 'Owner workspace' })).toBe(scroller)
  expect(nextOwnerOpener.dataset.requestId).toBe(opener.dataset.requestId)
  expect(opener.isConnected).toBe(false)
  expect(globalThis.document.activeElement).not.toBe(opener)
  expect(globalThis.document.activeElement).not.toBe(nextOwnerOpener)
  expect(scroller.scrollTop).toBe(90)
})
