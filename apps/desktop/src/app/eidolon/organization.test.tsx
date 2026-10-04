import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it } from 'vitest'

import { createPrototypeAdapter } from '../../../test-fixtures/organization-prototype'

import { OrganizationWorkspace } from './workspace'

it('opens an example agent inspector without inventing a runtime profile', () => {
  const adapter = createPrototypeAdapter()
  adapter.loadDemo()
  render(<MemoryRouter initialEntries={['/organization']}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  expect(screen.getByText(/Static fictional example/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /Inspect Engineering/ }))
  expect(screen.getByRole('complementary', { name: 'Agent details' })).toBeTruthy()
  expect(screen.getByText('No runtime profile linked')).toBeTruthy()
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(screen.queryByRole('complementary', { name: 'Agent details' })).not.toBeTruthy()
})
