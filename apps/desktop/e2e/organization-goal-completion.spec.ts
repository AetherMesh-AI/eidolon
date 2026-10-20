/** Native owner RPC → scheduler → loopback provider → independent acceptance. */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import path from 'node:path'

import type { Page } from '@playwright/test'

import { amendedCriterion, recommendation } from './organization-completion-fixture'
import { goalTitles, type Ledger, setupGoalCompletionFixture } from './organization-goal-completion-fixture'
import { expect, test } from './test'

let running: Awaited<ReturnType<typeof setupGoalCompletionFixture>> | undefined
const checkpoints: unknown[] = []
test.setTimeout(240_000)
interface Goals {
  organizationId: string; ownerId: string
  goals: Array<{ objectiveIds: string[]; reserved: { modelCalls: number; tokens: number; stages: number; projectRuns: number } }>
  plans: Array<{ enabled: boolean; controlRevision: number; sha256?: string; launches: unknown[];
    templateStates: Array<{ id: string; launched: boolean; blockers: string[] }> }>
}
interface Outcome { objectiveId: string; status: string; deliverableId: string; acceptanceRequestId: string; evidenceIds: string[] }

async function rpc<T>(page: Page, url: string, method: string, params: object = {}): Promise<T> {
  expect(new URL(url).hostname).toBe('127.0.0.1')

  const reply = await page.evaluate(({ url, method, params }) => new Promise<{ result?: T; error?: unknown }>((resolve, reject) => {
    const socket = new WebSocket(url)
    const timeout = setTimeout(() => { socket.close(); reject(new Error(`Timed out: ${method}`)) }, 15_000)
    socket.onopen = () => socket.send(JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }))

    socket.onerror = () => { clearTimeout(timeout); socket.close(); reject(new Error(`Connection failed: ${method}`)) }

    socket.onmessage = event => {
      const reply = JSON.parse(String(event.data))

      if (reply.id === 1) { clearTimeout(timeout); socket.close(); resolve(reply) }
    }
  }), { url, method, params })

  expect(reply.error, method).toBeUndefined()
  expect(reply.result, method).toBeDefined()

  return reply.result!
}

async function endpoint(page: Page) {
  const socket = page.waitForEvent('websocket')
  await page.reload()

  return (await socket).url()
}

// eslint-disable-next-line no-empty-pattern -- fixture owns actual Electron lifecycle
test.afterEach(async ({}, info) => {
  if (!running) { return }

  try {
    await info.attach('finite-goal-native-evidence', { contentType: 'application/json', body: JSON.stringify({
      sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
      sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
      sourceDiff: execFileSync('git', ['diff', '--'], { encoding: 'utf8' }),
      status: info.status, checkpoints, stages: running.stages, providerErrors: running.providerErrors, ledger: running.ledger()
    }, null, 2) })

    if (!running.fixture.page.isClosed()) {
      await info.attach('finite-goal-ui', { contentType: 'text/plain', body: await running.fixture.page.locator('body').ariaSnapshot() })
      await running.fixture.page.screenshot({ path: info.outputPath('finite-goal-final.png') })
    }
  } finally { await running.fixture.cleanup(); running = undefined; checkpoints.length = 0 }
})
// eslint-disable-next-line no-empty-pattern -- fixture owns actual Electron lifecycle
test('accepts two approved dependent templates exactly once across a disabled dependency-boundary restart', async ({}) => {
  running = await setupGoalCompletionFixture()
  let { page } = running.fixture
  let url = await endpoint(page)
  const descriptor = await rpc<{ metadata: { organizationId: string; owner: { ownerId: string } } }>(page, url, 'organization.descriptor')
  const identity = { expectedOrganizationId: descriptor.metadata.organizationId, expectedOwnerId: descriptor.metadata.owner.ownerId }
  const owner = <T>(method: string, params: object = {}) => rpc<T>(page, url, method, { ...identity, ...params })
  const goals = () => owner<Goals>('organization.goals')
  const outcomes = () => rpc<{ items: Outcome[] }>(page, url, 'organization.outcomes')
  const deadlineTimestamp = Math.floor(Date.now() / 1000) + 1800
  const goalId = 'finite-native-goal'
  await owner('organization.configureGoal', { goalId, objectiveIds: [running.seedId], limits: {
    modelCalls: 20, tokens: 800000, stages: 20, projectRuns: 0, costUsd: null, deadlineTimestamp,
    completionReserve: { modelCalls: 2, tokens: 65536, costUsd: null }
  }, paused: false, expectedRevision: 0, idempotencyKey: 'finite-allowance', confirmed: true })

  const templates = goalTitles.map((title, index) => ({ id: `step-${index + 1}`, title,
    description: 'Document the supplied regression-risk fact; project tests were not run.', priority: 'normal',
    acceptanceCriteria: [amendedCriterion],
    deliveryMode: 'managed_artifact', requiredChecks: [], projectIds: [], executiveId: 'executive', managerId: 'manager',
    dependencyIds: [], templateDependencyIds: index ? ['step-1'] : [] }))

  const preview = await owner<{ preview: { sha256: string } }>('organization.previewGoalContinuation', { goalId, templates, deadlineTimestamp })
  expect((await goals()).plans).toEqual([])
  const approval = { goalId, templates, deadlineTimestamp, previewSha256: preview.preview.sha256, idempotencyKey: 'finite-approve', confirmed: true }
  const receipt = await owner('organization.approveGoalContinuation', approval)
  expect(await owner('organization.approveGoalContinuation', approval)).toEqual(receipt)
  expect((await goals()).plans[0]).toMatchObject({ enabled: false, controlRevision: 1, launches: [] })

  // Observe a bounded interval spanning many production scheduler ticks, with a spare execution slot.
  const remains = async (launches: number, calls: number) => {
    const until = Date.now() + 2500

    do {
      expect(running!.ledger().launches).toHaveLength(launches)
      expect(running!.stages).toHaveLength(calls)
      await new Promise(resolve => setTimeout(resolve, 250))
    } while (Date.now() < until)
  }

  await remains(0, 0)

  const enable = async (enabled: boolean, revision: number, key: string) => owner('organization.setGoalContinuationEnabled', {
    goalId, enabled, expectedRevision: revision, idempotencyKey: key, confirmed: true
  })

  await enable(true, 1, 'finite-enable')
  await expect.poll(() => running!.stages.filter(stage => stage.kind === 'request.accept').length, { timeout: 90_000 }).toBe(1)
  const waiting = await goals()
  expect(waiting.plans[0].templateStates[1]).toEqual({ id: 'step-2', launched: false, blockers: ['template:step-1'] })
  expect((await outcomes()).items).toEqual([])
  expect(running.ledger().acceptances).toEqual([])
  await remains(1, 6)
  checkpoints.push({ phase: 'dependency-blocked-before-acceptance', waiting, ledger: running.ledger() })
  await enable(false, 2, 'finite-disable-boundary')
  running.releaseAcceptance()
  await expect.poll(async () => (await outcomes()).items[0]?.status, { timeout: 60_000 }).toBe('accepted')
  const before = running.ledger()
  const beforeGoals = await goals()
  const beforeOutcomes = await outcomes()
  expect(before.launches).toHaveLength(1)
  expect(before.acceptances).toHaveLength(1)
  expect(before.acceptances[0].agent_id).not.toBe(before.deliverables[0].author_id)
  expect(before.acceptances[0].approved).toBe(1)
  const ownership = () => JSON.parse(readFileSync(path.join(running!.fixture.sandbox.userDataDir, 'backend-ownership.json'), 'utf8')) as { backends: Array<{ pid: number; nonce: string }> }
  const oldBackend = ownership().backends
  expect(oldBackend).toHaveLength(1)
  checkpoints.push({ phase: 'accepted-disabled-boundary', goals: beforeGoals, outcomes: beforeOutcomes, ledger: before, backend: oldBackend })
  await running.restart()
  page = running.fixture.page; url = await endpoint(page)
  expect(ownership().backends).toHaveLength(1)
  expect(ownership().backends[0].pid).not.toBe(oldBackend[0].pid)
  expect(ownership().backends[0].nonce).not.toBe(oldBackend[0].nonce)
  expect(await goals()).toEqual(beforeGoals)
  expect(await outcomes()).toEqual(beforeOutcomes)
  expect(running.ledger()).toEqual(before)
  await remains(1, 6)
  await enable(true, 3, 'finite-resume')
  await expect.poll(async () => (await outcomes()).items.filter(item => item.status === 'accepted').length, { timeout: 90_000 }).toBe(2)
  const after = running.ledger()
  const finalGoals = await goals()
  const finalOutcomes = await outcomes()
  expect(after.launches.map(item => item.template_id)).toEqual(['step-1', 'step-2'])
  expect(new Set(after.launches.map(item => item.objective_id)).size).toBe(2)
  expect(after.launches[0]).toEqual(before.launches[0])
  expect(after.launches[1].created).toBeGreaterThanOrEqual(before.acceptances[0].created)
  expect(after.calls.slice(0, before.calls.length)).toEqual(before.calls)

  const assertReservations = (ledger: Ledger, projection: Goals) => {
    expect(projection.goals[0].reserved).toMatchObject({ modelCalls: ledger.calls.length,
      tokens: ledger.calls.reduce((sum, call) => sum + call.input_limit + call.output_limit, 0), stages: ledger.usage.length, projectRuns: 0 })
  }

  assertReservations(before, beforeGoals); assertReservations(after, finalGoals)
  expect(finalGoals.goals[0].reserved.tokens).toBeGreaterThan(beforeGoals.goals[0].reserved.tokens)
  expect(finalGoals.goals[0].objectiveIds.sort()).toEqual([running.seedId, ...after.launches.map(item => item.objective_id)].sort())

  for (const launch of after.launches) {
    expect(launch.scope_sha256).toMatch(/^[a-f0-9]{64}$/)
    const acceptance = after.acceptances.filter(item => item.objective_id === launch.objective_id)
    const deliverable = after.deliverables.find(item => item.objective_id === launch.objective_id)!

    expect(acceptance).toHaveLength(1)
    expect(acceptance[0].approved).toBe(1)
    expect(acceptance[0].agent_id).not.toBe(deliverable.author_id)
    const outcome = finalOutcomes.items.find(item => item.objectiveId === launch.objective_id)!
    expect(outcome.status).toBe('accepted'); expect(outcome.acceptanceRequestId).toBeTruthy(); expect(outcome.evidenceIds.length).toBeGreaterThan(0)
    const artifact = await rpc<{ content: string; sha256: string }>(page, url, 'organization.evidence', { id: outcome.deliverableId })
    expect(artifact.content).toBe(recommendation)
    expect(artifact.sha256).toBe(createHash('sha256').update(artifact.content).digest('hex'))

    for (const id of outcome.evidenceIds) {
      const evidence = await rpc<{ content: string; sha256: string }>(page, url, 'organization.evidence', { id })
      expect(evidence.sha256).toBe(createHash('sha256').update(evidence.content).digest('hex'))
    }

    const stages = running.stages.filter(stage => stage.objectiveTitle === goalTitles[launch.template_id === 'step-1' ? 0 : 1])
    expect(stages.map(stage => stage.kind)).toEqual(['request.decompose', 'request.plan', 'work.draft', 'request.review', 'request.integrate', 'request.accept'])
    expect(stages.at(-1)!.agentId).not.toBe(stages.find(stage => stage.kind === 'work.draft')!.agentId)
  }

  // Enabled, exhausted finite plan remains idle, including a second actual process restart.
  await remains(2, 12)
  await running.restart(); page = running.fixture.page; url = await endpoint(page)
  expect(await goals()).toEqual(finalGoals)
  expect(await outcomes()).toEqual(finalOutcomes)
  expect(running.ledger()).toEqual(after)
  await remains(2, 12)
  expect(running.providerErrors).toEqual([])
  checkpoints.push({ phase: 'finite-plan-exhausted-after-restart', goals: finalGoals, outcomes: finalOutcomes, ledger: after })
})
