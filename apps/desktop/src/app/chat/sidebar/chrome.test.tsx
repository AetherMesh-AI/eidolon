import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createPortal } from 'react-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SidebarDateDivider, SidebarRowShell } from './chrome'

afterEach(cleanup)

describe('SidebarDateDivider', () => {
  it('collapses the group when the caption is clicked', () => {
    const onToggle = vi.fn()

    render(
      <SidebarDateDivider label="Yesterday" toggle={{ ariaLabel: 'Hide Yesterday sessions', onToggle, open: true }} />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Hide Yesterday sessions' }))
    expect(onToggle).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: 'Hide Yesterday sessions' }).getAttribute('aria-expanded')).toBe('true')
  })

  it('stays a static caption when it is not collapsible', () => {
    render(<SidebarDateDivider label="Yesterday" />)

    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText('Yesterday')).toBeTruthy()
  })
})


describe('SidebarRowShell keyboard ownership', () => {
  it('preserves portal button keys while keeping in-row keyboard handlers', () => {
    const onKeyDown = vi.fn(event => event.preventDefault())
    const portal = window.document.createElement('div')
    window.document.body.append(portal)
    try {
      render(<SidebarRowShell onKeyDown={onKeyDown} tabIndex={0} aria-label="Reorder session">
        <button type="button">In-row action</button>
        {createPortal(<button type="button">Dialog cancel</button>, portal)}
      </SidebarRowShell>)
      const cancel = screen.getByRole('button', { name: 'Dialog cancel' })
      cancel.focus()
      expect(window.document.activeElement).toBe(cancel)
      for (const key of ['Enter', ' ', 'Escape']) {
        expect(fireEvent.keyDown(cancel, { key })).toBe(true)
      }
      expect(onKeyDown).not.toHaveBeenCalled()
      const inside = screen.getByRole('button', { name: 'In-row action' })
      expect(fireEvent.keyDown(inside, { key: 'Enter' })).toBe(false)
      expect(onKeyDown).toHaveBeenCalledOnce()
    } finally {
      cleanup()
      portal.remove()
    }
  })
})
