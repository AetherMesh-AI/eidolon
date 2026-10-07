import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { Link, MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway } from './runtime-adapter'
import type { Objective, OrganizationArtifact, OrganizationSnapshot } from './types'
import { OrganizationWorkspace } from './workspace'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => {resolve = yes; reject = no})

  return { promise, resolve, reject }
}

function snapshot(title?: string): OrganizationSnapshot {
  return {
    source: 'runtime', objectives: title ? [{ id: 'objective', title, description: 'Provided facts', ownerId: 'manager', createdAt: '2026-10-04T00:00:00Z', status: 'needs_input', source: 'runtime', progress: 0 }] : [],
    agents: [], tasks: [], activity: [], knowledge: [], requests: [],
    runtime: { state: 'ready', profile: 'research', capabilities: ['work.draft', 'work.analyze'], maxWorkers: 2, scope: 'Writing and analysis of submitted context. No external tools.' }
  }
}

function adapterFor(request: (...args: Parameters<OrganizationGateway['request']>) => Promise<unknown>) {
  return createRuntimeAdapter({ request: async <T,>(...args: Parameters<OrganizationGateway['request']>) => await request(...args) as T, getScope: () => ({ key: 'source:research', connected: true }), subscribeScope: () => () => undefined })
}

function open(adapter: ReturnType<typeof adapterFor>, path = '/home') {
  return render(<MemoryRouter initialEntries={[path]}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
}

it('retains a failed asynchronous goal and reuses its admission identity on retry; rapid submits dispatch once', async () => {
  const pending = deferred<{ objective: Objective; snapshot: OrganizationSnapshot }>()
  const request = vi.fn().mockImplementation((method: string) => method === 'organization.snapshot' ? Promise.resolve(snapshot()) : pending.promise)
  const adapter = adapterFor(request)
  const view = open(adapter)
  await waitFor(() => expect(adapter.getSnapshot().connection?.state).toBe('ready'))
  fireEvent.change(screen.getByRole('textbox', { name: 'Objective' }), { target: { value: 'Draft launch memo' } })
  fireEvent.change(screen.getByRole('textbox', { name: 'Submitted context (optional)' }), { target: { value: 'Facts from the team' } })
  const submit = screen.getByRole('button', { name: 'Create objective' })
  fireEvent.click(submit)
  fireEvent.submit(submit.closest('form')!)
  await waitFor(() => expect(request.mock.calls.filter(call => call[0] === 'organization.create')).toHaveLength(1))
  expect(adapter.getSnapshot().objectives).toEqual([])
  expect(screen.queryByRole('button', { name: 'Record local outcome' })).toBeNull()
  await act(async () => {pending.reject(new Error('Provider admission acknowledgement lost')); await Promise.resolve()})
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', 'Provider admission acknowledgement lost')
  expect(screen.getByRole('textbox', { name: 'Objective' })).toHaveProperty('value', 'Draft launch memo')
  const created = snapshot('Draft launch memo')
  request.mockImplementation((method: string) => Promise.resolve(method === 'organization.create' ? { objective: created.objectives[0], snapshot: created } : created))
  fireEvent.click(screen.getByRole('button', { name: 'Create objective' }))
  expect(await screen.findByRole('heading', { name: 'Draft launch memo' })).toBeTruthy()
  const admissions = request.mock.calls.filter(call => call[0] === 'organization.create')
  expect(admissions[1][1].idempotencyKey).toBe(admissions[0][1].idempotencyKey)
  expect(admissions[1][1].description).toBe('Facts from the team')
  expect(screen.queryByRole('combobox', { name: 'Local objective state' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Save local metadata' })).toBeNull()
  expect(screen.queryByText(/local estimate/)).toBeNull()
  view.unmount()
})

it('keeps unsupported work visibly pending with its reason and routes retry/cancel to the backend', async () => {
  const initial = snapshot('Send this email')
  initial.requests = [{ id: 'request-1', objectiveId: 'objective', type: 'work.send', team: 'delivery', priority: 4, status: 'pending_intervention', reason: 'External email delivery is unsupported.', attempts: 1, createdAt: '2026-10-04T00:00:00Z' }]
  const request = vi.fn().mockResolvedValue(initial)
  const adapter = adapterFor(request)
  const view = open(adapter, '/objectives/objective')
  expect(await screen.findByText('Pending intervention', { selector: '.eid-status' })).toBeTruthy()
  expect(screen.getByText('External email delivery is unsupported.')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Approve locally' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Inspect request: work.send' }))
  const inspector = within(screen.getByRole('complementary', { name: 'Request details' }))
  expect(inspector.getByText('No worker claimed this request')).toBeTruthy()
  expect(inspector.getByText('High')).toBeTruthy()
  fireEvent.keyDown(window.document, { key: 'Escape' })
  fireEvent.click(screen.getByRole('button', { name: 'Retry work.send' }))
  await waitFor(() => expect(request.mock.calls.some(call => call[0] === 'organization.retry' && call[1].id === 'request-1')).toBe(true))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel objective' })).toHaveProperty('disabled', false))
  const cancelled = { ...initial, objectives: [{ ...initial.objectives[0], status: 'cancelled' as const }], requests: initial.requests.map(item => ({ ...item, status: 'cancelled' as const })) }
  request.mockResolvedValue(cancelled)
  fireEvent.click(screen.getByRole('button', { name: 'Cancel objective' }))
  await waitFor(() => expect(adapter.getSnapshot().objectives[0].status).toBe('cancelled'))
  expect(request.mock.calls.some(call => call[0] === 'organization.cancel' && call[1].id === 'objective')).toBe(true)
  expect(screen.queryByRole('button', { name: 'Retry work.send' })).toBeNull()
  view.unmount()
})

it('shows full retained evidence without treating a truncated snapshot preview as the deliverable', async () => {
  const initial = snapshot('Analyze findings')
  initial.knowledge = [{ id: 'proof', objectiveId: 'objective', title: 'Analysis', kind: 'artifact', body: 'Short preview [truncated]' }]
  const evidence: OrganizationArtifact = { id: 'proof', content: 'The full output with the final conclusion.', sha256: 'abc123', summary: 'Analysis', createdAt: '2026-10-04T00:00:00Z', objectiveId: 'objective', taskId: 'task-1' }
  const request = vi.fn().mockImplementation((method: string) => Promise.resolve(method === 'organization.evidence' ? evidence : initial))
  const view = open(adapterFor(request), '/knowledge')
  fireEvent.click(await screen.findByRole('button', { name: /Analysis/ }))
  const inspector = within(screen.getByRole('complementary', { name: 'Artifact details' }))
  expect(await inspector.findByText(evidence.content)).toBeTruthy()
  expect(inspector.queryByText('Short preview [truncated]')).toBeNull()
  expect(inspector.getByText('abc123')).toBeTruthy()
  fireEvent.keyDown(window.document, { key: 'Escape' })
  expect(screen.queryByRole('complementary', { name: 'Artifact details' })).toBeNull()
  view.unmount()
})

it('does not navigate away after the user leaves an in-flight submission', async () => {
  const pending = deferred<{ objective: Objective; snapshot: OrganizationSnapshot }>()
  const request = vi.fn().mockImplementation((method: string) => method === 'organization.snapshot' ? Promise.resolve(snapshot()) : pending.promise)
  const adapter = adapterFor(request)
  const view = open(adapter)
  await waitFor(() => expect(adapter.getSnapshot().connection?.state).toBe('ready'))
  fireEvent.change(screen.getByRole('textbox', { name: 'Objective' }), { target: { value: 'Old intent' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create objective' }))
  await waitFor(() => expect(request.mock.calls.some(call => call[0] === 'organization.create')).toBe(true))
  fireEvent.click(screen.getByRole('link', { name: 'Ask a question' }))
  const created = snapshot('Old intent')
  await act(async () => {pending.resolve({ objective: created.objectives[0], snapshot: created }); await Promise.resolve()})
  expect(screen.queryByRole('heading', { name: 'Old intent' })).toBeNull()
  view.unmount()
})


it('reuses an uncertain admission after the composer is unmounted and reopened', async () => {
  const initial = snapshot()
  const request = vi.fn().mockImplementation((method: string) => method === 'organization.create' ? Promise.reject(new Error('Acknowledgement lost')) : Promise.resolve(initial))
  const adapter = adapterFor(request)
  const view = render(<MemoryRouter initialEntries={['/home']}><Link to="/home">Return to intake</Link><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  await waitFor(() => expect(adapter.getSnapshot().connection?.state).toBe('ready'))
  fireEvent.change(screen.getByRole('textbox', { name: 'Objective' }), { target: { value: 'The same goal' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create objective' }))
  await screen.findByText('Acknowledgement lost')
  fireEvent.click(screen.getByRole('link', { name: 'Ask a question' }))
  fireEvent.click(screen.getByRole('link', { name: 'Return to intake' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Objective' }), { target: { value: 'The same goal' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create objective' }))
  await screen.findByText('Acknowledgement lost')
  const calls = request.mock.calls.filter(call => call[0] === 'organization.create')
  expect(calls).toHaveLength(2)
  expect(calls[1][1].idempotencyKey).toBe(calls[0][1].idempotencyKey)
  view.unmount()
})


it.each(['/objectives/objective', '/requests'])('preserves an owner response through reconnects but isolates a new owner on %s', async path => {
  const initial = snapshot('Pending question')
  initial.requests = [{ id: 'question', objectiveId: 'objective', type: 'request.question', team: 'research', priority: 3, status: 'pending_intervention', attempts: 1, createdAt: '2026-10-04T00:00:00Z', allowedResolutions: [{ action: 'answer_request', label: 'Answer request', requiresText: true, requiresEvidence: false }] }]
  let scope = { key: 'socket-a', ownerKey: 'owner-a', connected: true }

  let scopeChanged = () => {}

  const adapter = createRuntimeAdapter({
    getScope: () => scope,
    subscribeScope(listener) { scopeChanged = listener;

 return () => {} },
    request: async <T,>() => initial as T
  })

  const view = open(adapter, path)
  fireEvent.click(await screen.findByRole('button', { name: 'Inspect request: request.question' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Response' }), { target: { value: 'Retain this answer for this owner' } })
  act(() => { scope = { ...scope, key: 'socket-b', connected: false }; scopeChanged() })
  expect(screen.getByRole('textbox', { name: 'Response' })).toHaveProperty('value', 'Retain this answer for this owner')
  expect(screen.getByRole('textbox', { name: 'Response' })).toHaveProperty('disabled', true)
  act(() => { scope = { ...scope, connected: true }; scopeChanged() })
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Response' })).toHaveProperty('disabled', false))
  expect(screen.getByRole('textbox', { name: 'Response' })).toHaveProperty('value', 'Retain this answer for this owner')
  act(() => { scope = { key: 'socket-c', ownerKey: 'owner-b', connected: true }; scopeChanged() })
  fireEvent.click(await screen.findByRole('button', { name: 'Inspect request: request.question' }))
  expect(screen.getByRole('textbox', { name: 'Response' })).toHaveProperty('value', '')
  view.unmount()
})
