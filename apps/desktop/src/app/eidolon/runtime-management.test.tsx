import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway } from './runtime-adapter'
import { validOrganizationConfiguration, validTransfers } from './runtime-management'
import type { OrganizationMemberConfiguration, OrganizationSnapshot } from './types'
import { OrganizationWorkspace } from './workspace'

const member: OrganizationMemberConfiguration = {
  id: 'writer',
  name: 'Release writer',
  role: 'Worker',
  manager_id: 'manager',
  team: 'communications',
  capabilities: ['work.draft'],
  enabled: true,
  provider: 'configured-provider',
  model: 'configured-model',
  tool_grants: ['read_file'],
  responsibilities: ['Write release notes'],
  purpose: 'Clear release notes',
  authority: [],
  managed_teams: []
}

function snapshot(): OrganizationSnapshot {
  return {
    source: 'runtime',
    objectives: [],
    requests: [],
    tasks: [],
    knowledge: [],
    activity: [],
    agents: [
      {
        id: 'manager',
        name: 'Release manager',
        role: 'Manager',
        status: 'idle',
        capabilities: ['request.plan'],
        responsibilities: [],
        summary: ''
      }
    ],
    runtime: {
      state: 'ready',
      maxWorkers: 2,
      maxInflight: 2,
      capabilities: ['work.draft'],
      scope: 'Existing granted scope',
      management: {
        generation: 3,
        allowedTools: ['read_file'],
        allowedCapabilities: ['work.draft', 'work.analyze'],
        configuration: { roster: [structuredClone(member)], max_inflight: 2, max_members: 16 }
      }
    }
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void

  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })

  return { promise, resolve, reject }
}

function open(request: (...args: Parameters<OrganizationGateway['request']>) => Promise<unknown>) {
  const gateway: OrganizationGateway = {
    request: async <T,>(...args: Parameters<OrganizationGateway['request']>) => (await request(...args)) as T,
    getScope: () => ({ key: 'a', connected: true }),
    subscribeScope: () => () => undefined
  }

  const adapter = createRuntimeAdapter(gateway)

  const view = render(
    <MemoryRouter initialEntries={['/organization']}>
      <OrganizationWorkspace adapter={adapter} />
    </MemoryRouter>
  )

  return { adapter, ...view }
}

it('saves persistent members and concurrency without losing untouched settings and coalesces rapid submissions', async () => {
  const initial = snapshot()
  const pending = deferred<OrganizationSnapshot>()

  const request = vi
    .fn()
    .mockImplementation((method: string) =>
      method === 'organization.snapshot' ? Promise.resolve(initial) : pending.promise
    )

  const view = open(request)
  fireEvent.click(await screen.findByRole('button', { name: 'Manage organization' }))
  const form = within(screen.getByRole('form', { name: 'Manage organization' }))
  fireEvent.change(form.getByRole('textbox', { name: 'Name' }), { target: { value: 'Technical writer' } })
  fireEvent.change(form.getByRole('spinbutton', { name: 'Concurrent execution limit' }), { target: { value: '3' } })
  fireEvent.click(form.getByRole('checkbox', { name: 'request.question' }))
  fireEvent.click(form.getByRole('checkbox', { name: 'answer.question' }))
  expect(form.queryByRole('checkbox', { name: 'patch' })).toBeNull()
  const save = form.getByRole('button', { name: 'Save organization' })
  fireEvent.click(save)
  fireEvent.submit(save.closest('form')!)
  await waitFor(() => expect(request.mock.calls.filter(call => call[0] === 'organization.configure')).toHaveLength(1))
  const params = request.mock.calls.find(call => call[0] === 'organization.configure')![1]
  expect(params).toMatchObject({
    expectedGeneration: 3,
    configuration: {
      max_inflight: 3,
      max_members: 16,
      roster: [
        {
          ...member,
          name: 'Technical writer',
          capabilities: ['work.draft', 'request.question'],
          authority: ['answer.question']
        }
      ]
    }
  })
  await act(async () => {
    pending.reject(new Error('Acknowledgement lost'))
  })
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Acknowledgement lost')
  expect(form.getByRole('textbox', { name: 'Name' })).toHaveProperty('value', 'Technical writer')
  request.mockResolvedValue(initial)
  fireEvent.click(form.getByRole('button', { name: 'Save organization' }))
  await waitFor(() => expect(screen.queryByRole('form', { name: 'Manage organization' })).toBeNull())
  const calls = request.mock.calls.filter(call => call[0] === 'organization.configure')
  expect(calls[1][1].idempotencyKey).toBe(params.idempotencyKey)
  view.unmount()
})

it('validates new members and prevents a stale editor from overwriting newer settings', async () => {
  const initial = snapshot()
  const request = vi.fn().mockResolvedValue(initial)
  const view = open(request)
  fireEvent.click(await screen.findByRole('button', { name: 'Manage organization' }))
  const form = within(screen.getByRole('form', { name: 'Manage organization' }))
  fireEvent.click(form.getByRole('button', { name: 'Add member' }))
  expect(form.getByRole('button', { name: 'Save organization' })).toHaveProperty('disabled', true)
  fireEvent.change(form.getByRole('textbox', { name: 'Identity ID' }), { target: { value: 'new-worker' } })
  fireEvent.change(form.getByRole('textbox', { name: 'Name' }), { target: { value: 'Research specialist' } })
  fireEvent.click(form.getByRole('checkbox', { name: 'work.analyze' }))
  expect(form.getByRole('button', { name: 'Save organization' })).toHaveProperty('disabled', false)
  fireEvent.change(form.getByRole('combobox', { name: 'Provider' }), { target: { value: 'provider' } })
  expect(form.getByRole('button', { name: 'Save organization' })).toHaveProperty('disabled', true)
  fireEvent.change(form.getByRole('combobox', { name: 'Model' }), { target: { value: 'model' } })
  const changed = snapshot()
  changed.runtime!.management!.generation = 4
  changed.runtime!.management!.configuration.max_inflight = 4
  request.mockResolvedValue(changed)
  await act(async () => {
    await view.adapter.refresh()
  })
  expect(screen.getByRole('alert').textContent).toMatch(/changed while you were editing/)
  expect(form.getByRole('button', { name: 'Save organization' })).toHaveProperty('disabled', true)
  expect(request.mock.calls.every(call => call[0] === 'organization.snapshot')).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Review latest configuration' }))
  expect(form.getByRole('spinbutton', { name: 'Concurrent execution limit' })).toHaveProperty('value', '4')
  fireEvent.click(form.getByRole('button', { name: 'Add member' }))
  fireEvent.change(form.getByRole('textbox', { name: 'Identity ID' }), { target: { value: 'researcher' } })
  fireEvent.change(form.getByRole('textbox', { name: 'Name' }), { target: { value: 'Research specialist' } })
  fireEvent.click(form.getByRole('checkbox', { name: 'work.analyze' }))
  fireEvent.click(form.getByRole('button', { name: 'Save organization' }))
  await waitFor(() => expect(request.mock.calls.some(call => call[0] === 'organization.configure')).toBe(true))
  const params = request.mock.calls.find(call => call[0] === 'organization.configure')![1]
  expect(params.expectedGeneration).toBe(4)
  expect(params.configuration.roster[1]).toMatchObject({
    id: 'researcher',
    capabilities: ['work.analyze'],
    manager_id: 'manager'
  })
  view.unmount()
})

it('rejects invalid authority, identity, leader, provider pair, and out-of-scope grants', () => {
  const initial = snapshot()
  const management = initial.runtime!.management!
  expect(validOrganizationConfiguration(management.configuration, management, initial)).toBe(true)

  for (const overrides of [
    { tool_grants: ['patch'] },
    { authority: ['staff.manage'] },
    { id: 'owner' },
    { manager_id: 'writer' },
    { provider: null },
    { capabilities: ['request.accept'] }
  ]) {
    expect(
      validOrganizationConfiguration(
        { ...management.configuration, roster: [{ ...member, ...overrides }] },
        management,
        initial
      )
    ).toBe(false)
  }
})

it('dismisses a pending form immediately without reopening it after its late response', async () => {
  const initial = snapshot()
  const pending = deferred<OrganizationSnapshot>()

  const request = vi
    .fn()
    .mockImplementation((method: string) =>
      method === 'organization.snapshot' ? Promise.resolve(initial) : pending.promise
    )

  const view = open(request)
  fireEvent.click(await screen.findByRole('button', { name: 'Manage organization' }))
  fireEvent.click(screen.getByRole('button', { name: 'Save organization' }))
  await waitFor(() => expect(request.mock.calls.some(call => call[0] === 'organization.configure')).toBe(true))
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(screen.queryByRole('form', { name: 'Manage organization' })).toBeNull()
  await act(async () => {
    pending.resolve(initial)
  })
  expect(screen.queryByRole('form', { name: 'Manage organization' })).toBeNull()
  view.unmount()
})

it('submits only explicitly selected task and memory handoffs and shows the returned audit', async () => {
  const initial = snapshot()
  initial.runtime!.management!.configuration.roster.push({ ...member, id: 'second-writer', name: 'Second writer' })
  initial.agents.push({
    id: 'writer',
    name: member.name,
    role: 'Worker',
    status: 'idle',
    capabilities: ['work.draft'],
    responsibilities: [],
    summary: ''
  })
  initial.tasks = [
    {
      id: 'open-task',
      objectiveId: 'goal',
      title: 'Open release draft',
      ownerId: 'writer',
      assignedAgentId: 'writer',
      status: 'blocked',
      dependsOn: []
    },
    {
      id: 'done-task',
      objectiveId: 'goal',
      title: 'Old completed draft',
      ownerId: 'writer',
      status: 'completed',
      dependsOn: []
    }
  ]
  expect(validTransfers([{ fromAgentId: 'writer', toAgentId: 'second-writer', taskIds: ['open-task'], workPackageIds: [], includeMemory: false }], initial.runtime!.management!.configuration, initial)).toBe(true)
  const request = vi.fn().mockResolvedValue(initial)
  const view = open(request)
  fireEvent.click(await screen.findByRole('button', { name: 'Manage organization' }))
  const form = within(screen.getByRole('form', { name: 'Manage organization' }))
  fireEvent.click(form.getByRole('button', { name: 'Add explicit handoff' }))
  expect(form.getByRole('button', { name: 'Save organization' })).toHaveProperty('disabled', true)
  fireEvent.change(form.getByRole('combobox', { name: 'Transfer from' }), { target: { value: 'writer' } })
  fireEvent.change(form.getByRole('combobox', { name: 'Transfer to' }), { target: { value: 'second-writer' } })
  expect(form.queryByRole('checkbox', { name: /Old completed draft/ })).toBeNull()
  fireEvent.click(form.getByRole('checkbox', { name: /Open release draft/ }))
  fireEvent.click(form.getByRole('checkbox', { name: 'Include retained memory' }))
  const updated = snapshot()
  updated.runtime!.management!.generation = 4
  updated.runtime!.management!.recentChanges = [
    {
      id: 'handoff',
      requestId: null,
      actorId: 'owner',
      kind: 'transfer',
      subjectId: 'second-writer',
      before: { agentId: 'writer' },
      after: { destinationRevision: 2 },
      createdAt: '2026-10-04T01:00:00Z'
    }
  ]
  request.mockResolvedValue(updated)
  fireEvent.click(form.getByRole('button', { name: 'Save organization' }))
  await waitFor(() => expect(request.mock.calls.some(call => call[0] === 'organization.configure')).toBe(true))
  expect(request.mock.calls.find(call => call[0] === 'organization.configure')![1].configuration.transfers).toStrictEqual([
    { fromAgentId: 'writer', toAgentId: 'second-writer', taskIds: ['open-task'], includeMemory: true }
  ])
  const history = within(screen.getByRole('region', { name: 'Recent organization changes' }))
  expect(history.getByText('transfer · second-writer')).toBeTruthy()
  expect(history.getByText(/destinationRevision/)).toBeTruthy()
  view.unmount()
})

it('removes only unsaved new members while preserving other roster edits', async () => {
  const request = vi.fn().mockResolvedValue(snapshot())
  const view = open(request)
  fireEvent.click(await screen.findByRole('button', { name: 'Manage organization' }))
  const form = within(screen.getByRole('form', { name: 'Manage organization' }))
  expect(form.queryByRole('button', { name: 'Remove unsaved member' })).toBeNull()
  fireEvent.change(form.getByRole('textbox', { name: 'Name' }), { target: { value: 'Preserved name' } })
  fireEvent.click(form.getByRole('button', { name: 'Add member' }))
  expect(form.getByRole('button', { name: 'Save organization' })).toHaveProperty('disabled', true)
  fireEvent.click(form.getByRole('button', { name: 'Remove unsaved member' }))
  expect(form.getByRole('textbox', { name: 'Name' })).toHaveProperty('value', 'Preserved name')
  expect(form.queryByRole('button', { name: 'Remove unsaved member' })).toBeNull()
  fireEvent.click(form.getByRole('button', { name: 'Save organization' }))
  await waitFor(() => expect(request.mock.calls.some(call => call[0] === 'organization.configure')).toBe(true))
  expect(request.mock.calls.find(call => call[0] === 'organization.configure')![1].configuration.roster).toEqual([{ ...member, name: 'Preserved name' }])
  view.unmount()
})

it('saves exact objective leadership without open tasks or memory and clears selections when the source changes', async () => {
  const initial = snapshot()
  initial.agents.push(
    { ...initial.agents[0], id: 'executive', name: 'Release executive', role: 'Executive' },
    { ...initial.agents[0], id: 'next-executive', name: 'Next executive', role: 'Executive' },
    { ...initial.agents[0], id: 'third-manager', name: 'Third manager' }
  )
  initial.runtime!.management!.configuration.roster.push({
    ...member,
    id: 'next-manager',
    name: 'Next manager',
    role: 'Manager',
    manager_id: 'executive',
    capabilities: ['request.plan', 'request.integrate'],
    tool_grants: []
  })

  const objective = {
    id: 'goal-before-planning',
    title: 'Release awaiting a plan',
    description: 'No tasks have been created',
    ownerId: 'owner',
    managerId: 'manager',
    executiveId: 'executive',
    status: 'planning' as const,
    source: 'runtime' as const,
    createdAt: '2026-10-04T00:00:00Z'
  }

  initial.objectives = [
    objective,
    { ...objective, id: 'goal-awaiting-acceptance', title: 'Release awaiting acceptance', status: 'needs_input' },
    { ...objective, id: 'other-goal', title: 'Another manager’s goal', managerId: 'third-manager' },
    { ...objective, id: 'completed-goal', title: 'Completed release', status: 'completed' },
    { ...objective, id: 'cancelled-goal', title: 'Cancelled release', status: 'cancelled' }
  ]
  initial.tasks = [{
    id: 'completed-task', objectiveId: 'goal-awaiting-acceptance', title: 'Completed release draft',
    ownerId: 'writer', managingAgentId: 'manager', status: 'completed', dependsOn: []
  }]
  const request = vi.fn().mockResolvedValue(initial)
  const view = open(request)
  fireEvent.click(await screen.findByRole('button', { name: 'Manage organization' }))
  const form = within(screen.getByRole('form', { name: 'Manage organization' }))
  fireEvent.click(form.getByRole('button', { name: 'Add explicit handoff' }))
  const from = form.getByRole('combobox', { name: 'Transfer from' })
  fireEvent.change(from, { target: { value: 'manager' } })
  fireEvent.change(form.getByRole('combobox', { name: 'Transfer to' }), { target: { value: 'next-manager' } })
  const leadership = within(form.getByRole('group', { name: 'Objective leadership transfer' }))
  expect(leadership.queryByRole('checkbox', { name: /Another manager’s goal|Completed release|Cancelled release/ })).toBeNull()
  expect(form.getByText('No open assignments for this member.')).toBeTruthy()
  fireEvent.click(leadership.getByRole('checkbox', { name: /Release awaiting a plan/ }))
  expect(form.getByRole('button', { name: 'Save organization' })).toHaveProperty('disabled', false)
  fireEvent.change(from, { target: { value: 'writer' } })
  expect(form.queryByRole('checkbox', { name: /Release awaiting a plan/ })).toBeNull()
  fireEvent.change(from, { target: { value: 'manager' } })
  expect(form.getByRole('checkbox', { name: /Release awaiting a plan/ })).toHaveProperty('checked', false)
  fireEvent.change(form.getByRole('combobox', { name: 'Transfer to' }), { target: { value: 'next-manager' } })
  fireEvent.click(form.getByRole('checkbox', { name: /Release awaiting a plan/ }))
  fireEvent.click(form.getByRole('checkbox', { name: /Release awaiting acceptance/ }))
  fireEvent.click(form.getByRole('button', { name: 'Save organization' }))
  await waitFor(() => expect(request.mock.calls.some(call => call[0] === 'organization.configure')).toBe(true))

  const transfer = {
    fromAgentId: 'manager', toAgentId: 'next-manager', taskIds: [],
    objectiveIds: [objective.id, 'goal-awaiting-acceptance'], includeMemory: false
  }

  expect(request.mock.calls.find(call => call[0] === 'organization.configure')![1].configuration.transfers).toEqual([transfer])
  const configuration = initial.runtime!.management!.configuration
  expect(validTransfers([transfer, { ...transfer, fromAgentId: 'executive', toAgentId: 'next-executive' }], configuration, initial)).toBe(true)
  expect(validTransfers([transfer, { ...transfer, toAgentId: 'third-manager' }], configuration, initial)).toBe(false)

  for (const objectiveIds of [['missing-goal'], ['other-goal'], ['completed-goal'], ['cancelled-goal'], [objective.id, objective.id]]) {
    expect(validTransfers([{ ...transfer, objectiveIds }], configuration, initial)).toBe(false)
  }

  view.unmount()
})

it('preserves explicit package-only handoffs and executive decomposition through the management form and RPC', async () => {
  const initial = snapshot()
  initial.agents.push(
    { ...initial.agents[0], id: 'owner', name: 'Owner', role: 'Owner' },
    { ...initial.agents[0], id: 'executive', name: 'Executive', role: 'Executive' },
    { ...initial.agents[0], id: 'third-manager', name: 'Third manager' }
  )
  initial.runtime!.management!.configuration.roster.push(
    { ...member, id: 'next-manager', name: 'Next manager', role: 'Manager', manager_id: 'executive', capabilities: ['request.plan'], tool_grants: [] },
    { ...member, id: 'next-executive', name: 'Next executive', role: 'Executive', manager_id: 'owner', capabilities: ['request.decompose', 'request.accept'], tool_grants: [] }
  )
  const workPackage = {
    id: 'selected-package', objectiveId: 'goal', round: 1, managerId: 'manager',
    title: 'Deliver reviewed API', description: 'Planning has completed; the root is awaiting acceptance.',
    criterionIndexes: [0], projectIds: [], dependencyIds: [], maxTasks: 1,
    planRequestId: 'package-plan', status: 'completed' as const, taskIds: []
  }
  const objective = {
    id: 'goal', title: 'Release', description: '', ownerId: 'executive', managerId: 'manager', executiveId: 'executive',
    source: 'runtime' as const, status: 'active' as const, createdAt: '', planningMode: 'executive_packages' as const,
    acceptance: { status: 'reviewing' as const, criteria: ['Reviewed release'], round: 1, maxReplans: 2, summary: null, deliverableId: null },
    workPackages: [workPackage,
      { ...workPackage, id: 'old-package', title: 'Previous package', round: 0 },
      { ...workPackage, id: 'other-package', title: 'Other manager package', managerId: 'third-manager' }
    ]
  }
  initial.objectives = [objective, {
    ...objective, id: 'settled', status: 'completed',
    workPackages: [{ ...workPackage, objectiveId: 'settled', id: 'settled-package', title: 'Settled package' }]
  }]
  const request = vi.fn().mockResolvedValue(initial)
  const view = open(request)
  fireEvent.click(await screen.findByRole('button', { name: 'Manage organization' }))
  const form = within(screen.getByRole('form', { name: 'Manage organization' }))
  fireEvent.change(form.getByRole('combobox', { name: 'Member' }), { target: { value: '2' } })
  expect(form.getByRole('checkbox', { name: 'request.decompose' })).toHaveProperty('checked', true)
  fireEvent.click(form.getByRole('button', { name: 'Add explicit handoff' }))
  const from = form.getByRole('combobox', { name: 'Transfer from' })
  fireEvent.change(from, { target: { value: 'manager' } })
  fireEvent.change(form.getByRole('combobox', { name: 'Transfer to' }), { target: { value: 'next-manager' } })
  const packages = within(form.getByRole('group', { name: 'Work package ownership transfer' }))
  expect(packages.queryByRole('checkbox', { name: /Previous package|Other manager package|Settled package/ })).toBeNull()
  fireEvent.click(packages.getByRole('checkbox', { name: /Deliver reviewed API/ }))
  expect(form.getByRole('button', { name: 'Save organization' })).toHaveProperty('disabled', false)
  fireEvent.change(from, { target: { value: 'third-manager' } })
  fireEvent.change(from, { target: { value: 'manager' } })
  expect(form.getByRole('checkbox', { name: /Deliver reviewed API/ })).toHaveProperty('checked', false)
  fireEvent.change(form.getByRole('combobox', { name: 'Transfer to' }), { target: { value: 'next-manager' } })
  fireEvent.click(form.getByRole('checkbox', { name: /Deliver reviewed API/ }))
  fireEvent.click(form.getByRole('button', { name: 'Save organization' }))
  await waitFor(() => expect(request.mock.calls.some(call => call[0] === 'organization.configure')).toBe(true))
  const saved = request.mock.calls.find(call => call[0] === 'organization.configure')![1].configuration
  const transfer = { fromAgentId: 'manager', toAgentId: 'next-manager', taskIds: [], workPackageIds: [workPackage.id], includeMemory: false }
  expect(saved.transfers).toEqual([transfer])
  expect(saved.roster.find((item: OrganizationMemberConfiguration) => item.id === 'next-executive').capabilities).toEqual(['request.decompose', 'request.accept'])
  for (const workPackageIds of [['old-package'], ['other-package'], ['settled-package'], ['missing'], [workPackage.id, workPackage.id]]) {
    expect(validTransfers([{ ...transfer, workPackageIds }], saved, initial)).toBe(false)
  }
  expect(validTransfers([transfer, { ...transfer, toAgentId: 'third-manager' }], saved, initial)).toBe(false)
  view.unmount()
})
