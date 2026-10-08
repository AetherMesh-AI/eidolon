import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ConfirmDialog } from './confirm-dialog'

afterEach(cleanup)

describe('ConfirmDialog secondary action', () => {
  function renderWithSecondary() {
    const onConfirm = vi.fn()
    const onClose = vi.fn()
    const onSecondary = vi.fn()

    render(
      <ConfirmDialog
        onClose={onClose}
        onConfirm={onConfirm}
        open
        secondaryAction={{ label: 'Remove from sidebar', onClick: onSecondary }}
        title="Remove worktree?"
      />
    )

    return { onClose, onConfirm, onSecondary }
  }

  it('runs the secondary action and closes without confirming', async () => {
    const { onClose, onConfirm, onSecondary } = renderWithSecondary()

    fireEvent.click(await screen.findByRole('button', { name: 'Remove from sidebar' }))

    expect(onSecondary).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it.each(['Enter', ' '])('leaves %s on Cancel and secondary actions to native button activation', async key => {
    const { onConfirm, onClose, onSecondary } = renderWithSecondary()
    const cancel = await screen.findByRole('button', { name: /^Cancel$/ })
    cancel.focus()
    // jsdom does not synthesize keyboard button clicks. A non-cancelled event
    // plus the subsequent native click must keep the focused action's meaning.
    expect(fireEvent.keyDown(cancel, { key })).toBe(true)
    expect(onConfirm).not.toHaveBeenCalled()
    fireEvent.click(cancel)
    expect(onClose).toHaveBeenCalledTimes(1)

    const secondary = screen.getByRole('button', { name: 'Remove from sidebar' })
    secondary.focus()
    expect(fireEvent.keyDown(secondary, { key })).toBe(true)
    expect(onConfirm).not.toHaveBeenCalled()
    fireEvent.click(secondary)
    expect(onSecondary).toHaveBeenCalledTimes(1)
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('opens focused on Confirm without activating the secondary action', async () => {
    const { onConfirm, onSecondary } = renderWithSecondary()

    const dialog = await screen.findByRole('dialog')

    // eslint-disable-next-line no-restricted-globals -- asserting real focus requires the live document
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true))
    // eslint-disable-next-line no-restricted-globals -- asserting real focus requires the live document
    expect(document.activeElement).toBe(screen.getByRole('button', { name: /^Confirm$/ }))
    fireEvent.click(screen.getByRole('button', { name: /^Confirm$/ }))

    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1))
    expect(onSecondary).not.toHaveBeenCalled()
  })
})
