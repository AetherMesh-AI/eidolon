import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { WorkspacePageHeading } from './workspace-page-heading'

afterEach(cleanup)

it('keeps heading semantics, descriptive copy and action ownership across presentation changes', () => {
  const onImport = vi.fn()
  const actions = <><input aria-label="Import filter" defaultValue="retained draft" /><button onClick={onImport}>Import selected</button></>
  const view = render(<WorkspacePageHeading description="Review before importing." title="Import history">{actions}</WorkspacePageHeading>)
  const filter = screen.getByRole('textbox', { name: 'Import filter' })
  filter.focus()
  fireEvent.change(filter, { target: { value: 'updated draft' } })

  view.rerender(<WorkspacePageHeading className="eid-page-search-heading" description="Review before importing." title="Recorded conversation history">{actions}</WorkspacePageHeading>)

  expect(screen.getByRole('heading', { level: 1, name: 'Recorded conversation history' })).toBeTruthy()
  expect(screen.getByText('Review before importing.')).toBeTruthy()
  expect(screen.getByRole('textbox', { name: 'Import filter' })).toBe(filter)
  expect((filter as HTMLInputElement).value).toBe('updated draft')
  expect(filter.ownerDocument.activeElement).toBe(filter)
  expect(onImport).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Import selected' }))
  expect(onImport).toHaveBeenCalledOnce()
})
