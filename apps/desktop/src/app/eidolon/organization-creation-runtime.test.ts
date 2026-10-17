import { atom } from 'nanostores'
import { beforeEach, expect, it, vi } from 'vitest'

import { $connectionsRegistry, $pendingConnectionId, selectConnectionWithOutcome } from '@/store/connections'
import { $activeGatewayRoute, $gateway } from '@/store/gateway'
import { $organizationObservationEpoch, $organizationObservationSwitching } from '@/store/organization-observation-mode'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection, $gatewayState } from '@/store/session'

import { deferred } from '../../test/deferred'

import { createRuntimeOrganizationCreation, createRuntimeOrganizationModelSetup } from './organization-creation-runtime'
import { createRuntimeOrganizationDirectory } from './organization-directory-runtime'

vi.mock('@/store/connections', () => ({
  $pendingConnectionId: atom(null),
  $connectionsRegistry: atom(null),
  selectConnectionWithOutcome: vi.fn()
}))
vi.mock('@/store/gateway', () => ({
  $gateway: atom(null),
  $localOwnerProofRevision: atom(0),
  $activeGatewayRoute: atom('default'),
  activeGatewayConnectionId: () => 'local',
  gatewayTransportGeneration: (gateway: { generation: number }) => gateway.generation
}))
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

const id = (n: number) => `${String(n).padStart(8, '0')}-0000-4000-8000-000000000000`
const profile = `org-${'a'.repeat(32)}`

const metadata = (n: number, name: string) => ({
  organizationId: id(n),
  displayName: name,
  owner: { ownerId: id(n + 1), displayName: 'Fixture owner' },
  revision: 1,
  createdAt: '2026-10-17T00:00:00Z',
  updatedAt: '2026-10-17T00:00:00Z'
})

const originMetadata = metadata(1, 'Origin')
const targetMetadata = metadata(3, 'Created')

const targetDescriptor = {
  capabilityVersion: 1,
  state: 'ready',
  scope: { kind: 'served-profile', profile },
  metadata: targetMetadata,
  isolation: {
    isolationVersion: 1,
    credentialMode: 'profile-local-openai-api',
    executionMode: 'tool-free-organization',
    supportedProviders: ['openai-api'],
    profile,
    organizationId: id(3),
    ownerId: id(4)
  }
}

const api = vi.fn().mockResolvedValue({ ok: true })
beforeEach(() => {
  localStorage.clear()
  api.mockClear()
  vi.mocked(selectConnectionWithOutcome).mockReset()
  window.hermesDesktop = { api } as never
  $connectionsRegistry.set({
    version: 2,
    primary: 'local',
    secureTokenStorage: true,
    connections: [{ id: 'local', kind: 'local', label: 'This device', tokenSet: false, tokenPreview: null }]
  })
  $connection.set({
    connectionId: 'local',
    mode: 'local',
    profile: 'default',
    baseUrl: 'http://127.0.0.1:6161'
  } as never)
  $gatewayState.set('open')
  $activeGatewayRoute.set('default')
  $activeGatewayProfile.set('default')
  $pendingConnectionId.set(null)
  $gatewaySwapTarget.set(null)
  $organizationObservationEpoch.set(0)
  $organizationObservationSwitching.set(false)
})

it('recovers its own observation-epoch change after refused proof and retries the same receipt through the real organization switch', async () => {
  const request = vi.fn(async (method: string, params: Record<string, unknown>) => {
    if (method === 'organization.workspaceCreation') {
      return {
        capabilityVersion: 1,
        state: 'available',
        ...targetDescriptor.isolation,
        activation: 'explicit-client',
        origin: { profile: 'default', organizationId: id(1), ownerId: id(2) }
      }
    }

    if (method === 'organization.createWorkspace') {
      return {
        capabilityVersion: 1,
        state: 'created',
        ...targetDescriptor.isolation,
        idempotencyKey: params.idempotencyKey,
        descriptor: targetMetadata,
        activated: false
      }
    }

    return {
      capabilityVersion: 1,
      state: 'ready',
      scope: { kind: 'served-profile', profile: 'default' },
      metadata: originMetadata
    }
  })

  const origin = { request, connectionState: 'open', generation: 1 }
  $gateway.set(origin as never)
  const directory = createRuntimeOrganizationDirectory()
  const client = createRuntimeOrganizationCreation(directory)
  await client.load()
  await client.create('Created', 'Fixture owner')
  expect(client.getSnapshot().stage).toBe('created')
  const initialEpoch = $organizationObservationEpoch.get()
  vi.mocked(selectConnectionWithOutcome).mockResolvedValueOnce({
    status: 'unchanged',
    reason: 'failed',
    requested: { connectionId: 'local', profile },
    observedRoute: { connectionId: 'local', profile: 'default' },
    resetStarted: false,
    published: false
  })
  await client.select()
  expect($organizationObservationEpoch.get()).toBeGreaterThan(initialEpoch)
  expect($gateway.get()).toBe(origin)
  expect(client.getSnapshot()).toMatchObject({ stage: 'created', error: 'selection' })
  expect(client.isCurrent()).toBe(true)

  vi.mocked(selectConnectionWithOutcome).mockImplementationOnce(async (_id, options) => {
    const gateway = { request: vi.fn().mockResolvedValue(targetDescriptor), connectionState: 'open', generation: 2 }
    const connection = { ...$connection.get()!, registryScoped: true, profile, baseUrl: 'http://127.0.0.1:7171' }
    const final = await options!.verify!(gateway as never, connection as never, new AbortController().signal)
    expect(final && final()).toBe(true)
    $gateway.set(gateway as never)
    $connection.set(connection)
    $activeGatewayProfile.set(profile)
    $activeGatewayRoute.set(profile)

    return {
      status: 'committed',
      reason: 'selected',
      requested: { connectionId: 'local', profile },
      observedRoute: { connectionId: 'local', profile },
      resetStarted: true,
      published: true
    }
  })
  await client.select()
  expect(client.getSnapshot().stage).toBe('selected')
  expect(client.canSetup()).toBe(true)
  expect(request.mock.calls.filter(call => call[0] === 'organization.createWorkspace')).toHaveLength(1)
  expect(api).not.toHaveBeenCalled()
  client.dispose()
  directory.directory.dispose()
})

it('pins model config to the selected owner and refuses same-object reconnect, connection or registry changes before PUT or saved paint', async () => {
  for (const change of ['none', 'generation', 'connection', 'registry', 'reply']) {
    const request = vi.fn().mockResolvedValue(targetDescriptor)
    const gateway = { request, connectionState: 'open', generation: 10 }
    $gateway.set(gateway as never)
    $connection.set({ connectionId: 'local', mode: 'local', profile, baseUrl: 'http://127.0.0.1:7171' } as never)
    $activeGatewayProfile.set(profile)
    $activeGatewayRoute.set(profile)
    api.mockClear()
    const runtime = createRuntimeOrganizationDirectory()
    const client = createRuntimeOrganizationModelSetup(runtime)
    await client.load()
    expect(client.getSnapshot().stage).toBe('ready')
    const proof = deferred<unknown>()
    request.mockReturnValueOnce(proof.promise)
    const response = deferred<{ ok: boolean }>()

    if (change === 'reply') {
      api.mockReturnValueOnce(response.promise)
    }

    const saving = client.save('explicit-fixture-model')

    if (change === 'generation') {
      gateway.generation++
    }

    if (change === 'connection') {
      $connection.set({ ...$connection.get()! })
    }

    if (change === 'registry') {
      $connectionsRegistry.set({ ...$connectionsRegistry.get()! })
    }

    proof.resolve(targetDescriptor)

    if (change === 'reply') {
      await vi.waitFor(() => expect(api).toHaveBeenCalledOnce())
      gateway.generation++
      response.resolve({ ok: true })
    }

    await saving

    if (change === 'none' || change === 'reply') {
      expect(api).toHaveBeenCalledExactlyOnceWith({
        connectionId: 'local',
        profile,
        path: '/api/config',
        method: 'PUT',
        body: { config: { model: { provider: 'openai-api', default: 'explicit-fixture-model' } } }
      })
    } else {
      expect(api).not.toHaveBeenCalled()
    }

    expect(client.getSnapshot().result).toBe(change === 'none' ? 'saved' : 'superseded')
    expect(request.mock.calls.map(call => call[0])).toEqual(['organization.descriptor', 'organization.descriptor'])
    client.dispose()
    runtime.directory.dispose()
  }
})
