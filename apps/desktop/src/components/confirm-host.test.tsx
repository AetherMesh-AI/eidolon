import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { $confirmRequest, confirm } from '@/store/confirm'

import { ConfirmHost } from './confirm-host'

afterEach(() => {
  cleanup()
  $confirmRequest.set(null)
})

/** Open a confirm and wait for the dialog, without awaiting the answer. */
async function ask(title = 'Delete it?') {
  let answer: boolean | undefined
  const pending = confirm({ title }).then(ok => (answer = ok))

  const dialog = await screen.findByRole('dialog')

  return { dialog, pending, read: () => answer }
}

describe('confirm()', () => {
  it('renders nothing until something asks', () => {
    render(<ConfirmHost />)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('resolves true when confirmed and false when cancelled', async () => {
    render(<ConfirmHost />)

    const yes = await ask()
    fireEvent.click(screen.getByRole('button', { name: /confirm/i }))
    await yes.pending
    expect(yes.read()).toBe(true)

    const no = await ask()
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))
    await no.pending
    expect(no.read()).toBe(false)
  })

  it('activates the focused Confirm button with Enter', async () => {
    const user = userEvent.setup()
    render(<ConfirmHost />)

    const { pending, read } = await ask()
    const button = screen.getByRole('button', { name: 'Confirm', exact: true })
    await waitFor(() => expect(window.document.activeElement).toBe(button))
    expect((button as HTMLButtonElement).disabled).toBe(false)
    await user.keyboard('{Enter}')

    await pending
    expect(read()).toBe(true)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it.each(['{Enter}', ' '])('cancels when the focused Cancel button receives %s', async key => {
    const user = userEvent.setup()
    render(<ConfirmHost />)

    const { pending, read } = await ask()
    await waitFor(() => expect(window.document.activeElement).toBe(screen.getByRole('button', { name: 'Confirm', exact: true })))
    await user.tab({ shift: true })
    const cancel = screen.getByRole('button', { name: 'Cancel', exact: true })
    expect(window.document.activeElement).toBe(cancel)
    expect((cancel as HTMLButtonElement).disabled).toBe(false)
    await user.keyboard(key)

    await pending
    expect(read()).toBe(false)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('answers no to Escape', async () => {
    render(<ConfirmHost />)

    const { pending, read } = await ask()
    fireEvent.keyDown(window.document, { key: 'Escape' })

    await pending
    expect(read()).toBe(false)
  })

  it('supersedes an open request, answering the one it replaces no', async () => {
    render(<ConfirmHost />)

    const first = await ask('First?')
    const second = await act(async () => ask('Second?'))

    await first.pending
    expect(first.read()).toBe(false)
    expect(screen.getByText('Second?')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /confirm/i }))
    await second.pending
    expect(second.read()).toBe(true)
  })
})
