import { fireEvent, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { expect, it, vi } from 'vitest'

import { captureInspectorOrigin, Inspector } from './inspector'
import { type WorkspaceFocusRestoration, WorkspaceFocusRestoreContext } from './workspace-navigation'

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

it('ignores a retained hidden inspector while Escape dismisses the top visible inspector', () => {
  const visibleClose = vi.fn()
  const hiddenClose = vi.fn()
  const view = render(<><Inspector kind="request" onClose={visibleClose} title="Visible">{null}</Inspector><div hidden inert><Inspector kind="artifact" onClose={hiddenClose} title="Retained">{null}</Inspector></div></>)
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(visibleClose).toHaveBeenCalledOnce()
  expect(hiddenClose).not.toHaveBeenCalled()
  view.unmount()
})

it.each(['hidden', 'inert'] as const)('does not restore focus or scroll to a retained %s opener', attribute => {
  const scroller = globalThis.document.createElement('section')
  const opener = globalThis.document.createElement('button')
  scroller.append(opener)
  globalThis.document.body.append(scroller)
  scroller.scrollTop = 45
  opener.focus()
  const view = render(<Inspector kind="request" onClose={vi.fn()} title="Recorded request">{null}</Inspector>)
  scroller.setAttribute(attribute, '')
  scroller.scrollTop = 90
  view.unmount()
  expect(globalThis.document.activeElement).not.toBe(opener)
  expect(scroller.scrollTop).toBe(90)
  scroller.remove()
})

it('resumes retained inspector Escape ownership when its pane becomes visible again', () => {
  const firstClose = vi.fn()
  const retainedClose = vi.fn()
  const draw = (hidden: boolean) => <><Inspector kind="request" onClose={firstClose} title="First">{null}</Inspector><div hidden={hidden} inert={hidden}><Inspector kind="artifact" onClose={retainedClose} title="Retained">{null}</Inspector></div></>
  const view = render(draw(false))
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(retainedClose).toHaveBeenCalledTimes(1)
  expect(firstClose).not.toHaveBeenCalled()
  view.rerender(draw(true))
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(firstClose).toHaveBeenCalledTimes(1)
  expect(retainedClose).toHaveBeenCalledTimes(1)
  view.rerender(draw(false))
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(retainedClose).toHaveBeenCalledTimes(2)
  expect(firstClose).toHaveBeenCalledTimes(1)
})

it('does not capture an unrelated foreground opener when mounting inside a retained hidden pane', () => {
  const scroller = globalThis.document.createElement('section')
  const first = globalThis.document.createElement('button')
  const next = globalThis.document.createElement('button')
  scroller.append(first, next)
  globalThis.document.body.append(scroller)
  scroller.scrollTop = 45
  first.focus()
  const view = render(<div hidden inert><Inspector kind="event" onClose={vi.fn()} title="Refreshed hidden event">{null}</Inspector></div>)
  expect(globalThis.document.activeElement).toBe(first)
  next.focus()
  scroller.scrollTop = 90
  view.unmount()
  expect(globalThis.document.activeElement).toBe(next)
  expect(scroller.scrollTop).toBe(90)
  scroller.remove()
})

it.each([
  ['one', '/activity', true],
  ['other', '/activity', false],
  ['one', '/organization', false]
] as const)('defers opt-in autofocus only for pending owner %s and route %s', (owner, route, matches) => {
  const opener = globalThis.document.createElement('button')
  globalThis.document.body.append(opener)
  opener.focus()
  const claim = createRef<WorkspaceFocusRestoration>()
  claim.current = { pending: { owner, route }, origin: { owner, route } }

  const view = render(<WorkspaceFocusRestoreContext.Provider value={claim}>
    <Inspector kind="event" onClose={() => {}} restorationScope={{ owner: 'one', route: '/activity' }} title="Event">Details</Inspector>
  </WorkspaceFocusRestoreContext.Provider>)

  expect(globalThis.document.activeElement).toBe(matches ? opener : screen.getByRole('button', { name: 'Close event details' }))
  view.unmount()
  opener.remove()
})

it('restores explicitly captured nested offsets while rejecting a subsequently hidden opener', () => {
  const outer = globalThis.document.createElement('section')
  const inner = globalThis.document.createElement('section')
  const opener = globalThis.document.createElement('button')
  inner.append(opener)
  outer.append(inner)
  globalThis.document.body.append(outer)
  outer.scrollTop = 2083
  inner.scrollTop = 37
  opener.focus()
  const origin = captureInspectorOrigin(opener)
  outer.scrollTop = 2291
  inner.scrollTop = 70
  const view = render(<Inspector kind="artifact" onClose={vi.fn()} origin={origin} title="Preview">Details</Inspector>)
  view.unmount()
  expect(outer.scrollTop).toBe(2083)
  expect(inner.scrollTop).toBe(37)
  const hidden = render(<Inspector kind="artifact" onClose={vi.fn()} origin={origin} title="Preview">Details</Inspector>)
  inner.hidden = true
  outer.scrollTop = 2291
  hidden.unmount()
  expect(outer.scrollTop).toBe(2291)
  expect(globalThis.document.activeElement).not.toBe(opener)
  outer.remove()
})
