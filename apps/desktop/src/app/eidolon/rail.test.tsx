import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
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
  fireEvent.click(screen.getByRole('link', { name: 'Home' }))
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
  const summary = screen.getByText('History', { selector: 'summary' })
  const details = summary.closest('details')!
  fireEvent.change(input, { target: { value: 'Unfinished side chat' } })

  for (let cycle = 0; cycle < 3; cycle++) {
    fireEvent.click(summary)
    expect(details.open).toBe(true)
    expect(screen.getByRole('link', { name: 'Legacy Kanban' })).toBeTruthy()
    fireEvent.click(screen.getByRole('link', { name: 'Legacy prototype history' }))
    fireEvent.click(summary)
    expect(details.open).toBe(false)
    fireEvent.click(screen.getByRole('link', { name: 'Home' }))
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

it('keeps the approved primary destinations in order and retains secondary operational routes', () => {
  const { container } = render(<MemoryRouter><OrganizationRail needsYouCount={3} sessions={<span>History</span>} /></MemoryRouter>)
  const primary = container.querySelector('nav[aria-label="Primary"]')!
  expect(Array.from(primary.querySelectorAll('a'), item => item.getAttribute('aria-label'))).toEqual(['Home', 'Messages', 'Work Overview', 'Needs You', 'Organization', 'Deliverables', 'Settings'])
  expect(screen.getByRole('link', { name: 'Settings' }).getAttribute('href')).toBe('/settings')
  fireEvent.click(screen.getByText('History', { selector: 'summary' }))
  expect(screen.getByRole('link', { name: 'Needs You' }).getAttribute('href')).toBe('/requests')
  expect(screen.queryByRole('link', { name: 'Activity' })).toBeNull()
})

it('keeps Settings in the primary destination order with one route owner', () => {
  const onNavigate = vi.fn()
  render(<MemoryRouter><OrganizationRail onNavigate={onNavigate} sessions={<span>History</span>} /></MemoryRouter>)
  const primary = screen.getByRole('navigation', { name: 'Primary' })
  expect([...primary.querySelectorAll('a')].map(link => link.getAttribute('href'))).toEqual([
    '/home', '/messages', '/work', '/requests', '/organization', '/artifacts?source=organization', '/settings'
  ])
  expect(screen.getAllByRole('link', { name: 'Settings' })).toHaveLength(1)
  fireEvent.click(screen.getByRole('link', { name: 'Settings' }))
  expect(onNavigate).toHaveBeenCalledWith('/settings')
})


it('keeps secondary features contextual and restores focus after Escape without remounting history', () => {
  const openProfileChats = vi.fn()
  render(<MemoryRouter initialEntries={['/home']}><OrganizationRail onOpenProfileChats={openProfileChats} sessions={<input aria-label="Retained history filter" defaultValue="draft filter" />} /></MemoryRouter>)
  const trigger = screen.getByText('History', { selector: 'summary' })
  const details = trigger.closest('details')!
  const input = screen.getByLabelText('Retained history filter')
  expect(details.open).toBe(false)
  expect(screen.queryByText('Tools and configuration')).toBeNull()
  expect(screen.queryByText('Organization evidence · current connection and profile')).toBeNull()
  fireEvent.click(trigger)
  fireEvent.click(screen.getByRole('button', { name: 'Profile chats' }))
  expect(openProfileChats).toHaveBeenCalledOnce()
  fireEvent.keyDown(input, { key: 'Escape' })
  expect(details.open).toBe(false)
  expect(document.activeElement).toBe(trigger)
  fireEvent.click(trigger)
  expect(screen.getByLabelText('Retained history filter')).toBe(input)
  expect((input as HTMLInputElement).value).toBe('draft filter')
})

it('leaves Escape with a portaled session dialog or a nested control that consumed it', () => {
  render(<MemoryRouter initialEntries={['/home']}><OrganizationRail sessions={<>
    {createPortal(<input aria-label="Session dialog input" />, document.body)}
    <input aria-label="Nested control" onKeyDown={event => event.preventDefault()} />
  </>} /></MemoryRouter>)
  const trigger = screen.getByText('History', { selector: 'summary' })
  const details = trigger.closest('details')!
  fireEvent.click(trigger)
  expect(details.open).toBe(true)
  const dialogInput = screen.getByLabelText('Session dialog input')
  dialogInput.focus()
  fireEvent.keyDown(dialogInput, { key: 'Escape' })
  expect(details.open).toBe(true)
  expect(document.activeElement).toBe(dialogInput)
  const nested = screen.getByLabelText('Nested control')
  nested.focus()
  fireEvent.keyDown(nested, { key: 'Escape' })
  expect(details.open).toBe(true)
  expect(document.activeElement).toBe(nested)
})

it.each(['/work', '/objectives', '/objectives/retained', '/activity'])('keeps Work selected and exposes its destinations at %s', route => {
  render(<MemoryRouter initialEntries={[route]}><OrganizationRail sessions={null} /></MemoryRouter>)
  expect(screen.getByRole('link', { name: 'Work Overview' }).getAttribute('aria-current')).toBe('page')
  expect(screen.getByRole('link', { name: 'Objectives' }).getAttribute('href')).toBe('/objectives')
  expect(screen.getByRole('link', { name: 'Activity' }).getAttribute('href')).toBe('/activity')
})

it.each(['/settings', '/skills', '/messaging', '/profiles'])('keeps configuration discoverable in Settings context at %s', route => {
  render(<MemoryRouter initialEntries={[route]}><OrganizationRail sessions={null} /></MemoryRouter>)
  expect(screen.getByRole('link', { name: 'Settings' }).getAttribute('aria-current')).toBe('page')
  expect(screen.getByRole('link', { name: 'Skills' }).getAttribute('href')).toBe('/skills')
})

it('opens organization deliverables while preserving a distinct Needs You destination', () => {
  render(<MemoryRouter initialEntries={['/artifacts']}><OrganizationRail needsYouCount={3} sessions={null} /></MemoryRouter>)
  expect(screen.getByRole('link', { name: 'Deliverables' }).getAttribute('href')).toBe('/artifacts?source=organization')
  expect(screen.getByRole('link', { name: 'Deliverables' }).getAttribute('aria-current')).toBe('page')
  expect(screen.getByRole('link', { name: 'Needs You' }).textContent).toContain('3')
  expect(screen.queryByRole('button', { name: 'More workspace options' })).toBeNull()
})
