/** Real native source return with scripted pre-admission transport failures. */
import { execFileSync } from 'node:child_process'

import { waitForAppReady } from './fixtures'
import { firstMessage, firstReply, setupOwnerChatFixture } from './organization-owner-chat-fixture'
import { startOrganizationSwitchGateway } from './organization-switch-fixture'
import { acknowledgeExpectedErrorBanner, expect, test } from './test'

let running: Awaited<ReturnType<typeof setupOwnerChatFixture>> | undefined
let remote: Awaited<ReturnType<typeof startOrganizationSwitchGateway>> | undefined

test.setTimeout(240_000)
test.afterEach(async () => {
  try {
    await remote?.close()
  } finally {
    await running?.fixture.cleanup()
    remote = undefined
    running = undefined
  }
})

test('recovers the exact uncertain owner send and renewal only after returning to their original source', async () => {
  running = await setupOwnerChatFixture()
  const { page, app, sandbox, mockUrl } = running.fixture
  remote = await startOrganizationSwitchGateway(sandbox, mockUrl)
  const beforeB = remote.ledger()

  const local = await page.evaluate(
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
    { url: remote.url, token: remote.token }
  )

  const writes: Array<{ destination: string; method: string; params: Record<string, unknown> }> = []
  const failed = new Set<string>()
  const portB = new URL(remote.url).port
  await page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as { id: number; method?: string; params: Record<string, unknown> }

      if (request.method === 'organization.ownerChat.send' || request.method === 'organization.ownerChat.renew') {
        const destination = new URL(socket.url()).port === portB ? 'b' : 'a'
        writes.push({ destination, method: request.method, params: request.params })

        if (!failed.has(request.method)) {
          failed.add(request.method)
          socket.send(
            JSON.stringify({
              jsonrpc: '2.0',
              id: request.id,
              error: { code: 5070, message: `Scripted uncertain ${request.method}` }
            })
          )

          return
        }
      }

      server.send(message)
    })
  })
  await page.reload()
  await waitForAppReady(running.fixture)

  const navigation = page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })

  const switcher = page.locator('.eid-workspace-header [data-slot="connection-switcher"]')

  async function choose(label: string) {
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
    await choose('Owner recovery B')
    await navigation.getByRole('link', { name: 'Home', exact: true }).click()
    expect(writes).toHaveLength(count)
    expect(writes.filter(item => item.destination === 'b')).toEqual([])
    expect(remote!.ledger()).toBe(beforeB)
    expect(remote!.starts()).toBe(0)
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
  expect(running.calls).toHaveLength(0)
  const routeA = page.url()
  await visitB()
  panel = await conversation()
  expect(page.url()).toBe(routeA)
  await expect(panel.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue(firstMessage)
  await panel.getByRole('button', { name: 'Retry same message', exact: true }).click()
  await expect(panel.getByText(firstReply, { exact: true })).toBeVisible({ timeout: 60_000 })
  expect(writes).toHaveLength(2)
  expect(writes[1]).toEqual(writes[0])
  expect(running.calls).toHaveLength(1)
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
  const renewalAlert = (await panel.getByRole('alert').textContent())!.trim()
  expect(renewalAlert).toContain('Scripted uncertain organization.ownerChat.renew')
  await page
    .getByRole('dialog', { name: 'Replenish allowance', exact: true })
    .getByRole('button', { name: 'Cancel', exact: true })
    .click()
  expect(writes).toHaveLength(3)
  await visitB()
  panel = await conversation()
  await panel.getByRole('button', { name: 'Retry same allowance update', exact: true }).click()
  await page
    .getByRole('dialog', { name: 'Replenish allowance', exact: true })
    .getByRole('button', { name: 'Confirm additional allowance', exact: true })
    .click()
  await expect(page.getByRole('dialog', { name: 'Replenish allowance', exact: true })).toBeHidden()
  expect(writes).toHaveLength(4)
  expect(writes[3]).toEqual(writes[2])
  expect(writes.every(item => item.destination === 'a')).toBe(true)
  expect(running.calls).toHaveLength(1)
  expect(running.providerErrors).toEqual([])
  expect(remote.ledger()).toBe(beforeB)
  expect(remote.starts()).toBe(0)
  await expect(panel.getByRole('button', { name: 'Retry same allowance update', exact: true })).toHaveCount(0)
  const window = await app.browserWindow(page)
  await test.info().attach('owner-return-recovered', {
    contentType: 'image/png',
    body: Buffer.from(
      await window.evaluate(async window => (await window.webContents.capturePage()).toPNG().toString('base64')),
      'base64'
    )
  })
  await test.info().attach('owner-return-evidence', {
    contentType: 'application/json',
    body: JSON.stringify({
      sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
      sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
      writes,
      scriptedProviderCalls: running.calls.length,
      schedulerStartsB: remote.starts(),
      failureModel:
        'scripted pre-admission send and renewal errors; actual retries use real gateway and loopback provider',
      productionActivationEnabled: false,
      switchingDoor: 'existing connection switcher'
    })
  })
  await acknowledgeExpectedErrorBanner(page, sendAlert)
  await acknowledgeExpectedErrorBanner(page, renewalAlert)
})
