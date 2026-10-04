import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it } from 'vitest'

import { createPrototypeAdapter } from './adapter'
import { OrganizationWorkspace } from './workspace'

it('captures local metadata and edits autonomy without changing work or permissions', () => {
  const adapter = createPrototypeAdapter()
  render(<MemoryRouter initialEntries={['/home']}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  fireEvent.change(screen.getByRole('textbox', { name: 'Objective' }), { target: { value: 'Launch' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create objective' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Summary' }), { target: { value: 'Prepare release' } })
  fireEvent.change(screen.getByRole('combobox', { name: 'Priority' }), { target: { value: 'high' } })
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Local progress (%)' }), { target: { value: '25' } })
  fireEvent.change(screen.getByRole('textbox', { name: 'Current phase' }), { target: { value: 'Scoping' } })
  fireEvent.change(screen.getByRole('textbox', { name: 'Recent milestone' }), { target: { value: 'Brief reviewed' } })
  fireEvent.change(screen.getByRole('textbox', { name: 'Autonomy intent' }), { target: { value: 'Ask before delivery' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save local metadata' }))
  const before = adapter.getSnapshot()
  expect(before.objectives[0]).toMatchObject({ description: 'Prepare release', priority: 'high', progress: 25, phase: 'Scoping', milestone: 'Brief reviewed', autonomyIntent: 'Ask before delivery' })
  fireEvent.change(screen.getByRole('textbox', { name: 'Autonomy intent' }), { target: { value: 'Plan only' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save local metadata' }))
  const after = adapter.getSnapshot()
  expect(after.objectives[0].autonomyIntent).toBe('Plan only')
  expect(after.tasks).toEqual(before.tasks)
  expect(after.agents).toEqual(before.agents)
  expect(after.decisions).toEqual(before.decisions)
  fireEvent.click(screen.getByRole('button', { name: 'Inspect objective' }))
  const inspector = within(screen.getByRole('complementary', { name: 'Objective details' }))

  for (const text of ['High', '25% · local estimate', 'Scoping', 'Brief reviewed', 'Plan only']) {expect(inspector.getByText(text)).toBeTruthy()}
  expect(inspector.getByText(/does not enforce permissions/)).toBeTruthy()
  fireEvent.keyDown(document, { key: 'Escape' })
  fireEvent.click(screen.getByRole('link', { name: '← Objectives' }))
  const card = within(screen.getByRole('link', { name: /Launch/ }))

  for (const text of ['Prepare release', '25% · local estimate', 'Brief reviewed']) {expect(card.getByText(text)).toBeTruthy()}
  expect(card.getByText(/Active agents: 0.*simulated/)).toBeTruthy()
})
