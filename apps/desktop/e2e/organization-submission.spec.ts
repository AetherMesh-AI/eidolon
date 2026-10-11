/** Real Electron and read-only receipt RPC; a scripted lost-reply fixture seeds a real ledger admission. */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import path from 'node:path'

import { buildAppEnv, type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { acknowledgeExpectedErrorBanner, expect, test } from './test'

let fixture: MockBackendFixture | undefined
const root = path.resolve(import.meta.dirname, '../../..')
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const title = 'Inspect an archived admission after a lost response'

test.setTimeout(180_000)
test.afterEach(async () => {
  await fixture?.cleanup()
  fixture = undefined
})

test('explicitly checks a retained key through the real profile RPC without creating or executing more work', async () => {
  fixture = await setupMockBackend()
  const { page, mock, sandbox } = fixture
  const env = buildAppEnv(sandbox)
  const methods: string[] = []
  let receiptId = ''
  let retainedKey = ''

  const ledger = () =>
    execFileSync(
      python,
      [
        '-c',
        `
import os,sqlite3
from pathlib import Path
with sqlite3.connect((Path(os.environ['HERMES_HOME'])/'organization'/'state.db').as_uri()+'?mode=ro',uri=True) as conn:
 print('\\n'.join(conn.iterdump()))
`
      ],
      { cwd: root, env, encoding: 'utf8' }
    )

  await page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as {
        id?: number
        method?: string
        params?: Record<string, unknown>
      }

      if (request.method?.startsWith('organization.')) {
        methods.push(request.method)
      }

      if (request.method === 'organization.create') {
        expect(retainedKey).toBe('')
        expect(request.params?.title).toBe(title)
        expect(request.params?.profile === undefined || request.params.profile === 'default').toBe(true)
        retainedKey = String(request.params?.idempotencyKey)
        // Script the lost creation response only. The receipt itself is committed
        // to the real temporary ledger, then cancelled/archived without a scheduler.
        receiptId = execFileSync(
          python,
          [
            '-c',
            `
import json,os,sys
from pathlib import Path
from eidolon_cli.organization_store import OrganizationStore
p=json.loads(sys.argv[1])
s=OrganizationStore(Path(os.environ['HERMES_HOME'])/'organization'/'state.db')
with s._write() as conn:
 ident=s._create_objective(conn,p['title'],idempotency_key=p['idempotencyKey'],delivery_mode=p['deliveryMode'])
 s._cancel_objective(conn,ident)
s.set_objective_archived(ident,True,expected_revision=0,idempotency_key='native-archive')
print(ident)
`,
            JSON.stringify(request.params)
          ],
          { cwd: root, env, encoding: 'utf8' }
        ).trim()
        socket.send(
          JSON.stringify({
            jsonrpc: '2.0',
            id: request.id,
            error: { code: 5070, message: 'Scripted creation response lost' }
          })
        )
      } else {
        if (request.method === 'organization.checkSubmission') {
          expect(request.params?.idempotencyKey).toBe(retainedKey)
        }

        server.send(message)
      }
    })
  })
  await page.reload()
  await waitForAppReady(fixture)

  const navigation = page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })

  await navigation.getByRole('link', { name: 'Home', exact: true }).click()
  await page.getByRole('button', { name: 'New objective', exact: true }).click()
  await page.getByRole('textbox', { name: 'Objective', exact: true }).fill(title)
  await page.getByRole('combobox', { name: 'Delivery scope', exact: true }).selectOption('managed_artifact')
  await page.getByRole('button', { name: 'Create objective', exact: true }).click()
  await expect(page.getByText('Scripted creation response lost', { exact: true })).toBeVisible()
  expect(methods.filter(method => method === 'organization.checkSubmission')).toHaveLength(0)
  const before = ledger()
  const providerCalls = mock.receivedPrompts.length
  await page.getByRole('button', { name: 'Check submission', exact: true }).click()
  await expect(page.getByText(/^Submission recorded\./)).toBeVisible()
  await expect(page.getByText('Recorded in profile: default · Archived history', { exact: true })).toBeVisible()
  const link = page.locator('.eid-command').getByRole('link', { name: title, exact: true })
  await expect(link).toHaveAttribute('href', `#/objectives/${receiptId}`)
  await expect(page.getByRole('textbox', { name: 'Objective', exact: true })).toHaveValue(title)
  await expect(page.getByRole('button', { name: 'Create objective', exact: true })).toBeDisabled()
  expect(ledger()).toBe(before)
  expect(mock.receivedPrompts.length).toBe(providerCalls)
  expect(providerCalls).toBe(0)
  expect(methods.filter(method => method === 'organization.create')).toHaveLength(1)
  expect(methods.filter(method => method === 'organization.checkSubmission')).toHaveLength(1)
  await link.scrollIntoViewIfNeeded()
  await page.screenshot({ path: test.info().outputPath('submission-receipt-found.png') })
  await test.info().attach('submission-read-evidence', {
    body: JSON.stringify({
      receiptId,
      profile: 'default',
      archived: true,
      creationRequests: 1,
      inspectionRequests: 1,
      providerCalls,
      ledgerSha256: createHash('sha256').update(before).digest('hex')
    }),
    contentType: 'application/json'
  })
  await link.click()
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
  expect(methods.filter(method => method === 'organization.create')).toHaveLength(1)
  expect(mock.receivedPrompts.length).toBe(0)
  await acknowledgeExpectedErrorBanner(page, 'Scripted creation response lost')
})
