import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway, type OrganizationScope } from './runtime-adapter'
import type { OrganizationOutcomePage } from './runtime-outcome-types'
import type { OrganizationSnapshot } from './types'

function deferred<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(yes => {
    resolve = yes
  })

  return { promise, resolve }
}

const page = (): OrganizationOutcomePage => ({
  generation: '1',
  items: [{ objectiveId: 'goal', status: 'accepted', title: 'Goal', summary: 'Recorded result', round: 1, deliverableId: 'artifact', acceptanceRequestId: 'accept', evidenceIds: ['artifact'], archived: false, revision: 1, seen: false, createdAt: 'now', updatedAt: 'now' }],
  unread: 1,
  total: 1,
  hasMore: false,
  nextCursor: null
})

const snapshot = (outcomes = page()): OrganizationSnapshot => ({
  source: 'runtime',
  outcomes,
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
      items: Array.from({ length: 101 }, (_, index) => ({ ...page().items[0], objectiveId: String(index) }))
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
  await expect(f.adapter.getOutcomes!()).rejects.toThrow('invalid outcome page')
  expect(f.adapter.getSnapshot()).toBe(before)
})

it('coalesces exact acknowledgements and rejects a page started before a newer acknowledgement', async () => {
  const f = harness()
  await f.adapter.refresh()
  const read = deferred<OrganizationOutcomePage>()
  const ack = deferred<OrganizationSnapshot>()
  f.request.mockReturnValueOnce(read.promise).mockReturnValueOnce(ack.promise)
  const reading = f.adapter.getOutcomes!({ limit: 25, unreadOnly: true })
  const stale = expect(reading).rejects.toThrow('Outcomes changed while reading')
  const first = f.adapter.markOutcomeSeen!({ id: 'goal', revision: 1 })
  const second = f.adapter.markOutcomeSeen!({ id: 'goal', revision: 1 })
  await Promise.resolve()
  expect(f.request.mock.calls.filter(call => call[0] === 'organization.markOutcomeSeen')).toHaveLength(1)
  const seen = page()
  seen.items[0].seen = true
  seen.unread = 0
  ack.resolve(snapshot(seen))
  await Promise.all([first, second])
  read.resolve(page())
  await stale
  expect(f.adapter.getSnapshot().outcomes).toMatchObject({ unread: 0, total: 1 })
  expect(f.adapter.getSnapshot().requests![0].status).toBe('pending_intervention')
})

it('aborts old-owner page reads and prevents a queued acknowledgement from targeting the new profile', async () => {
  const f = harness()
  await f.adapter.refresh()
  const read = deferred<OrganizationOutcomePage>()
  f.request.mockReturnValueOnce(read.promise)
  const reading = f.adapter.getOutcomes!()
  const rejected = expect(reading).rejects.toThrow('connection or profile changed')
  const signal = f.request.mock.calls.at(-1)![3]
  const ack = f.adapter.markOutcomeSeen!({ id: 'goal', revision: 1 })
  const notSent = expect(ack).rejects.toThrow('changed before the request was sent')
  f.change({ key: 'socket-b', ownerKey: 'owner-b', connected: true })
  expect(signal.aborted).toBe(true)
  read.resolve(page())
  await Promise.all([rejected, notSent])
  expect(f.request.mock.calls.some(call => call[0] === 'organization.markOutcomeSeen')).toBe(false)
  expect(f.adapter.getSnapshot().outcomes).toBeUndefined()
})
