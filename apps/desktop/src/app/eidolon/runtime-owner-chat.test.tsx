import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { Organization } from './organization'
import { OrganizationOwnerChat } from './runtime-owner-chat'
import { OwnerChatRenewalRejected } from './runtime-owner-chat-contract'
import { $ownerChatDrafts, $ownerChatRenewals } from './runtime-owner-chat-drafts'
import type { OwnerChatSend, OwnerChatThread } from './runtime-owner-chat-types'
import {
  admittedChat,
  answeredChat,
  deferred,
  ownerChatSnapshot,
  ownerChatThread
} from './runtime-owner-chat.test-support'
import type { RuntimeOrganizationAdapter } from './types'

beforeEach(() => {
  $ownerChatDrafts.set({})
  $ownerChatRenewals.set({})
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
      unobserve() {}
    }
  )
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function harness() {
  const h = {
    snapshot: ownerChatSnapshot(),
    thread: ownerChatThread(),
    adapter: {} as RuntimeOrganizationAdapter
  }

  h.adapter = {
    mode: 'runtime',
    getSnapshot: () => h.snapshot,
    subscribe: () => () => {},
    refresh: vi.fn(),
    openOwnerChat: vi.fn(async () => h.thread),
    readOwnerChat: vi.fn(async () => h.thread),
    sendOwnerChat: vi.fn(async (input: OwnerChatSend) => {
      if (!h.thread.turns.some(turn => turn.idempotencyKey === input.idempotencyKey)) {
        h.thread = admittedChat(h.thread, input)
      }

      return h.thread
    }),
    cancelOwnerChat: vi.fn(async () => {
      h.thread = {
        ...h.thread,
        activeTurnId: null,
        canSend: true,
        turns: h.thread.turns.map(turn =>
          turn.id === h.thread.activeTurnId
            ? { ...turn, status: 'cancelled', finishedAt: '2026-10-08T12:01:00Z' }
            : turn
        )
      }

      return h.thread
    }),
    createObjective: vi.fn(),
    cancelObjective: vi.fn(),
    retryRequest: vi.fn(),
    resolveRequest: vi.fn(),
    respondRequest: vi.fn(),
    configureOrganization: vi.fn(),
    getEvidence: vi.fn(),
    getToolReceipts: vi.fn()
  }

  const view = () => (
    <MemoryRouter>
      <Organization adapter={h.adapter} snapshot={h.snapshot} />
    </MemoryRouter>
  )

  return { h, view }
}

async function open() {
  fireEvent.click(screen.getByRole('button', { name: 'Inspect Worker 1' }))
  fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
  const message = await screen.findByRole('textbox', { name: 'Message' })
  await waitFor(() => expect((message as HTMLTextAreaElement).disabled).toBe(false))

  return message
}

it('opens the Worker identity, keeps formal decisions separate, sends exact replies and restores history after remount', async () => {
  const { h, view } = harness()
  h.snapshot.requests = [
    {
      id: 'formal-question',
      objectiveId: 'o1',
      type: 'request.clarify',
      team: 'Research',
      status: 'pending_intervention',
      priority: 1,
      attempts: 0,
      createdAt: '2026-10-08T00:00:00Z'
    }
  ]
  const rendered = render(view())
  const message = await open()
  expect(h.adapter.openOwnerChat).toHaveBeenCalledExactlyOnceWith({ agentId: 'worker', identityId: 'identity-worker' })
  const panel = within(screen.getByRole('region', { name: 'Owner conversation' }))
  expect((panel.getByRole('button', { name: 'Call' }) as HTMLButtonElement).disabled).toBe(true)
  expect(panel.getByText('Voice calls are not available yet.')).toBeTruthy()
  expect(screen.queryByText('Responsibilities')).toBeNull()
  expect(panel.getByRole('link', { name: 'Needs you (1)' }).getAttribute('href')).toBe('/requests')
  expect(panel.getByRole('link', { name: 'Create objective' }).getAttribute('href')).toBe('/home')
  expect(panel.getByText(/public identity and this conversation/)).toBeTruthy()
  expect(panel.getByText(/may incur usage charges/)).toBeTruthy()
  fireEvent.change(message, { target: { value: 'yes <script>unsafe()</script>' } })
  fireEvent.keyDown(message, { key: 'Enter', shiftKey: true })
  fireEvent.keyDown(message, { key: 'Enter', isComposing: true })
  expect(h.adapter.sendOwnerChat).not.toHaveBeenCalled()
  fireEvent.keyDown(message, { key: 'Enter' })
  await screen.findByText('yes <script>unsafe()</script>')
  expect(rendered.container.querySelector('script')).toBeNull()
  const sent = vi.mocked(h.adapter.sendOwnerChat!).mock.calls[0][0]
  expect(sent).toMatchObject({
    threadId: h.thread.id,
    identityId: h.thread.identityId,
    text: 'yes <script>unsafe()</script>',
    replyToMessageId: null
  })
  expect(panel.getByRole('button', { name: 'Cancel reply' })).toBeTruthy()
  expect(panel.getByRole('link', { name: 'Needs you (1)' })).toBeTruthy()
  h.thread = answeredChat(h.thread)
  fireEvent.click(screen.getByRole('button', { name: 'Hide conversation' }))
  expect(screen.getByText('Responsibilities')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
  await screen.findByText('Let’s discuss the idea.')
  const first = screen.getByText('yes <script>unsafe()</script>').closest('li')!
  first.scrollIntoView = vi.fn()
  fireEvent.click(
    screen.getByRole('button', { name: `Reply to message: ${sent.replyToMessageId ?? h.thread.messages[0].id}` })
  )
  expect(first.scrollIntoView).toHaveBeenCalledOnce()
  expect(globalThis.document.activeElement).toBe(first)
  fireEvent.change(screen.getByRole('textbox', { name: 'Message' }), { target: { value: 'Continue' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
  await waitFor(() => expect(h.adapter.sendOwnerChat).toHaveBeenCalledTimes(2))
  expect(vi.mocked(h.adapter.sendOwnerChat!).mock.calls[1][0].replyToMessageId).toBe(h.thread.messages.at(-2)?.id)
  rendered.unmount()
  $ownerChatDrafts.set({})
  render(view())
  await open()
  expect(screen.getByText('Let’s discuss the idea.')).toBeTruthy()
  expect(screen.getByText('Continue')).toBeTruthy()
  expect(h.adapter.createObjective).not.toHaveBeenCalled()
  expect(h.adapter.respondRequest).not.toHaveBeenCalled()
  expect(h.adapter.resolveRequest).not.toHaveBeenCalled()
  expect(h.snapshot.requests[0].status).toBe('pending_intervention')
})

it('preserves uncertain admission across dismissal, coalesces clicks and retries only the exact intent', async () => {
  const { h, view } = harness()
  const pending = deferred<OwnerChatThread>()
  vi.mocked(h.adapter.sendOwnerChat!).mockReturnValueOnce(pending.promise)
  render(view())
  const input = await open()
  fireEvent.change(input, { target: { value: 'A durable question' } })
  const send = screen.getByRole('button', { name: 'Send message' })
  fireEvent.click(send)
  fireEvent.click(send)
  expect(h.adapter.sendOwnerChat).toHaveBeenCalledOnce()
  const intent = vi.mocked(h.adapter.sendOwnerChat!).mock.calls[0][0]
  fireEvent.click(screen.getByRole('button', { name: 'Hide conversation' }))
  await act(async () => pending.reject(new Error('Acknowledgement lost')))
  fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
  await screen.findByText(/Delivery is not confirmed/)
  await waitFor(() =>
    expect((screen.getByRole('button', { name: 'Retry same message' }) as HTMLButtonElement).disabled).toBe(false)
  )
  fireEvent.click(screen.getByRole('button', { name: 'Retry same message' }))
  await screen.findByText('A durable question')
  expect(vi.mocked(h.adapter.sendOwnerChat!).mock.calls[1][0]).toEqual(intent)
  expect(h.thread.turns).toHaveLength(1)
  expect(screen.queryByText(/Delivery is not confirmed/)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Cancel reply' }))
  await screen.findByText('Reply cancelled')
  expect(h.adapter.cancelOwnerChat).toHaveBeenCalledExactlyOnceWith({
    threadId: h.thread.id,
    identityId: h.thread.identityId,
    turnId: h.thread.turns[0].id
  })
})

it('never mistakes another sender’s identical text for admission, preserves obsolete drafts and fences profile responses', async () => {
  const { h, view } = harness()
  const old = deferred<OwnerChatThread>()
  vi.mocked(h.adapter.sendOwnerChat!).mockReturnValueOnce(old.promise)
  const rendered = render(view())
  const input = await open()
  fireEvent.change(input, { target: { value: 'Same words' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
  const intent = vi.mocked(h.adapter.sendOwnerChat!).mock.calls[0][0]
  h.thread = admittedChat(h.thread, { ...intent, idempotencyKey: 'another-window' })
  fireEvent.click(screen.getByRole('button', { name: 'Hide conversation' }))
  fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
  await screen.findByText('Same words')
  expect(screen.queryByText(/Delivery is not confirmed/)).toBeNull()
  expect(screen.getByText(/The conversation changed/)).toBeTruthy()
  expect((screen.getByRole('textbox', { name: 'Message' }) as HTMLTextAreaElement).value).toBe('Same words')
  h.snapshot = {
    ...h.snapshot,
    connection: { scope: 'socket-b:other', ownerScope: 'connection-b:other', state: 'ready' },
    runtime: { ...h.snapshot.runtime!, profile: 'other' },
    agents: [{ ...h.snapshot.agents[0], identityId: 'another-worker' }]
  }
  h.thread = {
    ...ownerChatThread(),
    id: 'other-chat',
    identityId: 'another-worker',
    profile: 'other',
    recipient: { ...ownerChatThread().recipient, identityId: 'another-worker' }
  }
  rendered.rerender(view())
  expect(screen.queryByRole('textbox', { name: 'Message' })).toBeNull()
  await open()
  await act(async () => old.resolve(answeredChat(admittedChat(ownerChatThread(), intent), 'Old profile reply')))
  expect(screen.queryByText('Old profile reply')).toBeNull()
  expect(screen.queryByText('Same words')).toBeNull()
  expect((screen.getByRole('textbox', { name: 'Message' }) as HTMLTextAreaElement).value).toBe('')
  expect(Object.values($ownerChatDrafts.get()).some(draft => draft.text === intent.text && !draft.intent)).toBe(true)
})

it('keeps archived members readable and usage-exhausted conversations explicit without enabling send', async () => {
  const { h, view } = harness()
  h.thread = answeredChat(
    admittedChat(h.thread, {
      threadId: h.thread.id,
      identityId: h.thread.identityId,
      text: 'Retained history',
      replyToMessageId: null,
      idempotencyKey: 'old-intent'
    })
  )
  h.thread = {
    ...h.thread,
    canSend: false,
    recipient: { ...h.thread.recipient, lifecycle: 'retired' },
    unavailableReason: 'retired'
  }
  h.snapshot.agents[0].lifecycle = 'retired'
  const rendered = render(view())
  fireEvent.click(screen.getByRole('button', { name: 'Inspect Worker 1' }))
  fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
  await screen.findByText('Retained history')
  expect(screen.getByText(/Conversation history is read-only/)).toBeTruthy()
  expect((screen.getByRole('textbox', { name: 'Message' }) as HTMLTextAreaElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
  expect(h.adapter.sendOwnerChat).not.toHaveBeenCalled()
  rendered.unmount()
  h.snapshot.agents[0].lifecycle = 'active'
  h.thread = {
    ...h.thread,
    recipient: { ...h.thread.recipient, lifecycle: 'active' },
    budget: { ...h.thread.budget, remainingCalls: 0, callsReserved: 32 }
  }
  render(view())
  fireEvent.click(screen.getByRole('button', { name: 'Inspect Worker 1' }))
  fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
  await screen.findByText(/has reached its usage allowance/)
  expect(screen.getByText('Retained history')).toBeTruthy()
  expect((screen.getByRole('button', { name: 'Send message' }) as HTMLButtonElement).disabled).toBe(true)
})

it('keeps a focused editable draft through background checks and recovers visibly from failed reads', async () => {
  const { h } = harness()
  const read = deferred<OwnerChatThread>()
  vi.mocked(h.adapter.readOwnerChat!).mockReturnValueOnce(read.promise)

  const panel = () => (
    <MemoryRouter>
      <OrganizationOwnerChat adapter={h.adapter} agent={h.snapshot.agents[0]} snapshot={h.snapshot} />
    </MemoryRouter>
  )

  vi.useFakeTimers()
  render(panel())
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
    await Promise.resolve()
  })
  const input = screen.getByRole('textbox', { name: 'Message' }) as HTMLTextAreaElement
  expect(input.disabled).toBe(false)
  fireEvent.change(input, { target: { value: 'Keep this draft' } })
  input.focus()
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15000)
  })
  expect(h.adapter.readOwnerChat).toHaveBeenCalledOnce()
  expect(input.disabled).toBe(false)
  expect(globalThis.document.activeElement).toBe(input)
  expect(input.value).toBe('Keep this draft')
  await act(async () => read.reject(new Error('Temporary disconnect')))
  expect(screen.getByRole('alert').textContent).toContain('Temporary disconnect')
  expect((screen.getByRole('button', { name: 'Send message' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Check conversation' }))
  await act(async () => {
    await Promise.resolve()
  })
  expect(screen.queryByRole('alert')).toBeNull()
  expect(input.value).toBe('Keep this draft')
  expect((screen.getByRole('button', { name: 'Send message' }) as HTMLButtonElement).disabled).toBe(false)
})

it('offers real member contacts across ranks and lifecycle, without treating Owner or controls as chat recipients', () => {
  const { h } = harness()

  for (const [id, role, lifecycle, visible] of [
    ['worker', 'Worker', 'active', true],
    ['manager', 'Manager', 'active', true],
    ['executive', 'Executive', 'active', true],
    ['former-worker', 'Worker', 'retired', true],
    ['inactive-worker', 'Worker', 'available', true],
    ['owner', 'Owner', 'active', false],
    ['control:project', 'Manager', 'active', false],
    ['control:apply', 'Worker', 'active', false]
  ] as const) {
    const agent = { ...h.snapshot.agents[0], id, role, lifecycle }

    const view = render(
      <MemoryRouter>
        <OrganizationOwnerChat adapter={h.adapter} agent={agent} snapshot={h.snapshot} />
      </MemoryRouter>
    )

    const chat = screen.queryByRole('button', { name: 'Chat' }) as HTMLButtonElement | null
    expect(Boolean(chat)).toBe(visible)

    if (chat) {
      expect(chat.disabled).toBe(false)
    }

    view.unmount()
  }

  expect(h.adapter.openOwnerChat).not.toHaveBeenCalled()
})

it('reviews finite renewal costs, keeps an interrupted exact intent through remount, and never sends a provider turn', async () => {
  const { h, view } = harness()
  h.thread = {
    ...h.thread,
    policyGeneration: 7,
    budget: { ...h.thread.budget, version: 2 },
    renewal: {
      canRenew: true,
      maxAdditionalCalls: 3,
      maxOutstandingCalls: 32,
      maxCumulativeCalls: 1000000,
      tokensPerCall: 34816,
      unavailableReason: null
    }
  }
  const pending = deferred<OwnerChatThread>()
  h.adapter.renewOwnerChat = vi
    .fn()
    .mockReturnValueOnce(pending.promise)
    .mockImplementation(async intent => ({
      ...h.thread,
      budget: { ...h.thread.budget, version: 3 },
      renewalReceipt: {
        id: 'receipt',
        idempotencyKey: intent.idempotencyKey,
        additionalCalls: 3,
        budgetVersion: 3,
        policyGeneration: 7,
        createdAt: '2026-10-08T12:00:00Z'
      }
    }))
  let rendered = render(view())
  await open()
  fireEvent.click(screen.getByRole('button', { name: 'Replenish allowance' }))
  expect(screen.getByRole('dialog').textContent).toContain('3 sends')
  expect(screen.getByRole('dialog').textContent).toContain('104448')
  expect(screen.getByRole('dialog').textContent).toContain('Actual configured-provider usage charges apply')
  expect(screen.getByRole('dialog').textContent).toContain('never refunded')
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
  expect(h.adapter.renewOwnerChat).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Replenish allowance' }))
  const confirm = screen.getByRole('button', { name: 'Confirm additional allowance' })
  fireEvent.click(confirm)
  fireEvent.click(confirm)
  expect(h.adapter.renewOwnerChat).toHaveBeenCalledOnce()
  const intent = vi.mocked(h.adapter.renewOwnerChat).mock.calls[0][0]
  expect(intent).toMatchObject({ additionalCalls: 3, expectedBudgetVersion: 2, expectedPolicyGeneration: 7 })
  rendered.unmount()
  await act(async () => pending.reject(new Error('Connection interrupted')))
  rendered = render(view())
  await open()
  fireEvent.click(screen.getByRole('button', { name: 'Retry same allowance update' }))
  fireEvent.click(screen.getByRole('button', { name: 'Confirm additional allowance' }))
  await waitFor(() => expect(h.adapter.renewOwnerChat).toHaveBeenCalledTimes(2))
  expect(vi.mocked(h.adapter.renewOwnerChat).mock.calls[1][0]).toEqual(intent)
  expect(h.adapter.sendOwnerChat).not.toHaveBeenCalled()
  rendered.unmount()
  vi.mocked(h.adapter.renewOwnerChat).mockRejectedValueOnce(new OwnerChatRenewalRejected('Allowance changed before admission'))
  rendered = render(view())
  await open()
  fireEvent.click(screen.getByRole('button', { name: 'Replenish allowance' }))
  fireEvent.click(screen.getByRole('button', { name: 'Confirm additional allowance' }))
  await screen.findByText('Allowance changed before admission')
  expect(Object.keys($ownerChatRenewals.get())).toHaveLength(0)
  expect(screen.queryByRole('dialog')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Check conversation' }))
  await waitFor(() => expect((screen.getByRole('button', { name: 'Replenish allowance' }) as HTMLButtonElement).disabled).toBe(false))
  rendered.unmount()
})

it('loads exact earlier pages while preserving the latest reply target and fences late history after navigation', async () => {
  const { h, view } = harness()
  h.thread = answeredChat(
    admittedChat(h.thread, {
      threadId: h.thread.id,
      identityId: h.thread.identityId,
      text: 'Recent question',
      replyToMessageId: 'older-reply',
      idempotencyKey: 'recent'
    }),
    'Recent reply'
  )
  h.thread = {
    ...h.thread,
    latestMessageId: h.thread.messages.at(-1)!.id,
    history: { hasMore: true, oldestMessageId: h.thread.messages[0].id }
  }

  const older = {
    ...h.thread,
    messages: h.thread.messages.map(message => ({
      ...message,
      id: `old-${message.id}`,
      text: `Earlier ${message.role}`
    })),
    history: { hasMore: true, oldestMessageId: 'old-owner-1' }
  }

  vi.mocked(h.adapter.readOwnerChat!).mockResolvedValueOnce(older)
  const rendered = render(view())
  const input = await open()
  fireEvent.click(screen.getByRole('button', { name: 'Load earlier messages' }))
  await screen.findByText('Earlier owner')
  expect(h.adapter.readOwnerChat).toHaveBeenCalledWith({
    threadId: h.thread.id,
    identityId: h.thread.identityId,
    beforeMessageId: h.thread.messages[0].id,
    limit: 50
  })
  expect(screen.getByText(/Continuing after: Recent reply/)).toBeTruthy()
  fireEvent.change(input, { target: { value: 'Continue latest' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
  await waitFor(() => expect(h.adapter.sendOwnerChat).toHaveBeenCalledOnce())
  expect(vi.mocked(h.adapter.sendOwnerChat!).mock.calls[0][0].replyToMessageId).toBe('reply-2')
  const late = deferred<OwnerChatThread>()
  vi.mocked(h.adapter.readOwnerChat!).mockReturnValueOnce(late.promise)
  fireEvent.click(screen.getByRole('button', { name: 'Load earlier messages' }))
  fireEvent.click(screen.getByRole('button', { name: 'Hide conversation' }))
  await act(async () => late.resolve({ ...older, messages: [{ ...older.messages[0], text: 'Late historical page' }] }))
  fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
  await screen.findByText('Recent question')
  expect(screen.queryByText('Late historical page')).toBeNull()
  rendered.unmount()
})
