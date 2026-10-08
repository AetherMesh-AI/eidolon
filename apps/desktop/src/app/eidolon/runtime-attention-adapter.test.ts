import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway, type OrganizationScope } from './runtime-adapter'
import type { OrganizationAttentionPage } from './runtime-attention-types'
import type { OrganizationSnapshot } from './types'

function deferred<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(yes => {
    resolve = yes
  })

  return { promise, resolve }
}

const page = (): OrganizationAttentionPage => ({
  items: [{ requestId: 'request', objectiveId: 'goal', revision: 1, seen: false, createdAt: 'now', updatedAt: 'now' }],
  unread: 1,
  total: 1,
  hasMore: false,
  nextCursor: null
})

const snapshot = (attention = page()): OrganizationSnapshot => ({
  source: 'runtime',
  attention,
  objectives: [],
  agents: [],
  tasks: [],
  activity: [],
  knowledge: [],
  requests: [
    {
      id: 'request',
      objectiveId: 'goal',
      attentionRevision: 1,
      type: 'request.clarify',
      team: 'research',
      priority: 3,
      status: 'pending_intervention',
      attempts: 1,
      createdAt: 'now'
    }
  ],
  runtime: { capabilities: [], state: 'ready', maxWorkers: 2, scope: 'Submitted context' }
})

function harness() {
  let scope: OrganizationScope = { key: 'socket-a', ownerKey: 'owner-a', connected: true }

  let notify = () => {}
  const request = vi.fn<OrganizationGateway['request']>().mockResolvedValue(snapshot())

  const adapter = createRuntimeAdapter({
    request: request as OrganizationGateway['request'],
    getScope: () => scope,
    subscribeScope(listener) {
      notify = listener

      return () => {
        notify = () => {}
      }
    }
  })

  return {
    adapter,
    request,
    change(next: OrganizationScope) {
      scope = next
      notify()
    }
  }
}

it.each([
  ['duplicate requests', () => ({ ...page(), total: 2, items: [page().items[0], page().items[0]] })],
  [
    'unbounded pages',
    () => ({
      ...page(),
      total: 101,
      items: Array.from({ length: 101 }, (_, index) => ({ ...page().items[0], requestId: String(index) }))
    })
  ],
  ['invalid revision', () => ({ ...page(), items: [{ ...page().items[0], revision: 0 }] })],
  ['inconsistent counts', () => ({ ...page(), unread: 2 })],
  ['missing next cursor', () => ({ ...page(), hasMore: true })],
  ['mismatched cursor', () => ({ ...page(), hasMore: true, nextCursor: 'another-request' })]
] as const)('rejects %s without replacing the authoritative snapshot', async (_label, malformed) => {
  const f = harness()
  await f.adapter.refresh()
  const before = f.adapter.getSnapshot()
  f.request.mockResolvedValue(malformed())
  await expect(f.adapter.getAttention!()).rejects.toThrow('invalid attention page')
  expect(f.adapter.getSnapshot()).toBe(before)
})

it('coalesces exact acknowledgements and rejects a page started before a newer acknowledgement', async () => {
  const f = harness()
  await f.adapter.refresh()
  const read = deferred<OrganizationAttentionPage>()
  const ack = deferred<OrganizationSnapshot>()
  f.request.mockReturnValueOnce(read.promise).mockReturnValueOnce(ack.promise)
  const reading = f.adapter.getAttention!({ limit: 25, unreadOnly: true })
  const stale = expect(reading).rejects.toThrow('Requests changed while reading')
  const first = f.adapter.markAttentionSeen!({ id: 'request', revision: 1 })
  const second = f.adapter.markAttentionSeen!({ id: 'request', revision: 1 })
  await Promise.resolve()
  expect(f.request.mock.calls.filter(call => call[0] === 'organization.markAttentionSeen')).toHaveLength(1)
  const seen = page()
  seen.items[0].seen = true
  seen.unread = 0
  ack.resolve(snapshot(seen))
  await Promise.all([first, second])
  read.resolve(page())
  await stale
  expect(f.adapter.getSnapshot().attention).toMatchObject({ unread: 0, total: 1 })
  expect(f.adapter.getSnapshot().requests![0].status).toBe('pending_intervention')
})

it('aborts old-owner page reads and prevents a queued acknowledgement from targeting the new profile', async () => {
  const f = harness()
  await f.adapter.refresh()
  const read = deferred<OrganizationAttentionPage>()
  f.request.mockReturnValueOnce(read.promise)
  const reading = f.adapter.getAttention!()
  const rejected = expect(reading).rejects.toThrow('connection or profile changed')
  const signal = f.request.mock.calls.at(-1)![3]
  const ack = f.adapter.markAttentionSeen!({ id: 'request', revision: 1 })
  const notSent = expect(ack).rejects.toThrow('changed before the request was sent')
  f.change({ key: 'socket-b', ownerKey: 'owner-b', connected: true })
  expect(signal.aborted).toBe(true)
  read.resolve(page())
  await Promise.all([rejected, notSent])
  expect(f.request.mock.calls.some(call => call[0] === 'organization.markAttentionSeen')).toBe(false)
  expect(f.adapter.getSnapshot().attention).toBeUndefined()
})
