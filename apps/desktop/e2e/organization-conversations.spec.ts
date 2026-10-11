/** Native Electron renderer and real gateway transport, with bounded synthetic
 * organization snapshots. No provider work or conversation mutation is invoked. */
import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

interface ConversationFixture {
  id: string
  subject: string
  objectiveId: string
  taskId: string | null
  projectId: string | null
  participants: { id: string; name: string; team: string }[]
  status: 'waiting_reply' | 'answered'
  waitingAgentId: string | null
  messages: {
    id: string
    senderId: string
    recipientId: string
    body: string
    createdAt: string
    readAt: string | null
    replyToId: string | null
  }[]
}

let fixture: MockBackendFixture | undefined

test.setTimeout(180_000)
test.afterEach(async () => {
  await fixture?.cleanup()
  fixture = undefined
})

test('inspects waiting and answered internal threads without acknowledging recipient reads', async () => {
  fixture = await setupMockBackend()
  const { page, mock } = fixture
  const participants = [
    { id: 'writer', name: 'Alex', team: 'Research' },
    { id: 'reviewer', name: 'Alex', team: 'Review' }
  ]
  const thread: ConversationFixture = {
    id: 'thread-1',
    subject: 'Check the release evidence',
    objectiveId: 'objective-1',
    taskId: 'task-1',
    projectId: null,
    participants,
    status: 'waiting_reply',
    waitingAgentId: 'reviewer',
    messages: [
      {
        id: 'message-1',
        senderId: 'writer',
        recipientId: 'reviewer',
        body: 'Please check the source before review.\nThe audience is release managers.',
        createdAt: '2026-10-08T01:00:00Z',
        readAt: null,
        replyToId: null
      }
    ]
  }
  const unrelated: ConversationFixture = {
    ...thread,
    id: 'thread-2',
    subject: 'Independent team discussion',
    status: 'answered',
    waitingAgentId: null,
    participants: [
      { id: 'manager', name: 'Casey', team: 'Planning' },
      { id: 'other', name: 'Morgan', team: 'Planning' }
    ],
    messages: [
      {
        ...thread.messages[0],
        id: 'other-message',
        senderId: 'manager',
        recipientId: 'other',
        body: 'Independent planning context.'
      }
    ]
  }
  const snapshot = {
    source: 'runtime',
    runtime: { state: 'running', capabilities: [], maxWorkers: 2, scope: 'Synthetic internal text' },
    objectives: [
      {
        id: 'objective-1',
        title: 'Verify release evidence',
        description: '',
        status: 'active',
        source: 'runtime',
        ownerId: 'writer',
        createdAt: '2026-10-08T00:00:00Z'
      }
    ],
    agents: participants.map(person => ({
      ...person,
      role: 'Worker',
      responsibilities: [],
      capabilities: [],
      summary: 'Synthetic retained identity.',
      status: 'idle'
    })),
    tasks: [
      {
        id: 'task-1',
        objectiveId: 'objective-1',
        title: 'Check source',
        ownerId: 'writer',
        status: 'working',
        dependsOn: []
      }
    ],
    requests: [
      {
        id: 'request-1',
        objectiveId: 'objective-1',
        taskId: 'task-1',
        type: 'work.draft',
        team: 'Research',
        priority: 0,
        status: 'running',
        agentId: 'writer',
        attempts: 1,
        createdAt: '2026-10-08T00:00:00Z'
      }
    ],
    conversations: [thread, unrelated],
    activity: [],
    knowledge: []
  }
  const organizationMethods: string[] = []
  await page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as { id?: number; method?: string }

      if (request.method?.startsWith('organization.')) {
        organizationMethods.push(request.method)
      }

      if (request.method === 'organization.snapshot') {
        socket.send(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: snapshot }))
      } else {
        server.send(message)
      }
    })
  })
  await page.reload()
  await waitForAppReady(fixture)
  const initialProviderCalls = mock.receivedPrompts.length
  const navigation = page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })
  await navigation.getByRole('link', { name: 'Organization', exact: true }).click()
  const conversations = page.getByRole('region', { name: 'Agent conversations', exact: true })
  await expect(conversations.getByText('Waiting for reply', { exact: true })).toBeVisible()
  await expect(conversations.getByText('Waiting on agent: Alex (reviewer) · Review', { exact: true })).toBeVisible()
  await conversations.getByRole('button', { name: `Read conversation: ${thread.subject}`, exact: true }).click()
  const detail = page.getByRole('article', { name: thread.subject, exact: true })
  await expect(detail.getByText(thread.messages[0].body, { exact: true })).toBeVisible()
  await expect(detail.getByText('Not yet read by recipient runtime', { exact: true })).toBeVisible()
  expect(thread.messages[0].readAt).toBeNull()
  await expect(detail.getByRole('textbox')).toHaveCount(0)
  await detail.screenshot({ path: test.info().outputPath('internal-conversation-waiting.png') })

  // Only the simulated runtime delivery advances the read state. A poll updates
  // the already-selected thread without moving navigation or selecting another.
  thread.messages[0].readAt = '2026-10-08T01:01:00Z'
  thread.messages.push({
    id: 'message-2',
    senderId: 'reviewer',
    recipientId: 'writer',
    body: 'The source matches the release evidence.',
    createdAt: '2026-10-08T01:02:00Z',
    readAt: null,
    replyToId: 'message-1'
  })
  thread.status = 'answered'
  thread.waitingAgentId = null
  await expect(detail.getByText(thread.messages[1].body, { exact: true })).toBeVisible({ timeout: 20_000 })
  await expect(detail.getByText('Answered', { exact: true })).toBeVisible()
  const threadUrl = page.url()
  await detail.getByRole('button', { name: 'message-1', exact: true }).click()
  await expect(page).toHaveURL(threadUrl)
  await expect(detail.getByRole('list', { name: 'Messages', exact: true }).getByRole('listitem').first()).toBeFocused()
  await expect(detail.getByText(/Read by recipient runtime/)).toBeVisible()
  await detail.screenshot({ path: test.info().outputPath('internal-conversation-answered.png') })
  await conversations.getByRole('button', { name: 'Back to conversations', exact: true }).click()
  await expect(detail).toHaveCount(0)

  await page.getByRole('button', { name: 'Inspect Alex', exact: true }).nth(1).click()
  const agent = page.getByRole('complementary', { name: 'Agent details', exact: true })
  await expect(agent.getByRole('button', { name: `Read conversation: ${thread.subject}`, exact: true })).toBeVisible()
  await expect(agent.getByText(unrelated.subject, { exact: true })).toHaveCount(0)
  await agent.getByRole('button', { name: `Read conversation: ${thread.subject}`, exact: true }).click()
  await expect(agent.getByRole('article', { name: thread.subject, exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(agent).toHaveCount(0)
  await page.getByRole('button', { name: 'Inspect Alex', exact: true }).nth(0).click()
  await expect(agent.getByRole('article')).toHaveCount(0)
  await page.keyboard.press('Escape')

  await navigation.getByRole('link', { name: 'Work Overview', exact: true }).click()
  await page.goBack()
  await expect(page.getByRole('heading', { name: 'Organization', exact: true })).toBeVisible()
  await expect(page.getByRole('article', { name: thread.subject, exact: true })).toHaveCount(0)
  expect(thread.messages[1].readAt).toBeNull()
  expect(mock.receivedPrompts.length).toBe(initialProviderCalls)
  expect(organizationMethods.length).toBeGreaterThan(0)
  expect(
    organizationMethods.filter(
      method =>
        ![
          'organization.snapshot',
          'organization.outcomes',
          'organization.attention',
          'organization.history',
          'organization.projectSetup'
        ].includes(method)
    )
  ).toEqual([])
})
