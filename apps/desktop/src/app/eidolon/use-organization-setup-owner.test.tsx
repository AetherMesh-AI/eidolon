import { act, renderHook, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { beforeEach, expect, it, vi } from 'vitest'

import { $connectionsRegistry, $pendingConnectionId } from '@/store/connections'
import { $activeGatewayRoute, $gateway, $gatewayTransportRevision } from '@/store/gateway'
import { $gatewaySwitching } from '@/store/gateway-switch'
import { $organizationObservationEpoch, $organizationObservationSwitching } from '@/store/organization-observation-mode'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection, $gatewayState } from '@/store/session'
import { $sourceSwitchIntent } from '@/store/source-switch-intent'

import { deferred } from '../../test/deferred'

import { useOrganizationSetupOwner } from './use-organization-setup-owner'

vi.mock('@/store/connections', () => ({ $pendingConnectionId: atom(null), $connectionsRegistry: atom(null) }))
vi.mock('@/store/gateway', () => ({
  $gateway: atom(null),
  $localOwnerProofRevision: atom(0),
  $activeGatewayRoute: atom('default'),
  $gatewayTransportRevision: atom(0),
  activeGatewayConnectionId: () => 'local',
  gatewayTransportGeneration: (gateway: { generation: number }) => gateway.generation
}))
vi.mock('@/store/gateway-switch', () => ({ $gatewaySwitching: atom(false) }))
vi.mock('@/store/profile', () => ({ $activeGatewayProfile: atom('default'), $gatewaySwapTarget: atom(null) }))
vi.mock('@/store/session', () => ({ $connection: atom(null), $gatewayState: atom('closed') }))
vi.mock('@/app/gateway/hooks/use-gateway-request', () => ({
  useGatewayRequest: () => {
    throw new Error('No reconnect helper')
  }
}))
vi.mock('./runtime-adapter', () => ({
  createRuntimeAdapter: () => {
    throw new Error('No runtime observation')
  }
}))

const profile = 'fixture-served-home'
const organizationId = '11111111-1111-4111-8111-111111111111'
const ownerId = '22222222-2222-4222-8222-222222222222'

const descriptor = {
  capabilityVersion: 1,
  state: 'ready',
  scope: { kind: 'served-profile', profile },
  metadata: { organizationId, owner: { ownerId } },
  isolation: {
    profile,
    organizationId,
    ownerId,
    isolationVersion: 1,
    credentialMode: 'profile-local-openai-api',
    executionMode: 'tool-free-organization',
    supportedProviders: ['openai-api']
  }
}

beforeEach(() => {
  $connectionsRegistry.set({
    version: 2,
    primary: 'local',
    secureTokenStorage: true,
    connections: [{ id: 'local', kind: 'local', label: 'This device', tokenSet: false, tokenPreview: null }]
  })
  // A reloaded workspace may be the physical primary, still serving its own local home.
  $connection.set({ connectionId: 'local', mode: 'local', profile, baseUrl: 'http://127.0.0.1:6161' } as never)
  $activeGatewayProfile.set(profile)
  $activeGatewayRoute.set(profile)
  $gatewayState.set('open')
  $pendingConnectionId.set(null)
  $gatewaySwapTarget.set(null)
  $gatewaySwitching.set(false)
  $sourceSwitchIntent.set(0)
  $organizationObservationSwitching.set(false)
})

it.each(['owner', 'generation'])('requires fresh proof and then restores supported setup after a %s change', async change => {
  const response = deferred<unknown>()
  const gateway = { request: vi.fn().mockResolvedValue(descriptor), connectionState: 'open', generation: 1 }
  $gateway.set(gateway as never)

  if (change === 'owner') {
    $gatewayState.set('closed')
  }

  const view = renderHook(useOrganizationSetupOwner)

  if (change === 'owner') {
    expect(view.result.current).toBe(false)
    expect(gateway.request).not.toHaveBeenCalled()
  } else {
    await waitFor(() => expect(view.result.current).toBe(true))
  }

  const nextProfile = change === 'owner' ? 'another-served-home' : profile

  const nextDescriptor = {
    ...descriptor,
    scope: { ...descriptor.scope, profile: nextProfile },
    isolation: { ...descriptor.isolation, profile: nextProfile }
  }

  const nextGateway = change === 'owner' ? { ...gateway, request: vi.fn() } : gateway
  nextGateway.request.mockReturnValue(response.promise)

  await act(async () => {
    if (change === 'owner') {
      $gateway.set(nextGateway as never)
      $connection.set({ ...$connection.get()!, profile: nextProfile })
      $activeGatewayProfile.set(nextProfile)
      $activeGatewayRoute.set(nextProfile)
      $sourceSwitchIntent.set($sourceSwitchIntent.get() + 1)
      $organizationObservationEpoch.set($organizationObservationEpoch.get() + 1)
      $gatewayState.set('open')
    } else {
      gateway.generation++
      $gatewayTransportRevision.set($gatewayTransportRevision.get() + 1)
    }
  })

  expect(view.result.current).toBe(false)
  await act(async () => response.resolve(nextDescriptor))
  await waitFor(() => expect(view.result.current).toBe(true))
  expect(nextGateway.request.mock.calls.map(call => call[0])).toEqual(
    change === 'owner' ? ['organization.descriptor'] : ['organization.descriptor', 'organization.descriptor']
  )
  view.unmount()
})

it('requires current strict identity rather than a profile name or pending, foreign, legacy or remote proof, and re-verifies on remount', async () => {
  for (const kind of [
    'valid',
    'legacy',
    'foreign',
    'wrong-owner',
    'unsupported',
    'remote',
    'shared',
    'unknown-generation',
    'unregistered'
  ]) {
    const response = deferred<unknown>()

    const gateway = {
      request: vi.fn().mockReturnValue(response.promise),
      connectionState: 'open',
      generation: kind === 'unknown-generation' ? undefined : 1
    }

    if (kind === 'unregistered') {
      $connectionsRegistry.set({ ...$connectionsRegistry.get()!, connections: [] })
    }

    $gateway.set(gateway as never)
    $connection.set({
      ...$connection.get()!,
      mode: kind === 'remote' ? 'remote' : 'local',
      sharedPrimary: kind === 'shared'
    })
    const view = renderHook(useOrganizationSetupOwner)
    expect(view.result.current).toBe(false)
    await act(async () =>
      response.resolve(
        kind === 'legacy'
          ? { ...descriptor, isolation: undefined }
          : kind === 'foreign'
            ? { ...descriptor, scope: { ...descriptor.scope, profile: 'foreign' } }
            : kind === 'wrong-owner'
              ? { ...descriptor, isolation: { ...descriptor.isolation, ownerId: organizationId } }
              : kind === 'unsupported'
                ? { ...descriptor, capabilityVersion: 2 }
                : descriptor
      )
    )
    expect(view.result.current).toBe(kind === 'valid')
    expect(gateway.request.mock.calls.map(call => call[0])).toEqual(
      ['remote', 'shared', 'unknown-generation', 'unregistered'].includes(kind) ? [] : ['organization.descriptor']
    )
    view.unmount()

    if (kind === 'valid') {
      const again = deferred<unknown>()
      gateway.request.mockReturnValueOnce(again.promise)
      const remount = renderHook(useOrganizationSetupOwner)
      expect(remount.result.current).toBe(false)
      await act(async () => again.resolve(descriptor))
      expect(remount.result.current).toBe(true)
      remount.unmount()
    }
  }
})

it('revokes retained or delayed setup ownership on socket, generation, connection, registry, source intent, closure or disposal changes', async () => {
  for (const change of [
    'socket',
    'generation',
    'connection',
    'registry',
    'intent',
    'pending',
    'swap',
    'switching',
    'epoch',
    'route',
    'observation-switching',
    'closed',
    'disposed'
  ]) {
    $gatewayState.set('open')
    $gatewaySwitching.set(false)
    $pendingConnectionId.set(null)
    $gatewaySwapTarget.set(null)
    $activeGatewayRoute.set(profile)
    $organizationObservationSwitching.set(false)
    const response = deferred<unknown>()
    const gateway = { request: vi.fn().mockResolvedValue(descriptor), connectionState: 'open', generation: 1 }
    $gateway.set(gateway as never)
    const view = renderHook(useOrganizationSetupOwner)
    await waitFor(() => expect(view.result.current).toBe(true))
    gateway.request.mockReturnValue(response.promise)
    await act(async () => {
      if (change === 'socket') {
        $gateway.set({ ...gateway } as never)
      }

      if (change === 'generation') {
        gateway.generation++
        $gatewayTransportRevision.set($gatewayTransportRevision.get() + 1)
      }

      if (change === 'connection') {
        $connection.set({ ...$connection.get()! })
      }

      if (change === 'registry') {
        $connectionsRegistry.set({ ...$connectionsRegistry.get()! })
      }

      if (change === 'intent') {
        $sourceSwitchIntent.set($sourceSwitchIntent.get() + 1)
      }

      if (change === 'pending') {
        $pendingConnectionId.set('other')
      }

      if (change === 'swap') {
        $gatewaySwapTarget.set('other')
      }

      if (change === 'switching') {
        $gatewaySwitching.set(true)
      }

      if (change === 'epoch') {
        $organizationObservationEpoch.set($organizationObservationEpoch.get() + 1)
      }

      if (change === 'route') {
        $activeGatewayRoute.set('other')
      }

      if (change === 'observation-switching') {
        $organizationObservationSwitching.set(true)
      }

      if (change === 'closed') {
        gateway.connectionState = 'closed'
        $gatewayState.set('closed')
      }

      if (change === 'disposed') {
        view.unmount()
      }
    })

    if (change !== 'disposed') {
      expect(view.result.current).toBe(false)
    }

    // Retire the replacement read too; its delayed valid answer must not revive ownership.
    await act(async () => {
      $gatewayState.set('closed')
      response.resolve(descriptor)
    })

    if (change !== 'disposed') {
      expect(view.result.current).toBe(false)
      view.unmount()
    }
  }
})
