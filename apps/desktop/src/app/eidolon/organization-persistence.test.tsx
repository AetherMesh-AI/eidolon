import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it } from 'vitest'

import { I18nProvider } from '@/i18n/context'

import { responsibleAgents } from './objective-context'
import { Organization } from './organization'
import type { OrganizationAgent, OrganizationSnapshot } from './types'
import { WorkGraph } from './work-graph'

function member(id: string, role: string, managerId?: string): OrganizationAgent {
  return {
    id, identityId: id, persistent: true, createdAt: '2026-10-01T00:00:00Z',
    name: id, role, managerId, team: id, purpose: `Own ${id} responsibilities.`,
    responsibilities: [`${id} scope`], capabilities: ['work.analyze'],
    status: 'idle', lifecycle: 'active', summary: `${id} summary`, tools: [],
    context: {
      contextSummary: `${id} retained context`, revision: 1, updatedAt: '2026-10-03T00:00:00Z',
      memory: { facts: [`${id} fact`], decisions: [], lessons: [`${id} lesson`], openQuestions: [] },
      recentHistory: [{ requestId: `${id}-request`, objectiveId: 'prior-objective', objectiveTitle: 'Prior objective', taskId: `${id}-task`, requestType: 'work.analyze', summary: `${id} prior result`, evidenceIds: [`${id}-proof`], createdAt: '2026-10-03T00:00:00Z' }]
    }
  }
}

function snapshot(): OrganizationSnapshot {
  return {
    source: 'runtime', objectives: [], tasks: [], requests: [], activity: [], knowledge: [],
    agents: [member('executive', 'Executive'), member('director', 'Manager', 'executive'), member('engineering', 'Manager', 'executive'), member('analyst', 'Worker', 'engineering')],
    runtime: { state: 'ready', maxWorkers: 2, maxInflight: 1, rosterCount: 4, workingCount: 0, scope: 'Explicit grants only', capabilities: ['work.analyze'] }
  }
}

it('retains roster identities and selected memory through repeated polls, idle and stopped execution', () => {
  const active = snapshot()
  active.agents[3].status = 'working'
  active.runtime!.workingCount = 1
  const { rerender } = render(<MemoryRouter><Organization snapshot={active} /></MemoryRouter>)
  const row = screen.getByRole('button', { name: 'Inspect analyst' })
  fireEvent.click(row)
  const inspector = screen.getByRole('complementary', { name: 'Agent details' })
  expect(within(inspector).getByText('analyst fact')).toBeTruthy()
  expect(within(inspector).queryByText('engineering fact')).toBeNull()
  expect(within(inspector).getByText('analyst prior result')).toBeTruthy()
  // Old objectives outside the bounded snapshot are not broken navigation links.
  expect(within(inspector).queryByRole('link', { name: 'Prior objective' })).toBeNull()

  for (const state of ['ready', 'stopped', 'stopped', 'disabled']) {
    const next = snapshot()
    next.runtime!.state = state
    next.agents[3].lifecycle = state === 'disabled' ? 'disabled' : 'active'
    rerender(<MemoryRouter><Organization snapshot={next} /></MemoryRouter>)
    expect(screen.getByRole('button', { name: 'Inspect analyst' })).toBe(row)
    expect(screen.getAllByRole('button', { name: /^Inspect / })).toHaveLength(next.agents.length)
    expect(screen.getByRole('complementary', { name: 'Agent details' })).toBe(inspector)
    expect(within(inspector).getByText('analyst lesson')).toBeTruthy()
    expect(within(inspector).getByText('analyst prior result')).toBeTruthy()
  }

  const roster = screen.getByLabelText('Persistent roster')
  expect(within(roster).getByText('4')).toBeTruthy()
  expect(within(roster).getByText('0')).toBeTruthy()
  expect(within(roster).getByText('1')).toBeTruthy()
  expect(screen.queryByRole('button', { name: /hire|activate|enable|grant/i })).toBeNull()
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(screen.queryByRole('complementary', { name: 'Agent details' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Inspect engineering' }))
  expect(screen.getByText('engineering fact')).toBeTruthy()
  expect(screen.queryByText('analyst fact')).toBeNull()
})

it('shows cross-domain management separately from authorship and preserves role/history IDs in localized inspection', () => {
  const value = snapshot()
  value.objectives = [{ id: 'prior-objective', title: 'Prior objective', description: 'Review retained context', ownerId: 'executive', managerId: 'director', status: 'active', source: 'runtime', createdAt: '2026-10-01T00:00:00Z' }]
  value.tasks = [{ id: 'task', objectiveId: 'prior-objective', title: 'Analyze source context', ownerId: 'analyst', assignedById: 'director', managingAgentId: 'engineering', status: 'working', dependsOn: [] }]
  expect(responsibleAgents(value, value.objectives[0]).map(agent => agent.id)).toEqual(value.agents.map(agent => agent.id))
  const { unmount } = render(<MemoryRouter><Organization snapshot={value} /><WorkGraph objectiveId="prior-objective" snapshot={value} /></MemoryRouter>)
  const retainedDirector = within(screen.getByRole('button', { name: 'Inspect director' }))
  expect(retainedDirector.getByText('Manager')).toBeTruthy()
  expect(retainedDirector.getByText(/Planned assignment: Analyze source context/)).toBeTruthy()
  const domain = within(screen.getByRole('button', { name: 'Inspect engineering' }))
  expect(domain.getByText(/Managing assignment: Analyze source context/)).toBeTruthy()
  expect(screen.getByText(/collaboration never expands access/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Inspect task: Analyze source context' }))
  const task = within(screen.getByRole('complementary', { name: 'Task details' }))
  expect(task.getByText('Scoped manager')).toBeTruthy()
  expect(task.getByText('engineering')).toBeTruthy()
  expect(task.getByText('director')).toBeTruthy()
  unmount()

  render(<I18nProvider configClient={null} initialLocale="ja"><MemoryRouter><Organization snapshot={value} /></MemoryRouter></I18nProvider>)
  fireEvent.click(screen.getByRole('button', { name: 'Inspect analyst' }))
  const agent = within(screen.getByRole('complementary', { name: 'Agent details' }))
  expect(agent.getByRole('region', { name: '個別の記憶' })).toBeTruthy()
  expect(agent.getByText('ワーカー')).toBeTruthy()
  expect(agent.getByRole('link', { name: 'Prior objective' }).getAttribute('href')).toBe('/objectives/prior-objective')
  expect(agent.getByText('analyst-proof')).toBeTruthy()
})
