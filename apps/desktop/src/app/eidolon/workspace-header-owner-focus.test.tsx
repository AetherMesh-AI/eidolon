import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { useSyncExternalStore } from 'react'
import { MemoryRouter } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'

import { $activeGatewayProfile } from '@/store/profile'

import { createOrganizationMetadataClient, type MetadataDescriptor } from './organization-metadata-client'
import { WorkspaceHeader } from './workspace-header'

const state = vi.hoisted(() => ({
  client: null as ReturnType<typeof createOrganizationMetadataClient> | null,
  scope: 'socket-a:default',
  request: vi.fn()
}))

vi.mock('@/store/profile', () => ({ $activeGatewayProfile: atom('default'), $gatewaySwapTarget: atom(null) }))
vi.mock('@/store/connections', () => ({ $pendingConnectionId: atom(null) }))
vi.mock('@/store/command-palette', () => ({ openCommandPalettePage: vi.fn() }))
vi.mock('../chat/sidebar/connection-switcher', () => ({ ConnectionSwitcher: () => null }))
vi.mock('../contrib/organization-shell', () => ({ OrganizationWorkIndicator: () => null }))
vi.mock('../routes', () => ({ navigateToWorkspacePage: vi.fn() }))
vi.mock('./runtime-provider', () => ({
  useRuntimeOrganization: () => null,
  createOrganizationScopeReader: () => () => ({ key: state.scope, connected: true })
}))
vi.mock('./use-organization-metadata', () => ({
  useOrganizationMetadata: () => ({
    client: state.client!,
    view: useSyncExternalStore(state.client!.subscribe, state.client!.getSnapshot),
    scope: { key: state.scope, ownerKey: 'focus-test', eligible: true }
  })
}))

const unconfigured: MetadataDescriptor = {
  capabilityVersion: 1,
  state: 'unconfigured',
  metadata: null,
  scope: { kind: 'served-profile', profile: 'default' }
}

const receipt: MetadataDescriptor = {
  ...unconfigured,
  state: 'ready',
  metadata: {
    organizationId: '11111111-1111-4111-8111-111111111111',
    owner: { ownerId: '22222222-2222-4222-8222-222222222222', displayName: 'Alex' },
    displayName: 'Research',
    revision: 1,
    createdAt: '2026-10-11T00:00:00Z',
    updatedAt: '2026-10-11T00:00:00Z'
  }
}

beforeEach(async () => {
  state.scope = 'socket-a:default'
  state.request.mockReset().mockResolvedValue(unconfigured)
  $activeGatewayProfile.set('default')
  state.client = createOrganizationMetadataClient(() => ({
    key: state.scope,
    ownerKey: 'focus-test',
    profile: 'default',
    eligible: true,
    request: state.request
  }))
  await state.client.read()
})

function openEditor() {
  render(
    <MemoryRouter>
      <WorkspaceHeader />
    </MemoryRouter>
  )
  const trigger = screen.getByRole('button', { name: 'Edit organization and owner names' })
  fireEvent.click(trigger)
  fireEvent.change(screen.getByLabelText('Organization name'), { target: { value: 'Research' } })
  fireEvent.change(screen.getByLabelText('Your display name'), { target: { value: 'Alex' } })
  screen.getByLabelText('Organization name').focus()

  return trigger
}

it('returns focus after its verified first Save initializes a new identity', async () => {
  const trigger = openEditor()
  state.request.mockResolvedValueOnce(receipt)
  fireEvent.click(screen.getByRole('button', { name: 'Save names' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  await waitFor(() => expect(window.document.activeElement).toBe(trigger))
  expect(state.client!.getSnapshot().descriptor).toEqual(receipt)
})

it('does not treat an unrelated descriptor initialization as its own successful Save', async () => {
  const trigger = openEditor()
  state.request.mockResolvedValueOnce(receipt)
  await act(async () => {
    await state.client!.read()
  })
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  await waitFor(() => expect(window.document.activeElement).toBe(window.document.body))
  expect(window.document.activeElement).not.toBe(trigger)
})

it('does not restore focus across a source change while the first Save is pending', async () => {
  const trigger = openEditor()
  let resolve!: (value: MetadataDescriptor) => void
  state.request.mockImplementationOnce(
    () =>
      new Promise<MetadataDescriptor>(done => {
        resolve = done
      })
  )
  fireEvent.click(screen.getByRole('button', { name: 'Save names' }))
  act(() => {
    state.scope = 'socket-b:other'
    $activeGatewayProfile.set('other')
  })
  await act(async () => {
    resolve(receipt)
  })
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  await waitFor(() => expect(window.document.activeElement).toBe(window.document.body))
  expect(window.document.activeElement).not.toBe(trigger)
  expect(state.client!.getSnapshot().descriptor).toBeNull()
})
