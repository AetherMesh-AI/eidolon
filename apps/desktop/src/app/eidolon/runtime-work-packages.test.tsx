import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it } from 'vitest'

import { I18nProvider } from '@/i18n/context'

import { createRuntimeAdapter } from './runtime-adapter'
import { RuntimeObjectiveDetail } from './runtime-detail'
import { RuntimeWorkPackages } from './runtime-work-packages'
import type { OrganizationSnapshot, OrganizationWorkPackage } from './types'

function snapshot(): OrganizationSnapshot {
  const first: OrganizationWorkPackage = {
    id: 'package-backend',
    objectiveId: 'objective',
    round: 1,
    managerId: 'backend-manager',
    title: 'Deliver backend',
    description: 'Implement the backend contract.',
    criterionIndexes: [0],
    projectIds: ['backend'],
    dependencyIds: [],
    maxTasks: 2,
    planRequestId: 'plan-backend',
    status: 'completed',
    taskIds: ['task-backend']
  }

  return {
    source: 'runtime',
    agents: [
      {
        id: 'backend-manager',
        name: 'Backend manager',
        role: 'Manager',
        status: 'idle',
        responsibilities: [],
        capabilities: [],
        summary: ''
      }
    ],
    tasks: [
      {
        id: 'task-backend',
        workPackageId: first.id,
        objectiveId: 'objective',
        title: 'Implement API',
        ownerId: 'worker',
        managingAgentId: first.managerId,
        status: 'completed',
        dependsOn: []
      }
    ],
    activity: [],
    knowledge: [],
    requests: [],
    runtime: { state: 'ready', capabilities: [], maxWorkers: 2, scope: 'Synthetic test' },
    objectives: [
      {
        id: 'objective',
        title: 'Ship the coordinated change',
        description: 'Two repositories.',
        ownerId: 'executive',
        source: 'runtime',
        status: 'active',
        createdAt: '2026-10-08T00:00:00Z',
        planningMode: 'executive_packages',
        acceptance: {
          status: 'reviewing',
          round: 1,
          maxReplans: 2,
          criteria: ['API works', 'UI works'],
          summary: null,
          deliverableId: null
        },
        workPackages: [
          {
            ...first,
            id: 'package-old',
            title: 'Earlier approach',
            round: 0,
            managerId: 'retired-manager',
            status: 'cancelled',
            criterionIndexes: [4], criteria: ['The original release must include migration notes.'],
            taskIds: ['retained-old-task']
          },
          first,
          {
            ...first,
            id: 'package-ui',
            title: 'Deliver UI',
            managerId: 'ui-manager',
            status: 'working',
            criterionIndexes: [1],
            projectIds: ['frontend'],
            dependencyIds: [first.id],
            planRequestId: 'plan-ui',
            taskIds: []
          }
        ]
      }
    ]
  }
}

it('keeps manager packages, historical scope and task provenance separate from whole-objective acceptance', () => {
  const initial = snapshot()

  const adapter = createRuntimeAdapter({
    request: async <T,>() => initial as T,
    getScope: () => ({ key: 'test', connected: true }),
    subscribeScope: () => () => undefined
  })

  const view = render(
    <MemoryRouter>
      <RuntimeObjectiveDetail adapter={adapter} objective={initial.objectives[0]} snapshot={initial} />
    </MemoryRouter>
  )

  const packages = screen.getByRole('region', { name: 'Manager work packages' })
  const current = within(packages).getByRole('list', { name: 'Current round' })
  const backend = within(current).getByRole('listitem', { name: 'Deliver backend' })
  const frontend = within(current).getByRole('listitem', { name: 'Deliver UI' })
  expect(within(backend).getByText('Backend manager (backend-manager)')).toBeTruthy()
  expect(within(backend).getByText('Root criterion numbers').nextElementSibling?.textContent).toBe('1')
  expect(within(backend).getByText('Repository scope').nextElementSibling?.textContent).toBe('backend')
  expect(within(frontend).getByText('Package prerequisites').nextElementSibling?.textContent).toContain(
    'Deliver backend (Completed) · package-backend'
  )
  expect(within(frontend).getByText('Working')).toBeTruthy()
  expect(screen.getByRole('heading', { name: initial.objectives[0].title }).closest('header')?.textContent).toContain(
    'Active'
  )
  expect(screen.getByText('No accepted final result yet.')).toBeTruthy()
  expect(within(current).queryByText('Earlier approach')).toBeNull()
  fireEvent.click(within(packages).getByText('Previous-round packages (1)'))
  const historic = within(packages).getByRole('list', { name: 'Previous-round packages' })
  expect(within(historic).getByText('retired-manager')).toBeTruthy()
  expect(within(historic).getByText('Root criterion numbers').nextElementSibling?.textContent).toBe('5. The original release must include migration notes.')
  expect(within(historic).getByText('retained-old-task')).toBeTruthy()
  expect(within(historic).queryByText('API works')).toBeNull()

  const inspect = screen.getByRole('button', { name: 'Inspect objective' })
  inspect.focus()
  fireEvent.click(inspect)
  const inspector = screen.getByRole('complementary', { name: 'Objective details' })
  expect(within(inspector).getByRole('region', { name: 'Manager work packages' })).toBeTruthy()
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(screen.queryByRole('complementary')).toBeNull()
  expect(globalThis.document.activeElement).toBe(inspect)
  fireEvent.click(screen.getByRole('button', { name: 'Inspect task: Implement API' }))
  expect(within(screen.getByRole('complementary')).getByText('Work package').nextElementSibling?.textContent).toBe(
    'Deliver backend (package-backend)'
  )
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  fireEvent.click(screen.getByRole('tab', { name: 'Activity' }))
  fireEvent.click(screen.getByRole('tab', { name: 'Work' }))
  expect(screen.queryByRole('complementary')).toBeNull()
  const next = structuredClone(initial)
  next.objectives[0].workPackages!.forEach(item => {
    if (item.round === 1) {
      item.status = 'completed'
    }
  })
  view.rerender(
    <MemoryRouter>
      <RuntimeObjectiveDetail adapter={adapter} objective={next.objectives[0]} snapshot={next} />
    </MemoryRouter>
  )
  expect(screen.getByRole('heading', { name: initial.objectives[0].title }).closest('header')?.textContent).toContain(
    'Active'
  )
  expect(screen.getByText('No accepted final result yet.')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Inspect objective' }))
  expect(within(screen.getByRole('complementary')).getAllByText('Completed')).toHaveLength(2)
})

it('labels legacy planning without inventing packages and localizes empty delegation and package state', () => {
  const value = snapshot()
  const objective = { ...value.objectives[0], planningMode: 'legacy' as const, workPackages: [] }
  const view = render(<RuntimeWorkPackages objective={objective} snapshot={value} />)
  expect(screen.getByText('Planning mode: Legacy manager planning')).toBeTruthy()
  expect(screen.queryByRole('list')).toBeNull()
  view.rerender(
    <RuntimeWorkPackages
      objective={{ ...objective, planningMode: undefined, workPackages: undefined }}
      snapshot={value}
    />
  )
  expect(screen.queryByRole('region')).toBeNull()
  view.rerender(
    <I18nProvider configClient={null} initialLocale="ja">
      <RuntimeWorkPackages objective={{ ...objective, planningMode: 'executive_packages' }} snapshot={value} />
    </I18nProvider>
  )
  expect(screen.getByText('このラウンドの作業パッケージはまだ記録されていません。')).toBeTruthy()
  view.rerender(
    <I18nProvider configClient={null} initialLocale="ja">
      <RuntimeWorkPackages objective={value.objectives[0]} snapshot={value} />
    </I18nProvider>
  )
  expect(screen.getByText('作業中')).toBeTruthy()
  expect(screen.getAllByText('担当マネージャー')).toHaveLength(value.objectives[0].workPackages!.length)
})
