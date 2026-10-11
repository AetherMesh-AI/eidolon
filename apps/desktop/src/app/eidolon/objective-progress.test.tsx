import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { ObjectiveProgress } from './objective-progress'
import { objectiveWorkData } from './objective-work-data'
import type { Objective, OrganizationSnapshot, OrganizationTask, OrganizationWorkPackage } from './types'
import { WorkOverview } from './work-overview'

afterEach(cleanup)

const objective: Objective = {
  id: 'objective', title: '日本語 owner content', description: 'Exact requested result', ownerId: 'executive',
  status: 'active', source: 'runtime', createdAt: '2026-10-10T00:00:00Z', progress: 100,
  acceptance: { status: 'reviewing', round: 2, criteria: [], maxReplans: 2, summary: null, deliverableId: 'draft-result' }
}

const assignment: OrganizationWorkPackage = {
  id: 'package', objectiveId: objective.id, round: 2, managerId: 'manager', title: 'Current assignment', description: 'Recorded scope',
  criterionIndexes: [], projectIds: [], dependencyIds: [], maxTasks: 5, planRequestId: 'plan', status: 'working', taskIds: ['current']
}

const task: OrganizationTask = {
  id: 'current', objectiveId: objective.id, title: 'Current work', ownerId: 'worker', status: 'completed', dependsOn: [], currentRound: true, workPackageId: assignment.id
}

const snapshot: OrganizationSnapshot = {
  source: 'runtime', objectives: [objective], agents: [], activity: [], knowledge: [], tasks: [task], requests: []
}

it('excludes earlier rounds and other objectives without inferring delegation from reporting lines', () => {
  const record = { ...objective, workPackages: [assignment, { ...assignment, id: 'old-package', round: 1 }, { ...assignment, id: 'foreign', objectiveId: 'other' }] }

  const state = { ...snapshot, tasks: [task,
    { ...task, id: 'old', historical: true }, { ...task, id: 'prior', currentRound: false },
    { ...task, id: 'foreign', objectiveId: 'other' },
    { ...task, id: 'unlinked', workPackageId: null, managingAgentId: 'manager', status: 'working' as const }
  ] }

  const data = objectiveWorkData(record, state)
  expect(data.tasks.map(item => item.id)).toEqual(['current', 'unlinked'])
  expect(data.groups.map(item => item.package.id)).toEqual(['package'])
  expect(data.groups[0].tasks.map(item => item.id)).toEqual(['current'])
  expect(data.ungrouped.map(item => item.id)).toEqual(['unlinked'])
  expect(data.counts.completed).toBe(1)
  expect(data.accepted).toBe(false)
  expect(state.tasks).toHaveLength(5)
})

it('shows owner intervention only and keeps accepted delivery separate from completed tasks', () => {
  const requests = ['queued', 'running', 'pending_intervention', 'waiting_response'].map((status, i) => ({
    id: String(i), objectiveId: objective.id, status, team: 'Product', reason: `Reason ${i}`
  })) as OrganizationSnapshot['requests']

  const openRequests = vi.fn()
  const openWork = vi.fn()
  render(<ObjectiveProgress objective={objective} onReviewRequests={openRequests} onReviewWork={openWork} snapshot={{ ...snapshot, requests }} />)
  const attention = screen.getByRole('region', { name: 'Needs your input' })
  expect(attention.textContent).toContain('Reason 2')
  expect(attention.textContent).not.toContain('Reason 0')
  expect(screen.getByRole('region', { name: 'Delivery' }).textContent).toContain('has not been recorded')
  fireEvent.click(screen.getByRole('button', { name: 'Review owner requests' }))
  fireEvent.click(screen.getByRole('button', { name: 'Inspect acceptance and delivery' }))
  expect(openRequests).toHaveBeenCalledOnce()
  expect(openWork).toHaveBeenCalledOnce()
})

it('labels unknown progress and queues, never invents phase history, and requires an accepted deliverable ID', () => {
  const accepted = { ...objective, progress: undefined, acceptance: { ...objective.acceptance!, status: 'accepted' as const, deliverableId: null } }
  const { rerender } = render(<ObjectiveProgress objective={accepted} onReviewRequests={vi.fn()} onReviewWork={vi.fn()} snapshot={{ ...snapshot, requests: undefined }} />)
  expect(screen.queryByRole('progressbar')).toBeNull()
  expect(screen.getByText('Phase not reported')).toBeTruthy()
  expect(screen.getByText('This runtime has not supplied its request queue.')).toBeTruthy()
  expect(screen.getByRole('region', { name: 'Delivery' }).textContent).toContain('has not been recorded')
  rerender(<ObjectiveProgress objective={{ ...accepted, acceptance: { ...accepted.acceptance, deliverableId: 'accepted-evidence' } }} onReviewRequests={vi.fn()} onReviewWork={vi.fn()} snapshot={snapshot} />)
  expect(screen.getByRole('region', { name: 'Delivery' }).textContent).toContain('Accepted deliverable')
})

it('keeps work overview scoped to open objectives and labels retained data after connection loss', () => {
  const paused = { ...objective, dispatchControl: { revision: 1, paused: true, runningCount: 2 } } as Objective
  render(<MemoryRouter><WorkOverview snapshot={{ ...snapshot, objectives: [paused, { ...objective, id: 'closed', title: 'Closed work', status: 'completed' }], connection: { state: 'error', scope: 'scope' } }} /></MemoryRouter>)
  const card = screen.getByRole('region', { name: objective.title })
  expect(within(card).getByText(/New dispatch paused/)).toBeTruthy()
  expect(within(card).getByRole('link', { name: 'View objective →' }).getAttribute('href')).toBe('/objectives/objective')
  expect(screen.queryByRole('region', { name: 'Closed work' })).toBeNull()
  expect(screen.getByText('Last known records · reconnect to refresh')).toBeTruthy()
})
