import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'

import { PageSearchShell } from './page-search-shell'

afterEach(cleanup)

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
