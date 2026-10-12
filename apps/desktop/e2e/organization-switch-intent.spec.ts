/** Test-first candidate: existing source door, real isolated gateways, no production activation enablement. */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import path from 'node:path'

import { buildAppEnv, type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import {
  fixtureLedger,
  queuedSwitchTitle,
  startOrganizationSwitchGateway,
  switchPython
} from './organization-switch-fixture'
import { acknowledgeExpectedErrorBanner, expect, test } from './test'

const root = path.resolve(import.meta.dirname, '../../..')
let fixture: MockBackendFixture | undefined
let remote: Awaited<ReturnType<typeof startOrganizationSwitchGateway>> | undefined

test.setTimeout(240_000)
test.afterEach(async () => {
  // The remote child lives under the desktop sandbox; stop it before removing that home.
  try {
    await remote?.close()
  } finally {
    await fixture?.cleanup()
    remote = undefined
    fixture = undefined
  }
})

test('returns an uncertain intake to its original route owner and key without starting queued destination work', async () => {
  fixture = await setupMockBackend({ extraConfig: 'approvals:\n  mode: manual' })
  await waitForAppReady(fixture)
  remote = await startOrganizationSwitchGateway(fixture.sandbox, fixture.mockUrl)
  const { page, app, sandbox, mock } = fixture
  const env = buildAppEnv(sandbox)
  const title = 'Retain this original admission across two real sources'

  const methods: Array<{
    method: string
    destination: 'a' | 'b'
    key?: string
    nestedMethod?: string
    nestedKey?: string
  }> = []

  let holdDescriptor = false
  let releaseDescriptor: (() => void) | undefined
  let originalKey = ''
  let originalParams: Record<string, unknown> | undefined

  const registration = await page.evaluate(
    async ({ url, token }) => {
      const bridge = (
        window as unknown as {
          hermesDesktop: {
            connections: {
              list(): Promise<{ connections: Array<{ id: string; kind: string; label: string }> }>
              save(input: unknown): Promise<{ ok: boolean; connection: { id: string } }>
            }
          }
        }
      ).hermesDesktop

      const local = (await bridge.connections.list()).connections.find(item => item.kind === 'local')

      if (!local) {
        throw new Error('Expected isolated local fixture source')
      }

      const result = await bridge.connections.save({
        kind: 'remote',
        label: 'Observation B',
        url,
        authMode: 'token',
        token
      })

      if (!result.ok) {
        throw new Error('Fixture remote registration failed')
      }

      return { local, remoteId: result.connection.id }
    },
    { url: remote.url, token: remote.token }
  )

  const remotePort = new URL(remote.url).port
  await page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer()
    let heldId: number | undefined
    server.onMessage(message => {
      const response = JSON.parse(message.toString()) as { id?: number }

      if (heldId !== undefined && response.id === heldId) {
        heldId = undefined
        releaseDescriptor = () => socket.send(message)
      } else {
        socket.send(message)
      }
    })
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as {
        id?: number
        method?: string
        params?: Record<string, unknown>
      }

      const destination = new URL(socket.url()).port === remotePort ? 'b' : 'a'

      if (request.method?.startsWith('organization.')) {
        const nested = request.params?.params as Record<string, unknown> | undefined
        methods.push({
          method: request.method,
          destination,
          key: request.params?.idempotencyKey as string | undefined,
          ...(request.method === 'organization.observeRead'
            ? {
                nestedMethod: request.params?.method as string | undefined,
                nestedKey: nested?.idempotencyKey as string | undefined
              }
            : {})
        })
      }

      if (destination === 'b' && request.method === 'organization.descriptor' && holdDescriptor) {
        heldId = request.id
        holdDescriptor = false
      }

      if (destination === 'a' && request.method === 'organization.create') {
        expect(originalKey).toBe('')
        originalKey = String(request.params?.idempotencyKey)
        originalParams = request.params
        execFileSync(
          switchPython,
          [
            '-c',
            `
import json,os,sys
from pathlib import Path
from eidolon_cli.organization_store import OrganizationStore
p=json.loads(sys.argv[1]);s=OrganizationStore(Path(os.environ['HERMES_HOME'])/'organization'/'state.db')
with s._write() as c:
 ident=s._create_objective(c,p['title'],idempotency_key=p['idempotencyKey'],delivery_mode=p['deliveryMode'])
 s._cancel_objective(c,ident)
s.set_objective_archived(ident,True,expected_revision=0,idempotency_key='switch-fixture-archive')
`,
            JSON.stringify(request.params)
          ],
          { cwd: root, env }
        )
        socket.send(
          JSON.stringify({
            jsonrpc: '2.0',
            id: request.id,
            error: { code: 5070, message: 'Scripted cross-source admission reply lost' }
          })
        )
      } else {
        server.send(message)
      }
    })
  })
  await page.reload()
  await waitForAppReady(fixture)

  const nav = page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })

  await nav.getByRole('link', { name: 'Home', exact: true }).click()
  await page.getByRole('button', { name: 'New objective', exact: true }).click()
  await page.getByRole('textbox', { name: 'Objective', exact: true }).fill(title)
  await page.getByRole('combobox', { name: 'Delivery scope', exact: true }).selectOption('managed_artifact')
  await page.getByRole('button', { name: 'Create objective', exact: true }).click()
  await expect(page.getByText('Scripted cross-source admission reply lost', { exact: true })).toBeVisible()
  expect(originalKey).not.toBe('')
  const originalRoute = page.url()
  const beforeA = fixtureLedger(sandbox.hermesHome, env)
  const beforeB = remote.ledger()
  expect(remote.starts()).toBe(0)
  const switcher = page.locator('.eid-workspace-header [data-slot="connection-switcher"]')
  await switcher.getByRole('button').click()
  await page.keyboard.press('Escape')
  expect(page.url()).toBe(originalRoute)
  expect(methods.filter(item => item.destination === 'b')).toEqual([])
  expect(remote.ledger()).toBe(beforeB)

  async function choose(label: string) {
    await switcher.getByRole('button').click()
    await page.getByRole('menuitemradio', { name: label }).click()
    await expect(switcher.getByRole('button')).toContainText(label)
    await expect(switcher).toHaveAttribute('aria-busy', 'false')
  }

  await choose('Observation B')
  await nav.getByRole('link', { name: 'Home', exact: true }).click()
  await expect(page.getByText(queuedSwitchTitle, { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Organizations', exact: true }).click()
  const inspection = page.getByRole('dialog', { name: 'Organizations', exact: true })
  await inspection.getByRole('button', { name: 'Refresh current organization', exact: true }).click()
  await expect(inspection.getByRole('option')).toContainText('Observation B')
  await expect(inspection.getByRole('button', { name: 'Use selected organization', exact: true })).toBeDisabled()
  holdDescriptor = true
  await inspection.getByRole('button', { name: 'Refresh current organization', exact: true }).click()
  await expect.poll(() => Boolean(releaseDescriptor)).toBe(true)
  // Electron strips query values before persisting this URL. The save event must
  // still invalidate an in-flight read even though the displayed endpoint is unchanged.
  const hiddenQuery = 'synthetic-private-query'
  await page.evaluate(
    async ({ id, url, token }) => {
      const bridge = (
        window as unknown as { hermesDesktop: { connections: { save(input: unknown): Promise<{ ok: boolean }> } } }
      ).hermesDesktop

      if (
        !(await bridge.connections.save({ id, kind: 'remote', label: 'Observation B', url, authMode: 'token', token }))
          .ok
      ) {
        throw new Error('Fixture same-endpoint edit failed')
      }
    },
    { id: registration.remoteId, url: `${remote.url}?credential=${hiddenQuery}`, token: remote.token }
  )
  await expect(inspection).toBeHidden()
  releaseDescriptor!()
  await page.getByRole('button', { name: 'Organizations', exact: true }).click()
  await expect(inspection.getByRole('option')).toContainText('Last known')
  expect(await inspection.textContent()).not.toContain(hiddenQuery)
  await expect(inspection.getByRole('button', { name: 'Use selected organization', exact: true })).toBeDisabled()
  await page.keyboard.press('Escape')
  expect(remote.ledger()).toBe(beforeB)
  expect(remote.starts()).toBe(0)
  expect(mock.receivedPrompts).toHaveLength(0)
  const nativeWindow = await app.browserWindow(page)
  await test.info().attach('stopped-destination-observation', {
    body: Buffer.from(
      await nativeWindow.evaluate(async window => (await window.webContents.capturePage()).toPNG().toString('base64')),
      'base64'
    ),
    contentType: 'image/png'
  })

  await choose(registration.local.label)
  await nav.getByRole('link', { name: 'Home', exact: true }).click()
  await page.getByRole('button', { name: 'New objective', exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Objective', exact: true })).toHaveValue(title)
  expect(page.url()).toBe(originalRoute)
  await expect(page.getByRole('button', { name: 'Create objective', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Check submission', exact: true }).click()
  await expect(page.getByText(/^Submission recorded\./)).toBeVisible()
  expect(methods.filter(item => item.method === 'organization.create')).toEqual([
    { method: 'organization.create', destination: 'a', key: originalKey }
  ])
  expect(methods.filter(item => item.method === 'organization.checkSubmission')).toEqual([])
  expect(methods.filter(item => item.nestedMethod === 'organization.checkSubmission')).toEqual([
    {
      method: 'organization.observeRead',
      destination: 'a',
      key: undefined,
      nestedMethod: 'organization.checkSubmission',
      nestedKey: originalKey
    }
  ])
  expect(
    methods
      .filter(item => item.destination === 'b')
      .every(
        item =>
          item.method === 'organization.observe' ||
          item.method === 'organization.descriptor' ||
          item.method === 'organization.observeRead'
      )
  ).toBe(true)
  expect(fixtureLedger(sandbox.hermesHome, env)).toBe(beforeA)
  expect(remote.ledger()).toBe(beforeB)
  expect(remote.starts()).toBe(0)
  expect(mock.receivedPrompts).toHaveLength(0)
  await test.info().attach('returned-original-uncertain-intake', {
    body: Buffer.from(
      await nativeWindow.evaluate(async window => (await window.webContents.capturePage()).toPNG().toString('base64')),
      'base64'
    ),
    contentType: 'image/png'
  })
  await test.info().attach('native-source-return-evidence', {
    contentType: 'application/json',
    body: JSON.stringify({
      sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
      sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
      originalRoute,
      originalKey,
      originalParams,
      methods,
      schedulerStarts: remote.starts(),
      providerCalls: mock.receivedPrompts.length,
      originalLedgerSha256: createHash('sha256').update(beforeA).digest('hex'),
      destinationLedgerSha256: createHash('sha256').update(beforeB).digest('hex'),
      normalizedQuerySaveDropsLateRead: true,
      productionActivationEnabled: false,
      switchingDoor: 'existing connection switcher'
    })
  })
  await acknowledgeExpectedErrorBanner(page, 'Scripted cross-source admission reply lost')
})
