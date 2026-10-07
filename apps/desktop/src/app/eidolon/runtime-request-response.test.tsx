import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway } from './runtime-adapter'
import type { OrganizationRequest, OrganizationSnapshot } from './types'
import { OrganizationWorkspace } from './workspace'

function snapshot(request: Partial<OrganizationRequest> = {}): OrganizationSnapshot {
  return {
    source: 'runtime',
    objectives: [
      {
        id: 'goal',
        title: 'Release brief',
        description: 'Current facts',
        ownerId: 'manager',
        createdAt: '2026-10-04T00:00:00Z',
        status: 'needs_input',
        source: 'runtime'
      }
    ],
    agents: [
      {
        id: 'writer',
        name: 'Release writer',
        role: 'Worker',
        status: 'idle',
        capabilities: [],
        responsibilities: [],
        summary: ''
      }
    ],
    tasks: [],
    activity: [],
    knowledge: [],
    requests: [
      {
        id: 'question',
        objectiveId: 'goal',
        type: 'request.question',
        team: 'communications',
        priority: 3,
        status: 'pending_intervention',
        attempts: 1,
        createdAt: '2026-10-04T00:00:00Z',
        requesterId: 'writer',
        requestedOutcome: 'Which audience should this brief address?',
        requiredAuthority: 'answer.question',
        parentRequestId: 'draft',
        dependencyIds: ['facts'],
        evidenceIds: ['source-evidence'],
        allowedResolutions: [
          { action: 'answer_request', label: 'Answer request', requiresText: true, requiresEvidence: false }
        ],
        ...request
      }
    ],
    runtime: { state: 'ready', maxWorkers: 2, capabilities: ['work.draft'], scope: 'Current grants only' }
  }
}

function open(request: (...args: Parameters<OrganizationGateway['request']>) => Promise<unknown>) {
  const gateway: OrganizationGateway = {
    request: async <T,>(...args: Parameters<OrganizationGateway['request']>) => (await request(...args)) as T,
    getScope: () => ({ key: 'connection-a', connected: true }),
    subscribeScope: () => () => undefined
  }

  const adapter = createRuntimeAdapter(gateway)

  const view = render(
    <MemoryRouter initialEntries={['/requests']}>
      <OrganizationWorkspace adapter={adapter} />
    </MemoryRouter>
  )

  return { adapter, ...view }
}

it('answers typed questions with durable request provenance, exact evidence, and duplicate-submit protection', async () => {
  const initial = snapshot()
  let resolve!: (value: OrganizationSnapshot) => void

  const pending = new Promise<OrganizationSnapshot>(yes => {
    resolve = yes
  })

  const request = vi
    .fn()
    .mockImplementation((method: string) => (method === 'organization.respond' ? pending : Promise.resolve(initial)))

  const view = open(request)
  fireEvent.click(await screen.findByRole('button', { name: 'Inspect request: request.question' }))
  const inspector = within(screen.getByRole('complementary', { name: 'Request details' }))
  expect(inspector.getByText('Release writer')).toBeTruthy()
  expect(inspector.getByText('Which audience should this brief address?')).toBeTruthy()
  expect(inspector.getByText('answer.question')).toBeTruthy()
  expect(inspector.getByText('draft')).toBeTruthy()
  expect(inspector.getByText('facts')).toBeTruthy()
  expect(inspector.getByRole('button', { name: 'source-evidence' })).toBeTruthy()
  fireEvent.change(inspector.getByRole('textbox', { name: 'Response' }), {
    target: { value: 'Release managers and on-call engineers.' }
  })
  const submit = inspector.getByRole('button', { name: 'Submit response' })
  fireEvent.click(submit)
  fireEvent.submit(submit.closest('form')!)
  await waitFor(() => expect(request.mock.calls.filter(call => call[0] === 'organization.respond')).toHaveLength(1))
  expect(request.mock.calls.find(call => call[0] === 'organization.respond')![1]).toMatchObject({
    id: 'question',
    text: 'Release managers and on-call engineers.',
    decision: 'answered'
  })

  const completed = snapshot({
    status: 'completed',
    response: {
      text: 'Release managers and on-call engineers.',
      responderId: 'owner',
      decision: 'answered',
      createdAt: '2026-10-04T01:00:00Z'
    },
    allowedResolutions: []
  })

  request.mockResolvedValue(completed)
  await act(async () => {
    resolve(completed)
  })
  expect(screen.queryByRole('form', { name: 'Respond to request' })).toBeNull()
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  fireEvent.change(screen.getByRole('combobox', { name: 'Status' }), { target: { value: 'all' } })
  fireEvent.click(screen.getByRole('button', { name: 'Inspect request: request.question' }))
  expect(screen.getByRole('region', { name: 'Recorded response' }).textContent).toMatch(
    /Release managers and on-call engineers/
  )
  expect(screen.queryByRole('button', { name: 'Submit response' })).toBeNull()
  view.unmount()
})

it.each(['request.hire', 'request.permission'])(
  'shows the complete %s proposal and records only the chosen owner decision',
  async type => {
    const proposal = {
      members: [
        {
          id: 'new-writer',
          name: 'New writer',
          role: 'Worker' as const,
          manager_id: 'manager',
          team: 'communications',
          capabilities: ['work.draft'],
          enabled: true,
          provider: 'existing-provider',
          model: 'existing-model',
          tool_grants: [],
          responsibilities: ['Use exact release facts'],
          purpose: 'Draft releases',
          authority: [],
          managed_teams: []
        }
      ],
      transfers: [{ fromAgentId: 'writer', toAgentId: 'new-writer', taskIds: ['task-1'], includeMemory: true }]
    }

    const initial = snapshot({
      type,
      managementProposal: type === 'request.hire' ? proposal : undefined,
      requiredAuthority: type === 'request.hire' ? 'staff.manage' : 'owner',
      allowedResolutions: [
        { action: 'approve_request', label: 'Approve request', requiresText: true, requiresEvidence: false },
        { action: 'deny_request', label: 'Deny request', requiresText: true, requiresEvidence: false }
      ]
    })

    const request = vi.fn().mockResolvedValue(initial)
    const view = open(request)
    fireEvent.click(await screen.findByRole('button', { name: `Inspect request: ${type}` }))
    const inspector = within(screen.getByRole('complementary', { name: 'Request details' }))

    if (type === 'request.hire') {
      const preview = within(inspector.getByRole('region', { name: 'Exact staffing proposal' }))

      for (const value of [
        'new-writer',
        'existing-provider',
        'existing-model',
        'Use exact release facts',
        'Draft releases',
        'work.draft'
      ]) {
        expect(preview.getByText(value)).toBeTruthy()
      }

      expect(preview.getByText('writer → new-writer')).toBeTruthy()
      expect(preview.getByText('Task ID: task-1')).toBeTruthy()
    } else {
      expect(inspector.getByText(/Credentials and tool grants remain unchanged/)).toBeTruthy()
    }

    fireEvent.change(inspector.getByRole('combobox', { name: 'Action' }), { target: { value: 'deny_request' } })
    fireEvent.change(inspector.getByRole('textbox', { name: 'Response' }), {
      target: { value: 'Keep the current scope.' }
    })
    fireEvent.click(inspector.getByRole('button', { name: 'Submit response' }))
    await waitFor(() => expect(request.mock.calls.some(call => call[0] === 'organization.respond')).toBe(true))
    expect(request.mock.calls.find(call => call[0] === 'organization.respond')![1]).toMatchObject({
      decision: 'denied',
      text: 'Keep the current scope.'
    })
    expect(request.mock.calls.some(call => call[0] === 'organization.configure')).toBe(false)
    view.unmount()
  }
)

it('blocks hire approval without a full proposal and keeps waiting responses free of owner retry controls', async () => {
  const initial = snapshot({
    type: 'request.hire',
    allowedResolutions: [{ action: 'approve_request', label: 'Approve', requiresText: true, requiresEvidence: false }]
  })

  const request = vi.fn().mockResolvedValue(initial)
  const view = open(request)
  fireEvent.click(await screen.findByRole('button', { name: 'Inspect request: request.hire' }))
  expect(screen.getByRole('alert').textContent).toMatch(/complete staffing proposal/)
  fireEvent.change(screen.getByRole('textbox', { name: 'Response' }), { target: { value: 'Approved' } })
  expect(screen.getByRole('button', { name: 'Submit response' })).toHaveProperty('disabled', true)
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  request.mockResolvedValue(snapshot({ status: 'waiting_response', allowedResolutions: [] }))
  await act(async () => {
    await view.adapter.refresh()
  })
  expect(screen.queryByRole('button', { name: /Inspect request/ })).toBeNull()
  fireEvent.change(screen.getByRole('combobox', { name: 'Status' }), { target: { value: 'waiting_response' } })
  expect(screen.getByText('Waiting for response', { selector: '.eid-status' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: /Retry request|Respond to request/ })).toBeNull()
  view.unmount()
})

it('keeps malformed staffing data inspectable and deniable without allowing approval or crashing', async () => {
  const malformed = {
    members: [{ id: 'broken', name: { unexpected: 'object' } }],
    transfers: [{ fromAgentId: 'writer', toAgentId: 'broken', taskIds: null, includeMemory: true }]
  }

  const initial = snapshot({
    type: 'request.hire',
    managementProposal: malformed as unknown as OrganizationRequest['managementProposal'],
    allowedResolutions: [
      { action: 'approve_request', label: 'Approve', requiresText: true, requiresEvidence: false },
      { action: 'deny_request', label: 'Deny', requiresText: true, requiresEvidence: false }
    ]
  })

  const request = vi.fn().mockResolvedValue(initial)
  const view = open(request)
  fireEvent.click(await screen.findByRole('button', { name: 'Inspect request: request.hire' }))
  const inspector = within(screen.getByRole('complementary', { name: 'Request details' }))
  expect(inspector.getByRole('region', { name: 'Exact staffing proposal' }).textContent).toContain('unexpected')
  fireEvent.change(inspector.getByRole('textbox', { name: 'Response' }), {
    target: { value: 'The proposal is invalid.' }
  })
  expect(inspector.getByRole('button', { name: 'Submit response' })).toHaveProperty('disabled', true)
  fireEvent.change(inspector.getByRole('combobox', { name: 'Action' }), { target: { value: 'deny_request' } })
  fireEvent.click(inspector.getByRole('button', { name: 'Submit response' }))
  await waitFor(() => expect(request.mock.calls.some(call => call[0] === 'organization.respond')).toBe(true))
  expect(request.mock.calls.find(call => call[0] === 'organization.respond')![1]).toMatchObject({
    decision: 'denied',
    text: 'The proposal is invalid.'
  })
  view.unmount()
})

it('does not claim all clear when filters hide another team’s pending intervention', async () => {
  const initial = snapshot()
  initial.requests!.push({ ...initial.requests![0], id: 'done', team: 'engineering', type: 'work.draft', status: 'completed' })
  const view = open(vi.fn().mockResolvedValue(initial))
  await screen.findByRole('button', { name: 'Inspect request: request.question' })
  fireEvent.change(screen.getByRole('combobox', { name: 'Team' }), { target: { value: 'engineering' } })
  expect(screen.queryByText('Nothing needs your input')).toBeNull()
  expect(screen.getByText('No matching requests')).toBeTruthy()
  fireEvent.change(screen.getByRole('combobox', { name: 'Team' }), { target: { value: 'all' } })
  expect(screen.getByRole('button', { name: 'Inspect request: request.question' })).toBeTruthy()
  view.unmount()
})

it('waits for authoritative request state before asserting nothing needs input', async () => {
  let resolve!: (value: OrganizationSnapshot) => void
  const view = open(() => new Promise<OrganizationSnapshot>(yes => { resolve = yes }))
  expect(screen.queryByText('Nothing needs your input')).toBeNull()
  await waitFor(() => expect(resolve).toBeTypeOf('function'))
  await act(async () => { resolve({ ...snapshot(), requests: [] }) })
  expect(await screen.findByText('Nothing needs your input')).toBeTruthy()
  view.unmount()
})
