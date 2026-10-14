import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import type { MetadataScope } from './organization-metadata-client'
import { deferred } from './owner-policy-test-support'
import { allowanceDescriptor, rootId, rootOwner } from './shared-allowance-test-support'
import { useOrganizationSharedAllowance } from './use-organization-shared-allowance'

const state = vi.hoisted(() => ({
  scope: null as MetadataScope | null,
  metadata: null as unknown,
  refresh: vi.fn(async () => {})
}))

vi.mock('./use-organization-metadata', () => ({
  readOrganizationMetadataScope: () => state.scope,
  organizationMetadataClient: { getSnapshot: () => ({ descriptor: { metadata: state.metadata } }) },
  useOrganizationMetadata: () => ({
    scope: state.scope,
    view: { descriptor: { metadata: state.metadata }, busy: false },
    client: { read: state.refresh }
  })
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  sessionStorage.clear()
})

function Surface() {
  const allowance = useOrganizationSharedAllowance()

  return (
    <>
      <output>{allowance.view.descriptor?.scope.profile ?? 'unavailable'}</output>
      <button onClick={() => void allowance.refresh()}>Refresh</button>
    </>
  )
}

it('the real hook captures metadata identity and reads only the active authenticated request boundary', async () => {
  const request = vi.fn(async () => allowanceDescriptor())
  state.metadata = { organizationId: rootId, owner: { ownerId: rootOwner } }
  state.scope = {
    key: 'hook-socket-root',
    ownerKey: 'hook-owner-root',
    profile: 'default',
    eligible: true,
    request: request as MetadataScope['request']
  }
  render(<Surface />)
  await waitFor(() => expect(screen.getByText('default')).toBeTruthy())
  expect(request).toHaveBeenCalledExactlyOnceWith('organization.sharedAllowance', { expectedOrganizationId: rootId })
  await act(async () => {
    screen.getByText('Refresh').click()
  })
  expect(state.refresh).toHaveBeenCalledTimes(1)
  expect(request).toHaveBeenCalledTimes(2)
})

it('the real hook ignores late reads after disconnection without reconnecting or using a second route', async () => {
  const pending = deferred<ReturnType<typeof allowanceDescriptor>>()
  const request = vi.fn(() => pending.promise)
  state.metadata = { organizationId: rootId, owner: { ownerId: rootOwner } }
  state.scope = {
    key: 'hook-pending-socket',
    ownerKey: 'hook-pending-owner',
    profile: 'default',
    eligible: true,
    request: request as MetadataScope['request']
  }
  const view = render(<Surface />)
  await waitFor(() => expect(request).toHaveBeenCalledTimes(1))
  state.scope = { ...state.scope, key: 'hook-offline', eligible: false }
  view.rerender(<Surface />)
  await act(async () => {
    pending.resolve(allowanceDescriptor())
    await pending.promise
  })
  expect(screen.getByText('unavailable')).toBeTruthy()
  expect(request).toHaveBeenCalledTimes(1)
})
