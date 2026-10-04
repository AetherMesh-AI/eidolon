/**
 * Actual Electron → gateway → organization ledger → local provider → renderer.
 * The provider proposes an unsupported work route; no deployment is performed
 * and this test must never report the objective as completed.
 */
import http from 'node:http'

import {
  buildAppEnv,
  createSandbox,
  launchDesktop,
  type MockBackendFixture,
  waitForAppReady,
  writeEnvFile,
  writeMockProviderConfig
} from './fixtures'
import { startMockServer } from './mock-server'
import { expect, test } from './test'

const objectiveTitle = 'Deploy the unsupported organization fixture'
const legacyKey = 'eidolon.organization.v1'
const legacyBytes = JSON.stringify({
  objectives: [{ id: 'legacy-fixture', title: 'Retained prototype objective', description: 'Original local history.', result: 'Historical fixture result.' }],
  agents: [], tasks: [], activity: [], knowledge: []
})

let fixture: MockBackendFixture | null = null
const stages: string[] = []
const providerErrors: string[] = []

test.setTimeout(180_000)

// Keep the normal boot/readiness fixture, intercepting only actual organization
// stage requests. All forwarding is pinned to the existing localhost server.
async function localOrganizationProvider(mockUrl: string) {
  const server = http.createServer((request, response) => {
    const chunks: Buffer[] = []
    request.on('data', chunk => chunks.push(Buffer.from(chunk)))
    request.on('end', () => {
      const body = Buffer.concat(chunks)
      try {
        const payload = body.length ? JSON.parse(body.toString('utf8')) as {
          messages?: Array<{ role: string; content: unknown }>
          tools?: unknown[]
          stream?: boolean
        } : null
        const message = payload?.messages?.find(item => item.role === 'user' &&
          typeof item.content === 'string' && item.content.includes('Submitted context:\n'))
        if (message && typeof message.content === 'string') {
          const submitted = JSON.parse(message.content.split('Submitted context:\n', 2)[1]) as {
            request: { type: string }
            context: { objective: { title: string } }
          }
          const kind = submitted.request.type
          stages.push(kind)
          if (kind !== 'request.plan' || submitted.context.objective.title !== objectiveTitle || payload?.tools?.length) {
            throw new Error(`Unexpected organization provider request: ${kind}`)
          }
          const content = JSON.stringify({
            tasks: [{ title: 'Deploy to external production', description: 'Perform the requested external deployment, without substituting a draft.', type: 'work.deploy', team: 'general', dependsOn: [] }],
            workers: 1
          })
          const identity = { id: 'organization-unsupported-fixture', created: 1, model: 'mock-model' }
          const usage = { prompt_tokens: 30, completion_tokens: 20, total_tokens: 50 }
          if (payload?.stream) {
            response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
            response.write(`data: ${JSON.stringify({ ...identity, object: 'chat.completion.chunk', choices: [{ index: 0, delta: { role: 'assistant', content }, finish_reason: null }] })}\n\n`)
            response.write(`data: ${JSON.stringify({ ...identity, object: 'chat.completion.chunk', choices: [{ index: 0, delta: {}, finish_reason: 'stop' }], usage })}\n\n`)
            response.end('data: [DONE]\n\n')
          } else {
            response.writeHead(200, { 'Content-Type': 'application/json' })
            response.end(JSON.stringify({
              ...identity, object: 'chat.completion',
              choices: [{ index: 0, message: { role: 'assistant', content }, finish_reason: 'stop' }], usage
            }))
          }
          return
        }
        if (!request.url?.startsWith('/v1/')) { throw new Error('Unexpected provider endpoint') }
        const upstream = http.request(new URL(request.url, mockUrl), {
          method: request.method,
          headers: { ...request.headers, host: new URL(mockUrl).host }
        }, result => {
          response.writeHead(result.statusCode ?? 500, result.headers)
          result.pipe(response)
        })
        upstream.on('error', error => {
          providerErrors.push(error.message)
          if (!response.headersSent) { response.writeHead(502) }
          response.end()
        })
        upstream.end(body)
      } catch (error) {
        providerErrors.push(error instanceof Error ? error.message : String(error))
        response.writeHead(500, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify({ error: { message: 'Local organization fixture invariant failed' } }))
      }
    })
  })
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => resolve())
  })
  const address = server.address()
  if (!address || typeof address === 'string') { throw new Error('Local provider did not bind TCP') }
  return {
    url: `http://127.0.0.1:${address.port}`,
    close: () => new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
  }
}

test.beforeAll(async () => {
  stages.length = 0
  providerErrors.length = 0
  const mock = await startMockServer()
  const provider = await localOrganizationProvider(mock.url)
  const sandbox = createSandbox('organization-owner-loop')
  try {
    writeMockProviderConfig(sandbox.hermesHome, provider.url, undefined, 'approvals:\n  mode: manual\norganization:\n  max_inflight: 1\n  max_stages: 4')
    writeEnvFile(sandbox.hermesHome)
    const { app, page } = await launchDesktop(buildAppEnv(sandbox))
    fixture = {
      app, page, mock, mockUrl: provider.url, sandbox,
      cleanup: async () => {
        try { await app.close() } finally {
          try { await provider.close() } finally {
            try { await mock.close() } finally { sandbox.cleanup() }
          }
        }
      }
    }
    await waitForAppReady(fixture, 120_000)
  } catch (error) {
    if (fixture) { await fixture.cleanup() } else {
      await provider.close()
      await mock.close()
      sandbox.cleanup()
    }
    fixture = null
    throw error
  }
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('preserves unsupported work in Needs You across navigation and reload without completing it', async ({}, testInfo) => {
  const page = fixture!.page
  const navigation = page.getByRole('complementary', { name: 'Eidolon navigation' })
  const primary = navigation.getByRole('navigation', { name: 'Primary', exact: true })
  const capture = async (name: string) => {
    await page.screenshot({ path: testInfo.outputPath(`${name}.png`) })
  }

  await primary.getByRole('link', { name: 'Command', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'What should the organization do?', exact: true })).toBeVisible()
  await capture('01-command')
  await primary.getByRole('link', { name: 'Needs You', exact: true }).click()
  await expect(page.getByText('Nothing needs your input', { exact: true })).toBeVisible()

  await primary.getByRole('link', { name: 'Artifacts', exact: true }).click()
  const sources = page.getByRole('navigation', { name: 'Artifact sources' })
  await expect(sources.getByRole('link', { name: 'Session files' })).toHaveAttribute('aria-current', 'page')
  await sources.getByRole('link', { name: 'Organization evidence' }).click()
  await expect(sources.getByRole('link', { name: 'Organization evidence' })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByText('No organization evidence yet', { exact: true })).toBeVisible()
  await capture('02-organization-artifacts')

  // Seed only a legacy local-storage fixture. It must remain exact and must
  // never be submitted to the runtime ledger merely by opening its history.
  await page.evaluate(({ key, bytes }) => localStorage.setItem(key, bytes), { key: legacyKey, bytes: legacyBytes })
  await navigation.getByText('Advanced and history', { exact: true }).click()
  await navigation.getByRole('link', { name: 'Legacy prototype history', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Legacy prototype history', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Retained prototype objective', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Export original records', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Create objective', exact: true })).toHaveCount(0)
  await capture('03-read-only-legacy-history')
  expect(await page.evaluate(key => localStorage.getItem(key), legacyKey)).toBe(legacyBytes)
  expect(stages).toEqual([])

  await primary.getByRole('link', { name: 'Command', exact: true }).click()
  await page.getByRole('textbox', { name: 'Objective', exact: true }).fill(objectiveTitle)
  await page.getByRole('textbox', { name: 'Acceptance criteria', exact: true }).fill('An external deployment is actually performed; a text draft does not satisfy the objective.')
  await page.getByRole('button', { name: 'Create objective', exact: true }).click()
  await expect(page.getByRole('heading', { name: objectiveTitle, exact: true })).toBeVisible()
  const unsupported = page.getByRole('button', { name: 'Inspect request: work.deploy', exact: true })
  await expect(unsupported).toContainText('No eligible agent accepts work.deploy for team general', { timeout: 60_000 })
  await expect(unsupported).toContainText('Pending intervention')
  await expect(page.getByText('No accepted final result yet.', { exact: true })).toBeVisible()
  await capture('04-unsupported-objective')

  await primary.getByRole('link', { name: /^Needs You/ }).click()
  await expect(page.getByRole('combobox', { name: 'Status', exact: true })).toHaveValue('pending_intervention')
  await expect(unsupported).toBeVisible()
  await unsupported.click()
  const inspector = page.getByRole('complementary', { name: 'Request details' })
  await expect(inspector).toBeVisible()
  await expect(inspector).toContainText('No eligible agent accepts work.deploy for team general')
  await capture('05-needs-you-inspector')
  await page.keyboard.press('Escape')
  await expect(inspector).toHaveCount(0)
  await expect(unsupported).toBeFocused()
  await unsupported.click()
  await inspector.getByRole('button', { name: 'Close request details' }).click()
  await expect(inspector).toHaveCount(0)
  await expect(unsupported).toBeVisible()

  await page.reload()
  await expect(unsupported).toContainText('Pending intervention', { timeout: 60_000 })
  await expect(page.getByRole('combobox', { name: 'Status', exact: true })).toHaveValue('pending_intervention')
  await capture('06-needs-you-after-reload')
  expect(await page.evaluate(key => localStorage.getItem(key), legacyKey)).toBe(legacyBytes)
  expect(providerErrors).toEqual([])
  expect(stages).toEqual(['request.plan'])
  await testInfo.attach('organization-provider-coverage', {
    body: JSON.stringify({ stages, providerErrors, externalActionsPerformed: false, objectiveCompleted: false }, null, 2),
    contentType: 'application/json'
  })
})
