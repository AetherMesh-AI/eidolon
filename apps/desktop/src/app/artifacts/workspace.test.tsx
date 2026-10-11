import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { atom } from 'nanostores'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  owner: 'profile-a',
  getEvidence: vi.fn(),
  outcomes: [] as Array<{
    objectiveId: string
    status: string
    deliverableId: string | null
    title: string
    updatedAt: string
  }>
}))

vi.mock('@/store/connections', () => ({ $activeConnectionId: atom('local') }))
vi.mock('@/store/profile', () => ({ $activeGatewayProfile: atom('default') }))
vi.mock('@/app/eidolon/runtime-provider', () => ({
  useRuntimeOrganization: () => ({
    adapter: { getEvidence: state.getEvidence },
    snapshot: { connection: { ownerScope: state.owner }, knowledge: [], outcomes: { items: state.outcomes } }
  })
}))
vi.mock('@/app/eidolon/runtime-detail', () => ({ RuntimeStatus: () => <div>Runtime status</div> }))
vi.mock('@/app/eidolon/workspace', () => ({ Knowledge: () => <div>Organization evidence list</div> }))
vi.mock('./index', () => ({ ArtifactsView: () => <div>Original session artifacts</div> }))

import { ArtifactWorkspace } from './workspace'

const Location = () => <output>{useLocation().search}</output>
afterEach(() => {
  cleanup()
  state.outcomes = []
})

it('keeps artifact authority explicit and preserves existing filter deeplinks', () => {
  render(
    <MemoryRouter initialEntries={['/artifacts?tab=image']}>
      <ArtifactWorkspace />
      <Location />
    </MemoryRouter>
  )
  expect(screen.getByText('Original session artifacts')).toBeTruthy()
  expect(screen.queryByText('Organization evidence list')).toBeNull()
  expect(screen.getByText(/Files and links from chat history/)).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Session files' }).getAttribute('aria-current')).toBe('page')
  expect(screen.getByRole('link', { name: 'Organization evidence' }).getAttribute('aria-current')).toBeNull()
  fireEvent.click(screen.getByRole('link', { name: 'Organization evidence' }))
  expect(screen.getByText('Organization evidence list')).toBeTruthy()
  expect(screen.queryByText('Original session artifacts')).toBeNull()
  expect(screen.getByText(/current-profile organization ledger/)).toBeTruthy()
  expect(screen.getByText('?tab=image&source=organization')).toBeTruthy()
  fireEvent.click(screen.getByRole('link', { name: 'Session files' }))
  expect(screen.getByText('?tab=image&source=sessions')).toBeTruthy()
  expect(state.getEvidence).not.toHaveBeenCalled()
})

it('shows only explicitly accepted outcomes with recorded deliverables', () => {
  state.outcomes = [
    {
      objectiveId: 'accepted',
      status: 'accepted',
      deliverableId: 'evidence-a',
      title: 'Accepted result',
      updatedAt: '2026-10-01T10:00:00Z'
    },
    {
      objectiveId: 'completed',
      status: 'completed',
      deliverableId: 'evidence-b',
      title: 'Task completion is not acceptance',
      updatedAt: '2026-10-01T10:00:00Z'
    },
    {
      objectiveId: 'missing',
      status: 'accepted',
      deliverableId: null,
      title: 'Missing artifact',
      updatedAt: '2026-10-01T10:00:00Z'
    }
  ]
  render(
    <MemoryRouter initialEntries={['/artifacts?source=organization']}>
      <ArtifactWorkspace />
    </MemoryRouter>
  )
  expect(screen.getByRole('heading', { name: 'Accepted result' })).toBeTruthy()
  expect(screen.queryByText('Task completion is not acceptance')).toBeNull()
  expect(screen.queryByText('Missing artifact')).toBeNull()
  expect(screen.getByRole('link', { name: 'View objective →' }).getAttribute('href')).toBe('/objectives/accepted')
  expect(state.getEvidence).not.toHaveBeenCalled()
})
