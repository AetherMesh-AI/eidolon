import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it } from 'vitest'

import { createPrototypeAdapter } from './adapter'
import { OrganizationWorkspace } from './workspace'

it.each([['waiting', 'Waiting'], ['needs_input', 'Needs Input'], ['archived', 'Archived']])('keeps local %s state consistent through details, inspection and filtered navigation', (status, label) => {
  const adapter = createPrototypeAdapter()
  const objective = adapter.createObjective('Track launch')
  adapter.createObjective('Unrelated planning')
  const before = adapter.getSnapshot()
  render(<MemoryRouter initialEntries={[`/objectives/${objective.id}`]}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  fireEvent.change(screen.getByRole('combobox', { name: 'Local objective state' }), { target: { value: status } })
  const after = adapter.getSnapshot()
  expect(after.objectives.find(item => item.id === objective.id)?.status).toBe(status)
  expect(after.tasks).toEqual(before.tasks)
  expect(after.agents).toEqual(before.agents)
  expect(after.knowledge).toEqual(before.knowledge)
  expect(after.activity[0]).toMatchObject({ objectiveId: objective.id, source: 'prototype' })
  expect(after.activity[0].text).toContain('No live execution was changed.')
  fireEvent.click(screen.getByRole('button', { name: 'Inspect objective' }))
  expect(within(screen.getByRole('complementary', { name: 'Objective details' })).getByText(label)).toBeTruthy()
  fireEvent.keyDown(document, { key: 'Escape' })
  fireEvent.click(screen.getByRole('link', { name: '← Objectives' }))
  fireEvent.change(screen.getByRole('combobox', { name: 'Status' }), { target: { value: status } })
  expect(screen.getByRole('link', { name: new RegExp(`Track launch.*${label}`) })).toBeTruthy()
  expect(screen.queryByRole('link', { name: /Unrelated planning/ })).toBeNull()
  fireEvent.click(screen.getByRole('link', { name: /Track launch/ }))
  fireEvent.change(screen.getByRole('combobox', { name: 'Local objective state' }), { target: { value: 'planning' } })
  expect(adapter.getSnapshot().objectives.find(item => item.id === objective.id)?.status).toBe('planning')
})

it('offers only objective states in the objective filter', () => {
  render(<MemoryRouter initialEntries={['/objectives']}><OrganizationWorkspace adapter={createPrototypeAdapter()} /></MemoryRouter>)
  const filter = within(screen.getByRole('combobox', { name: 'Status' }))

  for (const label of ['Waiting', 'Needs Input', 'Archived']) {expect(filter.getByRole('option', { name: label })).toBeTruthy()}

  for (const label of ['Pending', 'Approved', 'Rejected', 'Recorded']) {expect(filter.queryByRole('option', { name: label })).toBeNull()}
})

it('records a user outcome and exposes its scoped artifact', () => {
  const adapter = createPrototypeAdapter()
  const objective = adapter.createObjective('Prepare launch notes')
  render(<MemoryRouter initialEntries={[`/objectives/${objective.id}`]}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  fireEvent.change(screen.getByRole('textbox', { name: 'Local outcome summary' }), { target: { value: 'Launch notes reviewed by the team.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Record local outcome' }))
  expect(screen.getByText('Completed', { selector: '.eid-status' })).toBeTruthy()
  fireEvent.click(screen.getByRole('tab', { name: 'Artifacts' }))
  fireEvent.click(screen.getByRole('button', { name: /Prepare launch notes — recorded outcome/ }))
  expect(screen.getByRole('complementary', { name: 'Artifact details' })).toBeTruthy()
  expect(screen.getByText(/User-recorded outcome/)).toBeTruthy()
})

it('inspects an objective without claiming a runtime owner and restores focus', () => {
  const adapter = createPrototypeAdapter()
  const objective = adapter.createObjective('Inspect launch')
  render(<MemoryRouter initialEntries={[`/objectives/${objective.id}`]}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  const opener = screen.getByRole('button', { name: 'Inspect objective' })
  opener.focus()
  fireEvent.click(opener)
  expect(screen.getByRole('complementary', { name: 'Objective details' })).toBeTruthy()
  expect(screen.getByText('No live session assigned', { selector: 'dd' })).toBeTruthy()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('complementary', { name: 'Objective details' })).toBeNull()
  expect(document.activeElement).toBe(opener)
})

it('surfaces a pending decision and records a prototype-only approval', () => {
  const adapter = createPrototypeAdapter()
  adapter.loadDemo()
  render(<MemoryRouter initialEntries={['/objectives/demo-identity']}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  fireEvent.click(screen.getByRole('tab', { name: 'Decisions' }))
  expect(screen.getByText('Review identity migration plan')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Approve locally' }))
  expect(screen.getByText('Approved')).toBeTruthy()
})

it('takes a goal from Home into its objective workspace without claiming execution', () => {
  render(<MemoryRouter initialEntries={['/home']}><OrganizationWorkspace adapter={createPrototypeAdapter()} /></MemoryRouter>)
  fireEvent.change(screen.getByRole('textbox', { name: 'Objective' }), { target: { value: 'Prepare launch notes' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create objective' }))
  expect(screen.getByRole('heading', { name: 'Prepare launch notes' })).toBeTruthy()
  expect(screen.getByText('Planning', { selector: '.eid-status' })).toBeTruthy()
  expect(screen.getByText(/No live planner is connected/)).toBeTruthy()
  fireEvent.click(screen.getByRole('tab', { name: 'Plan' }))
  expect(screen.getByRole('heading', { name: 'Clarify requirements' })).toBeTruthy()
  expect(screen.getByText(/Proposed local plan/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Pause objective' }))
  expect(screen.getByText('Paused', { selector: '.eid-status' })).toBeTruthy()
})
