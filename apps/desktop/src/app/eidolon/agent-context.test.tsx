import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { Link, MemoryRouter, useLocation } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { AgentContext, AgentContextDisclosure } from './agent-context'
import { Messages } from './messages'
import { ownerChatSnapshot } from './runtime-owner-chat.test-support'
import type { RuntimeOrganizationAdapter } from './types'
import { objectiveReturnTarget } from './workspace-navigation'

afterEach(cleanup)

function historyFixture() {
  const snapshot = ownerChatSnapshot()
  const objectiveId = 'retained /?#'
  snapshot.objectives = [{ id: objectiveId, title: 'Retained objective', description: '', ownerId: 'executive',
    status: 'completed', source: 'runtime', createdAt: '2026-10-10T00:00:00Z' }]
  snapshot.agents[0].context = { recentHistory: [{ requestId: 'retained-request', objectiveId, objectiveTitle: 'Retained objective',
    taskId: 'retained-task', requestType: 'work.analyze', summary: 'Recorded result', evidenceIds: [], createdAt: '2026-10-10T00:00:00Z' },
  { requestId: 'missing-request', objectiveId: 'omitted', objectiveTitle: 'Omitted objective', taskId: null, requestType: 'work.analyze',
    summary: 'Outside the current snapshot', evidenceIds: [], createdAt: '2026-10-09T00:00:00Z' }] }

  return snapshot
}

it('returns from retained history to the open disclosure for the exact Messages member without opening a chat or changing records', async () => {
  const snapshot = historyFixture()
  const member = snapshot.agents[0]
  const owner = snapshot.connection!.ownerScope!

  const adapter = {
    mode: 'runtime', getSnapshot: () => snapshot, createObjective: vi.fn(), openOwnerChat: vi.fn(),
    readOwnerChat: vi.fn(), sendOwnerChat: vi.fn(), getEvidence: vi.fn(), resolveRequest: vi.fn()
  } as unknown as RuntimeOrganizationAdapter

  const before = JSON.stringify(snapshot)

  function Surface() {
    const location = useLocation()

    return <>
      <output aria-label="Current route">{location.pathname + location.search}</output>
      {location.pathname === '/messages'
        ? <Messages adapter={adapter} snapshot={snapshot} />
        : <Link to={objectiveReturnTarget(new URLSearchParams(location.search), owner)}>Back to origin</Link>}
    </>
  }

  render(<MemoryRouter initialEntries={['/messages?view=history']}><Surface /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: /Worker 1/ }))
  const selection = screen.getByLabelText('Current route').textContent!
  const originParams = new URL(selection, 'https://workspace.invalid').searchParams
  expect(originParams.get('member')).toBe(member.id)
  expect(originParams.get('identity')).toBe(member.identityId)
  expect(originParams.get('owner')).toBe(owner)
  const disclosure = screen.getByText('Identity, memory and history').closest('details')!
  expect(disclosure.open).toBe(false)
  fireEvent.click(within(disclosure).getByText('Identity, memory and history'))
  await waitFor(() => expect(disclosure.open).toBe(true))
  await waitFor(() => expect(new URL(screen.getByLabelText('Current route').textContent!, 'https://workspace.invalid').searchParams.get('contextMember')).toBe(member.id))
  const origin = screen.getByLabelText('Current route').textContent!
  const context = screen.getByRole('region', { name: 'Member context' })
  const link = within(context).getByRole('link', { name: 'Retained objective' })
  const destination = new URL(link.getAttribute('href')!, 'https://workspace.invalid')
  expect(destination.pathname).toBe(`/objectives/${encodeURIComponent(snapshot.objectives[0].id)}`)
  expect(destination.searchParams.get('from')).toBe(origin)
  expect(destination.searchParams.get('originOwner')).toBe(owner)
  expect(objectiveReturnTarget(destination.searchParams, 'another-owner')).toBe('/objectives')
  expect(within(context).queryByRole('link', { name: 'Omitted objective' })).toBeNull()
  fireEvent.click(link)
  fireEvent.click(screen.getByRole('link', { name: 'Back to origin' }))
  expect(screen.getByLabelText('Current route').textContent).toBe(origin)
  expect(screen.getByRole('button', { name: /Worker 1/ }).getAttribute('aria-pressed')).toBe('true')
  expect(screen.getByText('Identity, memory and history').closest('details')!.open).toBe(true)
  expect(within(screen.getByRole('region', { name: 'Member context' })).getByRole('link', { name: 'Retained objective' })).not.toBeNull()
  expect(JSON.stringify(snapshot)).toBe(before)

  for (const action of [adapter.createObjective, adapter.openOwnerChat, adapter.readOwnerChat, adapter.sendOwnerChat, adapter.getEvidence, adapter.resolveRequest]) {
    expect(action).not.toHaveBeenCalled()
  }
})

it('keeps native disclosure state owner-, member- and identity-bound while preserving other route state', async () => {
  const snapshot = historyFixture()
  const member = snapshot.agents[0]
  const owner = snapshot.connection!.ownerScope!

  const params = new URLSearchParams({ member: member.id, identity: member.identityId!, owner, view: 'history',
    contextOwner: owner, contextMember: member.id, contextIdentity: member.identityId! })

  function Location() {
    const location = useLocation()

    return <output aria-label="Current route">{location.pathname + location.search}</output>
  }

  const tree = (agent = member, current = snapshot) => <MemoryRouter initialEntries={[`/messages?${params}`]}>
    <AgentContextDisclosure agent={agent} snapshot={current} summary="Retained context" /><Location />
  </MemoryRouter>

  const { rerender } = render(tree())
  const disclosure = () => screen.getByText('Retained context').closest('details')!
  expect(disclosure().open).toBe(true)
  rerender(tree({ ...member, id: 'another-member' }))
  expect(disclosure().open).toBe(false)
  rerender(tree({ ...member, identityId: 'replacement-identity' }))
  expect(disclosure().open).toBe(false)
  rerender(tree(member, { ...snapshot, connection: { ...snapshot.connection!, ownerScope: 'another-owner' } }))
  expect(disclosure().open).toBe(false)
  rerender(tree())
  expect(disclosure().open).toBe(true)
  fireEvent.click(screen.getByText('Retained context'))
  await waitFor(() => expect(new URL(screen.getByLabelText('Current route').textContent!, 'https://workspace.invalid').searchParams.has('contextMember')).toBe(false))
  expect(disclosure().open).toBe(false)
  const closed = new URL(screen.getByLabelText('Current route').textContent!, 'https://workspace.invalid').searchParams

  for (const key of ['member', 'identity', 'owner', 'view']) { expect(closed.get(key)).toBe(params.get(key)) }
  expect(closed.has('contextIdentity')).toBe(false)
  // Native toggle is queued. A member change before it fires must not adopt the old DOM state.
  fireEvent.click(screen.getByText('Retained context'))
  expect(disclosure().open).toBe(true)
  rerender(tree({ ...member, id: 'another-member' }))
  expect(disclosure().open).toBe(false)
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  expect(new URL(screen.getByLabelText('Current route').textContent!, 'https://workspace.invalid').searchParams.has('contextMember')).toBe(false)
  rerender(tree())
  expect(disclosure().open).toBe(false)
})

it.each(['/messages?member=worker&owner=unverified', '/settings'])('keeps canonical history links when the origin cannot be bound: %s', origin => {
  const snapshot = historyFixture()

  if (origin.startsWith('/messages')) { delete snapshot.connection }
  render(<MemoryRouter initialEntries={[origin]}><AgentContext agent={snapshot.agents[0]} snapshot={snapshot} /></MemoryRouter>)
  expect(screen.getByRole('link', { name: 'Retained objective' }).getAttribute('href')).toBe(`/objectives/${encodeURIComponent(snapshot.objectives[0].id)}`)
  expect(screen.queryByRole('link', { name: 'Omitted objective' })).toBeNull()
})
