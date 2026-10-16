import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { writeFileSync } from 'node:fs'

/** Actual Electron → authenticated gateway → identity ledger → loopback provider. */
import type { Page } from '@playwright/test'

import { validOwnerChatThread } from '../src/app/eidolon/runtime-owner-chat-contract'

import { verifyKeyboardFocus } from './focus-visibility'
import {
  continuedMessage,
  continuedReply,
  firstMessage,
  firstReply,
  lateReply,
  secondMessage,
  setupOwnerChatFixture
} from './organization-owner-chat-fixture'
import { setOwnerChatDetails } from './owner-chat-controls'
import { acknowledgeExpectedErrorBanner, expect, test } from './test'

interface RecordedSend {
  url: string
  params: Record<string, unknown>
}
interface ThreadView {
  id: string
  identityId: string
  canSend: boolean
  messages: Array<{ id: string; text: string; role: string }>
  latestMessageId: string | null
  activeTurnId: string | null
  history: { hasMore: boolean }
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

async function openWorkerChat(page: Page, entry: 'Organization' | 'Messages' = 'Messages') {
  await navigation(page).getByRole('link', { name: entry, exact: true }).click()

  if (entry === 'Organization') {
    await page.getByRole('button', { name: 'Inspect Worker 1', exact: true }).click()
    await page.getByRole('complementary', { name: 'Agent details', exact: true }).getByRole('button', { name: 'Chat', exact: true }).click()
  } else {
    await page.getByRole('textbox', { name: 'Search members', exact: true }).fill('Worker 1')
    await page.getByRole('navigation', { name: 'Choose an agent', exact: true }).getByRole('button', { name: /Worker 1/ }).click()
    await page.getByRole('region', { name: 'Member conversation', exact: true }).getByRole('button', { name: 'Chat', exact: true }).click()
  }

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

test('keeps a supported owner-chat draft mounted through compact member navigation without inference', async () => {
  running = await setupOwnerChatFixture()
  const { app, page } = running.fixture
  const actions: Array<{ method: string; params: Record<string, unknown> }> = []
  page.on('websocket', socket => socket.on('framesent', frame => {
    const request = JSON.parse(String(frame.payload)) as { method?: string; params: Record<string, unknown> }

    if (request.method && /^organization\.ownerChat\.(open|send|renew|cancel)$/.test(request.method)) {
      actions.push({ method: request.method, params: request.params })
    }
  }))
  await page.reload()

  async function resize(width: number, height: number, zoom = 1) {
    await page.evaluate(() => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(100))
    await app.evaluate(({ BrowserWindow }, size) => {
      const window = BrowserWindow.getAllWindows()[0]
      window.unmaximize()
      window.setMinimumSize(400, 360)
      window.setContentSize(size.width, size.height)
      window.focus()
    }, { width, height })
    await page.evaluate(value => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(value * 100), zoom)
    await expect.poll(() => page.evaluate(() => innerWidth)).toBe(Math.round(width / zoom))
    await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(zoom)
  }

  await resize(1536, 961)
  const conversation = await openWorkerChat(page)
  const composer = conversation.getByRole('textbox', { name: 'Message', exact: true })
  const originalComposer = await composer.elementHandle()
  const draft = 'Unsent compact-navigation draft for this exact worker identity.'
  await composer.fill(draft)
  await expect.poll(() => actions.filter(action => action.method === 'organization.ownerChat.open').length).toBe(1)
  expect(actions).toHaveLength(1)
  expect(running.calls).toHaveLength(0)
  const selectedRoute = await page.evaluate(() => location.hash)
  const selectedTuple = new URLSearchParams(selectedRoute.split('?')[1])
  expect(selectedTuple.get('member')).toBe('worker-1')
  expect(selectedTuple.get('identity')).toBeTruthy()
  expect(selectedTuple.get('owner')).toBeTruthy()
  const initialActions = JSON.stringify(actions)
  const captures: object[] = []

  async function capture(label: string) {
    const bytes = Buffer.from(await app.evaluate(async ({ BrowserWindow }) =>
      (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')), 'base64')
    writeFileSync(test.info().outputPath(`${label}.png`), bytes)
    await test.info().attach(label, { body: bytes, contentType: 'image/png' })
    captures.push({ label, sha256: createHash('sha256').update(bytes).digest('hex'),
      viewport: await page.evaluate(() => ({ width: innerWidth, height: innerHeight, devicePixelRatio })),
      selectedRoute, ownerChatActions: actions.length, scriptedProviderCalls: running!.calls.length })
  }

  for (const [width, height, zoom] of [[400, 620, 1], [1280, 720, 2]]) {
    await composer.focus()
    await resize(width, height, zoom)
    const label = `${width}x${height}-${zoom * 100}`
    const listPane = page.locator('.eid-messages-layout > [data-pane="list"]')
    const detailPane = page.locator('.eid-messages-layout > [data-pane="detail"]')
    const back = page.getByRole('button', { name: 'Back to members', exact: true })
    await expect(listPane).toBeHidden()
    await expect(listPane).toHaveAttribute('inert', '')
    await expect(composer).toBeFocused()
    await expect(composer).toHaveValue(draft)
    expect(await composer.evaluate((element, original) => element === original, originalComposer)).toBe(true)
    await composer.scrollIntoViewIfNeeded()
    await verifyKeyboardFocus(page, composer, `Compact owner-chat composer ${label}`)
    await expect(conversation.locator('.eid-owner-chat-charge')).toBeVisible()
    await capture(`compact-owner-chat-draft-${label}`)
    await back.scrollIntoViewIfNeeded()
    await verifyKeyboardFocus(page, back, `Compact owner-chat Back ${label}`)
    const detailScroll = await detailPane.evaluate(element => element.scrollTop)
    await capture(`compact-owner-chat-back-${label}`)
    await page.keyboard.press('Enter')
    await expect(detailPane).toBeHidden()
    await expect(detailPane).toHaveAttribute('inert', '')
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Send message', exact: true })).toHaveCount(0)
    const row = page.getByRole('navigation', { name: 'Choose an agent', exact: true }).getByRole('button', { name: /Worker 1/ })
    await expect(row).toBeFocused()
    await expect(row).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByRole('textbox', { name: 'Search members', exact: true })).toHaveValue('Worker 1')
    await expect.poll(() => page.evaluate(() => location.hash)).toBe(selectedRoute)
    await capture(`compact-owner-chat-members-${label}`)
    await page.keyboard.press('Enter')
    await expect(back).toBeFocused()
    await expect(composer).toHaveValue(draft)
    expect(await composer.evaluate((element, original) => element === original, originalComposer)).toBe(true)
    expect(await detailPane.evaluate(element => element.scrollTop)).toBe(detailScroll)
    await expect.poll(() => page.evaluate(() => location.hash)).toBe(selectedRoute)
    expect(JSON.stringify(actions)).toBe(initialActions)
    expect(running.calls).toHaveLength(0)
    await composer.focus()
    await resize(1536, 961)
    await expect(listPane).toBeVisible()
    await expect(detailPane).toBeVisible()
    await expect(composer).toBeFocused()
    expect(await composer.evaluate((element, original) => element === original, originalComposer)).toBe(true)
    await expect(composer).toHaveValue(draft)
  }

  expect(actions).toHaveLength(1)
  expect(actions[0].method).toBe('organization.ownerChat.open')
  expect(running.calls).toHaveLength(0)
  expect(running.providerErrors).toEqual([])
  writeFileSync(test.info().outputPath('compact-owner-chat-evidence.json'), JSON.stringify({
    sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
    sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
    scenario: 'supported-authenticated-owner-chat-presentation-only', captures, selectedRoute,
    ownerChatActions: actions, scriptedProviderCalls: running.calls.length
  }, null, 2))
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
  let conversation = await openWorkerChat(page, 'Organization')
  const draftBeforeSearch = conversation.getByRole('textbox', { name: 'Message', exact: true })
  await draftBeforeSearch.fill('Retain this unsent owner intent while inspecting loaded records.')
  const draftNode = await draftBeforeSearch.elementHandle()
  await page.getByRole('button', { name: 'Search loaded records', exact: true }).click()
  const recordSearch = page.getByRole('dialog', { name: 'Search loaded records', exact: true })
  await recordSearch.getByRole('textbox', { name: 'Search loaded records', exact: true }).fill('Worker 1')
  await recordSearch.getByRole('button', { name: /Worker 1.*Member/ }).click()
  await expect(recordSearch.getByRole('heading', { name: 'Worker 1', exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(recordSearch).toBeHidden()
  await expect(draftBeforeSearch).toHaveValue('Retain this unsent owner intent while inspecting loaded records.')
  expect(await draftBeforeSearch.evaluate((element, previous) => element === previous, draftNode)).toBe(true)
  expect(sends).toHaveLength(0)
  expect(renewals).toHaveLength(0)
  await page.screenshot({ path: test.info().outputPath('workspace-search-retained-owner-draft.png') })
  const directoryOrigin = page.url()
  await page.getByRole('button', { name: 'Unnamed organization · Organizations', exact: true }).click()
  const directory = page.getByRole('dialog', { name: 'Organizations', exact: true })
  await expect(directory.getByRole('button', { name: 'Use selected organization', exact: true })).toBeDisabled()
  await directory.getByRole('button', { name: 'Refresh current organization', exact: true }).click()
  await expect(directory.getByRole('option')).toHaveCount(1)
  await directory.getByRole('textbox', { name: 'Search organizations', exact: true }).fill('unmatched name')
  await page.keyboard.press('Escape')
  await expect(directory).toBeHidden()
  await expect(page.getByRole('button', { name: 'Unnamed organization · Organizations', exact: true })).toBeFocused()
  expect(page.url()).toBe(directoryOrigin)
  await expect(draftBeforeSearch).toHaveValue('Retain this unsent owner intent while inspecting loaded records.')
  expect(await draftBeforeSearch.evaluate((element, previous) => element === previous, draftNode)).toBe(true)
  expect(sends).toHaveLength(0)
  expect(renewals).toHaveLength(0)
  await page.screenshot({ path: test.info().outputPath('directory-retained-owner-draft.png') })
  await draftBeforeSearch.fill('')

  const transcriptMessage = (text: string) => conversation
    .getByRole('region', { name: 'Conversation messages', exact: true })
    .getByText(text, { exact: true })

  async function inspectComposer(label: string, width: number, height: number, zoom = 1) {
    await page.evaluate(() => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(100))
    await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(1)
    await running!.fixture.app.evaluate(({ BrowserWindow }, size) => {
      const window = BrowserWindow.getAllWindows()[0]
      window.unmaximize()
      window.setMinimumSize(600, 600)
      window.setContentSize(size.width, size.height)
    }, { width, height })
    await expect.poll(() => page.evaluate(() => innerWidth)).toBe(width)
    await page.evaluate(value => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(value * 100), zoom)
    await expect.poll(() => page.evaluate(() => innerWidth)).toBe(Math.round(width / zoom))
    await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(zoom)
    const scroll = conversation.getByRole('region', { name: 'Conversation messages', exact: true })
    const message = conversation.getByRole('textbox', { name: 'Message', exact: true })
    await scroll.scrollIntoViewIfNeeded()
    const transcriptFocus = await verifyKeyboardFocus(page, scroll, `${label} transcript`)
    await page.keyboard.press('End')
    await expect.poll(() => scroll.evaluate(element => element.scrollHeight - element.clientHeight - element.scrollTop)).toBeLessThanOrEqual(1)
    await expect(scroll.locator('.eid-owner-chat-bubble').last()).toBeInViewport()
    const composeDetails = conversation.locator('.eid-owner-chat-compose-details > summary')
    const detailsFocus = await verifyKeyboardFocus(page, composeDetails, `${label} reply and allowance details`)
    await setOwnerChatDetails(conversation, true)
    const allowance = conversation.locator('.eid-owner-chat-allowance')
    // Leave room on both edges at fractional layout coordinates under 200% zoom.
    await allowance.evaluate(element => element.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' }))
    await expect(allowance).toBeInViewport({ ratio: 1 })
    const allowanceBounds = await allowance.boundingBox()
    await setOwnerChatDetails(conversation, false)
    await message.scrollIntoViewIfNeeded()
    const composerFocus = await verifyKeyboardFocus(page, message, `${label} composer`)
    await expect(message).toBeInViewport({ ratio: 1 })
    const action = conversation.getByRole('button', { name: /^(Send message|Cancel reply)$/, exact: true })
    await expect(action).toBeInViewport({ ratio: 1 })
    await expect(conversation.locator('.eid-owner-chat-charge')).toBeInViewport({ ratio: 1 })

    if (width >= 1280 && zoom === 1) {
      const entry = await message.boundingBox()
      const send = await action.boundingBox()
      expect(entry!.height, 'compact composer entry remains at most 56px').toBeLessThanOrEqual(56)
      expect(Math.abs(entry!.y - send!.y), 'send action shares the entry row').toBeLessThanOrEqual(1)
      // At normal desktop sizes the actual inner panel must not scroll to
      // reach charges and composing with secondary details closed; history scrolls internally.
      const panel = conversation.locator('.eid-owner-chat-panel')
      expect(await panel.evaluate(element => element.scrollHeight - element.clientHeight)).toBeLessThanOrEqual(1)
      await expect(scroll.locator('.eid-owner-chat-bubble').last()).toBeInViewport({ ratio: 1 })
    }

    await setOwnerChatDetails(conversation, false)
    await message.focus()

    const bytes = Buffer.from(await running!.fixture.app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')), 'base64')
    writeFileSync(test.info().outputPath(`composer-${label}.png`), bytes)
    await test.info().attach(`composer-${label}-evidence`, { contentType: 'application/json', body: JSON.stringify({ sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(), sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(), viewport: await page.evaluate(() => ({ width: innerWidth, height: innerHeight, devicePixelRatio })), sha256: createHash('sha256').update(bytes).digest('hex'), panelGeometry: await conversation.locator('.eid-owner-chat-panel').evaluate(element => ({ scrollHeight: element.scrollHeight, clientHeight: element.clientHeight, scrollTop: element.scrollTop })), transcriptFocus, composerFocus, detailsFocus, allowanceBounds }) })
    await page.evaluate(() => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(100))
    await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(1)
  }

  await expect(conversation.getByRole('button', { name: 'Call', exact: true })).toBeDisabled()
  expect(running.calls).toHaveLength(0)
  await conversation.getByRole('textbox', { name: 'Message', exact: true }).fill(firstMessage)
  await conversation.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect(transcriptMessage(firstReply)).toBeVisible({ timeout: 60_000 })
  await transcriptMessage(firstReply).scrollIntoViewIfNeeded()
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
  await expect(transcriptMessage(firstMessage)).toBeVisible()
  await expect(transcriptMessage(firstReply)).toBeVisible()
  const transcript = conversation.locator('.eid-owner-chat-scroll')
  await expect.poll(() => transcript.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
  await transcriptMessage(firstReply).scrollIntoViewIfNeeded()
  await running.fixture.app.evaluate(({ BrowserWindow }) => {
    const window = BrowserWindow.getAllWindows()[0]
    window.unmaximize()
    window.setContentSize(1536, 961)
  })
  await expect.poll(() => page.evaluate(() => innerWidth)).toBe(1536)
  await expect(page.locator('.eid-message-members').getByRole('heading', { name: 'Messages', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Member context', exact: true })).toBeVisible()
  await transcriptMessage(firstReply).scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('native-messages-conversation-reference.png') })
  await page.screenshot({ path: test.info().outputPath('native-messages-conversation-wide.png') })

  // Reference comparison uses the supported, retained conversation above. The
  // visual-polish spec separately retains the older-runtime compatibility state.
  const referenceCaptures: object[] = []

  const retainedIdentity = {
    threadId: sends[0].params.threadId,
    identityId: sends[0].params.identityId,
    profile: sends[0].params.profile
  }

  const referenceCalls = running.calls.length
  const referenceSends = sends.length
  const referenceRenewals = renewals.length

  for (const mode of ['dark', 'light'] as const) {
    await navigation(page).getByRole('link', { name: 'Settings', exact: true }).click()
    await page.getByRole('button', { name: 'Appearance', exact: true }).click()
    await page.getByRole('button').filter({ has: page.getByText('AetherMesh', { exact: true }) }).click()
    await page.getByRole('button', { name: mode === 'dark' ? 'Dark' : 'Light', exact: true }).click()
    await expect(page.locator('html')).toHaveAttribute('data-hermes-theme', 'nous')
    await expect.poll(() => page.locator('html').evaluate(element => element.classList.contains('dark'))).toBe(mode === 'dark')
    conversation = await openWorkerChat(page)
    await page.getByRole('textbox', { name: 'Search members', exact: true }).fill('')
    await expect(transcriptMessage(firstMessage)).toBeVisible()
    await expect(transcriptMessage(firstReply)).toBeVisible()
    await expect(conversation.getByRole('textbox', { name: 'Message', exact: true })).toBeInViewport({ ratio: 1 })
    await expect(page.getByRole('region', { name: 'Member context', exact: true })).toBeVisible()
    const retained = await rpc<ThreadView>(page, sends[0], 'organization.ownerChat.read', retainedIdentity)
    expect(retained.error).toBeUndefined()
    expect(retained.result?.id).toBe(accepted.result?.id)
    expect(retained.result?.identityId).toBe(accepted.result?.identityId)
    expect(retained.result?.messages.map(message => message.id)).toEqual(accepted.result?.messages.map(message => message.id))
    expect(running.calls).toHaveLength(referenceCalls)
    expect(sends).toHaveLength(referenceSends)
    expect(renewals).toHaveLength(referenceRenewals)

    const geometry = await conversation.evaluate(element => {
      const box = (selector: string) => {
        const rect = element.querySelector(selector)!.getBoundingClientRect()

        return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, bottom: rect.bottom }
      }

      return { heading: box('.eid-owner-chat-heading'), recipient: box('.eid-message-recipient'),
        actions: box('.eid-owner-chat-heading .eid-owner-chat-actions'), transcript: box('.eid-owner-chat-scroll') }
    })

    // A normal desktop keeps explicit chat controls beside the recipient and
    // leaves useful reading space; the existing narrow/zoom matrix follows.
    expect(geometry.actions.y).toBeLessThan(geometry.recipient.bottom)
    expect(geometry.transcript.height).toBeGreaterThan(350)
    await expect(conversation.getByRole('button', { name: 'Hide conversation', exact: true })).toBeInViewport({ ratio: 1 })
    await setOwnerChatDetails(conversation, true)
    await expect(conversation.getByRole('button', { name: 'Replenish allowance', exact: true })).toBeInViewport({ ratio: 1 })

    await setOwnerChatDetails(conversation, false)
    await page.evaluate(async () => {
      if (document.activeElement instanceof HTMLElement) {document.activeElement.blur()}
      await document.fonts.ready
      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
    })

    const bytes = Buffer.from(await running.fixture.app.evaluate(async ({ BrowserWindow }) =>
      (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')), 'base64')

    const name = `reference-messages-populated-${mode}`
    writeFileSync(test.info().outputPath(`${name}.png`), bytes)
    referenceCaptures.push({ name, sha256: createHash('sha256').update(bytes).digest('hex'),
      viewport: await page.evaluate(() => ({ width: innerWidth, height: innerHeight, devicePixelRatio })),
      theme: await page.locator('html').getAttribute('data-hermes-theme'),
      mode: await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme),
      geometry, threadId: retained.result?.id, identityId: retained.result?.identityId,
      messageIds: retained.result?.messages.map(message => message.id),
      scriptedProviderCalls: running.calls.length, ownerSends: sends.length, allowanceRenewals: renewals.length })
  }

  writeFileSync(test.info().outputPath('populated-messages-reference-evidence.json'), JSON.stringify({
    sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
    sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
    scenario: 'supported-authenticated-retained-owner-chat-loopback-provider',
    initialScriptedProviderCalls: referenceCalls, initialOwnerSends: referenceSends,
    initialAllowanceRenewals: referenceRenewals, captures: referenceCaptures
  }, null, 2))
  // Restore the original theme before the existing composer/recovery matrix.
  await navigation(page).getByRole('link', { name: 'Settings', exact: true }).click()
  await page.getByRole('button', { name: 'Appearance', exact: true }).click()
  await page.getByRole('button').filter({ has: page.getByText('Eidolon', { exact: true }) }).click()
  await page.getByRole('button', { name: 'Dark', exact: true }).click()
  conversation = await openWorkerChat(page)


  for (const [width, height, zoom] of [[1536, 961, 1], [1280, 720, 1], [760, 900, 1], [1280, 720, 1.25], [1280, 720, 2]]) {
    await inspectComposer(`retained-${width}-${zoom}`, width, height, zoom)
  }

  await running.fixture.app.evaluate(({ BrowserWindow }) => {
    const window = BrowserWindow.getAllWindows()[0]
    window.unmaximize()
    window.setMinimumSize(600, 600)
    window.setContentSize(760, 900)
  })
  await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(760)
  await expect.poll(() => transcript.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
  await transcriptMessage(firstReply).scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('native-messages-conversation-narrow.png') })
  await running.fixture.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setContentSize(1220, 900))
  await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(1220)
  await conversation.getByRole('textbox', { name: 'Message', exact: true }).fill(secondMessage)
  await conversation.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => running!.calls.length).toBe(2)
  await inspectComposer('pending-reply-short', 1280, 720)
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
  await expect(transcriptMessage(secondMessage)).toBeVisible()
  await expect(conversation.getByText(lateReply, { exact: true })).toHaveCount(0)
  const retained = await rpc<ThreadView>(page, sends[0], 'organization.ownerChat.read', identity)
  expect(retained.result?.id).toBe(accepted.result?.id)
  expect(retained.result?.identityId).toBe(accepted.result?.identityId)
  expect(retained.result?.budget.callsReserved).toBe(2)
  expect(retained.result?.turns.at(-1)?.status).toBe('cancelled')

  // Real authenticated passive RPC, validated by the renderer's complete
  // contract. It must preserve the inner served profile and retained schema.
  const observed = await rpc<{ data: ThreadView & { profile: string }; scope: { profile: string } }>(page, sends[0], 'organization.observeRead', {
    method: 'organization.ownerChat.read', params: { threadId: identity.threadId, identityId: identity.identityId }
  })

  expect(observed.error).toBeUndefined()
  expect(validOwnerChatThread(observed.result?.data)).toBe(true)
  expect(observed.result?.data).toEqual(retained.result)
  expect(observed.result?.data.profile).toBe(observed.result?.scope.profile)
  expect(running.calls).toHaveLength(2)

  const snapshot = await rpc<{ objectives: unknown[] }>(page, sends[0], 'organization.snapshot', {
    profile: sends[0].params.profile
  })

  expect(snapshot.result?.objectives).toEqual([])
  expect(running.calls).toHaveLength(2)
  expect(running.providerErrors).toEqual([])
  await transcriptMessage(secondMessage).scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('native-owner-chat-cancelled.png') })

  // Allowance changes are explicit, reversible before confirmation, and never
  // themselves produce inference or erase the cancelled turn's reservation.
  await setOwnerChatDetails(conversation, true)
  await conversation.getByRole('button', { name: 'Replenish allowance', exact: true }).click()
  let renewalDialog = page.getByRole('dialog', { name: 'Replenish allowance', exact: true })
  await expect(renewalDialog).toBeVisible()
  await renewalDialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  expect(renewals).toHaveLength(0)
  expect(running.calls).toHaveLength(2)
  await setOwnerChatDetails(conversation, true)
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
  await expect(transcriptMessage(secondMessage)).toBeVisible()
  const afterReload = await rpc<ThreadView>(page, sends[0], 'organization.ownerChat.read', identity)
  expect(afterReload.result?.budget).toEqual(renewed.result?.budget)
  await setOwnerChatDetails(conversation, true)
  await expect(conversation.getByRole('button', { name: 'Replenish allowance', exact: true })).toBeDisabled()
  await page.screenshot({ path: test.info().outputPath('native-owner-chat-renewed.png') })
  await conversation.getByRole('textbox', { name: 'Message', exact: true }).fill(continuedMessage)
  await conversation.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect(transcriptMessage(continuedReply)).toBeVisible({ timeout: 60_000 })
  expect(running.calls).toHaveLength(3)
  expect(running.providerErrors).toEqual([])
  const continued = await rpc<ThreadView>(page, sends[0], 'organization.ownerChat.read', identity)
  expect(continued.result?.id).toBe(accepted.result?.id)
  expect(continued.result?.budget.callsReserved).toBe(3)
  await page.screenshot({ path: test.info().outputPath('native-owner-chat-continued.png') })

  // Fill one bounded page using explicit fixture sends through the actual
  // authenticated gateway. These are loopback scripted-provider calls only.
  let latest = continued.result!
  await expect
    .poll(
      async () => {
        const read = await rpc<ThreadView>(page, sends[0], 'organization.ownerChat.read', identity)
        expect(read.error).toBeUndefined()
        expect(read.result).toBeDefined()
        latest = read.result!

        return latest.activeTurnId === null && latest.canSend
      },
      { timeout: 30_000 }
    )
    .toBe(true)

  for (let index = 3; index < 27; index++) {
    const sent = await rpc<ThreadView>(page, sends[0], 'organization.ownerChat.send', {
      ...identity,
      text: `History fixture message ${index}`,
      replyToMessageId: latest.latestMessageId,
      idempotencyKey: `native-history-${index}`
    })

    expect(sent.error).toBeUndefined()
    await expect
      .poll(
        async () => {
          const read = await rpc<ThreadView>(page, sends[0], 'organization.ownerChat.read', identity)
          expect(read.error).toBeUndefined()
          expect(read.result).toBeDefined()
          latest = read.result!

          return latest.activeTurnId === null && latest.canSend
        },
        { timeout: 30_000 }
      )
      .toBe(true)
    expect(latest.turns.at(-1)?.status).toBe('completed')
    expect(latest.budget.callsReserved).toBe(index + 1)
  }

  expect(running.calls).toHaveLength(27)
  expect(latest.history.hasMore).toBe(true)
  const latestAnchor = latest.latestMessageId
  await page.reload()
  conversation = await openWorkerChat(page)
  await expect(conversation.getByText(firstMessage, { exact: true })).toHaveCount(0)
  await conversation.getByRole('button', { name: 'Load earlier messages', exact: true }).click()
  await expect(transcriptMessage(firstMessage)).toBeVisible()
  await expect(transcriptMessage(secondMessage)).toBeVisible()
  await page.screenshot({ path: test.info().outputPath('native-owner-chat-earlier-history.png') })
  await conversation.getByRole('button', { name: 'Return to latest messages', exact: true }).click()
  await expect(transcriptMessage('History fixture message 26')).toBeVisible()
  await expect(conversation.getByText(firstMessage, { exact: true })).toHaveCount(0)
  const afterHistory = await rpc<ThreadView>(page, sends[0], 'organization.ownerChat.read', identity)
  expect(afterHistory.result?.latestMessageId).toBe(latestAnchor)
  expect(afterHistory.result?.budget).toEqual(latest.budget)
  expect(running.calls).toHaveLength(27)
  expect(running.providerErrors).toEqual([])
  await page.screenshot({ path: test.info().outputPath('native-owner-chat-latest-history.png') })

  for (const [width, height, zoom] of [[1536, 961, 1], [1280, 720, 1], [760, 900, 1], [1280, 720, 2]]) {
    await inspectComposer(`long-history-${width}-${zoom}`, width, height, zoom)
  }

  // A read failure must preserve the retained transcript, draft and explicit
  // recovery controls. Only this synthetic response is intercepted; no send.
  let failReads = true
  await page.routeWebSocket('**/*', socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(String(message)) as { id: unknown; method: string }

      if (failReads && request.method === 'organization.ownerChat.read') {
        socket.send(JSON.stringify({ jsonrpc: '2.0', id: request.id, error: { code: -32000, message: 'Scripted retained conversation read failure' } }))
      } else {
        server.send(message)
      }
    })
  })
  await page.reload()
  conversation = await openWorkerChat(page)
  await conversation.getByRole('textbox', { name: 'Message', exact: true }).fill('Retained draft during a read failure')
  const readError = 'Could not verify the conversation Scripted retained conversation read failure'
  await expect(conversation.getByRole('alert')).toHaveText(readError, { timeout: 25_000 })
  await inspectComposer('read-error-short', 1280, 720)
  await expect(conversation.getByRole('alert')).toBeInViewport({ ratio: 1 })
  await expect(conversation.getByRole('button', { name: 'Check conversation', exact: true })).toBeInViewport({ ratio: 1 })
  await expect(conversation.getByRole('button', { name: 'Send message', exact: true })).toBeDisabled()
  failReads = false
  await conversation.getByRole('button', { name: 'Check conversation', exact: true }).click()
  await expect(conversation.getByRole('alert')).toHaveCount(0)
  await expect(conversation.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue('Retained draft during a read failure')
  await acknowledgeExpectedErrorBanner(page, readError)
  expect(running.calls).toHaveLength(27)
})
