import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it } from 'vitest'

import { I18nProvider } from '@/i18n/context'

import { Organization } from './organization'
import type { OrganizationSnapshot, OrganizationTask } from './types'
import { WorkGraph } from './work-graph'

function snapshot(): OrganizationSnapshot {
  return {
    source: 'runtime', objectives: [], requests: [], activity: [], knowledge: [],
    agents: ['worker', 'manager'].map(id => ({
      id, name: id, role: id === 'worker' ? 'Worker' : 'Manager',
      responsibilities: [], capabilities: [], status: 'idle', summary: ''
    })),
    tasks: [{
      id: 'waiting-task', objectiveId: 'objective', title: 'Update shared source',
      ownerId: 'worker', assignedById: 'manager', managingAgentId: 'manager',
      status: 'queued', dependsOn: [], writePaths: ['root0/src/shared.ts'],
      coordination: { state: 'waiting', reason: 'Another task owns this write path.', blockingTaskIds: ['holder-task', 'outside-window'] }
    }, {
      id: 'holder-task', objectiveId: 'another-objective', title: 'Finish shared change',
      ownerId: 'worker', status: 'working', dependsOn: []
    }]
  }
}

it('shows authoritative write waits independently from lifecycle and reconciles reservations without changing ownership', () => {
  const value = snapshot()
  const { rerender } = render(<MemoryRouter><WorkGraph objectiveId="objective" snapshot={value} /></MemoryRouter>)
  const row = screen.getByRole('button', { name: 'Inspect task: Update shared source' })
  expect(within(row).getByText('queued')).toBeTruthy()
  expect(within(row).getByText('Task coordination: Waiting')).toBeTruthy()
  expect(within(row).getByText(/Another task owns this write path/)).toBeTruthy()
  expect(within(row).getByText(/Finish shared change.*outside-window/)).toBeTruthy()
  expect(within(row).getByText('Declared write paths: root0/src/shared.ts')).toBeTruthy()
  expect(within(row).queryByText('working')).toBeNull()
  fireEvent.click(row)
  const inspector = screen.getByRole('complementary', { name: 'Task details' })
  expect(within(inspector).getByText('Owner').nextElementSibling?.textContent).toBe('worker')
  expect(within(inspector).getByText('Assigned by').nextElementSibling?.textContent).toBe('manager')
  expect(within(inspector).getByText('Status').nextElementSibling?.textContent).toBe('queued')
  expect(within(inspector).getByText('Task coordination: Waiting')).toBeTruthy()

  for (const [state, status, label] of [
    ['reserved', 'working', 'Reserved'], ['released', 'completed', 'Released']
  ] as const) {
    const next = { ...value, tasks: [{ ...value.tasks[0], status, coordination: { state, reason: null, blockingTaskIds: [] } }] }
    rerender(<MemoryRouter><WorkGraph objectiveId="objective" snapshot={next} /></MemoryRouter>)
    expect(within(inspector).getByText(`Task coordination: ${label}`)).toBeTruthy()
    expect(within(inspector).getByText('Status').nextElementSibling?.textContent).toBe(status)
    expect(within(inspector).getByText('Owner').nextElementSibling?.textContent).toBe('worker')
    expect(within(inspector).queryByText(/Another task owns/)).toBeNull()
    expect(within(inspector).queryByText(/outside-window/)).toBeNull()
  }

  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(screen.queryByRole('complementary')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'List' }))
  fireEvent.click(screen.getByRole('button', { name: 'Inspect task: Update shared source' }))
  expect(within(screen.getByRole('complementary')).getByText('Task coordination: Released')).toBeTruthy()
})

it('keeps waiting assignments attached to their worker and manager, and leaves legacy scopes unspecified', () => {
  const value = snapshot()
  const { rerender, unmount } = render(<MemoryRouter><Organization snapshot={value} /></MemoryRouter>)
  const worker = screen.getByRole('button', { name: 'Inspect worker' })
  expect(within(worker).getByText(/Update shared source \(queued · Waiting\)/)).toBeTruthy()
  expect(within(screen.getByRole('button', { name: 'Inspect manager' })).getByText(/Managing assignment: Update shared source \(queued · Waiting\)/)).toBeTruthy()
  fireEvent.click(worker)
  const inspector = screen.getByRole('complementary', { name: 'Agent details' })
  expect(within(inspector).getByText('Task coordination: Waiting')).toBeTruthy()
  expect(within(inspector).getByText('Declared write paths: root0/src/shared.ts')).toBeTruthy()
  expect(within(inspector).getByText(/Another task owns this write path/)).toBeTruthy()

  for (const writePaths of [undefined, null, []]) {
    const task: OrganizationTask = { ...value.tasks[0], writePaths, coordination: { state: 'unscoped', reason: null, blockingTaskIds: [] } }
    rerender(<MemoryRouter><Organization snapshot={{ ...value, tasks: [task] }} /></MemoryRouter>)
    expect(within(inspector).getByText('Declared write paths: Not specified')).toBeTruthy()
    expect(within(inspector).getByText('Task coordination: Unscoped')).toBeTruthy()
    expect(within(inspector).queryByText(/root0/)).toBeNull()
  }

  rerender(<MemoryRouter><Organization snapshot={{ ...value, tasks: [{ ...value.tasks[0], writePaths: undefined, coordination: undefined }] }} /></MemoryRouter>)
  expect(within(inspector).queryByText(/Task coordination:/)).toBeNull()
  expect(within(inspector).queryByText(/Declared write paths:/)).toBeNull()
  unmount()
  render(<I18nProvider configClient={null} initialLocale="ja"><MemoryRouter><WorkGraph objectiveId="objective" snapshot={value} /></MemoryRouter></I18nProvider>)
  expect(screen.getByText('タスク調整: 待機中')).toBeTruthy()
  expect(screen.getByText('宣言された書き込みパス: root0/src/shared.ts')).toBeTruthy()
})

it('shows recorded repository bindings independently of write reservations without inventing legacy bindings', () => {
  const value = snapshot()
  const task = { ...value.tasks[0], projectId: 'frontend', coordination: undefined, writePaths: undefined }
  const { rerender } = render(<MemoryRouter><WorkGraph objectiveId="objective" snapshot={{ ...value, tasks: [task] }} /></MemoryRouter>)
  const row = screen.getByRole('button', { name: 'Inspect task: Update shared source' })
  expect(within(row).getByText('Repository: frontend')).toBeTruthy()
  expect(within(row).queryByText(/Task coordination:/)).toBeNull()
  rerender(<MemoryRouter><WorkGraph objectiveId="objective" snapshot={{ ...value, tasks: [{ ...task, projectId: undefined }] }} /></MemoryRouter>)
  expect(within(row).queryByText(/Repository:/)).toBeNull()
})
