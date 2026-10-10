import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it } from 'vitest'

import { HomeObjective } from './home'
import { homeData } from './home-data'
import type { OrganizationOutcome } from './runtime-outcome-types'
import type { Objective, OrganizationSnapshot } from './types'

afterEach(cleanup)
const objective: Objective = { id: 'open', title: 'Owner content 日本語', description: 'Actual scope', ownerId: 'executive', source: 'runtime', status: 'active', createdAt: '2026-10-10T00:00:00Z', progress: 100 }
const snapshot: OrganizationSnapshot = { source: 'runtime', objectives: [objective], agents: [], tasks: [], activity: [], knowledge: [] }
const outcome: OrganizationOutcome = { objectiveId: 'accepted', title: 'Accepted result', summary: 'Recorded acceptance', status: 'accepted', deliverableId: 'artifact', acceptanceRequestId: 'review', evidenceIds: ['artifact'], revision: 1, seen: false, round: 1, archived: false, createdAt: objective.createdAt, updatedAt: objective.createdAt }

it('requires a retained acceptance and deliverable instead of inferring readiness from completed work', () => {
  const value = { ...snapshot, objectives: [objective, { ...objective, id: 'completed', status: 'completed' as const }, { ...objective, id: 'cancelled', status: 'cancelled' as const }], outcomes: { generation: '1', total: 99, unread: 99, hasMore: true, nextCursor: 'cursor', items: [outcome, { ...outcome, objectiveId: 'legacy', status: 'legacy_completed' as const }, { ...outcome, objectiveId: 'cancelled', status: 'cancelled' as const }, { ...outcome, objectiveId: 'missing', deliverableId: null }] } }
  expect(homeData(value).objectives.map(item => item.id)).toEqual(['open'])
  expect(homeData(value).deliverables.map(item => item.objectiveId)).toEqual(['accepted'])
  expect(homeData(snapshot).deliverables).toEqual([])
})

it('takes recent activity without mutating ledger order or including routine worker queues as owner requests', () => {
  const events = Array.from({ length: 6 }, (_, i) => ({ id: String(i), source: 'runtime' as const, kind: 'system' as const, text: `Recorded ${i}`, timestamp: `2026-10-10T00:00:0${i}Z` }))
  const requests = ['queued', 'running', 'pending_intervention', 'waiting_response', 'completed', 'cancelled'].map((status, i) => ({ id: String(i), status })) as OrganizationSnapshot['requests']
  const value = { ...snapshot, activity: events, requests }
  expect(homeData(value).activity.map(item => item.id)).toEqual(['5', '4', '3', '2'])
  expect(events.map(item => item.id)).toEqual(['0', '1', '2', '3', '4', '5'])
  expect(homeData(value).requests.map(item => item.id)).toEqual(['2'])
})

it('labels task completion independently of acceptance and preserves owner-authored content', () => {
  const view = render(<MemoryRouter><HomeObjective objective={objective} snapshot={snapshot} /></MemoryRouter>)
  expect(screen.getByRole('progressbar', { name: '100% of tasks completed' })).toBeTruthy()
  expect(screen.getByRole('link', { name: objective.title }).getAttribute('href')).toBe('/objectives/open')
  expect(screen.queryByText('Accepted deliverable')).toBeNull()
  view.rerender(<MemoryRouter><HomeObjective objective={{ ...objective, source: 'prototype' }} snapshot={snapshot} /></MemoryRouter>)
  expect(screen.getByRole('progressbar', { name: '100% · local estimate' })).toBeTruthy()
  view.rerender(<MemoryRouter><HomeObjective objective={{ ...objective, progress: undefined }} snapshot={snapshot} /></MemoryRouter>)
  expect(screen.queryByRole('progressbar')).toBeNull()
  expect(screen.getByText('Progress not reported')).toBeTruthy()
})
