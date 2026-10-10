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
  snapshot.agents.push({ ...worker, id: 'owner', name: 'Owner', role: 'Owner' }, { ...worker, id: 'transient', name: 'Transient', persistent: false })
  const adapter = { mode: 'runtime', getSnapshot: () => snapshot, openOwnerChat: vi.fn(async () => ownerChatThread()), readOwnerChat: vi.fn(), sendOwnerChat: vi.fn(), cancelOwnerChat: vi.fn() } as unknown as RuntimeOrganizationAdapter
  const view = render(<MemoryRouter><Messages adapter={adapter} snapshot={snapshot} /></MemoryRouter>)
  expect(screen.queryByRole('button', { name: /Owner|Transient/ })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /Worker 1/ }))
  expect(adapter.openOwnerChat).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: 'Call' })).toHaveProperty('disabled', true)
  fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
  await waitFor(() => expect(adapter.openOwnerChat).toHaveBeenCalledExactlyOnceWith({ agentId: 'worker', identityId: 'identity-worker' }))
  await screen.findByRole('textbox', { name: 'Message' })
  // A newly loaded profile cannot retain the previous profile's selected chat.
  view.rerender(<MemoryRouter><Messages adapter={adapter} key="other-owner" snapshot={{ ...snapshot, connection: { scope: 'other', ownerScope: 'other-owner', state: 'ready' } }} /></MemoryRouter>)
  expect(screen.queryByRole('textbox', { name: 'Message' })).toBeNull()
  expect(adapter.openOwnerChat).toHaveBeenCalledTimes(1)
  expect(adapter.sendOwnerChat).not.toHaveBeenCalled()
})
