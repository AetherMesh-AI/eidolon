import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { OverlayView } from './overlay-view'

afterEach(cleanup)

it('keeps inline Settings open on background clicks and releases its Escape handler on unmount', () => {
  const close = vi.fn()

  const view = render(
    <OverlayView closeLabel="Close settings" inline onClose={close}>
      <p>Settings controls</p>
    </OverlayView>
  )

  fireEvent.click(screen.getByRole('presentation'))
  expect(close).not.toHaveBeenCalled()
  fireEvent.keyDown(window, { key: 'Escape' })
  expect(close).toHaveBeenCalledTimes(1)
  view.unmount()
  fireEvent.keyDown(window, { key: 'Escape' })
  expect(close).toHaveBeenCalledTimes(1)
})
