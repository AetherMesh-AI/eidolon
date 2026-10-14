import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { PageSearchShell } from './page-search-shell'

afterEach(cleanup)

it('names standalone pages while retaining the controlled search and embedded owner', () => {
  const onSearchChange = vi.fn()
  const props = { heading: 'Capabilities', description: 'Existing tools and skills', onSearchChange, searchPlaceholder: 'Search skills', searchValue: 'retained query' }
  const view = render(<PageSearchShell {...props}>Existing content</PageSearchShell>)
  expect(screen.getByRole('heading', { level: 1, name: 'Capabilities' })).toBeTruthy()
  fireEvent.change(screen.getByRole('textbox', { name: 'Search skills' }), { target: { value: 'new query' } })
  expect(onSearchChange).toHaveBeenCalledExactlyOnceWith('new query')
  view.rerender(<PageSearchShell {...props} embedded>Existing content</PageSearchShell>)
  expect(screen.queryByRole('heading', { name: 'Capabilities' })).toBeNull()
  expect((screen.getByRole('textbox', { name: 'Search skills' }) as HTMLInputElement).value).toBe('retained query')
  expect(screen.getByText('Existing content')).toBeTruthy()
})

it('leaves titlebar clearance to the parent only when embedded', () => {
  const props = { onSearchChange: () => {}, searchPlaceholder: 'Search files', searchValue: '' }
  const view = render(<PageSearchShell {...props}>Content</PageSearchShell>)
  const header = () => screen.getByPlaceholderText('Search files').closest('.grid')!

  expect(header().className).toContain('pt-[calc(var(--titlebar-height)+0.5rem)]')
  view.rerender(
    <PageSearchShell {...props} embedded>
      Content
    </PageSearchShell>
  )
  expect(header().className).toContain('pt-2')
  expect(header().className).not.toContain('titlebar-height')
  expect(screen.getByText('Content')).toBeTruthy()
})
