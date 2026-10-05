import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway } from './runtime-adapter'
import { validOrganizationConfiguration } from './runtime-management'
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
  expect(request.mock.calls.find(call => call[0] === 'organization.configure')![1].configuration.transfers).toEqual([
    { fromAgentId: 'writer', toAgentId: 'second-writer', taskIds: ['open-task'], includeMemory: true }
  ])
  const history = within(screen.getByRole('region', { name: 'Recent organization changes' }))
  expect(history.getByText('transfer · second-writer')).toBeTruthy()
  expect(history.getByText(/destinationRevision/)).toBeTruthy()
  view.unmount()
})
