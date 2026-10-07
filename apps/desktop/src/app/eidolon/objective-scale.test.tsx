import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
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
  // A locked detail filter must not mount every unrelated objective as an option.
  const activityObjective = screen.getByRole('combobox', { name: 'Activity objective' })
  expect(activityObjective).toHaveProperty('disabled', true)
  expect(activityObjective).toHaveProperty('value', `scale-${count - 1}`)
  expect(within(activityObjective).getAllByRole('option')).toHaveLength(1)
})


it('distinguishes filtered results from an empty ledger and restores work by clearing filters', () => {
  const adapter = createPrototypeAdapter()
  adapter.createObjective('Existing objective')
  render(<MemoryRouter initialEntries={['/objectives']}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  fireEvent.change(screen.getByRole('textbox', { name: 'Search objectives' }), { target: { value: 'no matching title' } })
  expect(screen.getByRole('heading', { name: 'No matching objectives' })).toBeTruthy()
  expect(screen.queryByRole('heading', { name: 'No objectives yet' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
  expect(screen.getByRole('link', { name: /Existing objective/ })).toBeTruthy()
  fireEvent.change(screen.getByRole('combobox', { name: 'Status' }), { target: { value: 'completed' } })
  expect(screen.getByRole('heading', { name: 'No matching objectives' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
  expect(screen.getByRole('link', { name: /Existing objective/ })).toBeTruthy()
})
