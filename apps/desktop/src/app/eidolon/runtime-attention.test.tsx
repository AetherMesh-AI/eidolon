import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { Link, MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway, type OrganizationScope } from './runtime-adapter'
import type {
  OrganizationAttentionItem,
  OrganizationAttentionPage,
  OrganizationAttentionQuery
} from './runtime-attention-types'
import type { OrganizationRequest, OrganizationSnapshot } from './types'
import { OrganizationWorkspace } from './workspace'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void

  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })

  return { promise, resolve, reject }
}

function fixture(count = 1, supportsAttention = true) {
  let scope: OrganizationScope = { key: 'socket-a', ownerKey: 'owner-a', connected: true }

  let notify = () => {}
  const stamp = '2026-10-08T00:00:00Z'

  let requests: OrganizationRequest[] = Array.from({ length: count }, (_, index) => ({
    id: `request-${index}`,
    objectiveId: 'goal',
    type: `request.clarify.${index}`,
    team: 'research',
    priority: 4,
    status: 'pending_intervention',
    reason: `Question ${index}`,
    attempts: 1,
    createdAt: stamp,
    allowedResolutions: [{ action: 'answer_request', label: 'Answer', requiresText: true, requiresEvidence: false }]
  }))

  let attention: OrganizationAttentionItem[] = requests.map(item => ({
    requestId: item.id,
    objectiveId: item.objectiveId,
    revision: 1,
    seen: false,
    createdAt: stamp,
    updatedAt: stamp
  }))

  let intercept: ((method: string, params: Record<string, unknown>) => Promise<unknown> | undefined) | undefined

  const page = ({
    before,
    limit = 100,
    unreadOnly = false
  }: OrganizationAttentionQuery = {}): OrganizationAttentionPage => {
    const offset = before ? attention.findIndex(item => item.requestId === before) + 1 : 0

    const pending = attention.filter(
      item => requests.find(request => request.id === item.requestId)?.status === 'pending_intervention'
    )

    const matching = attention.slice(offset).filter(item => pending.includes(item) && (!unreadOnly || !item.seen))
    const items = matching.slice(0, limit)

    return {
      items,
      total: pending.length,
      unread: pending.filter(item => !item.seen).length,
      hasMore: matching.length > limit,
      nextCursor: matching.length > limit ? items.at(-1)!.requestId : null
    }
  }

  const snapshot = (): OrganizationSnapshot => ({
    source: 'runtime',
    requests: requests.map(item => ({
      ...item,
      attentionRevision: attention.find(row => row.requestId === item.id)?.revision
    })),
    attention: supportsAttention ? page() : undefined,
    objectives: [
      {
        id: 'goal',
        title: 'Owner objective',
        description: 'Original context',
        ownerId: 'executive',
        status: 'needs_input',
        source: 'runtime',
        createdAt: stamp
      }
    ],
    agents: [],
    tasks: [],
    activity: [],
    knowledge: [],
    runtime: { state: 'ready', capabilities: [], maxWorkers: 2, scope: 'Submitted context' }
  })

  const request = vi.fn<(...args: Parameters<OrganizationGateway['request']>) => Promise<unknown>>(
    async (method, params) => {
      const delayed = intercept?.(method, params)

      if (delayed) {
        return delayed
      }

      if (method === 'organization.snapshot') {
        return snapshot()
      }

      if (method === 'organization.attention') {
        return page(params)
      }

      if (method === 'organization.markAttentionSeen') {
        const item = attention.find(item => item.requestId === params.id)

        if (!item || item.revision !== params.revision) {
          throw new Error('Request changed. Refresh the inbox.')
        }

        attention = attention.map(item => (item.requestId === params.id ? { ...item, seen: true } : item))

        return snapshot()
      }

      if (method === 'organization.respond') {
        requests = requests.map(item => (item.id === params.id ? { ...item, status: 'completed' } : item))

        return snapshot()
      }

      if (method === 'organization.toolReceipts') {
        return []
      }

      if (method === 'organization.executionAudit') {
        return { requestId: params.id, contexts: [], evidencePasses: [], modelCalls: [] }
      }

      throw new Error(`Unexpected mutation: ${method}`)
    }
  )

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

  const mount = () =>
    render(
      <MemoryRouter initialEntries={['/requests']}>
        <Link to="/requests">Return to requests</Link>
        <Link to="/organization">Leave requests</Link>
        <OrganizationWorkspace adapter={adapter} />
      </MemoryRouter>
    )

  const view = mount()

  return {
    adapter,
    request,
    view,
    mount,
    snapshot,
    page,
    intercept(next: typeof intercept) {
      intercept = next
    },
    changeScope(next: OrganizationScope, replace = false) {
      scope = next

      if (replace) {
        requests = []
        attention = []
      }

      notify()
    },
    updateRequest(id: string, changes: Partial<OrganizationRequest>) {
      requests = requests.map(item => (item.id === id ? { ...item, ...changes } : item))
    },
    update(id: string, input: Partial<OrganizationAttentionItem>, status?: OrganizationRequest['status']) {
      attention = attention.map(item => (item.requestId === id ? { ...item, ...input } : item))

      if (status) {
        requests = requests.map(item => (item.id === id ? { ...item, status } : item))
      }
    }
  }
}

const inbox = () => within(screen.getByRole('region', { name: 'Attention inbox' }))
const mark = () => inbox().getByRole('button', { name: 'Mark seen' })
const calls = (f: ReturnType<typeof fixture>, method: string) => f.request.mock.calls.filter(call => call[0] === method)

it('inspection does not acknowledge; marking seen is deduplicated and never answers or lowers Needs You', async () => {
  const f = fixture()
  fireEvent.click(await screen.findByRole('button', { name: 'Inspect request: request.clarify.0' }))
  expect(calls(f, 'organization.markAttentionSeen')).toHaveLength(0)
  fireEvent.click(screen.getByRole('button', { name: 'Close response' }))
  const ack = deferred<OrganizationSnapshot>()
  f.intercept(method => (method === 'organization.markAttentionSeen' ? ack.promise : undefined))
  fireEvent.click(mark())
  fireEvent.click(mark())
  await waitFor(() => expect(calls(f, 'organization.markAttentionSeen')).toHaveLength(1))
  expect(calls(f, 'organization.markAttentionSeen')[0][1]).toEqual({ id: 'request-0', revision: 1 })
  f.update('request-0', { seen: true })
  await act(async () => {
    ack.resolve(f.snapshot())
  })
  expect(await inbox().findByRole('button', { name: 'Seen' })).toHaveProperty('disabled', true)
  expect(inbox().getByText('Needs You: 1 · Unread: 0')).toBeTruthy()
  expect(f.adapter.getSnapshot().requests![0].status).toBe('pending_intervention')
  expect(calls(f, 'organization.respond')).toHaveLength(0)
  expect(calls(f, 'organization.resolve')).toHaveLength(0)
  fireEvent.click(screen.getByRole('button', { name: 'Inspect request: request.clarify.0' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Response' }), { target: { value: 'Engineering leads' } })
  fireEvent.click(screen.getByRole('button', { name: 'Submit response' }))
  await inbox().findByText('Needs You: 0 · Unread: 0')
  expect(calls(f, 'organization.respond')[0][1]).toMatchObject({
    id: 'request-0',
    text: 'Engineering leads',
    decision: 'answered'
  })
  expect(inbox().queryByRole('button', { name: /Inspect request:/ })).toBeNull()
  f.view.unmount()
})

it('retains seen state through offline return and remount, and reopened/content-updated blockers become unread without duplicates', async () => {
  const f = fixture()
  await screen.findByRole('button', { name: 'Mark seen' })
  fireEvent.click(mark())
  await inbox().findByRole('button', { name: 'Seen' })
  act(() => f.changeScope({ key: 'offline', ownerKey: 'owner-a', connected: false }))
  expect(inbox().getByText(/last verified records/)).toBeTruthy()
  expect(inbox().getByRole('button', { name: 'Seen' })).toHaveProperty('disabled', true)
  f.update('request-0', { revision: 2, seen: false, updatedAt: '2026-10-08T01:00:00Z' })
  act(() => f.changeScope({ key: 'returned', ownerKey: 'owner-a', connected: true }))
  await inbox().findByText('Needs You: 1 · Unread: 1')
  await waitFor(() => expect(mark()).toHaveProperty('disabled', false))
  expect(inbox().getAllByRole('listitem')).toHaveLength(1)
  fireEvent.click(mark())
  await inbox().findByRole('button', { name: 'Seen' })
  f.view.unmount()
  const returned = f.mount()
  await screen.findByRole('button', { name: 'Seen' })
  f.update('request-0', {}, 'completed')
  await act(() => f.adapter.refresh())
  await inbox().findByText('Needs You: 0 · Unread: 0')
  f.update('request-0', { revision: 3, seen: false }, 'pending_intervention')
  await act(() => f.adapter.refresh())
  await inbox().findByRole('button', { name: 'Mark seen' })
  expect(inbox().getAllByRole('listitem')).toHaveLength(1)
  expect(inbox().getByText('Needs You: 1 · Unread: 1')).toBeTruthy()
  returned.unmount()
})

it('bounds each page and finds an older unread blocker outside the snapshot window', async () => {
  const f = fixture(131)

  for (let index = 0; index < 130; index++) {
    f.update(`request-${index}`, { seen: true })
  }

  await screen.findByRole('button', { name: 'Inspect request: request.clarify.0' })
  expect(inbox().getAllByRole('listitem')).toHaveLength(25)
  fireEvent.click(inbox().getByRole('button', { name: 'Next inbox page' }))
  await inbox().findByRole('button', { name: 'Inspect request: request.clarify.25' })
  expect(inbox().getAllByRole('listitem')).toHaveLength(25)
  expect(inbox().queryByRole('button', { name: 'Inspect request: request.clarify.0' })).toBeNull()
  fireEvent.click(inbox().getByRole('button', { name: 'Unread' }))
  await inbox().findByRole('button', { name: 'Inspect request: request.clarify.130' })
  expect(inbox().getAllByRole('listitem')).toHaveLength(1)
  expect(calls(f, 'organization.attention').at(-1)![1]).toEqual({ limit: 25, unreadOnly: true, before: undefined })
  expect(inbox().getByText('Needs You: 131 · Unread: 1')).toBeTruthy()
  f.view.unmount()
})

it('ignores late pagination after a newer filter, back navigation, and leaving Requests', async () => {
  const f = fixture(30)
  await screen.findByRole('button', { name: 'Inspect request: request.clarify.0' })
  const old = deferred<OrganizationAttentionPage>()
  f.intercept((method, params) => (method === 'organization.attention' && params.before ? old.promise : undefined))
  fireEvent.click(inbox().getByRole('button', { name: 'Next inbox page' }))
  await waitFor(() => expect(calls(f, 'organization.attention').at(-1)![1].before).toBe('request-24'))
  fireEvent.click(inbox().getByRole('button', { name: 'Previous inbox page' }))
  await inbox().findByRole('button', { name: 'Inspect request: request.clarify.0' })
  fireEvent.click(inbox().getByRole('button', { name: 'Unread' }))
  await waitFor(() => expect(calls(f, 'organization.attention').at(-1)![1].unreadOnly).toBe(true))
  await act(async () => {
    old.resolve(f.page({ before: 'request-24', limit: 25 }))
  })
  expect(inbox().queryByRole('button', { name: 'Inspect request: request.clarify.25' })).toBeNull()
  const afterLeave = deferred<OrganizationAttentionPage>()
  f.intercept(method => (method === 'organization.attention' ? afterLeave.promise : undefined))
  fireEvent.click(inbox().getByRole('button', { name: 'Refresh' }))
  fireEvent.click(screen.getByRole('link', { name: 'Leave requests' }))
  await act(async () => {
    afterLeave.resolve(f.page())
  })
  expect(screen.queryByRole('region', { name: 'Attention inbox' })).toBeNull()
  expect(calls(f, 'organization.markAttentionSeen')).toHaveLength(0)
  f.view.unmount()
})

it('fences late acknowledgement and page errors across owner profile changes', async () => {
  const f = fixture()
  await screen.findByRole('button', { name: 'Mark seen' })
  const ack = deferred<OrganizationSnapshot>()
  f.intercept(method => (method === 'organization.markAttentionSeen' ? ack.promise : undefined))
  fireEvent.click(mark())
  await waitFor(() => expect(calls(f, 'organization.markAttentionSeen')).toHaveLength(1))
  const original = f.snapshot()
  const signal = calls(f, 'organization.markAttentionSeen')[0][3] as AbortSignal
  act(() => f.changeScope({ key: 'socket-b', ownerKey: 'owner-b', connected: true }, true))
  expect(signal.aborted).toBe(true)
  await act(async () => {
    ack.resolve(original)
  })
  await inbox().findByText('Needs You: 0 · Unread: 0')
  expect(screen.queryByText('Question 0')).toBeNull()
  expect(screen.queryByRole('alert')).toBeNull()
  expect(f.adapter.getSnapshot().connection?.ownerScope).toBe('owner-b')
  f.view.unmount()
})

it('shows a recoverable failed acknowledgement and retains the older runtime request queue', async () => {
  const f = fixture()
  await screen.findByRole('button', { name: 'Mark seen' })
  f.intercept(method =>
    method === 'organization.markAttentionSeen'
      ? Promise.reject(new Error('Request changed. Refresh the inbox.'))
      : undefined
  )
  fireEvent.click(mark())
  await screen.findByText('Request changed. Refresh the inbox.')
  expect(inbox().getByText('Needs You: 1 · Unread: 1')).toBeTruthy()
  f.intercept(undefined)
  fireEvent.click(mark())
  await screen.findByRole('button', { name: 'Seen' })
  fireEvent.click(screen.getByRole('button', { name: 'Request queue' }))
  expect(screen.getByRole('combobox', { name: 'Status' })).toBeTruthy()
  f.view.unmount()
  const legacy = fixture(1, false)
  await screen.findByText('Question 0')
  expect(screen.queryByRole('button', { name: 'Attention inbox' })).toBeNull()
  expect(screen.getByRole('combobox', { name: 'Status' })).toBeTruthy()
  expect(calls(legacy, 'organization.attention')).toHaveLength(0)
  legacy.view.unmount()
})

it('never marks a newer page revision seen against older request content, and refreshes both before enabling it', async () => {
  const f = fixture()
  await screen.findByRole('button', { name: 'Mark seen' })
  const previous = f.snapshot()
  f.update('request-0', { revision: 2 })
  f.updateRequest('request-0', { reason: 'Changed audience question' })
  f.intercept(method => (method === 'organization.snapshot' ? Promise.resolve(previous) : undefined))
  fireEvent.click(inbox().getByRole('button', { name: 'Refresh' }))
  await inbox().findByText('Requests changed while reading. Refresh the inbox.')
  expect(inbox().getByText('Question 0')).toBeTruthy()
  expect(mark()).toHaveProperty('disabled', true)
  fireEvent.click(mark())
  expect(calls(f, 'organization.markAttentionSeen')).toHaveLength(0)
  f.intercept(undefined)
  fireEvent.click(inbox().getByRole('button', { name: 'Refresh' }))
  await inbox().findByText('Changed audience question')
  await waitFor(() => expect(mark()).toHaveProperty('disabled', false))
  fireEvent.click(mark())
  await screen.findByRole('button', { name: 'Seen' })
  expect(calls(f, 'organization.markAttentionSeen')[0][1]).toEqual({ id: 'request-0', revision: 2 })
  f.view.unmount()
})

it('does not refetch attention or disable controls for unrelated request bookkeeping', async () => {
  const f = fixture()
  await screen.findByRole('button', { name: 'Mark seen' })
  const reads = calls(f, 'organization.attention').length
  f.updateRequest('request-0', { attempts: 5, leaseExpiresAt: '2026-10-08T12:00:00Z' })
  await act(() => f.adapter.refresh())
  expect(calls(f, 'organization.attention')).toHaveLength(reads)
  expect(mark()).toHaveProperty('disabled', false)
  expect(inbox().getAllByRole('listitem')).toHaveLength(1)
  f.view.unmount()
})

it('does not claim an empty inbox when a newer page contains a blocker absent from the older snapshot', async () => {
  const f = fixture()
  await screen.findByRole('button', { name: 'Mark seen' })
  f.updateRequest('request-0', { status: 'completed' })
  await act(() => f.adapter.refresh())
  await inbox().findByText('Nothing needs your input')
  const previous = f.snapshot()
  f.update('request-0', { revision: 2, seen: false }, 'pending_intervention')
  f.intercept(method => (method === 'organization.snapshot' ? Promise.resolve(previous) : undefined))
  fireEvent.click(inbox().getByRole('button', { name: 'Refresh' }))
  await inbox().findByText('Requests changed while reading. Refresh the inbox.')
  expect(inbox().queryByText('Nothing needs your input')).toBeNull()
  expect(inbox().queryByRole('button', { name: 'Mark seen' })).toBeNull()
  f.intercept(undefined)
  fireEvent.click(inbox().getByRole('button', { name: 'Refresh' }))
  await inbox().findByText('Question 0')
  await waitFor(() => expect(mark()).toHaveProperty('disabled', false))
  f.view.unmount()
})
