import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { Messages } from './messages'
import { ownerChatSnapshot, ownerChatThread } from './runtime-owner-chat.test-support'
import type { RuntimeOrganizationAdapter } from './types'

afterEach(cleanup)

it('selects only persistent members and opens their exact durable identity only after Chat is requested', async () => {
  const snapshot = ownerChatSnapshot()
  const worker = snapshot.agents[0]
  snapshot.agents.push(
    { ...worker, id: 'owner', name: 'Owner', role: 'Owner' },
    { ...worker, id: 'transient', name: 'Transient', persistent: false }
  )

  const adapter = {
    mode: 'runtime',
    getSnapshot: () => snapshot,
    openOwnerChat: vi.fn(async () => ownerChatThread()),
    readOwnerChat: vi.fn(),
    sendOwnerChat: vi.fn(),
    cancelOwnerChat: vi.fn()
  } as unknown as RuntimeOrganizationAdapter

  const view = render(
    <MemoryRouter>
      <Messages adapter={adapter} snapshot={snapshot} />
    </MemoryRouter>
  )

  expect(screen.queryByRole('button', { name: /Owner|Transient/ })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /Worker 1/ }))
  expect(adapter.openOwnerChat).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: 'Call' })).toHaveProperty('disabled', true)
  fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
  await waitFor(() =>
    expect(adapter.openOwnerChat).toHaveBeenCalledExactlyOnceWith({ agentId: 'worker', identityId: 'identity-worker' })
  )
  await screen.findByRole('textbox', { name: 'Message' })
  // A newly loaded profile cannot retain the previous profile's selected chat.
  view.rerender(
    <MemoryRouter>
      <Messages
        adapter={adapter}
        key="other-owner"
        snapshot={{ ...snapshot, connection: { scope: 'other', ownerScope: 'other-owner', state: 'ready' } }}
      />
    </MemoryRouter>
  )
  expect(screen.queryByRole('textbox', { name: 'Message' })).toBeNull()
  expect(adapter.openOwnerChat).toHaveBeenCalledTimes(1)
  expect(adapter.sendOwnerChat).not.toHaveBeenCalled()
})

it('searches recorded members without closing a chat or retargeting a replaced identity', async () => {
  const snapshot = ownerChatSnapshot()

  const adapter = {
    mode: 'runtime', getSnapshot: () => snapshot,
    openOwnerChat: vi.fn(async () => ownerChatThread()), readOwnerChat: vi.fn(),
    sendOwnerChat: vi.fn(), cancelOwnerChat: vi.fn()
  } as unknown as RuntimeOrganizationAdapter

  const view = render(<MemoryRouter><Messages adapter={adapter} snapshot={snapshot} /></MemoryRouter>)
  fireEvent.change(screen.getByRole('textbox', { name: 'Search members' }), { target: { value: 'research' } })
  fireEvent.click(screen.getByRole('button', { name: /Worker 1/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
  const composer = await screen.findByRole('textbox', { name: 'Message' })
  fireEvent.change(composer, { target: { value: 'Keep this unsent draft' } })
  fireEvent.change(screen.getByRole('textbox', { name: 'Search members' }), { target: { value: 'unmatched member' } })
  expect(screen.getByRole('status').textContent).toContain('No members match')
  expect(screen.getByRole('textbox', { name: 'Message' })).toBe(composer)
  expect(composer).toHaveProperty('value', 'Keep this unsent draft')
  expect(adapter.openOwnerChat).toHaveBeenCalledTimes(1)
  expect(adapter.sendOwnerChat).not.toHaveBeenCalled()
  view.rerender(<MemoryRouter><Messages adapter={adapter} snapshot={{ ...snapshot, agents: [{ ...snapshot.agents[0], identityId: 'replacement' }] }} /></MemoryRouter>)
  expect(screen.queryByRole('textbox', { name: 'Message' })).toBeNull()
  expect(adapter.openOwnerChat).toHaveBeenCalledTimes(1)
})

it('links only recorded ownership and current assignments, without inferring team membership', () => {
  const snapshot = ownerChatSnapshot()
  snapshot.objectives = ['assigned', 'historical', 'old-round', 'same-team', 'leadership'].map(id => ({
    id, title: `Objective ${id}`, description: '', status: 'active', source: 'runtime',
    ownerId: id === 'leadership' ? 'worker' : 'executive', createdAt: '2026-10-08T12:00:00Z'
  }))
  snapshot.tasks = ['assigned', 'historical', 'old-round', 'same-team'].map(id => ({
    id, objectiveId: id, title: id, ownerId: id === 'same-team' ? 'other-worker' : 'worker',
    team: 'Research', status: 'working', dependsOn: [], historical: id === 'historical', currentRound: id !== 'old-round'
  }))
  render(<MemoryRouter><Messages snapshot={snapshot} /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: /Worker 1/ }))
  expect(screen.getByRole('link', { name: 'Objective assigned' }).getAttribute('href')).toBe('/objectives/assigned')
  expect(screen.getByRole('link', { name: 'Objective leadership' })).toBeTruthy()

  for (const id of ['historical', 'old-round', 'same-team']) {
    expect(screen.queryByRole('link', { name: `Objective ${id}` })).toBeNull()
  }
})


it('organization entry offers explicit executive choice without opening or sending to a default recipient', () => {
  const snapshot = ownerChatSnapshot()
  const member = snapshot.agents[0]
  snapshot.agents.push(
    { ...member, id: 'ops', identityId: 'ops-id', name: 'Operations executive', role: 'Executive' },
    { ...member, id: 'product', identityId: 'product-id', name: 'Product executive', role: 'Executive' },
    { ...member, id: 'unknown', identityId: 'unknown-id', name: 'Unknown lifecycle', role: 'Executive', lifecycle: undefined },
    { ...member, id: 'blank-id', identityId: '   ', name: 'Blank identity', role: 'Executive' },
    { ...member, id: 'no-id', identityId: undefined, name: 'Missing identity', role: 'Executive' },
    { ...member, id: 'retired', identityId: 'retired-id', name: 'Retired executive', role: 'Executive', lifecycle: 'retired' }
  )
  const adapter = { mode: 'runtime', getSnapshot: () => snapshot, openOwnerChat: vi.fn(), sendOwnerChat: vi.fn() } as unknown as RuntimeOrganizationAdapter
  const view = render(<MemoryRouter initialEntries={['/messages?recipient=executive']}><Messages adapter={adapter} snapshot={snapshot} /></MemoryRouter>)
  expect(screen.getByRole('button', { name: /Operations executive/ })).toBeTruthy()
  expect(screen.getByRole('button', { name: /Product executive/ })).toBeTruthy()
  expect(screen.queryByRole('button', { name: /Worker 1|Retired executive|Unknown lifecycle|Missing identity|Blank identity/ })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Chat' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /Product executive/ }))
  expect(screen.getByRole('heading', { name: 'Product executive' })).toBeTruthy()
  expect(adapter.openOwnerChat).not.toHaveBeenCalled()
  expect(adapter.sendOwnerChat).not.toHaveBeenCalled()
  view.rerender(<MemoryRouter initialEntries={['/messages?recipient=executive']}><Messages adapter={adapter} snapshot={{ ...snapshot, agents: [] }} /></MemoryRouter>)
  expect(screen.getAllByText(/No active executive with a verified identity is available/)).toHaveLength(2)
  expect(screen.getByRole('link', { name: 'Review organization roster' }).getAttribute('href')).toBe('/organization')
  expect(screen.queryByRole('button', { name: 'Chat' })).toBeNull()
})
