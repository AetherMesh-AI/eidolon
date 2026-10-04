import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { Inspector } from './inspector'

it('dismisses contextual details with Escape and restores the opener focus', () => {
  const opener = document.createElement('button')
  document.body.append(opener)
  opener.focus()
  const close = vi.fn()
  const view = render(<Inspector kind="event" onClose={close} title="Assignment recorded"><p>Prototype event</p></Inspector>)
  expect(screen.getByRole('complementary', { name: 'Event details' })).toBeTruthy()
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close event details' }))
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(close).toHaveBeenCalledOnce()
  view.unmount()
  expect(document.activeElement).toBe(opener)
  opener.remove()
})
