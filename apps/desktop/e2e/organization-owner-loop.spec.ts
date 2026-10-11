/**
 * Actual Electron → gateway → organization ledger → local provider → renderer.
 * The provider proposes an unsupported work route; no deployment is performed
 * and this test must never report the objective as completed.
 */
import http from 'node:http'

import type { Display } from 'electron'

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
import { openOrganizationDisclosure, openOrganizationRequests } from './organization-disclosures'
import { organizationPackageProposal } from './organization-package-proposal'
import { organizationProviderTarget } from './organization-provider-target'
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
const providerRequests: Array<{ method: string; path: string }> = []

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
        const target = organizationProviderTarget(mockUrl, request.method, request.url)
        providerRequests.push({ method: request.method!, path: target.pathname })
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
            context: { objective: { title: string; acceptanceCriteria: string[] } }
          }
          const kind = submitted.request.type
          stages.push(kind)
          if (!['request.decompose', 'request.plan'].includes(kind) || submitted.context.objective.title !== objectiveTitle || payload?.tools?.length) {
            throw new Error(`Unexpected organization provider request: ${kind}`)
          }
          const content = JSON.stringify(kind === 'request.decompose' ? organizationPackageProposal(submitted.context, 1) : {
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
        const upstream = http.request(target, {
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
  providerRequests.length = 0
  const mock = await startMockServer()
  const provider = await localOrganizationProvider(mock.url)
  const sandbox = createSandbox('organization-owner-loop')
  try {
    writeMockProviderConfig(sandbox.hermesHome, provider.url, undefined,
      'approvals:\n  mode: manual\norganization:\n  max_inflight: 1\n  max_stages: 4\n  max_context_tokens: 32768\n  max_output_tokens: 2048', 128000)
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

// eslint-disable-next-line no-empty-pattern -- fixture lifecycle is managed by this spec
test.afterEach(async ({}, testInfo) => {
  await testInfo.attach('organization-provider-coverage', {
    body: JSON.stringify({ stages, providerErrors, providerRequests, testStatus: testInfo.status,
      expectedOutcome: 'unsupported capability intervention; no worker dispatch or completion' }, null, 2),
    contentType: 'application/json'
  })
  if (fixture && !fixture.page.isClosed()) {
    await fixture.page.screenshot({ path: testInfo.outputPath('native-finished-state.png') })
  }
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

// eslint-disable-next-line no-empty-pattern -- fixture lifecycle is managed by this spec
test('preserves unsupported work and grants across navigation, dismissal, reload and cancellation', async ({}, testInfo) => {
  const page = fixture!.page
  const navigation = page.getByRole('complementary', { name: 'Eidolon navigation' })
  const primary = navigation.getByRole('navigation', { name: 'Primary', exact: true })
  const capture = async (name: string) => {
    await page.screenshot({ path: testInfo.outputPath(`${name}.png`) })
  }
  const assertNoToolGrants = async (captureName?: string) => {
    await openOrganizationDisclosure(page, 'Configured capabilities')
    const capabilities = page.getByRole('region', { name: 'Configured capabilities', exact: true })
    await expect(capabilities.getByText('Not enabled · submitted text only', { exact: true })).toBeVisible()
    await expect(capabilities.getByText('No patch grant configured', { exact: true })).toBeVisible()
    await expect(capabilities.getByText(/Granted read roots/)).toHaveCount(0)
    if (captureName) { await capture(captureName) }
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog', { name: 'System health details', exact: true })).toHaveCount(0)
  }

  const mountedSidebar = await navigation.locator('[data-tour="sessions-sidebar"]').elementHandle()
  expect(mountedSidebar).not.toBeNull()
  const nativeWindow = await fixture!.app.browserWindow(page)
  await testInfo.attach('owner-loop-restored-geometry', {
    body: JSON.stringify({
      window: await nativeWindow.evaluate(win => ({ bounds: win.getBounds(), contentSize: win.getContentSize() })),
      workAreas: await fixture!.app.evaluate(({ screen }) => screen.getAllDisplays().map((display: Display) => display.workArea)),
      viewport: await page.evaluate(() => ({ width: innerWidth, height: innerHeight }))
    }, null, 2),
    contentType: 'application/json'
  })
  // Production restoration intentionally caps saved bounds to the host work
  // area. Establish this layout scenario through the actual native window,
  // then wait for Chromium to observe the resize without faking its viewport.
  await nativeWindow.evaluate(win => {
    win.unmaximize()
    win.setMinimumSize(1220, 800)
    win.setPosition(0, 0, false)
    win.setContentSize(1220, 800, false)
  })
  await expect.poll(() => page.evaluate(() => ({ width: innerWidth, height: innerHeight }))).toEqual({ width: 1220, height: 800 })
  await nativeWindow.dispose()

  const assertRailLayout = async (name: string) => {
    const geometry = await navigation.evaluate(rail => {
      const heading = rail.querySelector('.eid-rail-history > summary')!.getBoundingClientRect()
      const tree = rail.querySelector('.eid-session-tree')!.getBoundingClientRect()
      const sidebar = rail.querySelector('[data-tour="sessions-sidebar"]')!.getBoundingClientRect()
      const footer = rail.querySelector('.eid-rail-footer')!.getBoundingClientRect()
      return { headingBottom: heading.bottom, treeTop: tree.top, treeBottom: tree.bottom,
        treeLeft: tree.left, treeRight: tree.right, sidebarBottom: sidebar.bottom,
        sidebarLeft: sidebar.left, sidebarRight: sidebar.right, footerTop: footer.top,
        scrollTop: rail.scrollTop, overflow: rail.scrollHeight > rail.clientHeight }
    })
    expect(geometry.headingBottom).toBeLessThanOrEqual(geometry.treeTop + 1)
    expect(geometry.treeBottom).toBeLessThanOrEqual(geometry.footerTop + 1)
    expect(geometry.sidebarBottom).toBeLessThanOrEqual(geometry.treeBottom + 1)
    expect(geometry.sidebarLeft).toBeGreaterThanOrEqual(geometry.treeLeft - 1)
    expect(geometry.sidebarRight).toBeLessThanOrEqual(geometry.treeRight + 1)
    await navigation.hover()
    await page.mouse.wheel(0, 2000)
    if (geometry.overflow) {
      await expect.poll(() => navigation.evaluate(rail => rail.scrollTop)).toBeGreaterThan(geometry.scrollTop)
    }
    await expect(navigation.locator('.eid-rail-footer')).toBeInViewport({ ratio: 1 })
    for (const label of ['New project', 'Manage gateways…']) {
      const control = navigation.getByRole('button', { name: label, exact: true })
      await expect(control).toBeInViewport({ ratio: 1 })
      // Trial proves the actual native-rendered control is hit-testable without
      // opening a dialog or changing the fixture's project/profile selection.
      await control.click({ trial: true })
    }
    expect(await mountedSidebar!.evaluate(node => node.isConnected)).toBe(true)
    await capture(name)
    await navigation.hover()
    await page.mouse.wheel(0, -2000)
    await expect.poll(() => navigation.evaluate(rail => rail.scrollTop)).toBe(0)
  }

  await primary.getByRole('link', { name: 'Home', exact: true }).click()
  await page.getByRole('button', { name: 'New objective', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'What should the organization do?', exact: true })).toBeVisible()
  await assertNoToolGrants('01-system-health-capabilities')
  await capture('01-command')
  const historyDisclosure = navigation.locator('.eid-rail-history')
  if (await historyDisclosure.getAttribute('open') !== null) {
    await historyDisclosure.locator(':scope > summary').click()
  }
  await expect(navigation.getByRole('button', { name: 'New project', exact: true })).toBeHidden()
  expect(await mountedSidebar!.evaluate(node => node.isConnected)).toBe(true)
  await navigation.locator('.eid-rail-history > summary').click()
  await assertRailLayout('01b-collapsed-rail-controls')
  await openOrganizationRequests(page)
  await expect(page.getByText('Nothing needs your input', { exact: true })).toBeVisible()

  await primary.getByRole('link', { name: 'Deliverables', exact: true }).click()
  const sources = page.getByRole('navigation', { name: 'Artifact sources' })
  await expect(sources.getByRole('link', { name: 'Session files' })).toHaveAttribute('aria-current', 'page')
  await sources.getByRole('link', { name: 'Organization evidence' }).click()
  await expect(sources.getByRole('link', { name: 'Organization evidence' })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByText('No organization evidence yet', { exact: true })).toBeVisible()
  await capture('02-organization-artifacts')

  // Seed only a legacy local-storage fixture. It must remain exact and must
  // never be submitted to the runtime ledger merely by opening its history.
  await page.evaluate(({ key, bytes }) => localStorage.setItem(key, bytes), { key: legacyKey, bytes: legacyBytes })
  const toolsDisclosure = navigation.getByText('Tools and configuration', { exact: true })
  if ((await toolsDisclosure.locator('..').getAttribute('open')) === null) { await toolsDisclosure.click() }
  await expect(toolsDisclosure.locator('..')).toHaveAttribute('open', '')
  await assertRailLayout('03a-expanded-rail-controls')
  await navigation.getByRole('link', { name: 'Legacy prototype history', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Legacy prototype history', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Retained prototype objective', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Export original records', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Create objective', exact: true })).toHaveCount(0)
  await capture('03-read-only-legacy-history')
  expect(await page.evaluate(key => localStorage.getItem(key), legacyKey)).toBe(legacyBytes)
  expect(stages).toEqual([])
  await navigation.getByText('Tools and configuration', { exact: true }).click()
  await assertRailLayout('03b-collapsed-history-controls')
  await navigation.getByText('Tools and configuration', { exact: true }).click()
  await assertRailLayout('03c-reexpanded-history-controls')

  await primary.getByRole('link', { name: 'Home', exact: true }).click()
  await page.getByRole('button', { name: 'New objective', exact: true }).click()
  const deliveryScope = page.getByRole('combobox', { name: 'Delivery scope', exact: true })
  await deliveryScope.selectOption('managed_artifact')
  await assertNoToolGrants()
  await deliveryScope.selectOption('source_project')
  await expect(page.getByText('Choosing a delivery scope does not grant source writes or external access.', { exact: true })).toBeVisible()
  await assertNoToolGrants()
  await page.getByRole('textbox', { name: 'Objective', exact: true }).fill(objectiveTitle)
  await openOrganizationDisclosure(page, 'Acceptance criteria')
  await page.getByRole('textbox', { name: 'Acceptance criteria', exact: true }).fill('An external deployment is actually performed; a text draft does not satisfy the objective.')
  await page.getByRole('button', { name: 'Create objective', exact: true }).click()
  await expect(page.getByRole('heading', { name: objectiveTitle, exact: true })).toBeVisible()
  const unsupported = page.getByRole('button', { name: 'Inspect request: work.deploy', exact: true })
  await expect(unsupported).toContainText('No eligible agent accepts work.deploy for team general', { timeout: 60_000 })
  await expect(unsupported).toContainText('Pending intervention')
  await expect(page.getByText('No accepted final result yet.', { exact: true })).toBeVisible()
  await capture('04-unsupported-objective')

  await openOrganizationRequests(page)
  await page.getByRole('button', { name: 'Request queue', exact: true }).click()
  await expect(page.getByRole('combobox', { name: 'Status', exact: true })).toHaveValue('pending_intervention')
  await expect(unsupported).toBeVisible()
  await unsupported.click()
  const inspector = page.getByRole('complementary', { name: 'Request details' })
  await expect(inspector).toBeVisible()
  await expect(inspector).toContainText('No eligible agent accepts work.deploy for team general')
  await capture('05-needs-you-inspector')
  await inspector.getByRole('combobox', { name: 'Action', exact: true }).selectOption('amend_scope')
  await inspector.getByRole('textbox', { name: 'Response', exact: true }).fill('Unsubmitted scope change must not replace the deployment requirement.')
  await expect(inspector.getByRole('button', { name: 'Submit response', exact: true })).toBeEnabled()
  await expect(inspector.getByText('Owner actions resolve the recorded cause. They never grant permissions or mark work complete.', { exact: true })).toBeVisible()
  await capture('05b-unsubmitted-scope-change')
  await page.keyboard.press('Escape')
  await expect(inspector).toHaveCount(0)
  await expect(unsupported).toBeFocused()
  await unsupported.click()
  await inspector.getByRole('button', { name: 'Close request details' }).click()
  await expect(inspector).toHaveCount(0)
  await expect(unsupported).toBeVisible()
  await assertNoToolGrants()

  await page.reload()
  await page.getByRole('button', { name: 'Request queue', exact: true }).click({ timeout: 60_000 })
  await expect(unsupported).toContainText('Pending intervention', { timeout: 60_000 })
  await expect(page.getByRole('combobox', { name: 'Status', exact: true })).toHaveValue('pending_intervention')
  await capture('06-needs-you-after-reload')
  expect(await page.evaluate(key => localStorage.getItem(key), legacyKey)).toBe(legacyBytes)
  await assertNoToolGrants()
  expect(providerErrors).toEqual([])
  expect(stages).toEqual(['request.decompose', 'request.plan'])

  // Cancellation is a durable terminal state, never a successful deployment.
  await unsupported.click()
  await inspector.getByRole('link', { name: 'Open objective', exact: true }).click()
  await expect(page.getByRole('heading', { name: objectiveTitle, exact: true })).toBeVisible()
  await expect(page.getByText('Unsubmitted scope change must not replace the deployment requirement.', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Cancel objective', exact: true }).click()
  const objectiveHeader = page.locator('.eid-page-header').filter({ has: page.getByRole('heading', { name: objectiveTitle, exact: true }) })
  await expect(objectiveHeader).toContainText('Cancelled')
  await expect(unsupported).toContainText('Cancelled')
  await expect(page.getByRole('button', { name: 'Cancel objective', exact: true })).toHaveCount(0)
  await expect(page.getByText('No accepted final result yet.', { exact: true })).toBeVisible()
  await capture('07-cancelled-objective')
  await page.reload()
  await expect(objectiveHeader).toContainText('Cancelled', { timeout: 60_000 })
  await expect(unsupported).toContainText('Cancelled')
  await assertNoToolGrants()
  await openOrganizationRequests(page)
  await expect(page.getByText('Nothing needs your input', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Request queue', exact: true }).click()
  await page.getByRole('combobox', { name: 'Status', exact: true }).selectOption('cancelled')
  await expect(unsupported).toContainText('Cancelled')
  await capture('08-cancelled-request-history')
  expect(await page.evaluate(key => localStorage.getItem(key), legacyKey)).toBe(legacyBytes)
  expect(providerErrors).toEqual([])
  expect(stages).toEqual(['request.decompose', 'request.plan'])
})
