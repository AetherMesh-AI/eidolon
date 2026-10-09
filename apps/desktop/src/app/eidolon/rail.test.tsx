import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { OrganizationRail } from './rail'

afterEach(cleanup)

it('fronts the workspace when re-clicking the current organization page', () => {
  const onNavigate = vi.fn()
  render(
    <MemoryRouter initialEntries={['/home']}>
      <OrganizationRail onNavigate={onNavigate} sessions={<span>Canonical session tree</span>} />
    </MemoryRouter>
  )
  fireEvent.click(screen.getByRole('link', { name: 'Command' }))
  expect(onNavigate).toHaveBeenCalledWith('/home')
})

it('preserves the canonical history and its input while expanding navigation and returning from a page', () => {
  const mounts = vi.fn()
  const unmounts = vi.fn()

  function History() {
    const [filter, setFilter] = useState('')
    useEffect(() => {
      mounts()

      return unmounts
    }, [])

    return (
      <label>
        History filter
        <input onChange={event => setFilter(event.target.value)} value={filter} />
      </label>
    )
  }

  render(
    <MemoryRouter initialEntries={['/home']}>
      <OrganizationRail pluginNav={<a href="/kanban">Legacy Kanban</a>} sessions={<History />} />
    </MemoryRouter>
  )
  const rail = screen.getByRole('complementary', { name: 'Eidolon navigation' })
  const input = screen.getByRole('textbox', { name: 'History filter' })
  const summary = screen.getByText('Tools and configuration')
  const details = summary.closest('details')!
  fireEvent.change(input, { target: { value: 'Unfinished side chat' } })

  for (let cycle = 0; cycle < 3; cycle++) {
    fireEvent.click(summary)
    expect(details.open).toBe(true)
    expect(screen.getByRole('link', { name: 'Legacy Kanban' })).toBeTruthy()
    fireEvent.click(screen.getByRole('link', { name: 'Legacy prototype history' }))
    fireEvent.click(summary)
    expect(details.open).toBe(false)
    fireEvent.click(screen.getByRole('link', { name: 'Command' }))
    expect(screen.getByRole('textbox', { name: 'History filter' })).toBe(input)
    expect((input as HTMLInputElement).value).toBe('Unfinished side chat')
  }

  expect(mounts).toHaveBeenCalledTimes(1)
  expect(unmounts).not.toHaveBeenCalled()
  // The outer scroll owner is keyboard-reachable, even if none of its lower
  // informational content has a focusable control of its own.
  rail.focus()
  expect(rail.ownerDocument.activeElement).toBe(rail)
})
