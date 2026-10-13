/** A registered URL is shared authority; registration and secret edits never grant observation. */
import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { startOrganizationSwitchGateway } from './organization-switch-fixture'
import { expect, test } from './test'

let fixture: MockBackendFixture | undefined
let remote: Awaited<ReturnType<typeof startOrganizationSwitchGateway>> | undefined

test.setTimeout(180_000)
test.afterEach(async () => {
  try {
    await remote?.close()
  } finally {
    await fixture?.cleanup()
    remote = undefined
    fixture = undefined
  }
})

test('refuses shared URL observation without reading or starting its queued organization', async () => {
  fixture = await setupMockBackend({ extraConfig: 'approvals:\n  mode: manual' })
  await waitForAppReady(fixture)
  remote = await startOrganizationSwitchGateway(fixture.sandbox, fixture.mockUrl)
  const { page, app, mock } = fixture
  const before = remote.ledger()

  const id = await page.evaluate(
    async ({ url, token }) => {
      const bridge = (
        window as unknown as {
          hermesDesktop: {
            connections: { save(input: unknown): Promise<{ ok: boolean; connection: { id: string } }> }
            getConnectionFor(input: unknown): Promise<{ sharedRemote?: boolean }>
          }
        }
      ).hermesDesktop

      const saved = await bridge.connections.save({
        kind: 'remote',
        label: 'Shared URL fixture',
        url,
        authMode: 'token',
        token
      })

      if (!saved.ok) {
        throw new Error('Shared fixture registration failed')
      }

      const route = await bridge.getConnectionFor({ connectionId: saved.connection.id, profile: 'default' })

      if (route.sharedRemote !== true) {
        throw new Error('Expected real shared URL descriptor')
      }

      return saved.connection.id
    },
    { url: remote.url, token: remote.token }
  )

  const reads: string[] = []
  await page.routeWebSocket(
    url => url.port === new URL(remote!.url).port,
    socket => {
      const server = socket.connectToServer()
      socket.onMessage(message => {
        const request = JSON.parse(message.toString()) as { method?: string }

        if (request.method?.startsWith('organization.')) {
          reads.push(request.method)
        }

        server.send(message)
      })
    }
  )
  await page.locator('.eid-workspace-header [data-slot="connection-switcher"]').getByRole('button').click()
  await page.getByRole('menuitemradio', { name: 'Shared URL fixture' }).click()
  await page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })
    .getByRole('link', { name: 'Home', exact: true })
    .click()
  await expect(page.getByText('Organization observation is unavailable.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Organizations', exact: true })).toBeDisabled()
  const hidden = 'synthetic-private-query'
  await page.evaluate(
    async ({ id, url, token }) => {
      const bridge = (
        window as unknown as { hermesDesktop: { connections: { save(input: unknown): Promise<{ ok: boolean }> } } }
      ).hermesDesktop

      if (
        !(
          await bridge.connections.save({
            id,
            kind: 'remote',
            label: 'Shared URL fixture',
            url,
            authMode: 'token',
            token
          })
        ).ok
      ) {
        throw new Error('Shared fixture edit failed')
      }
    },
    { id, url: `${remote.url}?credential=${hidden}`, token: remote.token }
  )
  await expect(page.getByRole('button', { name: 'Organizations', exact: true })).toBeDisabled()
  expect(await page.locator('body').textContent()).not.toContain(hidden)
  expect(await page.locator('body').textContent()).not.toContain(remote.token)
  expect(reads).toEqual([])
  expect(remote.ledger()).toBe(before)
  expect(remote.starts()).toBe(0)
  expect(mock.receivedPrompts).toHaveLength(0)
  const window = await app.browserWindow(page)
  await test
    .info()
    .attach('shared-url-refused', {
      contentType: 'image/png',
      body: Buffer.from(
        await window.evaluate(async window => (await window.webContents.capturePage()).toPNG().toString('base64')),
        'base64'
      )
    })
})
