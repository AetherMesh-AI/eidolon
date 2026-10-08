import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter } from './runtime-adapter'
import type { OrganizationSnapshot } from './types'

it('retains the last good ownership snapshot on malformed packages and accepts older runtimes', async () => {
  const value: OrganizationSnapshot = {
    source: 'runtime',
    tasks: [],
    agents: [],
    activity: [],
    knowledge: [],
    requests: [],
    runtime: { state: 'ready', capabilities: [], maxWorkers: 2, scope: '' },
    objectives: [
      {
        id: 'objective',
        title: 'Goal',
        description: '',
        ownerId: 'executive',
        source: 'runtime',
        status: 'active',
        createdAt: '',
        planningMode: 'executive_packages',
        workPackages: [
          {
            id: 'package',
            objectiveId: 'objective',
            round: 0,
            managerId: 'manager',
            title: 'Plan',
            description: '',
            criterionIndexes: [0],
            projectIds: [],
            dependencyIds: [],
            maxTasks: 2,
            planRequestId: 'plan',
            status: 'working',
            taskIds: []
          }
        ]
      }
    ]
  }

  const request = vi.fn().mockResolvedValue(value)

  const adapter = createRuntimeAdapter({
    request,
    getScope: () => ({ key: 'test', connected: true }),
    subscribeScope: () => () => undefined
  })

  await adapter.refresh()
  expect(adapter.getSnapshot().objectives).toEqual(value.objectives)

  for (const change of [
    { objectiveId: 'different' },
    { criterionIndexes: [-1] },
    { taskIds: 'task' },
    { status: 'accepted' }, { criteria: ['First', 'Mismatched second'] }, { criteria: [42] }
  ]) {
    const invalid = structuredClone(value)
    Object.assign(invalid.objectives[0].workPackages![0], change)
    request.mockResolvedValue(invalid)
    await adapter.refresh()
    expect(adapter.getSnapshot().connection?.state).toBe('error')
    expect(adapter.getSnapshot().objectives).toEqual(value.objectives)
  }

  const legacy = {
    ...value,
    objectives: value.objectives.map(({ planningMode: _mode, workPackages: _packages, ...objective }) => objective)
  }

  request.mockResolvedValue(legacy)
  await adapter.refresh()
  expect(adapter.getSnapshot().connection?.state).toBe('ready')
  expect(adapter.getSnapshot().objectives[0].workPackages).toBeUndefined()
})
