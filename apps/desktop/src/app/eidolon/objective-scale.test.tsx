import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it } from 'vitest'

import { createPrototypeAdapter } from '../../../test-fixtures/organization-prototype'

import { OrganizationWorkspace } from './workspace'

afterEach(cleanup)
it.each([1, 2, 300, 10000])('bounds objective rows and navigates/filter %i records', count => {
  const adapter = createPrototypeAdapter()
  const base = adapter.createObjective('Seed')
  const snapshot = { ...adapter.getSnapshot(), objectives: Array.from({ length: count }, (_, i) => ({ ...base, id: `scale-${i}`, title: `Goal ${String(i).padStart(5, '0')}` })) }
  render(<MemoryRouter initialEntries={['/objectives']}><OrganizationWorkspace adapter={{ ...adapter, getSnapshot: () => snapshot }} /></MemoryRouter>)
  expect(globalThis.document.querySelectorAll('.eid-list a.eid-row').length).toBe(Math.min(count, 25))

  if (count > 25) {
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(screen.getByRole('link', { name: /Goal 00025/ })).toBeTruthy()
  }

  fireEvent.change(screen.getByRole('textbox', { name: 'Search objectives' }), { target: { value: `Goal ${String(count - 1).padStart(5, '0')}` } })
  expect(globalThis.document.querySelectorAll('.eid-list a.eid-row').length).toBe(1)
  fireEvent.click(screen.getByRole('link', { name: new RegExp(`Goal ${String(count - 1).padStart(5, '0')}`) }))
  expect(screen.getByRole('heading', { name: `Goal ${String(count - 1).padStart(5, '0')}` })).toBeTruthy()
})
