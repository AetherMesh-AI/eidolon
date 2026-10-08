import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { Organization } from './organization'
import { createRuntimeAdapter, type OrganizationGateway } from './runtime-adapter'
import { OrganizationConversations } from './runtime-conversations'
import type { OrganizationConversation, OrganizationSnapshot } from './types'

const conversation = (id = 'thread-1'): OrganizationConversation => ({
  id,
  subject: `Check source ${id}`,
  objectiveId: 'objective-1',
  taskId: 'task-1',
  projectId: 'project-1',
  participants: [
    { id: 'writer', name: 'Alex', team: 'Research' },
    { id: 'retired-reviewer', name: 'Alex', team: 'Review' }
  ],
  status: 'waiting_reply',
  waitingAgentId: 'retired-reviewer',
  messages: [
    {
      id: 'message-1',
      senderId: 'writer',
      recipientId: 'retired-reviewer',
      body: 'Please inspect this evidence.\n<script>not executable</script>',
      createdAt: '2026-10-08T01:00:00Z',
      readAt: '2026-10-08T01:01:00Z',
      replyToId: null
    },
    {
      id: 'message-2',
      senderId: 'retired-reviewer',
      recipientId: 'writer',
      body: 'The first source is incomplete.',
      createdAt: '2026-10-08T01:02:00Z',
      readAt: null,
      replyToId: 'message-1'
    }
  ]
})

const snapshot = (): OrganizationSnapshot => ({
  source: 'runtime',
  connection: { scope: 'connection-a:default:socket-1', state: 'ready' },
  runtime: { state: 'ready', capabilities: [], maxWorkers: 2, scope: 'Submitted text' },
  objectives: [
    {
      id: 'objective-1',
      title: 'Verify evidence',
      description: '',
      status: 'active',
      source: 'runtime',
      ownerId: 'writer',
      createdAt: '2026-10-08T00:00:00Z'
    }
  ],
  tasks: [
    {
      id: 'task-1',
      objectiveId: 'objective-1',
      projectId: 'project-1',
      title: 'Check sources',
      ownerId: 'writer',
      status: 'working',
      dependsOn: []
    }
  ],
  agents: [
    {
      id: 'writer',
      name: 'Current writer',
      role: 'Worker',
      team: 'New team',
      responsibilities: [],
      capabilities: [],
      summary: '',
      status: 'working'
    }
  ],
  activity: [],
  knowledge: [],
  requests: [],
  conversations: [conversation()]
})

it('reads retained identities, messages, replies and recipient delivery without issuing owner mutations', async () => {
  const value = snapshot()

  const gateway: OrganizationGateway = {
    request: vi.fn().mockResolvedValue(value),
    getScope: () => ({ key: value.connection!.scope, connected: true }),
    subscribeScope: () => () => {}
  }

  const adapter = createRuntimeAdapter(gateway)
  await adapter.refresh()
  const before = JSON.stringify(adapter.getSnapshot().conversations)

  const view = render(
    <MemoryRouter>
      <Organization adapter={adapter} snapshot={adapter.getSnapshot()} />
    </MemoryRouter>
  )

  const list = within(screen.getByRole('region', { name: 'Agent conversations' }))
  expect(list.getByText('Waiting for reply')).toBeTruthy()
  expect(list.getByText(/Waiting on agent: Alex \(retired-reviewer\) · Review/)).toBeTruthy()
  fireEvent.click(list.getByRole('button', { name: `Read conversation: ${value.conversations![0].subject}` }))
  const thread = within(screen.getByRole('article', { name: value.conversations![0].subject }))
  expect(thread.getAllByText('Alex (retired-reviewer) · Review')).not.toHaveLength(0)
  expect(thread.getAllByText('Alex (writer) · Research')).not.toHaveLength(0)
  expect(thread.getByText(/Please inspect this evidence./).textContent).toBe(value.conversations![0].messages[0].body)
  expect(thread.getByText('The first source is incomplete.')).toBeTruthy()
  expect(thread.getByText(/Read by recipient runtime/)).toBeTruthy()
  expect(thread.getByText('Not yet read by recipient runtime')).toBeTruthy()
  const scroll = vi.fn()
  const firstMessage = thread.getByText(/Please inspect this evidence./).closest('li')!
  firstMessage.scrollIntoView = scroll
  fireEvent.click(thread.getByRole('button', { name: 'message-1' }))
  expect(scroll).toHaveBeenCalledOnce()
  expect(globalThis.document.activeElement).toBe(firstMessage)
  expect(thread.getByRole('link', { name: 'Check sources · task-1' }).getAttribute('href')).toBe(
    '/objectives/objective-1'
  )
  expect(thread.queryByRole('textbox')).toBeNull()
  expect(view.container.querySelector('script')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Back to conversations' }))
  fireEvent.click(screen.getByRole('button', { name: 'Inspect Current writer' }))
  const agent = within(screen.getByRole('complementary', { name: 'Agent details' }))
  fireEvent.click(agent.getByRole('button', { name: `Read conversation: ${value.conversations![0].subject}` }))
  expect(agent.getByText('The first source is incomplete.')).toBeTruthy()
  expect(JSON.stringify(adapter.getSnapshot().conversations)).toBe(before)
  expect(gateway.request).toHaveBeenCalledExactlyOnceWith('organization.snapshot', {}, 15000, expect.any(AbortSignal))
})

it('filters exact participants and drops stale selections on scope, agent and thread changes', () => {
  const value = snapshot()

  const other = {
    ...conversation('thread-other'),
    participants: [{ id: 'other', name: 'Alex', team: 'Research' }, conversation().participants[1]],
    status: 'answered' as const,
    waitingAgentId: null,
    messages: [{ ...conversation().messages[0], senderId: 'other' }]
  }

  value.conversations!.push(other)

  const view = render(
    <MemoryRouter>
      <OrganizationConversations agentId="writer" snapshot={value} />
    </MemoryRouter>
  )

  const open = () =>
    fireEvent.click(screen.getByRole('button', { name: `Read conversation: ${conversation().subject}` }))

  expect(screen.queryByText(other.subject)).toBeNull()
  open()
  const next = { ...value, connection: { scope: 'connection-b:default:socket-2', state: 'ready' as const } }
  view.rerender(
    <MemoryRouter>
      <OrganizationConversations agentId="writer" snapshot={next} />
    </MemoryRouter>
  )
  expect(screen.queryByRole('article')).toBeNull()
  view.rerender(
    <MemoryRouter>
      <OrganizationConversations agentId="writer" snapshot={value} />
    </MemoryRouter>
  )
  expect(screen.queryByRole('article')).toBeNull()
  open()
  view.rerender(
    <MemoryRouter>
      <OrganizationConversations agentId="retired-reviewer" snapshot={value} />
    </MemoryRouter>
  )
  expect(screen.queryByRole('article')).toBeNull()
  expect(screen.getByText(other.subject)).toBeTruthy()
  open()
  view.rerender(
    <MemoryRouter>
      <OrganizationConversations agentId="retired-reviewer" snapshot={{ ...value, conversations: [other] }} />
    </MemoryRouter>
  )
  expect(screen.queryByRole('article')).toBeNull()
  view.rerender(
    <MemoryRouter>
      <OrganizationConversations
        agentId="retired-reviewer"
        snapshot={{ ...value, objectives: [], tasks: [{ ...value.tasks[0], objectiveId: 'wrong-objective' }] }}
      />
    </MemoryRouter>
  )
  open()
  const thread = within(screen.getByRole('article'))
  expect(thread.queryByRole('link', { name: /Check sources|Verify evidence/ })).toBeNull()
  expect(thread.getByText('task-1')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Back to conversations' }))
  view.rerender(
    <MemoryRouter>
      <OrganizationConversations snapshot={{ ...value, conversations: undefined }} />
    </MemoryRouter>
  )
  expect(screen.getByText('This runtime has not reported internal conversations.')).toBeTruthy()
  view.rerender(
    <MemoryRouter>
      <OrganizationConversations snapshot={{ ...value, conversations: [] }} />
    </MemoryRouter>
  )
  expect(screen.getByText('No agent conversations recorded')).toBeTruthy()

  for (const [status, label] of [
    ['needs_input', 'Needs input'],
    ['cancelled', 'Cancelled']
  ] as const) {
    view.rerender(
      <MemoryRouter>
        <OrganizationConversations
          snapshot={{ ...value, conversations: [{ ...conversation(), status, waitingAgentId: null }] }}
        />
      </MemoryRouter>
    )
    expect(screen.getByText(label)).toBeTruthy()
  }

  view.rerender(
    <MemoryRouter>
      <Organization snapshot={value} />
    </MemoryRouter>
  )
  fireEvent.click(screen.getByRole('button', { name: 'Inspect Current writer' }))
  expect(screen.getByRole('complementary', { name: 'Agent details' })).toBeTruthy()
  view.rerender(
    <MemoryRouter>
      <Organization snapshot={next} />
    </MemoryRouter>
  )
  expect(screen.queryByRole('complementary', { name: 'Agent details' })).toBeNull()
  view.rerender(
    <MemoryRouter>
      <Organization snapshot={value} />
    </MemoryRouter>
  )
  expect(screen.queryByRole('complementary', { name: 'Agent details' })).toBeNull()
})
