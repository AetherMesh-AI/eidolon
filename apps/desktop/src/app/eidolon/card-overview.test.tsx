import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it } from 'vitest'

import { createPrototypeAdapter } from '../../../test-fixtures/organization-prototype'

import { OrganizationWorkspace } from './workspace'

it('identifies responsible teams from assigned agents and task owners, not roles', () => {
  const adapter = createPrototypeAdapter()
  adapter.loadDemo()
  render(<MemoryRouter initialEntries={['/objectives']}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  const card = within(screen.getByRole('link', { name: /Workspace navigation review/ }))
  expect(card.getByText('Responsible team: Engineering')).toBeTruthy()
})

it.each(['Grid', 'List'])('separates role, team and current assignment on %s agent cards', mode => {
  const adapter = createPrototypeAdapter()
  adapter.loadDemo()
  render(<MemoryRouter initialEntries={['/organization']}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: mode }))
  const card = within(screen.getByRole('button', { name: 'Inspect Engineering' }))
  expect(card.getByText('Architecture & implementation')).toBeTruthy()
  expect(card.getByText('Team: Engineering')).toBeTruthy()
  expect(card.getByText(/Current assignment:.*Implement account adapter/)).toBeTruthy()
  expect(card.getByText(/Current assignment:.*Review compact navigation proposal/)).toBeTruthy()
  expect(card.queryByText(/Current assignment:.*Define account architecture/)).toBeNull()
})


it('shows objective-scoped organization, decisions and artifacts on Overview', () => {
  const adapter = createPrototypeAdapter()
  adapter.loadDemo()
  const view = render(<MemoryRouter initialEntries={['/objectives/demo-workspace']}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  const org = within(screen.getByRole('region', { name: 'Responsible organization subtree' }))
  expect(org.getByText('Engineering')).toBeTruthy()
  expect(org.getByText('Lead')).toBeTruthy()
  expect(org.queryByText('Research')).toBeNull()
  expect(screen.queryByText('Review identity migration plan')).toBeNull()
  expect(screen.queryByText('Identity compatibility report')).toBeNull()
  view.unmount()
  render(<MemoryRouter initialEntries={['/objectives/demo-identity']}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  const decisions = within(screen.getByRole('region', { name: 'Recent decisions' }))
  expect(decisions.getByText('Review identity migration plan')).toBeTruthy()
  expect(decisions.getByText('Example: Lead accepted the staged migration recommendation.')).toBeTruthy()
  expect(decisions.getByText(/Recency unknown/)).toBeTruthy()
  const artifacts = within(screen.getByRole('region', { name: 'Major artifacts' }))
  expect(artifacts.getByText('Identity compatibility report')).toBeTruthy()
  expect(artifacts.queryByText('Token retention policy')).toBeNull()
  expect(artifacts.getByText(/Importance unknown/)).toBeTruthy()
})

it('makes absent responsibility, assignment, decisions and artifacts explicit', () => {
  const base = createPrototypeAdapter()
  const objective = base.createObjective('Unknown scope')
  const snapshot = { ...base.getSnapshot(), agents: [{ id: objective.ownerId, name: 'Owner', role: 'Not a team', responsibilities: [], capabilities: [], status: 'idle' as const, summary: '' }] }
  const adapter = { ...base, getSnapshot: () => snapshot }
  const view = render(<MemoryRouter initialEntries={['/objectives']}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  expect(screen.getByText('Responsible team: Unknown · team not recorded')).toBeTruthy()
  view.unmount()
  const detail = render(<MemoryRouter initialEntries={['/objectives/' + objective.id]}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  expect(within(screen.getByRole('region', { name: 'Recent decisions' })).getByText(/Unknown.*no decisions recorded/i)).toBeTruthy()
  expect(within(screen.getByRole('region', { name: 'Major artifacts' })).getByText(/Unknown.*no artifacts recorded/i)).toBeTruthy()
  detail.unmount()
  render(<MemoryRouter initialEntries={['/organization']}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  expect(screen.getByText('Team: Unknown · team not recorded')).toBeTruthy()
  expect(screen.getByText('Current assignment: Unknown · no current assignment recorded')).toBeTruthy()
})


it('does not present historical or prior-round work as a current roster assignment', () => {
  const base = createPrototypeAdapter()
  base.loadDemo()
  const snapshot = structuredClone(base.getSnapshot())
  const member = snapshot.agents.find(item => item.name === 'Engineering')!
  snapshot.tasks = [
    { id: 'current', objectiveId: 'demo-workspace', title: 'Current work', ownerId: member.id, status: 'working', dependsOn: [] },
    { id: 'historical', objectiveId: 'demo-workspace', title: 'Historical open work', ownerId: member.id, status: 'working', dependsOn: [], historical: true },
    { id: 'prior', objectiveId: 'demo-workspace', title: 'Prior-round open work', ownerId: member.id, status: 'working', dependsOn: [], currentRound: false }
  ]
  const adapter = { ...base, getSnapshot: () => snapshot }
  render(<MemoryRouter initialEntries={['/organization']}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  const card = within(screen.getByRole('button', { name: 'Inspect Engineering' }))
  expect(card.getByText(/Current assignment: Current work/)).toBeTruthy()
  expect(card.queryByText(/Historical open work|Prior-round open work/)).toBeNull()
})
