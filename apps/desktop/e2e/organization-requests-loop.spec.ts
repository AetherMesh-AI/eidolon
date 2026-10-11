/** Real Electron → gateway → persistent organization → deterministic local provider.
 * Staffing is applied by an explicitly scoped manager; an unhandled question
 * reaches the owner and resumes the exact persistent worker after its answer. */
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
import { openOrganizationDisclosure, openOrganizationRequests } from './organization-disclosures'
import { exactOrganizationEvidence, type OrganizationEvidenceContext } from './organization-evidence'
import { organizationPackageProposal } from './organization-package-proposal'
import { organizationProviderTarget } from './organization-provider-target'
import { expect, test } from './test'

const objectiveTitle = 'Write the audience-specific release brief'
const question = 'Who should receive the release brief?'
const answer = 'Release managers and on-call engineers.'
const deliverable = `Release brief for ${answer} The release improves request routing while preserving existing permissions.`

const proposedMember = {
  id: 'release-specialist',
  name: 'Release specialist',
  role: 'Worker',
  manager_id: 'manager',
  team: 'general',
  capabilities: ['work.draft'],
  enabled: true,
  provider: null,
  model: null,
  tool_grants: [],
  responsibilities: ['Draft audience-specific release notes'],
  purpose: 'Release communications',
  scope: 'Supplied release facts',
  authority: [],
  managed_teams: []
}

interface StageContext extends OrganizationEvidenceContext {
  objective: { title: string; acceptanceCriteria: string[] }
  agent: { id: string }
  requestResponses?: Array<{ type: string; response: { text: string; decision: string } }>
}
const stages: Array<{ kind: string; agentId: string; responses: unknown }> = []
const providerErrors: string[] = []
let fixture: MockBackendFixture | null = null

test.setTimeout(240_000)

function resultFor(kind: string, context: StageContext) {
  const responses = context.requestResponses ?? []
  const evidence = exactOrganizationEvidence(context)

  if (
    ['request.review', 'request.integrate', 'request.accept'].includes(kind) &&
    (!evidence.length || evidence.some(item => item.content !== deliverable))
  ) {
    throw new Error('The full audience-specific brief did not reach its reviewer or integrator')
  }

  const evidenceIds = evidence.map(item => item.id)

  const handlers: Record<string, () => unknown> = {
    'request.decompose': () => organizationPackageProposal(context, 1),
    'request.plan': () =>
      responses.some(item => item.type === 'request.hire' && item.response.decision === 'approved')
        ? {
            tasks: [
              {
                title: 'Draft the release brief',
                description: 'Use supplied facts and ask the owner for the intended audience.',
                type: 'work.draft',
                team: 'general',
                agentId: proposedMember.id,
                managerId: 'manager',
                dependsOn: []
              }
            ],
            workers: 1
          }
        : {
            requests: [
              {
                type: 'request.hire',
                requestedOutcome: 'Create the persistent release specialist for this objective.',
                team: 'general',
                managementProposal: { members: [proposedMember], transfers: [] }
              }
            ]
          },
    'work.draft': () => {
      if (context.agent.id !== proposedMember.id) {
        throw new Error('The resumed assignment changed worker identity')
      }

      if (!responses.length) {
        return { requests: [{ type: 'request.question', requestedOutcome: question, team: 'general' }] }
      }

      if (!responses.some(item => item.response.text === answer && item.response.decision === 'answered')) {
        throw new Error('The exact owner answer did not reach its requesting worker')
      }

      return {
        summary: 'Audience-specific brief',
        deliverable,
        memory: { facts: [answer], decisions: [], lessons: [], openQuestions: [] }
      }
    },
    'request.review': () => ({
      approved: true,
      summary: 'The brief uses the exact owner-provided audience and supplied facts.',
      evidenceIds
    }),
    'request.integrate': () => ({ summary: 'Complete release brief', deliverable }),
    'request.accept': () => ({
      approved: true,
      summary: 'The integrated brief satisfies each requested criterion.',
      evidenceIds,
      criteriaResults: context.objective.acceptanceCriteria.map(criterion => ({
        criterion,
        satisfied: true,
        evidenceIds,
        reason: 'The retained brief includes the exact owner audience and supplied release facts.'
      })),
      conflicts: []
    })
  }

  if (!handlers[kind]) {
    throw new Error(`Unexpected provider stage: ${kind}`)
  }

  return handlers[kind]()
}

async function startProvider(mockUrl: string) {
  const server = http.createServer((request, response) => {
    const chunks: Buffer[] = []
    request.on('data', chunk => chunks.push(Buffer.from(chunk)))
    request.on('end', () => {
      const body = Buffer.concat(chunks)

      try {
        const target = organizationProviderTarget(mockUrl, request.method, request.url)

        const payload = body.length
          ? (JSON.parse(body.toString('utf8')) as {
              messages?: Array<{ role: string; content: unknown }>
              tools?: unknown[]
              stream?: boolean
            })
          : null

        const message = payload?.messages?.find(
          item =>
            item.role === 'user' && typeof item.content === 'string' && item.content.includes('Submitted context:\n')
        )

        if (message && typeof message.content === 'string') {
          const submitted = JSON.parse(message.content.split('Submitted context:\n', 2)[1]) as {
            request: { type: string }
            context: StageContext
          }

          if (submitted.context.objective.title !== objectiveTitle || payload?.tools?.length) {
            throw new Error('Unexpected objective or expanded tool grants')
          }

          const kind = submitted.request.type
          stages.push({ kind, agentId: submitted.context.agent.id, responses: submitted.context.requestResponses })
          const content = JSON.stringify(resultFor(kind, submitted.context))
          const identity = { id: `organization-requests-${stages.length}`, created: 1, model: 'mock-model' }
          const usage = { prompt_tokens: 30, completion_tokens: 30, total_tokens: 60 }

          if (payload?.stream) {
            response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
            response.write(
              `data: ${JSON.stringify({ ...identity, object: 'chat.completion.chunk', choices: [{ index: 0, delta: { role: 'assistant', content }, finish_reason: null }] })}\n\n`
            )
            response.write(
              `data: ${JSON.stringify({ ...identity, object: 'chat.completion.chunk', choices: [{ index: 0, delta: {}, finish_reason: 'stop' }], usage })}\n\n`
            )
            response.end('data: [DONE]\n\n')
          } else {
            response.writeHead(200, { 'Content-Type': 'application/json' })
            response.end(
              JSON.stringify({
                ...identity,
                object: 'chat.completion',
                choices: [{ index: 0, message: { role: 'assistant', content }, finish_reason: 'stop' }],
                usage
              })
            )
          }

          return
        }

        const upstream = http.request(
          target,
          { method: request.method, headers: { ...request.headers, host: new URL(mockUrl).host } },
          result => {
            response.writeHead(result.statusCode ?? 500, result.headers)
            result.pipe(response)
          }
        )

        upstream.on('error', error => {
          providerErrors.push(error.message)

          if (!response.headersSent) {
            response.writeHead(502)
          }

          response.end()
        })
        upstream.end(body)
      } catch (error) {
        providerErrors.push(error instanceof Error ? error.message : String(error))
        response.writeHead(500, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify({ error: { message: 'Organization request fixture invariant failed' } }))
      }
    })
  })

  await new Promise<void>((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  const address = server.address()

  if (!address || typeof address === 'string') {
    throw new Error('Local provider did not bind TCP')
  }

  return {
    url: `http://127.0.0.1:${address.port}`,
    close: () => new Promise<void>((resolve, reject) => server.close(error => (error ? reject(error) : resolve())))
  }
}

test.beforeAll(async () => {
  const mock = await startMockServer()
  const provider = await startProvider(mock.url)
  const sandbox = createSandbox('organization-requests-loop')

  try {
    writeMockProviderConfig(
      sandbox.hermesHome,
      provider.url,
      undefined,
      'approvals:\n  mode: manual\norganization:\n  max_inflight: 1\n  max_stages: 40\n  max_context_tokens: 32768\n  max_output_tokens: 2048',
      128000
    )
    writeEnvFile(sandbox.hermesHome)
    const { app, page } = await launchDesktop(buildAppEnv(sandbox))
    fixture = {
      app,
      page,
      mock,
      mockUrl: provider.url,
      sandbox,
      cleanup: async () => {
        try {
          await app.close()
        } finally {
          try {
            await provider.close()
          } finally {
            try {
              await mock.close()
            } finally {
              sandbox.cleanup()
            }
          }
        }
      }
    }
    await waitForAppReady(fixture, 120_000)
  } catch (error) {
    if (fixture) {
      await fixture.cleanup()
    } else {
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
  await testInfo.attach('organization-request-coverage', {
    body: JSON.stringify({ stages, providerErrors, testStatus: testInfo.status }, null, 2),
    contentType: 'application/json'
  })

  if (fixture && !fixture.page.isClosed()) {
    await fixture.page.screenshot({ path: testInfo.outputPath('native-request-final-state.png') })
  }
})
test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

// eslint-disable-next-line no-empty-pattern -- fixture lifecycle is managed by this spec
test('retains owner attention across reloads, answers the requesting worker, and preserves scoped staffing history', async ({}, testInfo) => {
  const page = fixture!.page

  const primary = page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })

  await primary.getByRole('link', { name: 'Organization', exact: true }).click()
  await page.getByRole('button', { name: 'Manage organization', exact: true }).click()
  const form = page.getByRole('form', { name: 'Manage organization' })
  await form.getByRole('button', { name: 'Add member', exact: true }).click()
  await form.getByRole('textbox', { name: 'Identity ID', exact: true }).fill('staffing-lead')
  await form.getByRole('textbox', { name: 'Name', exact: true }).fill('Staffing lead')
  await form.getByRole('combobox', { name: 'Role', exact: true }).selectOption('Manager')
  await form
    .getByRole('textbox', { name: 'Purpose', exact: true })
    .fill('Staff the general team within existing grants')
  await form.getByRole('checkbox', { name: 'request.hire', exact: true }).check()
  await form.getByRole('checkbox', { name: 'staff.manage', exact: true }).check()
  await form.getByRole('textbox', { name: 'Managed teams (comma separated)', exact: true }).fill('general')
  await form.getByRole('spinbutton', { name: 'Concurrent execution limit', exact: true }).fill('2')
  await expect(form.getByRole('button', { name: 'Save organization', exact: true })).toBeEnabled()
  await page.screenshot({ path: testInfo.outputPath('01-scoped-staffing-editor.png') })
  await form.getByRole('button', { name: 'Save organization', exact: true }).click()
  await expect(form).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Inspect Staffing lead', exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('button', { name: 'Inspect Staffing lead', exact: true })).toBeVisible({
    timeout: 60_000
  })
  await page.getByRole('button', { name: 'Manage organization', exact: true }).click()
  await form
    .getByRole('combobox', { name: 'Member', exact: true })
    .selectOption({ label: 'Staffing lead (staffing-lead)' })
  await expect(form.getByRole('checkbox', { name: 'staff.manage', exact: true })).toBeChecked()
  await expect(form.getByRole('spinbutton', { name: 'Concurrent execution limit', exact: true })).toHaveValue('2')
  await page.keyboard.press('Escape')

  await primary.getByRole('link', { name: 'Home', exact: true }).click()
  await page.getByRole('button', { name: 'New objective', exact: true }).click()
  await page.getByRole('textbox', { name: 'Objective', exact: true }).fill(objectiveTitle)
  await openOrganizationDisclosure(page, 'Context')
  await page
    .getByRole('textbox', { name: 'Submitted context (optional)', exact: true })
    .fill('The release improves request routing while preserving existing permissions. Ask the owner for the audience.')
  await page.getByRole('combobox', { name: 'Delivery scope', exact: true }).selectOption('managed_artifact')
  await openOrganizationDisclosure(page, 'Acceptance criteria')
  await page
    .getByRole('textbox', { name: 'Acceptance criteria', exact: true })
    .fill('The complete brief addresses the audience supplied by the owner and uses the supplied release facts.')
  await page.getByRole('button', { name: 'Create objective', exact: true }).click()
  await expect(page.getByRole('heading', { name: objectiveTitle, exact: true })).toBeVisible()
  const questionRow = page.getByRole('button', { name: 'Inspect request: request.question', exact: true })
  await expect(questionRow).toContainText('Pending intervention', { timeout: 90_000 })
  await openOrganizationRequests(page)
  const inbox = page.getByRole('region', { name: 'Attention inbox', exact: true })
  await expect(inbox).toContainText('Needs You: 1 · Unread: 1')
  // A returning owner sees the durable blocker even when it arrived off-page.
  await page.reload()
  await expect(inbox).toContainText('Needs You: 1 · Unread: 1', { timeout: 60_000 })
  await inbox.getByRole('button', { name: 'Mark seen', exact: true }).click()
  await expect(inbox).toContainText('Needs You: 1 · Unread: 0')
  await expect(inbox.getByRole('button', { name: 'Seen', exact: true })).toBeDisabled()
  await page.reload()
  await expect(inbox).toContainText('Needs You: 1 · Unread: 0', { timeout: 60_000 })
  await expect(inbox.getByRole('button', { name: 'Seen', exact: true })).toBeDisabled()
  // Acknowledgement cannot answer the question or resume the blocked worker.
  expect(stages.filter(stage => stage.kind === 'work.draft')).toHaveLength(1)
  await page.screenshot({ path: testInfo.outputPath('02-durable-seen-blocker.png') })
  await questionRow.click()
  const inspector = page.getByRole('complementary', { name: 'Request details' })
  await expect(inspector.getByText(question, { exact: true })).toBeVisible()
  await expect(inspector.getByText('Release specialist', { exact: true })).toBeVisible()
  await expect(inspector.getByText('answer.question', { exact: true })).toBeVisible()
  await inspector.getByRole('textbox', { name: 'Response', exact: true }).fill(answer)
  await inspector.getByRole('button', { name: 'Open parent request', exact: true }).click()
  await expect(inspector.getByRole('region', { name: 'Task scope', exact: true })).toContainText(
    'Use supplied facts and ask the owner for the intended audience.'
  )
  await page.screenshot({ path: testInfo.outputPath('02a-parent-task-context.png') })
  await inspector.getByRole('button', { name: 'Back', exact: true }).click()
  await expect(inspector.getByRole('textbox', { name: 'Response', exact: true })).toHaveValue(answer)
  await inspector.getByRole('button', { name: 'Open parent request', exact: true }).click()
  await page.keyboard.press('Escape')
  await expect(inspector).toHaveCount(0)
  expect(stages.filter(stage => stage.kind === 'work.draft')).toHaveLength(1)
  await questionRow.click()
  await inspector.getByRole('textbox', { name: 'Response', exact: true }).fill(answer)
  await page.screenshot({ path: testInfo.outputPath('02-question-owner-response.png') })
  await inspector.getByRole('button', { name: 'Submit response', exact: true }).click()
  await expect(inspector.getByRole('button', { name: 'Submit response', exact: true })).toHaveCount(0)

  // The response receipt can remain open; navigate explicitly to its objective.
  if (await inspector.isVisible()) {
    await inspector.getByRole('link', { name: 'Open objective', exact: true }).click()
  } else {
    await primary.getByRole('link', { name: 'Objectives', exact: true }).click()
    await page
      .getByRole('link')
      .filter({ has: page.getByText(objectiveTitle, { exact: true }) })
      .click()
  }

  const header = page
    .locator('.eid-page-header')
    .filter({ has: page.getByRole('heading', { name: objectiveTitle, exact: true }) })

  await expect(header).toContainText('Completed', { timeout: 90_000 })
  await expect(page.getByText(deliverable, { exact: true }).first()).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('03-accepted-request-outcome.png') })
  expect(providerErrors).toEqual([])
  expect(stages.filter(stage => stage.kind === 'work.draft').map(stage => stage.agentId)).toEqual([
    proposedMember.id,
    proposedMember.id
  ])
  expect(stages.filter(stage => stage.kind === 'request.plan')).toHaveLength(2)
  expect(stages.some(stage => stage.kind === 'request.question')).toBe(false)

  await primary.getByRole('link', { name: 'Organization', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Inspect Release specialist', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Recent organization changes' })).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: 'Inspect Release specialist', exact: true }).click({ timeout: 60_000 })
  await expect(
    page.getByRole('complementary', { name: 'Agent details' }).getByText(answer, { exact: true })
  ).toBeVisible()
  await page.keyboard.press('Escape')
  await openOrganizationRequests(page)
  await expect(inbox).toContainText('Needs You: 0 · Unread: 0')
  await expect(page.getByText('Nothing needs your input', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Request queue', exact: true }).click()
  await page.getByRole('combobox', { name: 'Status', exact: true }).selectOption('all')
  await questionRow.click()
  await expect(page.getByRole('region', { name: 'Recorded response' }).getByText(answer, { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Recorded response' })).toContainText('Answered')
})
