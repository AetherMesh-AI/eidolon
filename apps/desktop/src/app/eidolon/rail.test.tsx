import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { OrganizationRail } from './rail'

it('fronts the workspace when re-clicking the current organization page', () => {
  const onNavigate = vi.fn()
  render(<MemoryRouter initialEntries={['/home']}><OrganizationRail onNavigate={onNavigate} sessions={<span>Canonical session tree</span>} /></MemoryRouter>)
  fireEvent.click(screen.getByRole('link', { name: 'Command' }))
  expect(onNavigate).toHaveBeenCalledWith('/home')
})
