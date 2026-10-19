import { expect, it, vi } from 'vitest'

import type { MetadataScope } from './organization-metadata-client'
import { createOrganizationSetupConnectionClient } from './organization-setup-connection-client'
import type { SubscriptionStatus } from './team-setup-subscription-client'
import { subscriptionSetup, subscriptionStatus } from './team-setup-subscription-fixtures'

function harness() {
  let setup = { ...subscriptionSetup, state: 'configured' as const }
  let status: SubscriptionStatus = { ...subscriptionStatus, connectionRevision: 2, connectionState: 'active' }

  let subscriber = () => {}

  const ticket = {
    version: 1,
    sequence: '1',
    nonce: 'a'.repeat(64),
    kind: 'revoke',
    bindingDigest: 'b'.repeat(64),
    authorizationEpoch: '00000000-0000-4000-8000-000000000004',
    priorStoreRevision: 2,
    sourceRevision: setup.sourceRevision
  }

  const request = vi.fn(async (method: string) => {
    if (method === 'organization.teamSetup') {
      return setup
    }

    if (method === 'organization.setupSubscriptionStatus') {
      return status
    }

    if (method === 'organization.prepareSetupSubscriptionOperation') {
      return { state: 'prepared', ticket }
    }

    if (method === 'organization.executeSetupSubscriptionOperation') {
      status = { ...status, connectionState: 'disconnected', connectionRevision: 3 }

      return { state: 'completed' }
    }

    if (method === 'organization.recoverSetupSubscriptionOperation') {
      return { state: 'uncertain' }
    }

    throw new Error('Unexpected RPC: ' + method)
  })

  let scope: MetadataScope = {
    key: 'socket-a',
    ownerKey: 'owner-a',
    profile: 'default',
    eligible: true,
    request: request as MetadataScope['request']
  }

  const client = createOrganizationSetupConnectionClient(
    () => scope,
    changed => {
      subscriber = changed

      return () => {}
    }
  )

  return {
    client,
    request,
    ticket,
    organizationId: setup.organizationId!,
    setStatus: (next: Partial<SubscriptionStatus>) => {
      status = { ...status, ...next }
    },
    setSetup: (next: Partial<typeof setup>) => {
      setup = { ...setup, ...next }
    },
    switchOwner: () => {
      scope = { ...scope, key: 'socket-b', ownerKey: 'owner-b' }
      subscriber()
    }
  }
}

it('reads a configured owner connection passively and disconnects only the displayed source and revision', async () => {
  const h = harness()
  const view = await h.client.inspect(h.organizationId)
  expect(h.request.mock.calls.map(([method]) => method)).toEqual([
    'organization.teamSetup',
    'organization.setupSubscriptionStatus'
  ])
  const result = await h.client.disconnect(view)
  expect(result.status?.connectionState).toBe('disconnected')
  expect(h.request).toHaveBeenCalledWith('organization.prepareSetupSubscriptionOperation', {
    expectedOrganizationId: h.organizationId,
    sourceRevision: subscriptionSetup.sourceRevision,
    kind: 'revoke',
    expectedRevision: 2,
    confirmed: true
  })
  expect(h.request.mock.calls.map(([method]) => method)).toEqual([
    'organization.teamSetup',
    'organization.setupSubscriptionStatus',
    'organization.teamSetup',
    'organization.setupSubscriptionStatus',
    'organization.prepareSetupSubscriptionOperation',
    'organization.executeSetupSubscriptionOperation',
    'organization.teamSetup',
    'organization.setupSubscriptionStatus'
  ])
})

it('accepts local retirement of a stranded refresh without executing or claiming remote revocation', async () => {
  const h = harness()
  h.setStatus({ connectionState: 'refreshing' })
  const view = await h.client.inspect(h.organizationId)
  const original = h.request.getMockImplementation()!
  h.request.mockImplementation(async method => {
    if (method === 'organization.prepareSetupSubscriptionOperation') {
      h.setStatus({ connectionState: 'disconnected', connectionRevision: 3 })

      return { state: 'local_retired', ticket: null, receipt: { remoteRevocation: 'unconfirmed' } } as never
    }

    return original(method)
  })
  expect((await h.client.disconnect(view)).status?.connectionState).toBe('disconnected')
  expect(h.request.mock.calls.some(([method]) => method === 'organization.executeSetupSubscriptionOperation')).toBe(
    false
  )
})

it.each(['source', 'revision', 'organization', 'capability'] as const)(
  'fences stale %s before any mutation',
  async field => {
    const h = harness()
    const view = await h.client.inspect(h.organizationId)

    if (field === 'source') {
      h.setSetup({ sourceRevision: 'e'.repeat(64) })
    }

    if (field === 'revision') {
      h.setStatus({ connectionRevision: 3 })
    }

    if (field === 'organization') {
      h.setSetup({ organizationId: '00000000-0000-4000-8000-000000000007' })
    }

    if (field === 'capability') {
      h.setSetup({ setupSubscriptionState: 'local_owner_required' })
    }

    await expect(h.client.disconnect(view)).rejects.toThrow()
    expect(
      h.request.mock.calls.every(([method]) =>
        ['organization.teamSetup', 'organization.setupSubscriptionStatus'].includes(method)
      )
    ).toBe(true)
  }
)

it('retires late prepare replies on an owner switch before execute and rejects concurrent actions', async () => {
  const h = harness()
  const view = await h.client.inspect(h.organizationId)
  let release!: (value: unknown) => void
  const original = h.request.getMockImplementation()!
  h.request.mockImplementation(async method =>
    method === 'organization.prepareSetupSubscriptionOperation'
      ? ((await new Promise<unknown>(resolve => {
          release = resolve
        })) as never)
      : original(method)
  )
  const pending = h.client.disconnect(view)
  await vi.waitFor(() => expect(release).toBeTypeOf('function'))
  await expect(h.client.disconnect(view)).rejects.toThrow()
  h.switchOwner()
  release({ state: 'prepared', ticket: h.ticket })
  await expect(pending).rejects.toThrow()
  expect(h.request.mock.calls.some(([method]) => method === 'organization.executeSetupSubscriptionOperation')).toBe(
    false
  )
})

it('keeps explicit recovery reachable for unknown status and never retries a failed mutation', async () => {
  const h = harness()
  const original = h.request.getMockImplementation()!
  h.request.mockImplementation(async method => {
    if (method === 'organization.setupSubscriptionStatus') {
      throw new Error('publication_uncertain')
    }

    return original(method)
  })
  const view = await h.client.inspect(h.organizationId)
  expect(view.status).toBeNull()
  await expect(h.client.disconnect(view)).rejects.toThrow()
  await h.client.recover(view)
  expect(
    h.request.mock.calls.filter(([method]) => method === 'organization.recoverSetupSubscriptionOperation')
  ).toHaveLength(1)
  expect(
    h.request.mock.calls.some(([method]) => /prepare|execute|begin|Models|Policy|generate|improve/.test(method))
  ).toBe(false)
  h.request.mockImplementation(async method => {
    if (method === 'organization.recoverSetupSubscriptionOperation') {
      throw new Error('lost_reply')
    }

    return original(method)
  })
  const verified = await h.client.inspect(h.organizationId)
  await expect(h.client.recover(verified)).rejects.toThrow('lost_reply')
  expect(
    h.request.mock.calls.filter(([method]) => method === 'organization.recoverSetupSubscriptionOperation')
  ).toHaveLength(2)
  h.client.dispose()
  await expect(h.client.inspect(h.organizationId)).rejects.toThrow()
})

it('returns an informational unsupported capability without reading status or allowing lifecycle mutations', async () => {
  const h = harness()
  h.setSetup({ setupSubscriptionState: 'unsupported_platform' })
  const view = await h.client.inspect(h.organizationId)
  expect(view).toMatchObject({ capability: 'unavailable', reason: 'unsupported_platform', status: null })
  expect(h.request.mock.calls.map(([method]) => method)).toEqual(['organization.teamSetup'])
  await expect(h.client.recover(view)).rejects.toThrow()
  await expect(h.client.disconnect(view)).rejects.toThrow()
  expect(h.request).toHaveBeenCalledTimes(1)
  h.setSetup({ setupSubscriptionState: 'available' })
  const checked = await h.client.inspect(h.organizationId)
  expect(checked).toMatchObject({ capability: 'available', status: { connectionState: 'active' } })
  expect(h.request.mock.calls.map(([method]) => method)).toEqual([
    'organization.teamSetup',
    'organization.teamSetup',
    'organization.setupSubscriptionStatus'
  ])
})

it.each([
  'organization',
  'source',
  'version',
  'subscription_version',
  'missing',
  'unknown',
  'local_owner_required',
  'transport'
])('keeps %s evidence a verification failure rather than unsupported capability', async failure => {
  const h = harness()
  h.setSetup({ setupSubscriptionState: 'unsupported_platform' })

  if (failure === 'organization') {
    h.setSetup({ organizationId: '00000000-0000-4000-8000-000000000007' })
  }

  if (failure === 'source') {
    h.setSetup({ sourceRevision: 'malformed' })
  }

  if (failure === 'version') {
    h.setSetup({ capabilityVersion: 2 as never })
  }

  if (failure === 'subscription_version') {
    h.setSetup({ setupSubscriptionVersion: undefined })
  }

  if (failure === 'missing') {
    h.setSetup({ setupSubscriptionState: undefined })
  }

  if (failure === 'unknown') {
    h.setSetup({ setupSubscriptionState: 'unsupported' as never })
  }

  if (failure === 'local_owner_required') {
    h.setSetup({ setupSubscriptionState: 'local_owner_required' })
  }

  if (failure === 'transport') {
    h.request.mockRejectedValueOnce(new Error('unavailable'))
  }

  await expect(h.client.inspect(h.organizationId)).rejects.toThrow()
  expect(h.request.mock.calls.map(([method]) => method)).toEqual(['organization.teamSetup'])
})

it.each(['recover', 'disconnect'] as const)(
  'rejects capability loss on %s revalidation before any mutation',
  async action => {
    const h = harness()
    const view = await h.client.inspect(h.organizationId)
    h.setSetup({ setupSubscriptionState: 'unsupported_platform' })
    await expect(h.client[action](view)).rejects.toThrow()
    expect(h.request.mock.calls.map(([method]) => method)).toEqual([
      'organization.teamSetup',
      'organization.setupSubscriptionStatus',
      'organization.teamSetup'
    ])
  }
)
