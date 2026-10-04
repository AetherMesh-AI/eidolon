import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway, type OrganizationScope } from './runtime-adapter'
import type { OrganizationSnapshot } from './types'
import { OrganizationWorkspace } from './workspace'

function pending() {
  let resolve!: (value: OrganizationSnapshot) => void
  let reject!: (error: Error) => void
  const promise = new Promise<OrganizationSnapshot>((yes, no) => {resolve = yes; reject = no})

  return { promise, resolve, reject }
}

function snapshot(): OrganizationSnapshot {
  return { source: 'runtime', agents: [], tasks: [], activity: [], knowledge: [],
    objectives: [{ id: 'goal', title: 'Launch summary', description: 'Facts', status: 'needs_input', source: 'runtime', ownerId: 'manager', createdAt: '2026-10-04T00:00:00Z' }],
    requests: [{ id: 'request', objectiveId: 'goal', type: 'request.clarify', team: 'research', priority: 4, attempts: 1, status: 'pending_intervention', reason: 'Which audience?', createdAt: '2026-10-04T00:00:00Z', allowedResolutions: [{ action: 'provide_input', label: 'Provide requested input', requiresText: true, requiresEvidence: false }] }],
    runtime: { capabilities: ['work.draft'], scope: 'Submitted context', state: 'ready', maxWorkers: 2 } }
}

function setup(initial = snapshot()) {
  let scope: OrganizationScope = { key: 'socket-a', ownerKey: 'owner-a', connected: true }
  let notify: () => void = () => undefined
  const request = vi.fn().mockResolvedValue(initial)

  const adapter = createRuntimeAdapter({ request: request as OrganizationGateway['request'], getScope: () => scope, subscribeScope: listener => {notify = listener;

 return () => undefined} })

  const view = render(<MemoryRouter initialEntries={['/requests']}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)

  return { adapter, request, view, switchScope(next: OrganizationScope) {scope = next; notify()} }
}

it('resolves the typed cause once, preserves failed text and retry identity, and displays durable history', async () => {
  const fixture = setup()
  await screen.findByText('Which audience?')
  expect(screen.queryByRole('button', { name: /mark.*(done|complete)/i })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Respond to request' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Response' }), { target: { value: 'Engineering leads' } })
  const response = pending()
  fixture.request.mockImplementation((method: string) => method === 'organization.resolve' ? response.promise : Promise.resolve(snapshot()))
  const button = screen.getByRole('button', { name: 'Submit response' })
  fireEvent.click(button)
  fireEvent.submit(button.closest('form')!)
  await waitFor(() => expect(fixture.request.mock.calls.filter(call => call[0] === 'organization.resolve')).toHaveLength(1))
  await act(async () => {response.reject(new Error('Acknowledgement lost')); await Promise.resolve()})
  expect(await screen.findByText('Acknowledgement lost')).toBeTruthy()
  expect(screen.getByRole('textbox', { name: 'Response' })).toHaveProperty('value', 'Engineering leads')
  const resolved = snapshot()
  resolved.requests![0] = { ...resolved.requests![0], status: 'completed', allowedResolutions: [] }
  resolved.objectives[0].ownerResolutions = [{ id: 'resolution', requestId: 'request', action: 'provide_input', text: 'Engineering leads', evidenceIds: [], createdAt: '2026-10-04T12:00:00Z' }]
  fixture.request.mockResolvedValue(resolved)
  // Closing after a lost acknowledgement must not mint a second response identity.
  fireEvent.click(screen.getByRole('button', { name: 'Close response' }))
  fireEvent.click(screen.getByRole('button', { name: 'Respond to request' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Response' }), { target: { value: 'Engineering leads' } })
  fireEvent.click(screen.getByRole('button', { name: 'Submit response' }))
  await screen.findByText('Nothing needs your input')
  const calls = fixture.request.mock.calls.filter(call => call[0] === 'organization.resolve')
  expect(calls[1][1].idempotencyKey).toBe(calls[0][1].idempotencyKey)
  expect(calls[1][1]).toMatchObject({ id: 'request', action: 'provide_input', text: 'Engineering leads' })
  expect(screen.getByText('Engineering leads')).toBeTruthy()
  fixture.view.unmount()
})

it('retains same-owner disconnected queue but fences a late response after switching profile', async () => {
  const fixture = setup()
  await screen.findByText('Which audience?')
  fireEvent.click(screen.getByRole('button', { name: 'Respond to request' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Response' }), { target: { value: 'Private A context' } })
  act(() => fixture.switchScope({ key: 'socket-offline', ownerKey: 'owner-a', connected: false }))
  expect(screen.getByText('Which audience?', { selector: 'dd' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Submit response' })).toHaveProperty('disabled', true)
  act(() => fixture.switchScope({ key: 'socket-new', ownerKey: 'owner-a', connected: true }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Submit response' })).toHaveProperty('disabled', false))
  expect(screen.getByRole('textbox', { name: 'Response' })).toHaveProperty('value', 'Private A context')
  const response = pending()
  fixture.request.mockImplementation((method: string) => method === 'organization.resolve' ? response.promise : Promise.resolve({ ...snapshot(), objectives: [], requests: [] }))
  fireEvent.click(screen.getByRole('button', { name: 'Submit response' }))
  await waitFor(() => expect(fixture.request.mock.calls.some(call => call[0] === 'organization.resolve')).toBe(true))
  const signal = fixture.request.mock.calls.find(call => call[0] === 'organization.resolve')![3] as AbortSignal
  act(() => fixture.switchScope({ key: 'socket-b', ownerKey: 'owner-b', connected: true }))
  expect(signal.aborted).toBe(true)
  await act(async () => {response.resolve(snapshot()); await Promise.resolve()})
  expect(screen.queryByDisplayValue('Private A context')).toBeNull()
  expect(screen.queryByText('Which audience?')).toBeNull()
  fixture.view.unmount()
})

it('requires exact handoff evidence and dismisses without dispatch or permission changes', async () => {
  const initial = snapshot()
  initial.requests![0].allowedResolutions = [{ action: 'record_handoff', label: 'Record external handoff', requiresText: true, requiresEvidence: true, evidenceIds: ['exact-proposal'], sourceManifest: [{ path: 'root-1/earlier.txt', revision: 2, sha256: 'a'.repeat(64), proposalId: 'earlier-proposal', evidenceId: 'earlier-evidence' }] }]
  initial.knowledge = [{ id: 'exact-proposal', title: 'Reviewed change', body: '', kind: 'artifact', objectiveId: 'goal' }, { id: 'unrelated', title: 'Wrong evidence', body: '', kind: 'artifact', objectiveId: 'goal' }]
  const fixture = setup(initial)
  fireEvent.click(await screen.findByRole('button', { name: 'Respond to request' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Response' }), { target: { value: 'Recorded external result' } })
  expect(screen.getByRole('button', { name: 'Submit response' })).toHaveProperty('disabled', true)
  expect(screen.queryByRole('checkbox', { name: 'Wrong evidence' })).toBeNull()
  fireEvent.click(screen.getByRole('checkbox', { name: 'Reviewed change' }))
  expect(screen.getByRole('button', { name: 'Submit response' })).toHaveProperty('disabled', false)
  fixture.request.mockImplementation((method: string) => Promise.resolve(method === 'organization.evidence' ? { id: 'earlier-evidence', content: 'Earlier retained source bytes', sha256: 'a'.repeat(64), summary: 'Earlier proposal', createdAt: '2026-10-04T00:00:00Z', objectiveId: 'goal', taskId: 'earlier-task' } : initial))
  fireEvent.click(screen.getByRole('button', { name: 'Retained evidence: root-1/earlier.txt' }))
  await screen.findByText('Earlier retained source bytes')
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(screen.queryByRole('complementary', { name: 'Artifact details' })).toBeNull()
  expect(screen.getByRole('complementary', { name: 'Request details' })).toBeTruthy()
  expect(screen.getByRole('textbox', { name: 'Response' })).toHaveProperty('value', 'Recorded external result')
  fireEvent.click(screen.getByRole('button', { name: 'Close response' }))
  expect(screen.queryByRole('complementary')).toBeNull()
  expect(fixture.request.mock.calls.some(call => call[0] === 'organization.resolve')).toBe(false)
  fixture.view.unmount()
})
