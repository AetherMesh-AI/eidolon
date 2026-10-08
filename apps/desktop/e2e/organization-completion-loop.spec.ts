/** Actual Electron → gateway → durable ledger → loopback HTTP provider. */
import { createHash } from 'node:crypto'

import type { Page } from '@playwright/test'

import {
  amendedCriterion,
  amendedScope,
  budgetTitle,
  completionTitle,
  originalScope,
  recommendation,
  setupCompletionFixture
} from './organization-completion-fixture'
import { expect, test } from './test'

const projectTests = 'Project tests (requires external verification when unavailable)'
let running: Awaited<ReturnType<typeof setupCompletionFixture>> | undefined

test.setTimeout(360_000)

function primary(page: Page) {
  return page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })
}

function objectiveHeader(page: Page, title: string) {
  return page.locator('.eid-page-header').filter({ has: page.getByRole('heading', { name: title, exact: true }) })
}

async function openObjective(page: Page, title: string) {
  await primary(page).getByRole('link', { name: 'Objectives', exact: true }).click()
  // The real row's accessible name also includes its description and status.
  // Match its exact title child without treating that extra context as absence.
  await page
    .getByRole('link')
    .filter({ has: page.getByText(title, { exact: true }) })
    .first()
    .click()
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
}

async function createObjective(page: Page, title: string, requireTests: boolean) {
  await primary(page).getByRole('link', { name: 'Command', exact: true }).click()
  await page.getByRole('textbox', { name: 'Objective', exact: true }).fill(title)
  await page.getByRole('textbox', { name: 'Submitted context (optional)', exact: true }).fill(originalScope)
  await page
    .getByRole('textbox', { name: 'Acceptance criteria', exact: true })
    .fill(
      requireTests
        ? 'The evidence-backed recommendation includes successful project-test verification.'
        : amendedCriterion
    )
  await page.getByRole('combobox', { name: 'Delivery scope', exact: true }).selectOption('managed_artifact')

  if (requireTests) {
    await page.getByRole('checkbox', { name: projectTests, exact: true }).check()
  }

  await page.getByRole('button', { name: 'Create objective', exact: true }).click()
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
}

interface RecordedResolution {
  url: string
  params: Record<string, unknown>
}

async function organizationRequest<T>(page: Page, recorded: RecordedResolution, method: string) {
  const destination = new URL(recorded.url)
  expect(destination.protocol).toBe('ws:')
  expect(['127.0.0.1', 'localhost', '[::1]']).toContain(destination.hostname)

  // Read durable receipts and replay the UI-produced owner resolution over
  // real local transport, without mocking RPC or changing renderer state.
  return page.evaluate(
    async ({ url, params, method }) =>
      new Promise<{ error?: unknown; result?: T }>((resolve, reject) => {
        const socket = new WebSocket(url)
        const id = `e2e-${method}`

        const timeout = setTimeout(() => {
          socket.close()
          reject(new Error('Organization request timed out'))
        }, 15_000)

        socket.addEventListener(
          'error',
          () => {
            clearTimeout(timeout)
            socket.close()
            reject(new Error('Organization request connection failed'))
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
    { ...recorded, method }
  )
}

// eslint-disable-next-line no-empty-pattern -- actual Electron lifecycle belongs to this spec
test.afterEach(async ({}, testInfo) => {
  if (!running) {
    return
  }

  try {
    await testInfo.attach('completion-provider-receipts', {
      body: JSON.stringify(
        { stages: running.stages, providerErrors: running.providerErrors, status: testInfo.status },
        null,
        2
      ),
      contentType: 'application/json'
    })

    if (!running.fixture.page.isClosed()) {
      const uiState = await running.fixture.page.locator('body').ariaSnapshot()
      await testInfo.attach('completion-ui-state', { body: uiState, contentType: 'text/plain' })

      if (testInfo.status !== testInfo.expectedStatus) {
        // Capture before closing Electron; reporter-generated error context
        // runs later and cannot inspect a renderer already torn down here.
        console.error(`Completion failure state (${testInfo.title}):\n${uiState}`)
      }

      await running.fixture.page.screenshot({ path: testInfo.outputPath('native-completion-final-state.png') })
    }
  } finally {
    await running.fixture.cleanup()
    running = undefined
  }
})

// eslint-disable-next-line no-empty-pattern -- actual Electron lifecycle belongs to this spec
test('requires explicit check replacement, retains its audit, and completes only after fresh independent acceptance', async ({}, testInfo) => {
  running = await setupCompletionFixture(false)
  let { page } = running.fixture
  const { stages, providerErrors } = running
  const ownerWrites: RecordedResolution[] = []
  page.on('websocket', socket =>
    socket.on('framesent', frame => {
      const request = JSON.parse(String(frame.payload)) as { method?: string; params: Record<string, unknown> }

      if (request.method === 'organization.resolve') {
        ownerWrites.push({ url: socket.url(), params: request.params })
      }
    })
  )
  // Observe the real transport established by this reload, including the exact
  // owner write later used to test a lost-response replay.
  await page.reload()
  await expect(primary(page)).toBeVisible({ timeout: 60_000 })
  await createObjective(page, completionTitle, true)
  const header = objectiveHeader(page, completionTitle)
  const acceptance = page.getByRole('region', { name: 'Final acceptance', exact: true })
  const blocked = page.getByRole('button', { name: 'Inspect request: request.project_test', exact: true })
  await expect(blocked).toContainText('Project execution requires', { timeout: 90_000 })
  await expect(blocked).toContainText('Pending intervention')
  await expect(header).not.toContainText('Completed')
  await expect(acceptance.getByText(projectTests, { exact: true })).toBeVisible()
  await expect(acceptance.getByText('No accepted final result yet.', { exact: true })).toBeVisible()
  expect(stages.map(stage => stage.kind)).toEqual(['request.decompose', 'request.plan', 'work.draft', 'request.review'])
  expect(stages.every(stage => stage.requiredChecks.includes('project_tests'))).toBe(true)
  const packages = page.getByRole('region', { name: 'Manager work packages', exact: true })
  await expect(packages.getByText('Completed', { exact: true })).toBeVisible()
  await expect(packages).toContainText('Responsible manager')
  await expect(packages).toContainText('Executive delegation')
  await expect(header).not.toContainText('Completed')
  await page.screenshot({ path: testInfo.outputPath('01-unverified-model-approval-blocked.png') })
  await primary(page).getByRole('link', { name: 'Objectives', exact: true }).click()
  await expect(
    page.getByRole('region', { name: 'Outcomes inbox', exact: true }).getByText('No outcomes yet', { exact: true })
  ).toBeVisible()
  await expect(page.getByRole('region', { name: 'Outcomes inbox', exact: true }).getByRole('listitem')).toHaveCount(0)
  await primary(page)
    .getByRole('link', { name: /^Needs You/ })
    .click()
  // Attention rows show the blocker reason, not the request-queue status label.
  await expect(blocked).toContainText(
    'Project execution requires an independently reviewed inspection or edit by an explicitly granted worker'
  )
  await openObjective(page, completionTitle)

  await page.getByRole('button', { name: 'Inspect request: request.review', exact: true }).click()
  const inspector = page.getByRole('complementary', { name: 'Request details' })
  await expect(inspector.getByRole('region', { name: 'Model and evidence audit', exact: true })).toHaveCount(0)
  await inspector.getByRole('button', { name: 'Inspect model and evidence audit', exact: true }).click()
  const executionAudit = inspector.getByRole('region', { name: 'Model and evidence audit', exact: true })

  const contextRecord = executionAudit
    .getByRole('region', { name: 'Context records', exact: true })
    .locator('[data-selectable-text="true"]')

  await expect(contextRecord).toHaveCount(1)

  const report = JSON.parse(await contextRecord.innerText()) as {
    status: string
    fullEvidence: boolean
    artifacts: Array<{ id: string; sha256: string }>
  }

  expect(report.status).toBe('complete')
  expect(report.fullEvidence).toBe(true)
  expect(report.artifacts.length).toBeGreaterThan(0)
  expect(report.artifacts.every(artifact => artifact.id && /^[a-f0-9]{64}$/.test(artifact.sha256))).toBe(true)
  await expect(executionAudit.getByRole('region', { name: 'Model call reservations', exact: true })).toContainText(
    'mock-model'
  )
  await expect(header).not.toContainText('Completed')
  await testInfo.attach('blocked-acceptance-context-receipt', {
    body: JSON.stringify(report, null, 2),
    contentType: 'application/json'
  })
  await inspector.getByRole('button', { name: 'Close request details', exact: true }).click()
  await blocked.click()
  await inspector.getByRole('combobox', { name: 'Action', exact: true }).selectOption('amend_scope')
  const verification = inspector.getByRole('combobox', { name: 'Verification for amended scope', exact: true })
  await expect(verification).toHaveValue('keep')
  await expect(inspector.getByRole('region', { name: 'Current required verification', exact: true })).toContainText(
    projectTests
  )
  await verification.selectOption('replace')
  await inspector.getByRole('checkbox', { name: projectTests, exact: true }).uncheck()
  await inspector.getByRole('textbox', { name: 'Response', exact: true }).fill(amendedScope)
  await inspector.getByRole('textbox', { name: 'Acceptance criteria', exact: true }).fill(amendedCriterion)
  // Dismissing a filled amendment must not mutate authority or its audit.
  await page.keyboard.press('Escape')
  await expect(inspector).toHaveCount(0)
  await expect(blocked).toBeFocused()
  await expect(acceptance.getByText(projectTests, { exact: true })).toBeVisible()
  await page.reload()
  await expect(blocked).toContainText('Pending intervention', { timeout: 60_000 })
  expect(stages).toHaveLength(4)
  await blocked.click()
  await inspector.getByRole('combobox', { name: 'Action', exact: true }).selectOption('amend_scope')
  await expect(verification).toHaveValue('keep')
  await expect(inspector.getByRole('textbox', { name: 'Response', exact: true })).toBeEmpty()
  await verification.selectOption('replace')
  await expect(inspector.getByRole('checkbox', { name: projectTests, exact: true })).toBeChecked()
  await inspector.getByRole('checkbox', { name: projectTests, exact: true }).uncheck()
  await inspector.getByRole('textbox', { name: 'Response', exact: true }).fill(amendedScope)
  await inspector.getByRole('textbox', { name: 'Acceptance criteria', exact: true }).fill(amendedCriterion)
  await expect(inspector).toContainText('It does not run or pass any test.')
  await page.screenshot({ path: testInfo.outputPath('02-explicit-verification-replacement.png') })
  await inspector.getByRole('button', { name: 'Submit response', exact: true }).click()
  await expect.poll(() => stages.filter(stage => stage.kind === 'request.plan').length).toBe(2)
  await expect(inspector.getByRole('button', { name: 'Submit response', exact: true })).toHaveCount(0)

  // A successful snapshot can unmount the response form before its promise
  // closes the inspector, leaving the cancelled original request as a receipt.
  // Dismiss that read-only receipt explicitly, as in the typed-request flow.
  if (await inspector.isVisible()) {
    await expect(inspector).toContainText('Cancelled')
    await inspector.getByRole('button', { name: 'Close request details', exact: true }).click()
  }

  await expect(inspector).toHaveCount(0)
  const revisedPlan = stages[stages.length - 1]
  expect(revisedPlan.scope).toBe(amendedScope)
  expect(revisedPlan.requiredChecks).toEqual([])
  expect(revisedPlan.acceptanceCriteria).toEqual([amendedCriterion])
  await expect(header).not.toContainText('Completed')
  await expect(acceptance.getByText('No accepted final result yet.', { exact: true })).toBeVisible()
  await expect(acceptance.getByText(projectTests, { exact: true })).toHaveCount(0)

  const history = page.locator('.eid-owner-history')
  await history.locator('summary').click()
  await expect(history.locator('summary')).toContainText('(1)')
  const before = history.getByRole('region', { name: 'Before amendment', exact: true })
  const after = history.getByRole('region', { name: 'After amendment', exact: true })
  await expect(before).toContainText(originalScope)
  await expect(before).toContainText(projectTests)
  await expect(after).toContainText(amendedScope)
  await expect(after).toContainText(amendedCriterion)
  await expect(after).toContainText('No explicit verification requirements')
  const audit = await history.innerText()
  expect(audit).toMatch(/Receipt SHA-256: [a-f0-9]{64}/)
  await history.scrollIntoViewIfNeeded()
  await page.screenshot({ path: testInfo.outputPath('03-amendment-audit-before-completion.png') })
  await page.reload()
  await expect(header).toBeVisible({ timeout: 60_000 })
  await history.locator('summary').click()
  await expect(history).toHaveText(audit, { useInnerText: true })
  await expect(header).not.toContainText('Completed')
  expect(stages).toHaveLength(6)
  expect(ownerWrites).toHaveLength(1)
  expect(ownerWrites[0].params.requiredChecks).toEqual([])
  expect(ownerWrites[0].params.acceptanceCriteria).toEqual([amendedCriterion])
  expect(ownerWrites[0].params.idempotencyKey).toEqual(expect.any(String))
  const replay = await organizationRequest<{
    objectives: Array<{ title: string; status: string; ownerResolutions: unknown[] }>
  }>(page, ownerWrites[0], 'organization.resolve')
  expect(replay.error).toBeUndefined()
  const replayed = replay.result?.objectives.find(objective => objective.title === completionTitle)
  expect(replayed?.ownerResolutions).toHaveLength(1)
  expect(replayed?.status).not.toBe('completed')
  await expect(history).toHaveText(audit, { useInnerText: true })
  expect(stages).toHaveLength(6)
  // Finish via the real provider while the owner is composing elsewhere.
  // Poll the real gateway receipt, never seed terminal state or intercept RPC.
  await primary(page).getByRole('link', { name: 'Command', exact: true }).click()
  const nextObjective = page.getByRole('textbox', { name: 'Objective', exact: true })
  await nextObjective.fill('Keep my foreground draft')
  await nextObjective.focus()
  const foregroundUrl = page.url()
  running.releasePlan()
  interface OutcomePage {
    items: Array<{
      title: string
      status: string
      seen: boolean
      deliverableId: string
      acceptanceRequestId: string
      evidenceIds: string[]
    }>
    unread: number
    total: number
  }
  const readOutcomes = () =>
    organizationRequest<OutcomePage>(page, { url: ownerWrites[0].url, params: {} }, 'organization.outcomes')
  await expect
    .poll(
      async () => {
        const receipt = await readOutcomes()
        expect(receipt.error).toBeUndefined()
        return receipt.result?.items.find(item => item.title === completionTitle)?.status
      },
      { timeout: 90_000 }
    )
    .toBe('accepted')
  expect(page.url()).toBe(foregroundUrl)
  await expect(nextObjective).toHaveValue('Keep my foreground draft')
  await expect(nextObjective).toBeFocused()
  await expect(primary(page).getByLabel('Unread outcomes: 1', { exact: true })).toBeVisible()
  const outcomes = (await readOutcomes()).result!
  expect(outcomes.unread).toBe(1)
  expect(outcomes.total).toBe(1)
  expect(outcomes.items[0]).toMatchObject({ seen: false, status: 'accepted' })
  expect(outcomes.items[0].deliverableId).toBeTruthy()
  expect(outcomes.items[0].acceptanceRequestId).toBeTruthy()
  expect(outcomes.items[0].evidenceIds.length).toBeGreaterThan(0)
  await testInfo.attach('durable-accepted-outcome-before-owner-return', {
    body: JSON.stringify(outcomes, null, 2),
    contentType: 'application/json'
  })
  await openObjective(page, completionTitle)
  await history.locator('summary').click()

  await expect(header).toContainText('Completed', { timeout: 90_000 })
  await packages.getByText('Previous-round packages (1)', { exact: true }).click()
  await expect(packages.getByRole('list', { name: 'Previous-round packages', exact: true })).toContainText('Completed')
  await expect(packages.getByRole('list', { name: 'Current round', exact: true })).toContainText('Completed')
  await expect(acceptance.getByText(recommendation, { exact: true })).toBeVisible()
  await expect(history).toHaveText(audit, { useInnerText: true })
  expect(providerErrors).toEqual([])
  const firstRound = stages.slice(0, 4)
  const secondRound = stages.slice(4)
  expect(secondRound.map(stage => stage.kind)).toEqual([
    'request.decompose',
    'request.plan',
    'work.draft',
    'request.review',
    'request.integrate',
    'request.accept'
  ])
  expect(secondRound.slice(0, 4).map(stage => stage.agentId)).toEqual(firstRound.map(stage => stage.agentId))
  expect(firstRound[2].agentId).toBe('worker-1')
  expect(firstRound[3].agentId).not.toBe(firstRound[2].agentId)
  expect(secondRound[5].agentId).not.toBe(secondRound[4].agentId)
  expect(secondRound.every(stage => stage.requiredChecks.length === 0)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('04-independently-accepted-revised-scope.png') })
  await page.reload()
  await expect(header).toContainText('Completed', { timeout: 60_000 })
  await history.locator('summary').click()
  await expect(history).toHaveText(audit, { useInnerText: true })
  expect(stages).toHaveLength(10)

  const inbox = () => page.getByRole('region', { name: 'Outcomes inbox', exact: true })
  const receipt = () => inbox().getByRole('listitem', { name: completionTitle, exact: true })
  const openInbox = async () => {
    await primary(page).getByRole('link', { name: 'Objectives', exact: true }).click()
    await expect(receipt()).toContainText('Accepted')
  }
  const inspectDeliverable = async () => {
    await receipt().getByRole('link', { name: completionTitle, exact: true }).click()
    await expect(objectiveHeader(page, completionTitle)).toContainText('Completed')
    await page.getByRole('button', { name: 'Read final deliverable', exact: true }).click()
    const artifact = page.getByRole('complementary', { name: 'Artifact details', exact: true })
    await expect(artifact.locator('p.eid-result-text').first()).toHaveText(recommendation)
    await expect(artifact.locator('dd.eid-result-text')).toHaveText(
      createHash('sha256').update(recommendation).digest('hex')
    )
    await artifact.getByRole('button', { name: 'Close artifact details', exact: true }).click()
  }
  await openInbox()
  await expect(receipt().getByRole('button', { name: 'Mark seen', exact: true })).toBeVisible()
  await inspectDeliverable()
  await openInbox()
  // Merely reading the retained result is not acknowledgement.
  await expect(receipt().getByRole('button', { name: 'Mark seen', exact: true })).toBeVisible()
  await page.reload()
  await expect(receipt().getByRole('button', { name: 'Mark seen', exact: true })).toBeVisible({ timeout: 60_000 })
  await running.restart()
  page = running.fixture.page
  await openInbox()
  await expect(receipt().getByRole('button', { name: 'Mark seen', exact: true })).toBeVisible()
  await inspectDeliverable()
  const acceptedBeforeSeen = await page.getByRole('region', { name: 'Final acceptance', exact: true }).innerText()
  await openInbox()
  await receipt().getByRole('button', { name: 'Mark seen', exact: true }).click()
  await expect(receipt().getByRole('button', { name: 'Seen', exact: true })).toBeVisible()
  await expect(primary(page).getByLabel('Unread outcomes: 1', { exact: true })).toHaveCount(0)
  await inbox().getByRole('button', { name: 'Unread', exact: true }).click()
  await expect(inbox().getByText('No unread outcomes', { exact: true })).toBeVisible()
  await expect(receipt()).toHaveCount(0)
  await inbox().getByRole('button', { name: 'All outcomes', exact: true }).click()
  await inspectDeliverable()
  await expect(page.getByRole('region', { name: 'Final acceptance', exact: true })).toHaveText(acceptedBeforeSeen, {
    useInnerText: true
  })
  await page.getByRole('button', { name: 'Archive objective', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Restore to current history', exact: true })).toBeVisible()
  await openInbox()
  await expect(receipt()).toContainText('Archived history')
  await expect(receipt().getByRole('button', { name: 'Seen', exact: true })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('05-seen-archived-outcome.png') })
  await running.restart()
  page = running.fixture.page
  await openInbox()
  await expect(receipt()).toContainText('Archived history')
  await expect(receipt().getByRole('button', { name: 'Seen', exact: true })).toBeVisible()
  await inspectDeliverable()
  await expect(page.getByRole('region', { name: 'Final acceptance', exact: true })).toHaveText(acceptedBeforeSeen, {
    useInnerText: true
  })
  await primary(page)
    .getByRole('link', { name: /^Needs You/ })
    .click()
  await expect(page.getByText('Nothing needs your input', { exact: true })).toBeVisible()
  expect(stages).toHaveLength(10)
  expect(providerErrors).toEqual([])
})

// eslint-disable-next-line no-empty-pattern -- actual Electron lifecycle belongs to this spec
test('retains a visible model-call intervention across full process restart and cancellation', async ({}, testInfo) => {
  running = await setupCompletionFixture(true)
  let { page } = running.fixture
  await createObjective(page, budgetTitle, false)
  const reason = 'Objective model-call budget exhausted. Automatic execution has stopped.'

  const assertBlocked = async () => {
    await expect(objectiveHeader(page, budgetTitle)).not.toContainText('Completed')
    const acceptance = page.getByRole('region', { name: 'Final acceptance', exact: true })
    await expect(acceptance.getByText('No accepted final result yet.', { exact: true })).toBeVisible()
    const details = acceptance.locator('details')

    if (!(await details.evaluate(element => element.hasAttribute('open')))) {
      await details.locator('summary').click()
    }

    await expect(details.locator('dt').filter({ hasText: 'Model calls reserved / limit' }).locator('+ dd')).toHaveText(
      '1 / 1'
    )
    await expect(details).toContainText(
      'Durable reservations include interrupted or unreported model calls and are never refunded.'
    )
    await details.scrollIntoViewIfNeeded()
    expect(running!.stages.map(stage => stage.kind)).toEqual(['request.decompose'])
    expect(running!.providerErrors).toEqual([])

    return details.locator('time').getAttribute('datetime')
  }

  let blocked = page.getByRole('button', { name: 'Inspect request: request.plan', exact: true })
  await expect(blocked).toContainText(reason, { timeout: 90_000 })
  const deadline = await assertBlocked()
  expect(deadline).toBeTruthy()
  await page.screenshot({ path: testInfo.outputPath('01-model-call-limit-stopped-dispatch.png') })
  await primary(page)
    .getByRole('link', { name: /^Needs You/ })
    .click()
  await blocked.click()
  await expect(page.getByRole('complementary', { name: 'Request details' })).toContainText(reason)
  await expect(page.getByRole('option', { name: 'Retry after fixing configuration', exact: true })).toHaveCount(0)
  await page.keyboard.press('Escape')

  // This closes Electron AND its owned gateway. Reopening only the renderer
  // would not prove that the budget survives loss of the scheduler process.
  await running.restart()
  page = running.fixture.page
  await openObjective(page, budgetTitle)
  blocked = page.getByRole('button', { name: 'Inspect request: request.plan', exact: true })
  await expect(blocked).toContainText(reason)
  expect(await assertBlocked()).toBe(deadline)
  await page.screenshot({ path: testInfo.outputPath('02-budget-survives-electron-restart.png') })
  await page.getByRole('button', { name: 'Cancel objective', exact: true }).click()
  await expect(objectiveHeader(page, budgetTitle)).toContainText('Cancelled')
  await expect(blocked).toContainText('Cancelled')
  await page.reload()
  await expect(objectiveHeader(page, budgetTitle)).toContainText('Cancelled', { timeout: 60_000 })
  expect(await assertBlocked()).toBe(deadline)
  await primary(page)
    .getByRole('link', { name: /^Needs You/ })
    .click()
  await expect(page.getByText('Nothing needs your input', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Request queue', exact: true }).click()
  await page.getByRole('combobox', { name: 'Status', exact: true }).selectOption('cancelled')
  await expect(blocked).toContainText('Cancelled')
  await page.screenshot({ path: testInfo.outputPath('03-cancelled-budget-request-history.png') })
  await primary(page).getByRole('link', { name: 'Objectives', exact: true }).click()
  const cancelled = page
    .getByRole('region', { name: 'Outcomes inbox', exact: true })
    .getByRole('listitem', { name: budgetTitle, exact: true })
  await expect(cancelled).toContainText('Cancelled')
  await expect(cancelled).not.toContainText('Accepted')
  await cancelled.getByRole('button', { name: 'Mark seen', exact: true }).click()
  await expect(cancelled.getByRole('button', { name: 'Seen', exact: true })).toBeVisible()
  await cancelled.getByRole('link', { name: budgetTitle, exact: true }).click()
  await expect(objectiveHeader(page, budgetTitle)).toContainText('Cancelled')
  expect(await assertBlocked()).toBe(deadline)
  expect(running.stages.map(stage => stage.kind)).toEqual(['request.decompose'])
})
