/** Actual Electron → authenticated gateway → identity ledger → loopback provider. */
import type { Page } from '@playwright/test'

import {
  continuedMessage,
  continuedReply,
  firstMessage,
  firstReply,
  lateReply,
  secondMessage,
  setupOwnerChatFixture
} from './organization-owner-chat-fixture'
import { expect, test } from './test'

interface RecordedSend {
  url: string
  params: Record<string, unknown>
}
interface ThreadView {
  id: string
  identityId: string
  canSend: boolean
  messages: Array<{ text: string; role: string }>
  turns: Array<{ status: string }>
  budget: { callsReserved: number; tokensReserved: number; remainingCalls: number; maxCalls: number; version: number }
  renewalReceipt: { id: string; idempotencyKey: string } | null
}
let running: Awaited<ReturnType<typeof setupOwnerChatFixture>> | undefined

test.setTimeout(240_000)

async function rpc<T>(page: Page, recorded: RecordedSend, method: string, params = recorded.params) {
  const target = new URL(recorded.url)
  expect(target.protocol).toBe('ws:')
  expect(['127.0.0.1', 'localhost', '[::1]']).toContain(target.hostname)

  return page.evaluate(
    async ({ url, method, params }) =>
      new Promise<{ result?: T; error?: unknown }>((resolve, reject) => {
        const socket = new WebSocket(url)
        const id = `owner-chat-native-${method}`

        const timeout = setTimeout(() => {
          socket.close()
          reject(new Error('Owner chat RPC timed out'))
        }, 15_000)

        socket.addEventListener(
          'error',
          () => {
            clearTimeout(timeout)
            socket.close()
            reject(new Error('Owner chat RPC connection failed'))
          },
          { once: true }
        )
        socket.addEventListener('open', () => socket.send(JSON.stringify({ jsonrpc: '2.0', id, method, params })))
        socket.addEventListener('message', event => {
          const response = JSON.parse(String(event.data))

          if (response.id !== id) {
            return
          }

          clearTimeout(timeout)
          socket.close()
          resolve(response)
        })
      }),
    { url: recorded.url, method, params }
  )
}

function navigation(page: Page) {
  return page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })
}

async function openWorkerChat(page: Page) {
  await navigation(page).getByRole('link', { name: 'Organization', exact: true }).click()
  await page.getByRole('button', { name: 'Inspect Worker 1', exact: true }).click()
  const inspector = page.getByRole('complementary', { name: 'Agent details', exact: true })
  await inspector.getByRole('button', { name: 'Chat', exact: true }).click()
  const conversation = page.getByRole('region', { name: 'Owner conversation', exact: true })
  await expect(conversation.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible()

  return conversation
}

// eslint-disable-next-line no-empty-pattern -- actual Electron lifecycle belongs to this spec
test.afterEach(async ({}, testInfo) => {
  if (!running) {
    return
  }

  try {
    await testInfo.attach('owner-chat-provider-receipts', {
      body: JSON.stringify({ calls: running.calls, errors: running.providerErrors }, null, 2),
      contentType: 'application/json'
    })

    if (!running.fixture.page.isClosed()) {
      const state = await running.fixture.page.locator('body').ariaSnapshot()
      await testInfo.attach('owner-chat-ui-state', { body: state, contentType: 'text/plain' })
      await running.fixture.page.screenshot({ path: testInfo.outputPath('native-owner-chat.png') })
    }
  } finally {
    await running.fixture.cleanup()
    running = undefined
  }
})

test('retains exact worker chat, deduplicates owner send and fences a cancelled late reply', async () => {
  running = await setupOwnerChatFixture()
  const { page } = running.fixture
  const sends: RecordedSend[] = []
  const renewals: RecordedSend[] = []
  page.on('websocket', socket =>
    socket.on('framesent', frame => {
      const request = JSON.parse(String(frame.payload)) as { method?: string; params: Record<string, unknown> }

      if (request.method === 'organization.ownerChat.send') {
        sends.push({ url: socket.url(), params: request.params })
      } else if (request.method === 'organization.ownerChat.renew') {
        renewals.push({ url: socket.url(), params: request.params })
      }
    })
  )
  await page.reload()
  let conversation = await openWorkerChat(page)
  await expect(conversation.getByRole('button', { name: 'Call', exact: true })).toBeDisabled()
  expect(running.calls).toHaveLength(0)
  await conversation.getByRole('textbox', { name: 'Message', exact: true }).fill(firstMessage)
  await conversation.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect(conversation.getByText(firstReply, { exact: true })).toBeVisible({ timeout: 60_000 })
  await conversation.getByText(firstReply, { exact: true }).scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('native-owner-chat-reply.png') })
  expect(sends).toHaveLength(1)
  expect(running.calls).toHaveLength(1)
  const accepted = await rpc<ThreadView>(page, sends[0], 'organization.ownerChat.send')
  expect(accepted.error).toBeUndefined()
  expect(accepted.result?.budget.callsReserved).toBe(1)
  expect(accepted.result?.messages.filter(message => message.text === firstMessage)).toHaveLength(1)
  expect(running.calls).toHaveLength(1)

  await page.reload()
  conversation = await openWorkerChat(page)
  await expect(conversation.getByText(firstMessage, { exact: true })).toBeVisible()
  await expect(conversation.getByText(firstReply, { exact: true })).toBeVisible()
  await conversation.getByRole('textbox', { name: 'Message', exact: true }).fill(secondMessage)
  await conversation.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => running!.calls.length).toBe(2)
  await conversation.getByRole('button', { name: 'Cancel reply', exact: true }).click()

  const identity = {
    threadId: sends[0].params.threadId,
    identityId: sends[0].params.identityId,
    profile: sends[0].params.profile
  }

  await expect
    .poll(
      async () =>
        (await rpc<ThreadView>(page, sends[0], 'organization.ownerChat.read', identity)).result?.turns.at(-1)?.status
    )
    .toBe('cancelled')
  running.releaseReplies()
  // The backend keeps its per-thread fence until the provider worker has exited.
  // Do not assert absence while a delayed reply could still be processed.
  await expect
    .poll(
      async () => (await rpc<ThreadView>(page, sends[0], 'organization.ownerChat.read', identity)).result?.canSend,
      { timeout: 100_000 }
    )
    .toBe(true)
  await page.reload()
  conversation = await openWorkerChat(page)
  await expect(conversation.getByText(secondMessage, { exact: true })).toBeVisible()
  await expect(conversation.getByText(lateReply, { exact: true })).toHaveCount(0)
  const retained = await rpc<ThreadView>(page, sends[0], 'organization.ownerChat.read', identity)
  expect(retained.result?.id).toBe(accepted.result?.id)
  expect(retained.result?.identityId).toBe(accepted.result?.identityId)
  expect(retained.result?.budget.callsReserved).toBe(2)
  expect(retained.result?.turns.at(-1)?.status).toBe('cancelled')

  const snapshot = await rpc<{ objectives: unknown[] }>(page, sends[0], 'organization.snapshot', {
    profile: sends[0].params.profile
  })

  expect(snapshot.result?.objectives).toEqual([])
  expect(running.calls).toHaveLength(2)
  expect(running.providerErrors).toEqual([])
  await conversation.getByText(secondMessage, { exact: true }).scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('native-owner-chat-cancelled.png') })

  // Allowance changes are explicit, reversible before confirmation, and never
  // themselves produce inference or erase the cancelled turn's reservation.
  await conversation.getByRole('button', { name: 'Replenish allowance', exact: true }).click()
  let renewalDialog = page.getByRole('dialog', { name: 'Replenish allowance', exact: true })
  await expect(renewalDialog).toBeVisible()
  await renewalDialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  expect(renewals).toHaveLength(0)
  expect(running.calls).toHaveLength(2)
  await conversation.getByRole('button', { name: 'Replenish allowance', exact: true }).click()
  renewalDialog = page.getByRole('dialog', { name: 'Replenish allowance', exact: true })
  await page.screenshot({ path: test.info().outputPath('native-owner-chat-renewal-confirm.png') })
  await renewalDialog.getByRole('button', { name: 'Confirm additional allowance', exact: true }).click()
  await expect(renewalDialog).toHaveCount(0)
  expect(renewals).toHaveLength(1)
  const renewed = await rpc<ThreadView>(page, sends[0], 'organization.ownerChat.read', identity)
  expect(renewed.result?.budget.callsReserved).toBe(retained.result?.budget.callsReserved)
  expect(renewed.result?.budget.tokensReserved).toBe(retained.result?.budget.tokensReserved)
  expect(renewed.result?.budget.remainingCalls).toBe(retained.result!.budget.remainingCalls + 2)
  expect(renewed.result?.budget.version).toBe(retained.result!.budget.version + 1)
  const replayed = await rpc<ThreadView>(page, renewals[0], 'organization.ownerChat.renew')
  expect(replayed.error).toBeUndefined()
  expect(replayed.result?.renewalReceipt?.id).toBe(renewed.result?.renewalReceipt?.id)
  expect(replayed.result?.budget).toEqual(renewed.result?.budget)
  const stale = await rpc<{ renewalRejected: boolean }>(page, renewals[0], 'organization.ownerChat.renew', {
    ...renewals[0].params,
    idempotencyKey: 'native-stale-renewal'
  })
  expect(stale.error).toBeUndefined()
  expect(stale.result?.renewalRejected).toBe(true)
  expect(running.calls).toHaveLength(2)
  await page.reload()
  conversation = await openWorkerChat(page)
  await expect(conversation.getByText(secondMessage, { exact: true })).toBeVisible()
  const afterReload = await rpc<ThreadView>(page, sends[0], 'organization.ownerChat.read', identity)
  expect(afterReload.result?.budget).toEqual(renewed.result?.budget)
  await expect(conversation.getByRole('button', { name: 'Replenish allowance', exact: true })).toBeDisabled()
  await page.screenshot({ path: test.info().outputPath('native-owner-chat-renewed.png') })
  await conversation.getByRole('textbox', { name: 'Message', exact: true }).fill(continuedMessage)
  await conversation.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect(conversation.getByText(continuedReply, { exact: true })).toBeVisible({ timeout: 60_000 })
  expect(running.calls).toHaveLength(3)
  expect(running.providerErrors).toEqual([])
  const continued = await rpc<ThreadView>(page, sends[0], 'organization.ownerChat.read', identity)
  expect(continued.result?.id).toBe(accepted.result?.id)
  expect(continued.result?.budget.callsReserved).toBe(3)
  await page.screenshot({ path: test.info().outputPath('native-owner-chat-continued.png') })
})
