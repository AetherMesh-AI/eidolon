import { execFileSync } from 'node:child_process'

import { directoryRecovery, directoryRecoveryDriver } from './directory-recovery-driver'
import { buildAppEnv, waitForAppReady } from './fixtures'
import { profileA, profileB } from './organization-dedicated-profile-fixture'
import { firstMessage, firstReply, setupOwnerChatFixture } from './organization-owner-chat-fixture'
import { fixtureLedger, startOrganizationSwitchGateway } from './organization-switch-fixture'
/** Actual durable admissions; only their successful acknowledgement is replaced. */
import { setOwnerChatDetails } from './owner-chat-controls'
import { acknowledgeExpectedErrorBanner, expect, test } from './test'

let running: Awaited<ReturnType<typeof setupOwnerChatFixture>> | undefined
let remote: Awaited<ReturnType<typeof startOrganizationSwitchGateway>> | undefined
let observationDiagnostics: Array<Record<string, unknown>> = []

test.setTimeout(240_000)
// eslint-disable-next-line no-empty-pattern -- diagnostic attachment uses test info only
test.afterEach(async ({}, info) => {
  try {
    const banners = await running?.fixture.page.evaluate(() =>
      (window as unknown as { __OWNER_OBSERVATION_DIAGNOSTICS__?: unknown[] }).__OWNER_OBSERVATION_DIAGNOSTICS__ ?? []
    ).catch(() => [])

    await info.attach('owner-observation-scope-diagnostics', {
      contentType: 'application/json',
      body: JSON.stringify({ events: observationDiagnostics, banners: banners ?? [],
        limitation: 'Existing traffic and DOM facts only; private renderer scope flags are not exposed.' })
    })
    observationDiagnostics = []
  } finally {
    try {
      await remote?.close()
    } finally {
      await running?.fixture.cleanup()
      remote = undefined
      running = undefined
    }
  }
})

test('recovers durably accepted owner send and renewal after lost acknowledgements and source return', async () => {
  running = await setupOwnerChatFixture({ dedicatedProfiles: directoryRecovery })
  const { page, app, sandbox, mockUrl } = running.fixture
  const dedicated = running.dedicated
  const directory = directoryRecoveryDriver(page)

  if (!dedicated) { remote = await startOrganizationSwitchGateway(sandbox, mockUrl) }
  const destinationLedger = () => dedicated ? dedicated.ledger(1) : remote!.ledger()
  const destinationStarts = () => dedicated ? dedicated.starts(1) : remote!.starts()
  const sourceHome = dedicated?.homes[0] ?? sandbox.hermesHome

  const local = dedicated ? profileA : await page.evaluate(
    async ({ url, token }) => {
      const bridge = (
        window as unknown as {
          hermesDesktop: {
            connections: {
              list(): Promise<{ connections: Array<{ kind: string; label: string }> }>
              save(input: unknown): Promise<{ ok: boolean }>
            }
          }
        }
      ).hermesDesktop

      const local = (await bridge.connections.list()).connections.find(item => item.kind === 'local')

      if (
        !local ||
        !(await bridge.connections.save({ kind: 'remote', label: 'Owner recovery B', url, authMode: 'token', token }))
          .ok
      ) {
        throw new Error('Isolated source registration failed')
      }

      return local.label
    },
    { url: remote!.url, token: remote!.token }
  )

  observationDiagnostics = []
  await page.addInitScript(() => {
    const events: Array<Record<string, unknown>> = []

    ;(window as unknown as { __OWNER_OBSERVATION_DIAGNOSTICS__?: unknown[] }).__OWNER_OBSERVATION_DIAGNOSTICS__ = events

    const observer = new MutationObserver(() => {
      if (events.length || !Array.from(document.querySelectorAll('[role="alert"]')).some(item =>
        item.textContent?.trim() === 'Organization observation is unavailable.')) {return}

      events.push({ time: Date.now(), kind: 'first-observation-banner',
        onMessagesRoute: location.hash.includes('/messages'),
        switching: document.querySelector('.eid-workspace-header [data-slot="connection-switcher"]')?.getAttribute('aria-busy') ?? null })
    })

    const start = () => observer.observe(document.body, { childList: true, subtree: true })

    if (document.body) {start()}
    else {document.addEventListener('DOMContentLoaded', start, { once: true })}
  })

  const recordObservation = (event: Record<string, unknown>) => {
    if (observationDiagnostics.length < 200) {observationDiagnostics.push({ time: Date.now(), ...event })}
  }

  const writes: Array<{ destination: string; method: string; params: Record<string, unknown> }> = []
  const accepted: Array<{ method: string; params: Record<string, unknown>; result: Record<string, unknown> }> = []
  const portB = new URL(dedicated ? dedicated.routes[1].baseUrl : remote!.url).port
  await page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer()
    const operations = new Map<number, { method: string; params: Record<string, unknown> }>()
    const observations = new Map<number, string>()
    const destination = new URL(socket.url()).port === portB ? 'b' : 'a'
    recordObservation({ kind: 'socket', destination })
    server.onMessage(message => {
      const reply = JSON.parse(message.toString()) as { id: number; result?: Record<string, unknown>; error?: unknown }
      const observedMethod = observations.get(reply.id)

      if (observedMethod) {
        observations.delete(reply.id)
        const result = reply.result as { state?: unknown; capabilityVersion?: unknown; scope?: { kind?: unknown; profile?: unknown } } | undefined
        const error = reply.error as { code?: unknown } | undefined
        const profile = result?.scope?.profile
        recordObservation({ kind: 'response', destination, method: observedMethod,
          errorCode: typeof error?.code === 'number' ? error.code : null,
          state: ['available', 'unconfigured', 'unsupported'].includes(String(result?.state)) ? result?.state : null,
          capabilityVersion: typeof result?.capabilityVersion === 'number' ? result.capabilityVersion : null,
          servedProfileScope: result?.scope?.kind === 'served-profile',
          profile: profile === 'default' || profile === profileA || profile === profileB ? profile : 'other-or-absent' })
      }

      const operation = operations.get(reply.id)

      if (operation) {
        operations.delete(reply.id)
        expect(reply.error).toBeUndefined()
        expect(reply.result).toBeTruthy()
        accepted.push({ ...operation, result: reply.result! })
        expect(fixtureLedger(sourceHome, buildAppEnv(sandbox))).toContain(
          String(operation.params.idempotencyKey)
        )
        socket.send(
          JSON.stringify({
            jsonrpc: '2.0',
            id: reply.id,
            error: { code: 5070, message: `Scripted uncertain ${operation.method}` }
          })
        )
      } else {
        socket.send(message)
      }
    })
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as { id: number; method?: string; params: Record<string, unknown> }

      if (request.method === 'organization.observe' || request.method === 'organization.observeRead') {
        observations.set(request.id, request.method)
        recordObservation({ kind: 'request', destination, method: request.method,
          readMethod: request.method === 'organization.observeRead' &&
            ['organization.outcomes', 'organization.attention'].includes(String(request.params?.method)) ? request.params.method : null })
      }

      if (request.method === 'organization.ownerChat.send' || request.method === 'organization.ownerChat.renew') {
        const destination = new URL(socket.url()).port === portB ? 'b' : 'a'
        writes.push({ destination, method: request.method, params: request.params })
        operations.set(request.id, { method: request.method, params: request.params })
      }

      server.send(message)
    })
  })
  await page.reload()
  await waitForAppReady(running.fixture)

  if (dedicated) {
    await dedicated.seedQueuedWork()
    await directory.bootstrap()
  }

  const beforeB = destinationLedger()

  const navigation = page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })

  const switcher = page.locator('.eid-workspace-header [data-slot="connection-switcher"]')

  async function choose(label: string) {
    if (dedicated) {
      await directory.choose(label)

      return
    }

    await switcher.getByRole('button').click()
    await page.getByRole('menuitemradio', { name: label }).click()
    await expect(switcher.getByRole('button')).toContainText(label)
    await expect(switcher).toHaveAttribute('aria-busy', 'false')
  }

  async function conversation() {
    await navigation.getByRole('link', { name: 'Messages', exact: true }).click()
    await page.getByRole('textbox', { name: 'Search members', exact: true }).fill('Worker 1')
    await page
      .getByRole('navigation', { name: 'Choose an agent', exact: true })
      .getByRole('button', { name: /Worker 1/ })
      .click()
    await page
      .getByRole('region', { name: 'Member conversation', exact: true })
      .getByRole('button', { name: 'Chat', exact: true })
      .click()

    return page.getByRole('region', { name: 'Owner conversation', exact: true })
  }

  async function visitB() {
    const count = writes.length
    await choose(dedicated ? profileB : 'Owner recovery B')
    await navigation.getByRole('link', { name: 'Home', exact: true }).click()
    expect(writes).toHaveLength(count)
    expect(writes.filter(item => item.destination === 'b')).toEqual([])
    expect(destinationLedger()).toBe(beforeB)
    expect(destinationStarts()).toBe(0)
    expect(await page.locator('body').textContent()).not.toContain(firstMessage)
    await choose(local)
  }

  let panel = await conversation()
  await panel.getByRole('textbox', { name: 'Message', exact: true }).fill(firstMessage)
  await panel.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect(panel.getByText(/Delivery is not confirmed/)).toBeVisible()
  const sendAlert = (await panel.getByRole('alert').textContent())!.trim()
  expect(sendAlert).toContain('Scripted uncertain organization.ownerChat.send')
  expect(writes).toHaveLength(1)
  const routeA = page.url()
  await visitB()
  panel = await conversation()
  expect(page.url()).toBe(routeA)
  await expect(panel.getByRole('button', { name: 'Retry same message', exact: true })).toHaveCount(0)
  await expect(panel.getByText(firstReply, { exact: true })).toBeVisible({ timeout: 60_000 })
  expect(writes).toHaveLength(1)
  expect(accepted).toHaveLength(1)
  expect(accepted[0].params).toEqual(writes[0].params)
  expect(accepted[0].result.turns).toEqual(
    expect.arrayContaining([expect.objectContaining({ idempotencyKey: writes[0].params.idempotencyKey })])
  )
  expect(running.calls).toHaveLength(1)
  await setOwnerChatDetails(panel, true)
  await panel.getByRole('button', { name: 'Replenish allowance', exact: true }).click()
  await page
    .getByRole('dialog', { name: 'Replenish allowance', exact: true })
    .getByRole('button', { name: 'Confirm additional allowance', exact: true })
    .click()
  await expect(
    page
      .getByRole('dialog', { name: 'Replenish allowance', exact: true })
      .getByText('Scripted uncertain organization.ownerChat.renew', { exact: true })
  ).toBeVisible()
  await page
    .getByRole('dialog', { name: 'Replenish allowance', exact: true })
    .getByRole('button', { name: 'Cancel', exact: true })
    .click()
  // Closing the modal restores the owner region to the accessibility tree.
  // Capture the exact visible alert; the global error guard remains unchanged.
  await expect(panel.getByRole('alert')).toBeVisible()
  const renewalAlert = (await panel.getByRole('alert').textContent())!.trim()
  expect(renewalAlert).toContain('Scripted uncertain organization.ownerChat.renew')
  expect(writes).toHaveLength(2)
  const beforeRenewalReturn = fixtureLedger(sourceHome, buildAppEnv(sandbox))
  await visitB()
  expect(fixtureLedger(sourceHome, buildAppEnv(sandbox))).toBe(beforeRenewalReturn)
  panel = await conversation()
  await expect(panel.getByText(firstReply, { exact: true })).toBeVisible()
  await setOwnerChatDetails(panel, true)
  await expect(panel.getByRole('button', { name: 'Retry same allowance update', exact: true })).toHaveCount(0)
  expect(writes).toHaveLength(2)
  expect(accepted).toHaveLength(2)
  expect(accepted[1].result.renewalReceipt).toMatchObject({
    idempotencyKey: writes[1].params.idempotencyKey,
    additionalCalls: writes[1].params.additionalCalls,
    budgetVersion: Number(writes[1].params.expectedBudgetVersion) + 1,
    policyGeneration: writes[1].params.expectedPolicyGeneration
  })
  const durable = fixtureLedger(sourceHome, buildAppEnv(sandbox))
  expect(durable).toContain(String(writes[0].params.idempotencyKey))
  expect(durable).toContain(String(writes[1].params.idempotencyKey))
  expect(writes.every(item => item.destination === 'a')).toBe(true)
  expect(running.calls).toHaveLength(1)
  expect(running.providerErrors).toEqual([])
  expect(destinationLedger()).toBe(beforeB)
  expect(destinationStarts()).toBe(0)
  await setOwnerChatDetails(panel, true)
  await expect(panel.getByRole('button', { name: 'Retry same allowance update', exact: true })).toHaveCount(0)

  if (remote) { expect(remote.updateChecks()).toBeGreaterThan(0) }
  await expect(page.getByRole('status').filter({ has: page.getByText('Update ready', { exact: true }) })).toHaveCount(0)
  const window = await app.browserWindow(page)
  await test.info().attach(directoryRecovery ? 'directory-owner-accepted-return-recovered' : 'owner-accepted-return-recovered', {
    contentType: 'image/png',
    body: Buffer.from(
      await window.evaluate(async window => (await window.webContents.capturePage()).toPNG().toString('base64')),
      'base64'
    )
  })
  await test.info().attach(directoryRecovery ? 'directory-owner-accepted-return-evidence' : 'owner-accepted-return-evidence', {
    contentType: 'application/json',
    body: JSON.stringify({
      sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
      sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
      writes,
      accepted,
      scriptedProviderCalls: running.calls.length,
      unintendedProviderCalls: running.fixture.mock.receivedPrompts.length,
      schedulerStartsB: destinationStarts(),
      schedulerStartsA: dedicated?.starts(0),
      descriptors: dedicated?.routes,
      isolatedBackendUpdateChecks: remote?.updateChecks(),
      failureModel:
        'actual gateway admissions and durable SQLite keys; successful acknowledgements replaced; return reads reconcile without resubmission',
      productionActivationEnabled: false,
      switchingDoor: dedicated ? 'actual Organizations directory with explicit confirmation' : 'existing connection switcher'
    })
  })

  if (dedicated) {
    expect(dedicated.starts(0)).toBe(0)
    expect(dedicated.starts(1)).toBe(0)
    expect(running.fixture.mock.receivedPrompts).toHaveLength(0)
  }

  expect(running.providerErrors).toEqual([])
  await acknowledgeExpectedErrorBanner(page, sendAlert)
  await acknowledgeExpectedErrorBanner(page, renewalAlert)
})
