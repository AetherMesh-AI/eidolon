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

it('preserves checks by default and only sends an explicit replacement for an amended scope, with durable retry identity and audit', async () => {
  const initial = snapshot()
  initial.objectives[0].requiredChecks = ['project_tests']
  initial.requests![0].allowedResolutions = [
    { action: 'amend_scope', label: 'Amend scope', requiresText: true, requiresEvidence: false },
    { action: 'request_replan', label: 'Replan', requiresText: true, requiresEvidence: false }
  ]
  const fixture = setup(initial)
  fireEvent.click(await screen.findByRole('button', { name: 'Respond to request' }))
  expect(screen.getByRole('region', { name: 'Current required verification' }).textContent).toContain('Project tests')
  expect(screen.getByRole('combobox', { name: 'Verification for amended scope' })).toHaveProperty('value', 'keep')
  fireEvent.change(screen.getByRole('textbox', { name: 'Response' }), {
    target: { value: 'Prepare documentation only.' }
  })
  fixture.request.mockImplementation((method: string) =>
    method === 'organization.resolve' ? Promise.reject(new Error('Acknowledgement lost')) : Promise.resolve(initial)
  )
  fireEvent.click(screen.getByRole('button', { name: 'Submit response' }))
  await screen.findByText('Acknowledgement lost')
  const preserved = fixture.request.mock.calls.find(call => call[0] === 'organization.resolve')![1]
  expect(preserved.requiredChecks).toBeUndefined()
  fireEvent.change(screen.getByRole('combobox', { name: 'Verification for amended scope' }), {
    target: { value: 'replace' }
  })
  expect(screen.getByText(/Removing a requirement allows final acceptance without that verification/)).toBeTruthy()
  const tests = screen.getByRole('checkbox', { name: /Project tests/ })
  expect(tests).toHaveProperty('checked', true)
  fireEvent.click(tests)
  fireEvent.change(screen.getByRole('textbox', { name: 'Acceptance criteria' }), {
    target: { value: 'Document the exact findings\nExplain which checks were not executed' }
  })
  fireEvent.click(screen.getByRole('button', { name: 'Submit response' }))
  await waitFor(() =>
    expect(fixture.request.mock.calls.filter(call => call[0] === 'organization.resolve')).toHaveLength(2)
  )
  await screen.findByText('Acknowledgement lost')
  const changed = fixture.request.mock.calls.filter(call => call[0] === 'organization.resolve')[1][1]
  expect(changed).toMatchObject({
    requiredChecks: [],
    acceptanceCriteria: ['Document the exact findings', 'Explain which checks were not executed']
  })
  expect(changed.idempotencyKey).not.toBe(preserved.idempotencyKey)
  fireEvent.click(screen.getByRole('button', { name: 'Submit response' }))
  await waitFor(() =>
    expect(fixture.request.mock.calls.filter(call => call[0] === 'organization.resolve')).toHaveLength(3)
  )
  expect(fixture.request.mock.calls.filter(call => call[0] === 'organization.resolve')[2][1].idempotencyKey).toBe(
    changed.idempotencyKey
  )
  await screen.findByText('Acknowledgement lost')
  fireEvent.change(screen.getByRole('combobox', { name: 'Action' }), { target: { value: 'request_replan' } })
  expect(screen.queryByRole('combobox', { name: 'Verification for amended scope' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Submit response' }))
  await waitFor(() =>
    expect(fixture.request.mock.calls.filter(call => call[0] === 'organization.resolve')).toHaveLength(4)
  )
  const replan = fixture.request.mock.calls.filter(call => call[0] === 'organization.resolve')[3][1]
  expect(replan.requiredChecks).toBeUndefined()
  expect(replan.acceptanceCriteria).toBeUndefined()
  await screen.findByText('Acknowledgement lost')
  fireEvent.click(screen.getByRole('button', { name: 'Close response' }))
  const completed = snapshot()
  completed.requests![0].status = 'completed'
  completed.objectives[0].ownerResolutions = [
    {
      id: 'amendment',
      requestId: 'request',
      action: 'amend_scope',
      text: changed.text,
      evidenceIds: [],
      createdAt: '2026-10-04T12:00:00Z',
      scopeAmendment: {
        inputSha256: 'input-digest',
        sha256: 'audit-digest',
        choices: { requiredChecks: [], acceptanceCriteria: changed.acceptanceCriteria },
        before: {
          scope: 'Original full implementation',
          acceptanceCriteria: ['Execute project tests'],
          requiredChecks: ['project_tests'],
          round: 0
        },
        after: { scope: changed.text, acceptanceCriteria: changed.acceptanceCriteria, requiredChecks: [], round: 1 }
      }
    }
  ]
  fixture.request.mockResolvedValue(completed)
  await act(async () => {
    await fixture.adapter.refresh()
  })
  expect(screen.getByRole('region', { name: 'Before amendment', hidden: true }).textContent).toMatch(
    /Original full implementation.*Execute project tests.*Project tests/
  )
  expect(screen.getByRole('region', { name: 'After amendment', hidden: true }).textContent).toContain(
    'No explicit verification requirements'
  )
  expect(screen.getByText(/audit-digest/)).toBeTruthy()
  fixture.view.unmount()
})

it('bounds amended criteria and keeps cancelling or switching profiles from leaking a replacement choice', async () => {
  const initial = snapshot()
  initial.objectives[0].requiredChecks = ['project_tests']
  initial.requests![0].allowedResolutions = [
    { action: 'amend_scope', label: 'Amend', requiresText: true, requiresEvidence: false }
  ]
  const fixture = setup(initial)
  fireEvent.click(await screen.findByRole('button', { name: 'Respond to request' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Response' }), { target: { value: 'x'.repeat(2001) } })
  expect(screen.getByRole('button', { name: 'Submit response' })).toHaveProperty('disabled', true)
  expect(screen.getByRole('alert').textContent).toContain('at most 12 criteria')
  fireEvent.change(screen.getByRole('textbox', { name: 'Acceptance criteria' }), {
    target: { value: 'One verifiable result' }
  })
  expect(screen.getByRole('button', { name: 'Submit response' })).toHaveProperty('disabled', false)
  fireEvent.change(screen.getByRole('textbox', { name: 'Acceptance criteria' }), {
    target: { value: Array(13).fill('Outcome').join('\n') }
  })
  expect(screen.getByRole('button', { name: 'Submit response' })).toHaveProperty('disabled', true)
  fireEvent.change(screen.getByRole('combobox', { name: 'Verification for amended scope' }), {
    target: { value: 'replace' }
  })
  fireEvent.click(screen.getByRole('checkbox', { name: /Project tests/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Close response' }))
  expect(fixture.request.mock.calls.some(call => call[0] === 'organization.resolve')).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: 'Respond to request' }))
  expect(screen.getByRole('combobox', { name: 'Verification for amended scope' })).toHaveProperty('value', 'keep')
  expect(screen.getByRole('textbox', { name: 'Acceptance criteria' })).toHaveProperty('value', '')
  fixture.request.mockResolvedValue({ ...initial, requests: [], objectives: [] })
  act(() => fixture.switchScope({ key: 'socket-b', ownerKey: 'owner-b', connected: true }))
  await waitFor(() => expect(screen.queryByRole('form', { name: 'Respond to request' })).toBeNull())
  fixture.view.unmount()
})
