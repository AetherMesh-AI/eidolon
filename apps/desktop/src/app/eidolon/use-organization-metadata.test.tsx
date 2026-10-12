import { act, renderHook, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { beforeEach, expect, it, vi } from 'vitest'

import { $pendingConnectionId } from '@/store/connections'
import { $activeGatewayRoute, $gateway } from '@/store/gateway'
import { $organizationObservationEpoch, $organizationObservationSwitching } from '@/store/organization-observation-mode'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection, $gatewayState } from '@/store/session'

import { useOrganizationMetadata } from './use-organization-metadata'

vi.mock('@/store/connections', () => ({ $pendingConnectionId: atom(null) }))
vi.mock('@/store/gateway', () => ({
  $gateway: atom(null), $localOwnerProofRevision: atom(0),
  $activeGatewayRoute: atom('default'),
  activeGatewayConnectionId: () => 'local'
}))
vi.mock('@/store/profile', () => ({ $activeGatewayProfile: atom('default'), $gatewaySwapTarget: atom(null) }))
vi.mock('@/store/session', () => ({ $connection: atom(null), $gatewayState: atom('closed') }))
vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({
  useGatewayRequest: () => {
    throw new Error('Metadata must not use reconnect helper')
  }
}))
vi.mock('./runtime-adapter', () => ({
  createRuntimeAdapter: () => {
    throw new Error('Metadata must not start execution observation')
  }
}))

const descriptor = {
  capabilityVersion: 1,
  state: 'unconfigured',
  metadata: null,
  scope: { kind: 'served-profile', profile: 'default' }
}

let request = vi.fn()
beforeEach(() => {
  $organizationObservationEpoch.set(0)
  $organizationObservationSwitching.set(false)
  request = vi.fn().mockResolvedValue(descriptor)
  $gateway.set({ request } as unknown as NonNullable<ReturnType<typeof $gateway.get>>)
  $connection.set({ connectionId: 'local', profile: 'default', baseUrl: 'http://localhost:4321' } as NonNullable<
    ReturnType<typeof $connection.get>
  >)
  $activeGatewayRoute.set('default')
  $activeGatewayProfile.set('default')
  $gatewayState.set('open')
  $gatewaySwapTarget.set(null)
  $pendingConnectionId.set(null)
})

it('reads once through the captured socket and does not reload on rerender', async () => {
  const h = renderHook(useOrganizationMetadata)
  await waitFor(() => expect(h.result.current.view.descriptor?.state).toBe('unconfigured'))
  h.rerender()
  expect(request).toHaveBeenCalledExactlyOnceWith('organization.descriptor', {}, 15000)
})

it.each(['sharedPrimary', 'sharedRemote'] as const)(
  'does not authorize %s registry sockets to edit names',
  async property => {
    $connection.set({ ...$connection.get()!, [property]: true })
    const h = renderHook(useOrganizationMetadata)
    await act(async () => {
      await h.result.current.client.read()
      await h.result.current.client.save('Name', 'Owner')
    })
    expect(h.result.current.scope.eligible).toBe(false)
    expect(request).not.toHaveBeenCalled()
  }
)

it('ignores an old descriptor while a new connection selection is pending', async () => {
  let finish!: (value: unknown) => void
  request.mockReturnValueOnce(
    new Promise(resolve => {
      finish = resolve
    })
  )
  const h = renderHook(useOrganizationMetadata)
  act(() => $pendingConnectionId.set('other'))
  await act(async () => {
    finish(descriptor)
  })
  expect(h.result.current.view.descriptor).toBeNull()
  expect(h.result.current.scope.eligible).toBe(false)
  expect(request).toHaveBeenCalledTimes(1)
})

it('holds reads during a profile mismatch and resumes only after scope agreement', async () => {
  $activeGatewayProfile.set('other')
  const h = renderHook(useOrganizationMetadata)
  expect(h.result.current.scope.eligible).toBe(false)
  expect(request).not.toHaveBeenCalled()
  act(() => $activeGatewayProfile.set('default'))
  await waitFor(() => expect(h.result.current.view.descriptor?.state).toBe('unconfigured'))
  expect(request).toHaveBeenCalledTimes(1)
})


it('revokes metadata during same-route observation switching and rereads after its generation changes', async () => {
  const h = renderHook(useOrganizationMetadata)
  await waitFor(() => expect(h.result.current.view.descriptor?.state).toBe('unconfigured'))
  const original = h.result.current.scope.key
  act(() => {
    $organizationObservationSwitching.set(true)
    $organizationObservationEpoch.set(1)
  })
  expect(h.result.current.scope.eligible).toBe(false)
  expect(h.result.current.scope.key).not.toBe(original)
  expect(h.result.current.view.descriptor).toBeNull()
  expect(request).toHaveBeenCalledTimes(1)
  act(() => $organizationObservationSwitching.set(false))
  await waitFor(() => expect(h.result.current.view.descriptor?.state).toBe('unconfigured'))
  expect(request).toHaveBeenCalledTimes(2)
  expect(request.mock.calls.every(([method]) => method === 'organization.descriptor')).toBe(true)
})
