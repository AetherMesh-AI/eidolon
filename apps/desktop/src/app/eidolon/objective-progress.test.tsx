import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'
import { I18nProvider } from '@/i18n/context'

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
  expect(within(card).getAllByRole('list', { name: 'Recorded task states' })).toHaveLength(1)
  expect(within(card).getByRole('link', { name: 'View objective →' }).getAttribute('href')).toBe('/objectives/objective')
  expect(screen.queryByRole('region', { name: 'Closed work' })).toBeNull()
  expect(screen.getByText('Last known records · reconnect to refresh')).toBeTruthy()
})


it('shows only retained objective details and preserves authored phase text', () => {
  const openWork = vi.fn()
  const openRequests = vi.fn()
  const { rerender } = render(<ObjectiveProgress objective={objective} onReviewRequests={openRequests} onReviewWork={openWork} snapshot={snapshot} />)
  const details = within(screen.getByRole('region', { name: 'Objective details' }))
  expect(details.getByText(objective.ownerId!)).toBeTruthy()
  expect(details.getAllByText('Not recorded')).toHaveLength(2)
  expect(screen.getByRole('region', { name: 'Objective details' }).querySelector('time')?.dateTime).toBe(objective.createdAt)
  const changed = { ...objective, phase: 'Owner phase 日本語', priority: 'P2' as const }
  rerender(<ObjectiveProgress objective={changed} onReviewRequests={openRequests} onReviewWork={openWork} snapshot={snapshot} />)
  expect(details.getByText(changed.phase)).toBeTruthy()
  expect(details.getByText(changed.priority)).toBeTruthy()
  expect(openWork).not.toHaveBeenCalled()
  expect(openRequests).not.toHaveBeenCalled()
})


it.each(['ja', 'zh', 'zh-hant', 'ar', 'ru'] as const)('keeps review truth and authored work while localizing the overview (%s)', locale => {
  const record = { ...objective, phase: 'Recorded phase العربية', workPackages: [assignment] }
  const state = { ...snapshot, objectives: [record] }
  const before = JSON.stringify(state)
  const copy = TRANSLATIONS[locale].organizationHome.detail
  const openWork = vi.fn()
  const openRequests = vi.fn()
  render(<I18nProvider configClient={null} initialLocale={locale}><ObjectiveProgress objective={record} onReviewRequests={openRequests} onReviewWork={openWork} snapshot={state} /></I18nProvider>)
  const overview = within(screen.getByRole('region', { name: copy.overview }))
  expect(overview.getByRole('heading', { name: copy.progress })).toBeTruthy()
  expect(overview.getByText(copy.progressNote)).toBeTruthy()
  expect(overview.getByRole('region', { name: copy.delivery }).textContent).toContain(copy.notAccepted)
  expect(overview.getByText(task.title)).toBeTruthy()
  expect(overview.getByText(assignment.description)).toBeTruthy()
  expect(overview.getAllByText(record.phase)).toHaveLength(2)
  expect(overview.queryByRole('button', { name: 'Inspect work and evidence' })).toBeNull()
  expect(openWork).not.toHaveBeenCalled()
  expect(openRequests).not.toHaveBeenCalled()
  fireEvent.click(overview.getByRole('button', { name: copy.inspectWork }))
  fireEvent.click(overview.getByRole('button', { name: copy.inspectRequests }))
  expect(openWork).toHaveBeenCalledOnce()
  expect(openRequests).toHaveBeenCalledOnce()
  expect(JSON.stringify(state)).toBe(before)
})


it.each(['en', 'ar'] as const)('isolates authored RTL and LTR records from interface direction (%s)', locale => {
  const record = { ...objective, phase: 'مرحلة المراجعة (P2)', workPackages: [{ ...assignment, title: 'مراجعة الإصدار', description: 'Scope (release-2)' }] }
  const state = { ...snapshot, objectives: [record], tasks: [{ ...task, title: 'التحقق من النتيجة' }] }
  const before = JSON.stringify(state)
  render(<I18nProvider configClient={null} initialLocale={locale}><ObjectiveProgress objective={record} onReviewRequests={vi.fn()} onReviewWork={vi.fn()} snapshot={state} /></I18nProvider>)

  for (const value of [record.phase, record.workPackages[0].title, record.workPackages[0].description, state.tasks[0].title]) {
    for (const element of screen.getAllByText(value)) { expect(element.getAttribute('dir')).toBe('auto') }
  }

  expect(JSON.stringify(state)).toBe(before)
})

it.each([['ar', 'المدير المسؤول', 'قيد العمل'], ['ru', 'Ответственный менеджер', 'В работе']] as const)('translates recorded manager ownership and assignment state in %s', (locale, managerLabel, stateLabel) => {
  render(<I18nProvider configClient={null} initialLocale={locale}><ObjectiveProgress objective={{ ...objective, workPackages: [assignment] }} onReviewRequests={vi.fn()} onReviewWork={vi.fn()} snapshot={snapshot} /></I18nProvider>)
  expect(screen.getByText(`${managerLabel} ·`, { exact: false }).querySelector('bdi')?.textContent).toBe('manager')
  expect(screen.getByText(stateLabel, { exact: false })).toBeTruthy()
  expect(screen.queryByText('Manager owner', { exact: false })).toBeNull()
})


it.each([['ar', 'التنفيذ والمراجعة'], ['ru', 'Выполнение и проверка']] as const)('uses typed runtime phases consistently in the overview and details (%s)', (locale, label) => {
  const record = { ...objective, phaseCode: 'execution_review', phase: 'Execution and review' }
  render(<I18nProvider configClient={null} initialLocale={locale}><ObjectiveProgress objective={record} onReviewRequests={vi.fn()} onReviewWork={vi.fn()} snapshot={snapshot} /></I18nProvider>)
  expect(screen.getAllByText(label)).toHaveLength(2)
  expect(screen.queryByText('Execution and review')).toBeNull()
  expect(record.phase).toBe('Execution and review')
})


it.each(['executive_planning', 'execution_review', 'integration_review', 'accepted'] as const)(
  'marks only the recorded %s lifecycle step without inventing completed history', phaseCode => {
    render(<ObjectiveProgress objective={{ ...objective, phaseCode }} onReviewRequests={vi.fn()} onReviewWork={vi.fn()} snapshot={snapshot} />)
    const phase = within(screen.getByRole('region', { name: 'Current phase' }))
    const steps = within(phase.getByRole('list', { name: 'Current phase' })).getAllByRole('listitem')
    expect(steps.filter(step => step.getAttribute('aria-current') === 'step')).toHaveLength(1)
    const names = { executive_planning: 'Executive decomposition and manager planning', execution_review: 'Execution and review', integration_review: 'Integrated outcome acceptance', accepted: 'Accepted integrated outcome' }
    expect(steps.find(step => step.getAttribute('aria-current') === 'step')?.textContent).toBe(names[phaseCode])
    expect(steps.filter(step => step.getAttribute('aria-current') === null)).toHaveLength(steps.length - 1)
  }
)

it('keeps intervention and unrecognized phase text outside the lifecycle sequence', () => {
  const { rerender } = render(<ObjectiveProgress objective={{ ...objective, phaseCode: 'intervention' }} onReviewRequests={vi.fn()} onReviewWork={vi.fn()} snapshot={snapshot} />)
  const phase = screen.getByRole('region', { name: 'Current phase' })
  expect(within(phase).queryByRole('list')).toBeNull()
  expect(phase.textContent).toContain('Pending intervention')
  rerender(<ObjectiveProgress objective={{ ...objective, phaseCode: 'future-code', phase: 'Retained future phase' }} onReviewRequests={vi.fn()} onReviewWork={vi.fn()} snapshot={snapshot} />)
  expect(within(phase).queryByRole('list')).toBeNull()
  expect(phase.textContent).toContain('Retained future phase')
})
