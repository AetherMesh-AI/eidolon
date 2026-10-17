import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { Home } from './home'
import { ownerChatSnapshot } from './runtime-owner-chat.test-support'
import type { RuntimeOrganizationAdapter } from './types'
import { objectiveReturnTarget } from './workspace-navigation'

afterEach(cleanup)

it.each([true, false])('keeps accepted-delivery navigation read-only and bound to its Home origin (durable owner: %s)', durableOwner => {
  const snapshot = ownerChatSnapshot()
  if (!durableOwner) { delete snapshot.connection!.ownerScope }
  const owner = snapshot.connection!.ownerScope ?? snapshot.connection!.scope
  const id = 'accepted /?#'
  const origin = '/home?view=accepted&query=reviewed'
  snapshot.outcomes = {
    generation: 'accepted-generation', total: 1, unread: 0, hasMore: false, nextCursor: null,
    items: [{
      objectiveId: id, title: 'Retained accepted delivery', summary: 'Reviewed synthetic result',
      status: 'accepted', deliverableId: 'retained-artifact', acceptanceRequestId: 'review', evidenceIds: ['retained-artifact'],
      revision: 1, seen: false, round: 1, archived: false,
      createdAt: '2026-10-10T00:00:00Z', updatedAt: '2026-10-10T00:00:00Z'
    }]
  }
  const adapter = {
    mode: 'runtime', getSnapshot: () => snapshot, createObjective: vi.fn(),
    openOwnerChat: vi.fn(), sendOwnerChat: vi.fn(), getEvidence: vi.fn(), resolveRequest: vi.fn()
  } as unknown as RuntimeOrganizationAdapter
  const before = JSON.stringify(snapshot)

  function Location() {
    const location = useLocation()

    return <output aria-label="Current route">{location.pathname + location.search}</output>
  }

  render(<MemoryRouter initialEntries={[origin]}><Home adapter={adapter} snapshot={snapshot} /><Location /></MemoryRouter>)
  const card = screen.getByRole('heading', { name: 'Retained accepted delivery' }).closest('article')!
  const link = within(card).getByRole('link', { name: 'View objective →' })
  const destination = new URL(link.getAttribute('href')!, 'https://workspace.invalid')
  expect(destination.pathname).toBe(`/objectives/${encodeURIComponent(id)}`)
  expect(destination.searchParams.get('from')).toBe(origin)
  expect(destination.searchParams.get('originOwner')).toBe(owner)
  expect(objectiveReturnTarget(destination.searchParams, owner)).toBe(origin)
  expect(objectiveReturnTarget(destination.searchParams, 'another-owner')).toBe('/objectives')
  fireEvent.click(link)
  expect(screen.getByLabelText('Current route').textContent).toBe(destination.pathname + destination.search)
  expect(JSON.stringify(snapshot)).toBe(before)
  for (const action of [adapter.createObjective, adapter.openOwnerChat, adapter.sendOwnerChat, adapter.getEvidence, adapter.resolveRequest]) {
    expect(action).not.toHaveBeenCalled()
  }
})
