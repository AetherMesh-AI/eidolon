/** Test-first candidate: existing source door, real isolated gateways, no production activation enablement. */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import path from 'node:path'

import { type MockBackendFixture } from './fixtures'
import { profileA, profileB, queuedSourceTitle, setupDedicatedProfiles } from './organization-dedicated-profile-fixture'
import { queuedSwitchTitle, switchPython } from './organization-switch-fixture'
import { acknowledgeExpectedErrorBanner, expect, test } from './test'

const root = path.resolve(import.meta.dirname, '../../..')
let fixture: MockBackendFixture | undefined

test.setTimeout(240_000)
test.afterEach(async () => {
  await fixture?.cleanup()
  fixture = undefined
})

test('returns an uncertain intake through dedicated profile owners without starting queued destination work', async () => {
  const dedicated = await setupDedicatedProfiles()
  fixture = dedicated.fixture
  const { page, app, mock } = fixture
  const env = { ...dedicated.env, HERMES_HOME: dedicated.homes[0] }

  const remote = {
    url: dedicated.routes[1].baseUrl,
    ledger: () => dedicated.ledger(1),
    starts: () => dedicated.starts(1)
  }

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

  const registration = await page.evaluate(async () => {
    const bridge = (
      window as unknown as {
        hermesDesktop: {
          connections: { list(): Promise<{ connections: Array<{ id: string; kind: string; label: string }> }> }
        }
      }
    ).hermesDesktop

    const local = (await bridge.connections.list()).connections.find(item => item.kind === 'local')

    if (!local) {
      throw new Error('Missing actual local registry source')
    }

    return { local }
  })

  const localPort = new URL(dedicated.routes[0].baseUrl).port
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

      const port = new URL(socket.url()).port

      if (port !== remotePort && port !== localPort) {
        server.send(message)

        return
      }

      const destination = port === remotePort ? 'b' : 'a'

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

  const nav = page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })

  // Bootstrap only: visit both real dedicated profiles to retain their work owners.
  // The uncertain-intake journey below uses OrganizationWorkPicker exclusively.
  for (const profile of [profileA, profileB, profileA]) {
    await page.evaluate(() => {
      location.hash = '#/'
    })
    await page.locator('[data-slot="profile-rail"]').getByRole('button', { name: profile, exact: true }).click()
    await nav.getByRole('link', { name: 'Home', exact: true }).click()
    await expect(
      page.getByText(profile === profileA ? queuedSourceTitle : queuedSwitchTitle, { exact: true })
    ).toBeVisible()
    await expect(page.locator('.eid-workspace-header').getByRole('button', { name: new RegExp(profile) })).toBeVisible()
  }

  expect(dedicated.starts(0)).toBe(0)
  expect(dedicated.starts(1)).toBe(0)
  await nav.getByRole('link', { name: 'Home', exact: true }).click()
  await page.getByRole('button', { name: 'New objective', exact: true }).click()
  await page.getByRole('textbox', { name: 'Objective', exact: true }).fill(title)
  await page.getByRole('combobox', { name: 'Delivery scope', exact: true }).selectOption('managed_artifact')
  await page.getByRole('button', { name: 'Create objective', exact: true }).click()
  await expect(page.getByText('Scripted cross-source admission reply lost', { exact: true })).toBeVisible()
  expect(originalKey).not.toBe('')
  const originalRoute = page.url()
  const beforeA = dedicated.ledger(0)
  const beforeB = remote.ledger()
  expect(remote.starts()).toBe(0)
  const work = page.locator('.eid-workspace-header').getByRole('button', { name: /^Organization work:/ })
  const readsBeforeDismissal = methods.filter(item => item.destination === 'b').length
  await work.click()
  await page.keyboard.press('Escape')
  expect(page.url()).toBe(originalRoute)
  expect(methods.filter(item => item.destination === 'b')).toHaveLength(readsBeforeDismissal)
  expect(remote.ledger()).toBe(beforeB)

  async function choose(profile: string) {
    await work.click()
    await page
      .getByRole('region', { name: 'Work by connection and profile', exact: true })
      .getByRole('button', { name: `Open requests: ${registration.local.label} / ${profile}`, exact: true })
      .click()
    await expect(page.getByRole('region', { name: 'Work by connection and profile', exact: true })).toBeHidden()
    await expect(page.locator('.eid-workspace-header').getByRole('button', { name: new RegExp(profile) })).toBeVisible()
  }

  await choose(profileB)
  await nav.getByRole('link', { name: 'Home', exact: true }).click()
  await expect(page.getByText(queuedSwitchTitle, { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Organizations', exact: true }).click()
  const inspection = page.getByRole('dialog', { name: 'Organizations', exact: true })
  await inspection.getByRole('button', { name: 'Refresh current organization', exact: true }).click()
  await expect(inspection.getByRole('option')).toContainText(profileB)
  await expect(inspection.getByRole('button', { name: 'Use selected organization', exact: true })).toBeDisabled()
  holdDescriptor = true
  await inspection.getByRole('button', { name: 'Refresh current organization', exact: true }).click()
  await expect.poll(() => Boolean(releaseDescriptor)).toBe(true)
  // A registry save must revoke an in-flight read even when the dedicated
  // profile endpoint is unchanged. URL credentials remain a separate refusal gate.
  await page.evaluate(async local => {
    const bridge = (
      window as unknown as { hermesDesktop: { connections: { save(input: unknown): Promise<{ ok: boolean }> } } }
    ).hermesDesktop

    if (!(await bridge.connections.save({ id: local.id, kind: 'local', label: local.label })).ok) {
      throw new Error('Fixture registry save failed')
    }
  }, registration.local)
  await expect(inspection).toBeHidden()
  releaseDescriptor!()
  await page.getByRole('button', { name: 'Organizations', exact: true }).click()
  await expect(inspection.getByRole('option')).toContainText('Last known')
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

  await choose(profileA)
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
  expect(dedicated.ledger(0)).toBe(beforeA)
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
      sameEndpointRegistrySaveDropsLateRead: true,
      productionActivationEnabled: false,
      switchingDoor: 'OrganizationWorkPicker → selectOrganizationConnection',
      authorityScope: 'distinct dedicated local profiles on one registry source; not distinct connections',
      descriptors: dedicated.routes,
      schedulerStartsA: dedicated.starts(0)
    })
  })
  expect(dedicated.starts(0)).toBe(0)
  await acknowledgeExpectedErrorBanner(page, 'Scripted cross-source admission reply lost')
})
