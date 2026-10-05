/** Scripted loopback inference only; all state and acceptance remain backend-owned. */
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
import { exactOrganizationEvidence, type OrganizationEvidenceContext } from './organization-evidence'
import { organizationProviderTarget } from './organization-provider-target'

export const completionTitle = 'Prepare the evidence-backed release recommendation'
export const budgetTitle = 'Prepare a recommendation within one model call'
export const originalScope = 'Recommend retaining the release safety gate and execute the project tests.'
export const amendedScope = 'Document the recommendation only. Project tests are outside this revised scope.'
export const amendedCriterion =
  'The recommendation uses the supplied release facts and explicitly says project tests were not run.'
export const recommendation =
  'Retain the release safety gate because the supplied facts identify a regression risk. Project tests were not run.'

interface StageContext extends OrganizationEvidenceContext {
  objective: {
    title: string
    description: string
    acceptanceCriteria: string[]
    requiredChecks: string[]
  }
  agent: { id: string }
}

interface ProviderPayload {
  messages?: Array<{ role: string; content: unknown }>
  tools?: unknown[]
  stream?: boolean
  max_tokens?: number
  max_completion_tokens?: number
}

export interface CompletionStage {
  kind: string
  agentId: string
  scope: string
  requiredChecks: string[]
  acceptanceCriteria: string[]
  outputLimit: number | undefined
}

function resultFor(kind: string, context: StageContext) {
  const evidence = exactOrganizationEvidence(context)

  if (
    ['request.review', 'request.integrate', 'request.accept'].includes(kind) &&
    (!evidence.length || evidence.some(item => item.content !== recommendation))
  ) {
    throw new Error('The exact complete recommendation did not reach its reviewer or integrator')
  }

  const evidenceIds = evidence.map(item => item.id)

  const handlers: Record<string, () => unknown> = {
    'request.plan': () => ({
      tasks: [
        {
          title: 'Draft the release recommendation',
          description: 'Recommend retaining the release safety gate using only the submitted regression-risk fact.',
          type: 'work.draft',
          team: 'general',
          agentId: 'worker-1',
          managerId: 'manager',
          dependsOn: []
        }
      ],
      workers: 1,
      // A model's empty list must not erase the owner's project_tests check.
      requiredChecks: []
    }),
    'work.draft': () => ({ summary: 'Evidence-backed recommendation', deliverable: recommendation }),
    'request.review': () => ({ approved: true, summary: 'The recommendation matches the supplied fact.', evidenceIds }),
    'request.integrate': () => ({ summary: 'Complete recommendation', deliverable: recommendation }),
    // Deliberately optimistic: the backend must still reject the first round
    // because no project-test evidence exists, regardless of model approval.
    'request.accept': () => ({
      approved: true,
      summary: 'The recommendation is ready.',
      evidenceIds,
      criteriaResults: context.objective.acceptanceCriteria.map(criterion => ({
        criterion,
        satisfied: true,
        evidenceIds,
        reason: 'The recommendation includes the supplied fact.'
      })),
      conflicts: [],
      requiredChecks: []
    })
  }

  if (!handlers[kind]) {
    throw new Error(`Unexpected completion stage: ${kind}`)
  }

  return handlers[kind]()
}

export async function setupCompletionFixture(budgetLimited: boolean) {
  const stages: CompletionStage[] = []
  const providerErrors: string[] = []
  const pendingReplies: Array<() => void> = []
  const mock = await startMockServer()
  const sandbox = createSandbox(budgetLimited ? 'completion-budget' : 'completion-scope')

  const server = http.createServer((request, response) => {
    const chunks: Buffer[] = []
    request.on('data', chunk => chunks.push(Buffer.from(chunk)))
    request.on('end', () => {
      const body = Buffer.concat(chunks)

      try {
        const target = organizationProviderTarget(mock.url, request.method, request.url)
        const payload = body.length ? (JSON.parse(body.toString('utf8')) as ProviderPayload) : null

        const message = payload?.messages?.find(
          item =>
            item.role === 'user' && typeof item.content === 'string' && item.content.includes('Submitted context:\n')
        )

        if (message && typeof message.content === 'string') {
          const submitted = JSON.parse(message.content.split('Submitted context:\n', 2)[1]) as {
            request: { type: string }
            context: StageContext
          }

          const { type: kind } = submitted.request
          const { context } = submitted
          const outputLimit = payload?.max_completion_tokens ?? payload?.max_tokens
          stages.push({
            kind,
            agentId: context.agent.id,
            scope: context.objective.description,
            requiredChecks: context.objective.requiredChecks,
            acceptanceCriteria: context.objective.acceptanceCriteria,
            outputLimit
          })

          if (context.objective.title !== (budgetLimited ? budgetTitle : completionTitle) || payload?.tools?.length) {
            throw new Error('Unexpected objective or expanded tool grants')
          }

          if (typeof outputLimit !== 'number' || outputLimit < 1 || outputLimit > 2048) {
            throw new Error('The actual HTTP request omitted or exceeded its configured output ceiling')
          }

          if (budgetLimited && stages.length > 1) {
            throw new Error('The model-call ceiling allowed an additional HTTP completion')
          }

          const content = JSON.stringify(resultFor(kind, context))
          const identity = { id: `completion-fixture-${stages.length}`, created: 1, model: 'mock-model' }
          const usage = { prompt_tokens: 30, completion_tokens: 30, total_tokens: 60 }

          const reply = () => {
            if (response.destroyed) {
              return
            }

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
          }

          // Hold real inference at the revised plan, so owner submission and
          // renderer reload cannot accidentally appear to complete the work.
          if (kind === 'request.plan' && context.objective.description === amendedScope) {
            pendingReplies.push(reply)
          } else {
            reply()
          }

          return
        }

        const upstream = http.request(
          target,
          {
            method: request.method,
            headers: { ...request.headers, host: new URL(mock.url).host }
          },
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
        response.end(JSON.stringify({ error: { message: 'Completion fixture invariant failed' } }))
      }
    })
  })

  const closeProvider = () =>
    new Promise<void>((resolve, reject) => server.close(error => (error ? reject(error) : resolve())))

  const releasePlan = () => pendingReplies.splice(0).forEach(reply => reply())
  let fixture: MockBackendFixture | undefined

  try {
    await new Promise<void>((resolve, reject) => {
      server.once('error', reject)
      server.listen(0, '127.0.0.1', resolve)
    })
    const address = server.address()

    if (!address || typeof address === 'string') {
      throw new Error('Completion provider did not bind TCP')
    }

    const providerUrl = `http://127.0.0.1:${address.port}`
    writeMockProviderConfig(
      sandbox.hermesHome,
      providerUrl,
      undefined,
      `approvals:\n  mode: manual\norganization:\n  max_inflight: 1\n  max_stages: 40\n  max_context_tokens: 32768\n  max_output_tokens: 2048\n  max_model_calls: ${budgetLimited ? 1 : 40}`,
      // The agent requires a >=64K model window. The independent organization
      // policy above still admits at most 32K for this scripted scenario.
      128000,
      false
    )
    writeEnvFile(sandbox.hermesHome)
    const launch = await launchDesktop(buildAppEnv(sandbox))
    fixture = {
      ...launch,
      mock,
      mockUrl: providerUrl,
      sandbox,
      cleanup: async () => {
        releasePlan()

        try {
          await fixture!.app.close()
        } finally {
          try {
            await closeProvider()
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
      try {
        if (server.listening) {
          await closeProvider()
        }
      } finally {
        try {
          await mock.close()
        } finally {
          sandbox.cleanup()
        }
      }
    }

    throw error
  }

  return {
    fixture,
    stages,
    providerErrors,
    releasePlan,
    restart: async () => {
      await fixture.app.close()
      const launched = await launchDesktop(buildAppEnv(sandbox))
      fixture.app = launched.app
      fixture.page = launched.page
      await waitForAppReady(fixture, 120_000)
    }
  }
}
