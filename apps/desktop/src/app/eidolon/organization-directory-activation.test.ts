import { afterEach, expect, it, vi } from 'vitest'

import type { ConnectionSwitchOutcome } from '@/store/connections'

import { deferred } from '../../test/deferred'

import { createOrganizationDirectory, type DirectorySource } from './organization-directory'
import { createOrganizationDirectoryActivation } from './organization-directory-activation'
import { organizationOwnerKey } from './organization-owner'
import { $intakeDrafts, setIntakeDraft } from './runtime-intake-drafts'
import { $ownerChatDrafts, $ownerChatRenewals } from './runtime-owner-chat-drafts'

const result = (status: ConnectionSwitchOutcome['status'], connectionId = 'b'): ConnectionSwitchOutcome => ({
  status,
  reason: status === 'superseded' ? 'superseded' : status === 'unchanged' ? 'failed' : 'selected',
  requested: { connectionId, profile: 'default' },
  observedRoute: { connectionId: status === 'unchanged' ? 'a' : connectionId, profile: 'default' },
  resetStarted: true,
  published: status === 'committed'
})

async function setup() {
  const request = vi.fn().mockResolvedValue({
    capabilityVersion: 1,
    state: 'unconfigured',
    metadata: null,
    scope: { kind: 'served-profile', profile: 'default' }
  })

  let changed = () => {}

  const source: DirectorySource = {
    connections: ['a', 'b'].map(id => ({ id, label: id, endpoint: null })),
    current: null
  }

  const go = (id: string) => {
    source.current = {
      connectionId: id,
      endpoint: null,
      scope: { key: id, ownerKey: id, profile: 'default', eligible: true, request }
    }
    changed()
  }

  const directory = createOrganizationDirectory(
    () => source,
    listener => {
      changed = listener

      return () => {}
    }
  )

  go('b')
  await directory.refresh()
  go('a')
  await directory.refresh()
  request.mockClear()

  const select = vi.fn(async (id: string) => {
    go(id)

    return result('committed', id)
  })

  const args = { directory, readSource: () => source, select }
  const activation = createOrganizationDirectoryActivation({ ...args, enabled: () => true })

  return {
    directory,
    source,
    request,
    select,
    activation,
    args,
    go,
    changed: () => changed(),
    key: JSON.stringify(['b', 'default'])
  }
}

afterEach(() => {
  $intakeDrafts.set({})
  $ownerChatDrafts.set({})
  $ownerChatRenewals.set({})
})

it('has a closed default release gate even after explicit preparation', async () => {
  const h = await setup()
  const closed = createOrganizationDirectoryActivation(h.args)
  expect(closed.prepare(h.key)).toBe(true)
  expect(await closed.confirm()).toEqual({ status: 'disabled' })
  expect(h.select).not.toHaveBeenCalled()
  expect(h.request).not.toHaveBeenCalled()
  h.directory.dispose()
})
it('separates highlighting/preparation and cancellation from explicit activation', async () => {
  const h = await setup()
  expect(h.activation.prepare(h.key)).toBe(true)
  expect(h.select).not.toHaveBeenCalled()
  expect(h.activation.cancel()).toBe(true)
  expect(await h.activation.confirm()).toEqual({ status: 'cancelled' })
  expect(h.select).not.toHaveBeenCalled()
  h.directory.dispose()
})
it.each(['owner', 'endpoint', 'removed'] as const)('rejects confirmation after %s changes', async change => {
  const h = await setup()
  h.activation.prepare(h.key)

  if (change === 'owner') {
    h.go('b')
  }

  if (change === 'endpoint') {
    h.source.connections = h.source.connections.map(item =>
      item.id === 'b' ? { ...item, endpoint: 'https://changed.example/rpc' } : item
    )
    h.changed()
  }

  if (change === 'removed') {
    h.source.connections = h.source.connections.filter(item => item.id !== 'b')
    h.changed()
  }

  expect((await h.activation.confirm()).status).toBe(change === 'owner' ? 'superseded' : 'unavailable')
  expect(h.select).not.toHaveBeenCalled()
  h.directory.dispose()
})
it.each(['unchanged', 'superseded'] as const)('does not read or navigate after %s outcome', async status => {
  const h = await setup()
  h.select.mockResolvedValueOnce(result(status))
  h.activation.prepare(h.key)
  expect((await h.activation.confirm()).status).toBe(status)
  expect(h.request).not.toHaveBeenCalled()
  expect(h.source.current?.connectionId).toBe('a')
  h.directory.dispose()
})
it('verifies committed scope while retaining a trailing error and never rolling back', async () => {
  const h = await setup()
  const error = new Error('bookkeeping failed after publication')
  h.select.mockImplementationOnce(async id => {
    h.go(id)

    return { ...result('committed'), error }
  })
  h.activation.prepare(h.key)
  const outcome = await h.activation.confirm()
  expect(outcome).toMatchObject({ status: 'selected-unconfigured', outcome: { error, published: true } })
  expect(h.source.current?.connectionId).toBe('b')
  expect(h.select).toHaveBeenCalledExactlyOnceWith('b', 'default')
  expect(h.request.mock.calls).toEqual([['organization.descriptor', {}]])
  h.directory.dispose()
})
it('reports committed-unverified on destination read failure without replaying or reversing selection', async () => {
  const h = await setup()
  h.request.mockRejectedValueOnce(new Error('auth failed'))
  h.activation.prepare(h.key)
  expect((await h.activation.confirm()).status).toBe('committed-unverified')
  expect(h.source.current?.connectionId).toBe('b')
  expect(h.select).toHaveBeenCalledTimes(1)
  h.directory.dispose()
})
it('does not pretend an admitted switch can be cancelled or initiate a duplicate switch', async () => {
  const h = await setup()
  const pending = deferred<ConnectionSwitchOutcome>()
  h.select.mockReturnValueOnce(pending.promise)
  h.activation.prepare(h.key)
  const confirmation = h.activation.confirm()
  expect(h.activation.cancel()).toBe(false)
  expect((await h.activation.confirm()).status).toBe('unavailable')
  h.go('b')
  pending.resolve(result('committed'))
  expect((await confirmation).status).toBe('selected-unconfigured')
  expect(h.select).toHaveBeenCalledTimes(1)
  h.directory.dispose()
})
it('retains original uncertain intake, send and renewal keys through synthetic A → B → A', async () => {
  const h = await setup()

  for (const id of ['a', 'b']) {
    const owner = organizationOwnerKey(id, 'default', { mode: 'local' })
    const key = JSON.stringify([owner, 'copied-identity'])
    const fields = { goal: `${id} draft`, criteria: 'Evidence', metadata: {} }
    setIntakeDraft(JSON.stringify(['intake', owner]), {
      ...fields,
      submitted: fields,
      idempotencyKey: `${id}-original-intake`,
      busy: false,
      error: 'Uncertain'
    })
    $ownerChatDrafts.set({
      ...$ownerChatDrafts.get(),
      [key]: {
        text: `${id} draft`,
        intent: {
          threadId: 'copied-thread',
          identityId: 'copied-identity',
          text: `${id} draft`,
          replyToMessageId: null,
          idempotencyKey: `${id}-original-send`
        }
      }
    })
    $ownerChatRenewals.set({
      ...$ownerChatRenewals.get(),
      [key]: {
        threadId: 'copied-thread',
        identityId: 'copied-identity',
        idempotencyKey: `${id}-original-renewal`,
        expectedBudgetVersion: 2,
        expectedPolicyGeneration: 3,
        additionalCalls: 1
      }
    })
  }

  const before = structuredClone([$intakeDrafts.get(), $ownerChatDrafts.get(), $ownerChatRenewals.get()])
  h.activation.prepare(h.key)
  expect((await h.activation.confirm()).status).toBe('selected-unconfigured')
  h.activation.prepare(JSON.stringify(['a', 'default']))
  expect((await h.activation.confirm()).status).toBe('selected-unconfigured')
  expect([$intakeDrafts.get(), $ownerChatDrafts.get(), $ownerChatRenewals.get()]).toEqual(before)
  expect(h.request.mock.calls.every(([method]) => method === 'organization.descriptor')).toBe(true)
  h.directory.dispose()
})

it('suppresses completion when the source changes during destination verification', async () => {
  const h = await setup()
  const late = deferred<unknown>()
  h.request.mockReturnValueOnce(late.promise)
  h.activation.prepare(h.key)
  const confirmation = h.activation.confirm()
  await Promise.resolve()
  h.go('a')
  late.resolve({
    capabilityVersion: 1,
    state: 'unconfigured',
    metadata: null,
    scope: { kind: 'served-profile', profile: 'default' }
  })
  expect((await confirmation).status).toBe('superseded')
  expect(h.source.current?.connectionId).toBe('a')
  h.directory.dispose()
})

it.each(['shared', 'profile-mismatch', 'malformed'] as const)(
  'does not trust a cached destination after a fresh %s descriptor',
  async kind => {
    const h = await setup()
    h.request.mockResolvedValueOnce({
      capabilityVersion: kind === 'malformed' ? '1' : 1,
      state: 'unconfigured',
      metadata: null,
      scope:
        kind === 'shared'
          ? { kind: 'shared' }
          : { kind: 'served-profile', profile: kind === 'profile-mismatch' ? 'other' : 'default' }
    })
    h.activation.prepare(h.key)
    const answer = await h.activation.confirm()
    expect(answer.status).toBe('committed-unverified')
    expect(answer.outcome).toMatchObject({ status: 'committed', published: true })
    expect(h.source.current?.connectionId).toBe('b')
    expect(h.select).toHaveBeenCalledExactlyOnceWith('b', 'default')
    expect(h.request.mock.calls).toEqual([['organization.descriptor', {}]])
    expect(h.directory.getSnapshot().entries.find(entry => entry.key === h.key)?.freshness).not.toBe('fresh')
    h.directory.dispose()
  }
)

it('checks the release gate again after preparation without touching either source', async () => {
  const h = await setup()
  let enabled = true
  const activation = createOrganizationDirectoryActivation({ ...h.args, enabled: () => enabled })
  expect(activation.prepare(h.key)).toBe(true)
  enabled = false
  expect(await activation.confirm()).toEqual({ status: 'disabled' })
  expect(h.source.current?.connectionId).toBe('a')
  expect(h.select).not.toHaveBeenCalled()
  expect(h.request).not.toHaveBeenCalled()
  h.directory.dispose()
})

it('preserves a pre-publication failure without reading the cached destination or replaying selection', async () => {
  const h = await setup()
  const error = new Error('Authentication refused before publication')
  h.select.mockResolvedValueOnce({ ...result('unchanged'), error, published: false })
  h.activation.prepare(h.key)
  const answer = await h.activation.confirm()
  expect(answer).toMatchObject({ status: 'unchanged', outcome: { error, published: false } })
  expect(h.source.current?.connectionId).toBe('a')
  expect(h.request).not.toHaveBeenCalled()
  expect(h.select).toHaveBeenCalledTimes(1)
  expect(await h.activation.confirm()).toEqual({ status: 'cancelled' })
  expect(h.select).toHaveBeenCalledTimes(1)
  h.directory.dispose()
})

it.each([
  ['11111111-1111-4111-8111-111111111111', '11111111-1111-4111-8111-111111111111', 'verified'],
  ['11111111-1111-4111-8111-111111111111', '33333333-3333-4333-8333-333333333333', 'committed-unverified'],
  [null, null, 'selected-unconfigured'],
  ['11111111-1111-4111-8111-111111111111', null, 'committed-unverified'],
  [null, '11111111-1111-4111-8111-111111111111', 'committed-unverified']
] as const)('verifies the selected identity/state %s → %s as %s', async (before, after, expected) => {
  const h = await setup()

  const metadata = (organizationId: string | null) => ({
    capabilityVersion: 1,
    state: organizationId === null ? 'unconfigured' : 'ready',
    scope: { kind: 'served-profile', profile: 'default' },
    metadata:
      organizationId === null
        ? null
        : {
            organizationId,
            displayName: 'Selected name',
            revision: 1,
            owner: { ownerId: '22222222-2222-4222-8222-222222222222', displayName: 'Owner' },
            createdAt: '2026-10-12T00:00:00Z',
            updatedAt: '2026-10-12T00:00:00Z'
          }
  })

  try {
    h.go('b')
    h.request.mockResolvedValueOnce(metadata(before))
    await h.directory.refresh()
    h.go('a')
    expect(h.activation.prepare(h.key)).toBe(true)
    h.request.mockResolvedValueOnce(metadata(after))
    expect((await h.activation.confirm()).status).toBe(expected)
    expect(h.select).toHaveBeenCalledExactlyOnceWith('b', 'default')
  } finally {
    h.directory.dispose()
  }
})
