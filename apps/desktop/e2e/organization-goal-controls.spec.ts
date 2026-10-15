/** Actual RPC/durable SQLite owner proof; scheduler dispatch is deliberately disabled. */
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import type { Page } from '@playwright/test'
import { setupGoalNativeFixture } from './organization-goal-native-fixture'
import { acknowledgeExpectedErrorBanner, expect, test } from './test'
let running: Awaited<ReturnType<typeof setupGoalNativeFixture>> | undefined
test.setTimeout(240_000)
test.afterEach(async () => { await running?.fixture.cleanup(); running = undefined })
const confirmation = 'I reviewed this exact action and its limits.'
async function open(page: Page) {
  await page.evaluate(() => { location.hash = '/organization' })
  await expect(page.getByRole('heading', { name: 'Organization', exact: true })).toBeVisible()
  const goals = page.locator('details').filter({ has: page.locator('summary', { hasText: /^Goal budgets and continuation$/ }) })
  await goals.locator('summary').click()
  await goals.getByRole('button', { name: 'Load goal controls', exact: true }).click()
  await expect(goals.getByLabel('Goal ID', { exact: true })).toBeVisible()
  return goals
}
// eslint-disable-next-line no-empty-pattern -- real fixture lifecycle is managed here
test('persists finite owner controls across actual backend restart without dispatch', async ({}, info) => {
  running = await setupGoalNativeFixture()
  let { page } = running.fixture
  const writes: Array<{ method: string; params: Record<string, unknown> }> = []
  let loseAllowanceReply = true; let staleOwner = false; let descriptors = 0
  let disconnect: (() => Promise<void>) | undefined
  await page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer(); const pending = new Map<number, string>()
    disconnect = () => socket.close({ code: 1012, reason: 'Synthetic owner reconnect' })
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as { id: number; method: string; params: Record<string, unknown> }
      if (request.method === 'organization.descriptor') { pending.set(request.id, request.method) }
      if (['organization.configureGoal','organization.approveGoalContinuation','organization.setGoalContinuationEnabled'].includes(request.method)) {
        writes.push({ method: request.method, params: structuredClone(request.params) }); pending.set(request.id, request.method)
      }
      if (staleOwner && request.method === 'organization.goals') {
        request.params.expectedOwnerId = 'stale-owner-fixture'; server.send(JSON.stringify(request)); return
      }
      server.send(message)
    })
    server.onMessage(message => {
      const reply = JSON.parse(message.toString()) as { id: number; result?: unknown; error?: unknown }
      if (pending.get(reply.id) === 'organization.descriptor' && reply.result) { descriptors += 1 }
      if (loseAllowanceReply && pending.get(reply.id) === 'organization.configureGoal' && reply.result) {
        loseAllowanceReply = false
        socket.send(JSON.stringify({ jsonrpc: '2.0', id: reply.id, error: { code: 5070, message: 'Fixture lost goal acknowledgement' } })); return
      }
      socket.send(message)
    })
  })
  await page.reload()
  let goals = await open(page)
  const milestones = page.locator('details').filter({ has: page.locator('summary', { hasText: /^Project milestones$/ }) })
  await milestones.locator('summary').click()
  await milestones.getByRole('button', { name: 'Load milestones', exact: true }).click()
  await milestones.getByLabel('Milestone ID', { exact: true }).fill('native-milestone')
  await milestones.getByLabel('Title', { exact: true }).fill('Bounded planning milestone')
  await milestones.getByLabel('Existing bounded planning objective · planning', { exact: true }).check()
  await milestones.getByLabel('I reviewed the linked objectives and prerequisites.', { exact: true }).check()
  await milestones.getByRole('button', { name: 'Review and save milestone', exact: true }).click()
  await expect(milestones.getByRole('button', { name: 'Bounded planning milestone · In progress', exact: true })).toBeVisible()
  expect(running.ledger().organization_milestone_revisions).toHaveLength(1)
  await goals.getByLabel('Goal ID', { exact: true }).fill('native-goal')
  await goals.getByLabel('Existing bounded planning objective', { exact: true }).check()
  for (const [label,value] of [['Model-call ceiling','12'],['Reserved-token ceiling','24000'],['Stage ceiling','24'],['Project-run ceiling','0']]) {
    await goals.getByLabel(label, { exact: true }).first().fill(value)
  }
  const reserve = goals.getByRole('group', { name: 'Protected completion reserve', exact: true })
  await reserve.getByLabel('Model-call ceiling', { exact: true }).fill('2')
  await reserve.getByLabel('Reserved-token ceiling', { exact: true }).fill('4000')
  const deadlineText = await page.evaluate(() => { const future = new Date(Date.now() + 172800000); return new Date(future.getTime() - future.getTimezoneOffset()*60000).toISOString().slice(0,16) })
  const deadline = await page.evaluate(value => Math.floor(new Date(value).getTime() / 1000), deadlineText)
  await goals.getByLabel('Deadline', { exact: true }).fill(deadlineText)
  await goals.getByLabel(confirmation, { exact: true }).check()
  await goals.getByRole('button', { name: 'Save goal allowance', exact: true }).click()
  await expect(goals.getByRole('alert')).toContainText('Fixture lost goal acknowledgement')
  expect(running.ledger().organization_goal_revisions).toHaveLength(1)
  const priorDescriptors = descriptors
  await disconnect!()
  await expect.poll(() => descriptors).toBeGreaterThan(priorDescriptors)
  await goals.getByRole('button', { name: 'Check pending receipt', exact: true }).click()
  await expect(goals.getByRole('button', { name: 'Check pending receipt', exact: true })).toHaveCount(0)
  expect(writes.filter(w => w.method === 'organization.configureGoal')).toHaveLength(1)
  await milestones.getByRole('button', { name: 'Load milestones', exact: true }).click()
  await acknowledgeExpectedErrorBanner(page, 'Fixture lost goal acknowledgement')
  await acknowledgeExpectedErrorBanner(page, 'The owner connection changed. Load milestones to open the current organization.')
  await goals.getByLabel('Goal ID', { exact: true }).fill('native-goal')
  for (const number of [1,2]) {
    await goals.getByLabel('Step title', { exact: true }).fill(`Bounded step ${number}`)
    await goals.getByLabel('Exact work instructions', { exact: true }).fill(`Produce synthetic deliverable ${number}.`)
    await goals.getByLabel('Acceptance criteria (one per line)', { exact: true }).fill(`Deliverable ${number} is independently reviewed.`)
    await goals.getByRole('button', { name: 'Add finite step', exact: true }).click()
  }
  await goals.getByRole('button', { name: 'Review continuation plan', exact: true }).click()
  const approve = goals.getByRole('button', { name: 'Approve plan, keep disabled', exact: true })
  await expect(approve).toBeDisabled(); await goals.getByLabel(confirmation, { exact: true }).check(); await approve.click()
  // A click only dispatches the async RPC. Observe its committed projection before reading SQLite.
  await expect(goals.getByRole('button', { name: 'Enable approved continuation', exact: true })).toBeVisible()
  let state = running.ledger()
  expect(state.organization_continuation_controls.map(row => row.enabled)).toEqual([0])
  expect(state.organization_continuation_plans).toHaveLength(1)
  const record = JSON.parse(String(state.organization_goal_revisions[0].record))
  expect(record.limits).toEqual({ modelCalls: 12, tokens: 24000, stages: 24, projectRuns: 0, costUsd: null,
    deadlineTimestamp: deadline, completionReserve: { modelCalls: 2, tokens: 4000, costUsd: null } })
  const plan = JSON.parse(String(state.organization_continuation_plans[0].record))
  expect(plan.templates).toHaveLength(2); expect(plan.templates[1].templateDependencyIds).toEqual(['step-1']); expect(plan.deadlineTimestamp).toBe(deadline)
  await expect(goals.getByLabel('Model-call ceiling', { exact: true }).first()).toHaveValue('12')
  await expect(goals.getByLabel('Deadline', { exact: true })).toHaveValue(deadlineText)
  await goals.getByLabel(confirmation, { exact: true }).check()
  await goals.getByRole('button', { name: 'Enable approved continuation', exact: true }).click()
  await expect(goals.getByRole('button', { name: 'Disable continuation', exact: true })).toBeVisible()
  await goals.getByLabel(confirmation, { exact: true }).check()
  await goals.getByRole('button', { name: 'Disable continuation', exact: true }).click()
  await expect(goals.getByRole('button', { name: 'Enable approved continuation', exact: true })).toBeVisible()
  state = running.ledger()
  expect(state.organization_continuation_controls.map(row => row.enabled)).toEqual([0,1,0])
  expect(state.organization_continuation_receipts).toHaveLength(3)
  expect(state.organization_continuation_launches).toHaveLength(0)
  expect(state.attempts).toBe(0); expect(state.modelCalls).toBe(0); expect(running.fixture.mock.receivedPrompts).toHaveLength(0)
  const beforeStale = JSON.stringify(state); staleOwner = true
  await goals.getByRole('button', { name: 'Load goal controls', exact: true }).click()
  await expect(goals.getByRole('alert')).toContainText('Goal organization or owner changed')
  expect(JSON.stringify(running.ledger())).toBe(beforeStale); staleOwner = false
  await goals.getByRole('button', { name: 'Load goal controls', exact: true }).click()
  await expect(goals.getByRole('alert')).toHaveCount(0)
  await acknowledgeExpectedErrorBanner(page, 'Goal organization or owner changed')
  const oldPid = running.backendPid(); await running.restart(); page = running.fixture.page
  await expect.poll(() => running!.backendPid()).not.toBe(oldPid)
  goals = await open(page); await goals.getByLabel('Goal ID', { exact: true }).fill('native-goal')
  await expect(goals.getByRole('button', { name: 'Enable approved continuation', exact: true })).toBeVisible()
  expect(running.ledger()).toEqual(state); expect(running.fixture.mock.receivedPrompts).toHaveLength(0)
  await page.screenshot({ path: info.outputPath('goal-owner-controls-after-restart.png') })
  await info.attach('goal-owner-durable-evidence', { contentType: 'application/json', body: JSON.stringify({
    sourceCommit: execFileSync('git', ['rev-parse','HEAD'], { encoding:'utf8' }).trim(), dispatchDisabled: true,
    workerAttempts: state.attempts, providerPrompts: running.fixture.mock.receivedPrompts.length,
    ledgerSha256: createHash('sha256').update(JSON.stringify(state)).digest('hex'), controlRevisions: state.organization_continuation_controls,
    writes, backendRestarted: true }) })
})
