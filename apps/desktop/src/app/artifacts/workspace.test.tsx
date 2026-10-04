import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { atom } from 'nanostores'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({ owner: 'profile-a', getEvidence: vi.fn() }))
vi.mock('@/store/connections', () => ({ $activeConnectionId: atom('local') }))
vi.mock('@/store/profile', () => ({ $activeGatewayProfile: atom('default') }))
vi.mock('@/app/eidolon/runtime-provider', () => ({
  useRuntimeOrganization: () => ({
    adapter: { getEvidence: state.getEvidence },
    snapshot: { connection: { ownerScope: state.owner }, knowledge: [] }
  })
}))
vi.mock('@/app/eidolon/runtime-detail', () => ({ RuntimeStatus: () => <div>Runtime status</div> }))
vi.mock('@/app/eidolon/workspace', () => ({ Knowledge: () => <div>Organization evidence list</div> }))
vi.mock('./index', () => ({ ArtifactsView: () => <div>Original session artifacts</div> }))

import { ArtifactWorkspace } from './workspace'

const Location = () => <output>{useLocation().search}</output>
afterEach(cleanup)

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
