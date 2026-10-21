import { expect, it, vi } from 'vitest'

import { deferred } from '../../test/deferred'

import { createOrganizationCreationClient } from './organization-creation-client'
import {
  createdWorkspace,
  creationCapability,
  restoreCreationIntent,
  verifyCreatedWorkspace,
  type WorkspaceCreationIntent
} from './organization-creation-contract'
import type { DirectorySource } from './organization-directory'

const id = (number: number) => `${String(number).padStart(8, '0')}-0000-4000-8000-000000000000`

const capability = {
  capabilityVersion: 1,
  state: 'available',
  isolationVersion: 1,
  credentialMode: 'profile-local-openai-api',
  executionMode: 'tool-free-organization',
  activation: 'explicit-client',
  supportedProviders: ['openai-api'],
  origin: { profile: 'default', organizationId: id(1), ownerId: id(2) }
}

const intent: WorkspaceCreationIntent = {
  capabilityVersion: 1,
  expectedOrganizationId: id(1),
  expectedOwnerId: id(2),
  idempotencyKey: id(3),
  displayName: 'Second organization',
  ownerDisplayName: 'Second owner'
}

const receipt = {
  capabilityVersion: 1,
  state: 'created',
  idempotencyKey: id(3),
  profile: `org-${'a'.repeat(32)}`,
  organizationId: id(4),
  ownerId: id(5),
  isolationVersion: 1,
  credentialMode: 'profile-local-openai-api',
  executionMode: 'tool-free-organization',
  activated: false,
  descriptor: {
    organizationId: id(4),
    displayName: intent.displayName,
    owner: { ownerId: id(5), displayName: intent.ownerDisplayName },
    revision: 1,
    createdAt: '2026-10-17T00:00:00Z',
    updatedAt: '2026-10-17T00:00:00Z'
  }
}

const liveDescriptor = {
  capabilityVersion: 1,
  state: 'ready',
  metadata: receipt.descriptor,
  scope: { kind: 'served-profile', profile: receipt.profile },
  isolation: { ...receipt, supportedProviders: ['openai-api'] }
}

function setup() {
  const data = new Map<string, string>()

  const storage = {
    getItem: vi.fn((key: string) => data.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      data.set(key, value)
    }),
    removeItem: vi.fn((key: string) => {
      data.delete(key)
    })
  }

  const request = vi.fn(async (method: string): Promise<unknown> =>
    method === 'organization.workspaceCreation'
      ? capability
      : method === 'organization.createWorkspace'
        ? receipt
        : method === 'organization.workspaceCreationStatus'
          ? receipt
          : liveDescriptor
  )

  const source: DirectorySource = {
    ready: true,
    connections: [{ id: 'local', label: 'This device', endpoint: null }],
    current: {
      connectionId: 'local',
      endpoint: null,
      scope: {
        key: 'socket-origin',
        ownerKey: 'owner-origin',
        profile: 'default',
        eligible: true,
        request: request as never
      }
    }
  }

  const origin = source.current!

  const activate = vi.fn(async (connectionId: string, profile: string, verify: (value: unknown) => void) => {
    verify(liveDescriptor)
    source.current = {
      ...origin,
      scope: { ...origin.scope, key: 'socket-created', ownerKey: 'owner-created', profile }
    }

    return {
      status: 'committed' as const,
      reason: 'selected' as const,
      requested: { connectionId, profile },
      observedRoute: { connectionId, profile },
      resetStarted: true,
      published: true
    }
  })

  const deps = {
    readSource: () => source,
    rememberCurrent: vi.fn(async () => 'verified'),
    storage,
    activate,
    newKey: () => id(3)
  }

  return { deps, data, source, origin, request, activate, client: () => createOrganizationCreationClient(deps) }
}

it('fails closed for malformed capability, foreign receipts, unsupported runtimes and stale source replies', async () => {
  expect(creationCapability(capability, 'default')).toEqual(capability)

  for (const change of [
    { capabilityVersion: 2 },
    { isolationVersion: 2 },
    { credentialMode: 'root-fallback' },
    { executionMode: 'general-chat' },
    { origin: { ...capability.origin, ownerId: 'bad' } },
    { supportedProviders: ['openai-api', 'other'] }
  ]) {
    expect(() => creationCapability({ ...capability, ...change }, 'default')).toThrow()
  }

  for (const change of [
    { capabilityVersion: 2 },
    { organizationId: id(1) },
    { idempotencyKey: id(9) },
    { activated: true },
    { credentialMode: 'root-fallback' },
    { executionMode: 'general-chat' },
    { profile: '../default' }
  ]) {
    expect(() => createdWorkspace({ ...receipt, ...change }, intent)).toThrow()
  }

  const parsed = createdWorkspace(receipt, intent)

  for (const change of [
    { isolation: null },
    { isolation: { ...liveDescriptor.isolation, ownerId: id(9) } },
    { scope: { kind: 'served-profile', profile: 'default' } },
    { metadata: { ...receipt.descriptor, organizationId: id(1) } }
  ]) {
    expect(() => verifyCreatedWorkspace({ ...liveDescriptor, ...change }, parsed)).toThrow()
  }

  expect(() => restoreCreationIntent(' '.repeat(8193), capability as never)).toThrow()
  expect(() =>
    restoreCreationIntent(JSON.stringify({ ...intent, expectedOwnerId: id(9) }), capability as never)
  ).toThrow()
  const h = setup()
  h.request.mockRejectedValueOnce({ code: -32601 })
  const oldRuntime = h.client()
  await oldRuntime.load()
  await oldRuntime.create('No fallback', 'Owner')
  expect(oldRuntime.getSnapshot().stage).toBe('unavailable')
  expect(h.request.mock.calls.map(call => call[0])).toEqual(['organization.workspaceCreation'])
  expect(h.data.size).toBe(0)
  oldRuntime.dispose()
  const late = deferred<unknown>()
  h.request.mockReturnValueOnce(late.promise)
  const stale = h.client()
  const loading = stale.load()
  h.source.current = { ...h.origin, scope: { ...h.origin.scope, key: 'new-socket' } }
  late.resolve(capability)
  await loading
  await stale.create('No stale write', 'Owner')
  expect(h.data.size).toBe(0)
  expect(h.activate).not.toHaveBeenCalled()
  stale.dispose()
})

it('persists one exact creation intent, reconciles a lost reply on reload and requires separate verified selection', async () => {
  const h = setup()
  const first = h.client()
  await first.load()
  const write = deferred<unknown>()
  h.request.mockReturnValueOnce(write.promise)
  const creating = first.create(intent.displayName, intent.ownerDisplayName)
  await first.create('Duplicate click', 'Other owner')
  expect(h.request.mock.calls.filter(call => call[0] === 'organization.createWorkspace')).toHaveLength(1)
  expect(h.data.size).toBe(1)
  expect(h.source.current).toBe(h.origin)
  write.reject(new Error('reply lost'))
  await creating
  expect(first.getSnapshot()).toMatchObject({ stage: 'uncertain', canRetry: false })
  await first.retry()
  expect(h.request.mock.calls.filter(call => call[0] === 'organization.createWorkspace')).toHaveLength(1)
  first.dispose()

  h.request
    .mockImplementationOnce(async () => capability)
    .mockImplementationOnce(async () => ({ capabilityVersion: 1, state: 'pending' }))
  const reloaded = h.client()
  await reloaded.load()
  expect(reloaded.getSnapshot()).toMatchObject({ stage: 'uncertain', canRetry: true, intent })
  await reloaded.retry()
  expect(reloaded.getSnapshot()).toMatchObject({ stage: 'created', canRetry: false })
  const writes = h.request.mock.calls.filter(call => call[0] === 'organization.createWorkspace')
  expect(writes).toHaveLength(2)
  expect(writes[0]).toEqual(writes[1])
  expect(h.activate).not.toHaveBeenCalled()
  expect(h.source.current).toBe(h.origin)
  await reloaded.select()
  expect(reloaded.getSnapshot().stage).toBe('selected')
  expect(h.source.current?.scope.profile).toBe(receipt.profile)
  expect(h.data.size).toBe(0)
  expect(reloaded.canSetup()).toBe(true)
  h.source.current = { ...h.source.current!, scope: { ...h.source.current!.scope, key: 'reconnected-socket' } }
  expect(reloaded.canSetup()).toBe(false)
  reloaded.dispose()
})

const sourceSupport = {
  version: 1,
  mode: 'scheduler-scoped-organization',
  requestTypes: ['work.inspect', 'work.edit'],
  provider: 'openai-api',
  credentialScope: 'profile-local',
  grantsIncluded: false,
  host: { sourceReading: 'supported', projectValidation: 'unsupported' }
}

it('loads current source capability after creation without deriving it from the receipt', async () => {
  const h = setup()
  const client = h.client()
  await client.load()
  await client.create(intent.displayName, intent.ownerDisplayName)
  expect(client.getSnapshot().runtimeCapability).toBeNull()
  h.request.mockResolvedValueOnce({ ...liveDescriptor, runtimeCapability: sourceSupport })
  await client.select()
  expect(client.getSnapshot().stage).toBe('selected')
  expect(client.getSnapshot().runtimeCapability).toEqual(sourceSupport)
  expect(client.getSnapshot().result).toEqual(receipt)
  client.dispose()
})

it.each([undefined, null, { ...sourceSupport, version: 2 }, { ...sourceSupport, grantsIncluded: true }])(
  'does not infer current source support from historical mode for %j',
  async runtimeCapability => {
    const h = setup()
    const client = h.client()
    await client.load()
    await client.create(intent.displayName, intent.ownerDisplayName)
    h.request.mockResolvedValueOnce({ ...liveDescriptor, runtimeCapability })
    await client.select()
    expect(client.getSnapshot().stage).toBe('selected')
    expect(client.getSnapshot().runtimeCapability).toBeNull()
    client.dispose()
  }
)

it.each(['failure', 'switch'])('discards current capability on descriptor %s', async change => {
  const h = setup()
  const client = h.client()
  await client.load()
  await client.create(intent.displayName, intent.ownerDisplayName)
  const response = deferred<unknown>()
  h.request.mockReturnValueOnce(response.promise)
  const selecting = client.select()
  await Promise.resolve()
  if (change === 'switch') {
    h.source.current = { ...h.origin, scope: { ...h.origin.scope, key: 'other-profile' } }
    response.resolve({ ...liveDescriptor, runtimeCapability: sourceSupport })
  } else {
    response.reject(new Error('unavailable'))
  }
  await selecting
  expect(client.getSnapshot().stage).not.toBe('selected')
  expect(client.getSnapshot().runtimeCapability).toBeNull()
  expect(client.canSetup()).toBe(false)
  client.dispose()
})
