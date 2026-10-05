/** Loopback inference scripts decisions; real backend owns reads, tests and Git writes. */
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import http from 'node:http'
import path from 'node:path'

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

export const projectTitle = 'Fix addition and verify the reviewed source branch'
export const projectCriterion =
  'Addition handles positive, negative and zero operands; reviewed tests pass on the exact integrated source branch.'
export const originalApp = 'def add(left, right):\n    return left - right\n'
export const correctedApp = 'def add(left, right):\n    return left + right\n'
export const projectTests = `import unittest
from app import add

class AdditionTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(add(2, 3), 5)
    def test_negative(self):
        self.assertEqual(add(-2, -3), -5)
    def test_zero(self):
        self.assertEqual(add(9, 0), 9)
`
const deliverable =
  'Addition now handles positive, negative and zero operands. Three real isolated unittest cases passed on the exact reviewed bytes and received independent review before a new local Git branch was created. The original working tree and index are unchanged. No remote push or deployment occurred.'

type BodyReference = string | { bodySha256: string; utf8Bytes: number }
interface Proposal {
  id: string
  proposalSha256: string
  files: Array<{ baseContent: BodyReference; newContent: BodyReference }>
}
interface ProjectContext extends OrganizationEvidenceContext {
  agent: { id: string }
  objective: { title: string; acceptanceCriteria: string[]; requiredChecks: string[] }
  evidence?: Array<
    NonNullable<OrganizationEvidenceContext['evidence']>[number] & { kind?: string; editProposal?: Proposal }
  >
}
interface ProviderPayload {
  messages?: Array<{ role: string; content: string | null }>
  tools?: Array<{ function: { name: string } }>
  stream?: boolean
}
export interface ProjectStage {
  kind: string
  agent: string
  evidenceIds: string[]
}
interface ProjectRunEvidence {
  snapshot: Array<{ path: string; content: string; sha256: string; revision: number }>
  execution: {
    status: string
    exitCode: number
    testCount: number
    stdout: string
    stderr: string
    snapshotSha256: string
    command: string[]
    limitations: string
    isolation: { established: boolean; network: string; childProcessesAllowed: boolean }
  }
}

const digest = (text: string) => createHash('sha256').update(text).digest('hex')

function exact(value: BodyReference, context: ProjectContext) {
  if (typeof value === 'string') {
    return value
  }
  const text = context.evidenceBodies?.[value.bodySha256]
  assert.equal(typeof text, 'string')
  assert.equal(digest(text!), value.bodySha256)
  assert.equal(Buffer.byteLength(text!), value.utf8Bytes)
  return text!
}

function responseFor(kind: string, context: ProjectContext, payload: ProviderPayload, runs: ProjectRunEvidence[]) {
  const evidence = exactOrganizationEvidence(context)
  const evidenceIds = evidence.map(item => item.id)
  const textResult = (output: object) => ({ role: 'assistant', content: JSON.stringify(output) })
  if (kind === 'request.plan') {
    assert.equal(payload.tools?.length ?? 0, 0)
    return textResult({
      workers: 1,
      tasks: [
        {
          title: 'Inspect addition and its tests',
          type: 'work.inspect',
          team: 'general',
          agentId: 'editor',
          dependsOn: [],
          description:
            'Read root0/app.py and root0/test_app.py. Identify the subtraction bug and preserve the existing regression cases.'
        },
        {
          title: 'Fix addition without weakening tests',
          type: 'work.edit',
          team: 'general',
          agentId: 'editor',
          dependsOn: [0],
          description: 'Replace left - right with left + right in root0/app.py. Keep all tests unchanged.'
        }
      ]
    })
  }
  if (kind === 'work.inspect' || kind === 'work.edit') {
    assert.deepEqual(
      payload.tools?.map(tool => tool.function.name),
      ['read_file']
    )
    const results = payload.messages!.filter(message => message.role === 'tool')
    const paths = kind === 'work.inspect' ? ['root0/app.py', 'root0/test_app.py'] : ['root0/app.py']
    if (results.length < paths.length) {
      return {
        role: 'assistant',
        content: null,
        tool_calls: [
          {
            id: `read-${kind}-${results.length}`,
            type: 'function',
            function: { name: 'read_file', arguments: JSON.stringify({ path: paths[results.length] }) }
          }
        ]
      }
    }
    const result = JSON.parse(results[0].content!) as {
      content: string
      workspaceRevision: number
      sourceSha256: string
    }
    if (kind === 'work.inspect') {
      assert.equal(result.content, '1|def add(left, right):\n2|    return left - right')
      assert.match(results[1].content!, /test_positive/)
      return textResult({
        summary: 'Found subtraction instead of addition',
        deliverable:
          'root0/app.py lines 1–2 subtract operands. root0/test_app.py lines 1–10 covers positive, negative and zero addition. Change only the implementation.'
      })
    }
    assert.equal(result.content, originalApp)
    assert.equal(result.sourceSha256, digest(originalApp))
    return textResult({
      summary: 'Correct addition and retain all regression tests',
      edits: [
        {
          path: 'root0/app.py',
          baseRevision: result.workspaceRevision,
          baseSha256: result.sourceSha256,
          oldText: 'left - right',
          newText: 'left + right'
        }
      ],
      validations: [{ kind: 'python_syntax', path: 'root0/app.py' }]
    })
  }
  assert.equal(payload.tools?.length ?? 0, 0)
  if (kind === 'request.review') {
    const proposal = context.evidence?.[0]?.editProposal
    if (proposal) {
      assert.equal(proposal.files.length, 1)
      assert.equal(exact(proposal.files[0].baseContent, context), originalApp)
      assert.equal(exact(proposal.files[0].newContent, context), correctedApp)
    }
    return textResult({
      approved: true,
      summary: 'The exact read receipts and unchanged regression cases support this narrow correction.',
      evidenceIds,
      ...(proposal ? { proposalId: proposal.id, proposalSha256: proposal.proposalSha256 } : {})
    })
  }
  if (kind === 'request.test_review') {
    assert.equal(evidence.length, 1)
    const run = JSON.parse(evidence[0].content) as ProjectRunEvidence
    assert.equal(run.execution.status, 'passed')
    assert.equal(run.execution.exitCode, 0)
    assert.equal(run.execution.testCount, 3)
    assert.equal(run.execution.isolation.established, true)
    assert.deepEqual(Object.fromEntries(run.snapshot.map(file => [file.path, file.content])), {
      'root0/app.py': correctedApp,
      'root0/test_app.py': projectTests
    })
    assert.match(run.execution.stderr, /test_positive/)
    assert.match(run.execution.stderr, /test_negative/)
    runs.push(run)
    return textResult({
      approved: true,
      evidenceIds,
      summary:
        'Three real assertions cover positive, negative and zero operands on the exact copied source. The isolated fixed unittest recipe provides bounded coverage; it is not deployment or universal correctness proof.'
    })
  }
  if (kind === 'request.integrate') {
    assert(context.evidence!.some(item => item.kind === 'project_execution'))
    assert(context.evidence!.some(item => item.kind === 'source_integration'))
    return textResult({ summary: 'Reviewed source fix, actual tests and verified local branch', deliverable })
  }
  assert.equal(kind, 'request.accept')
  return textResult({
    approved: true,
    summary: 'The exact tested source branch and independent reviews satisfy the stated goal.',
    evidenceIds,
    criteriaResults: context.objective.acceptanceCriteria.map(criterion => ({
      criterion,
      satisfied: true,
      evidenceIds,
      reason: 'The exact corrected implementation, substantive three-case tests, actual run and local branch match.'
    })),
    conflicts: []
  })
}

export async function setupProjectFixture(options: { budgetLimited?: boolean; holdPlan?: boolean } = {}) {
  const stages: ProjectStage[] = []
  const providerErrors: string[] = []
  const runEvidence: ProjectRunEvidence[] = []
  const pendingReplies: Array<() => void> = []
  const mock = await startMockServer()
  const sandbox = createSandbox('organization-project')
  const source = path.join(sandbox.root, 'source')
  fs.mkdirSync(source)
  fs.writeFileSync(path.join(source, 'app.py'), originalApp)
  fs.writeFileSync(path.join(source, 'test_app.py'), projectTests)
  const git = (...args: string[]) =>
    execFileSync('git', ['--no-optional-locks', '-C', source, ...args], { encoding: 'utf8', windowsHide: true }).trim()
  git('init', '--initial-branch=main')
  git('add', 'app.py', 'test_app.py')
  git('-c', 'user.name=Local fixture', '-c', 'user.email=fixture@localhost', 'commit', '-m', 'Exact starting project')
  const sourceHead = git('rev-parse', 'HEAD')
  const sourceIndex = fs.readFileSync(path.join(source, '.git', 'index'))
  const server = http.createServer((request, response) => {
    const chunks: Buffer[] = []
    request.on('data', chunk => chunks.push(Buffer.from(chunk)))
    request.on('end', () => {
      const body = Buffer.concat(chunks)
      try {
        const target = organizationProviderTarget(mock.url, request.method, request.url)
        const payload = body.length ? (JSON.parse(body.toString('utf8')) as ProviderPayload) : null
        const prompt = payload?.messages?.find(
          message => message.role === 'user' && message.content?.includes('Submitted context:\n')
        )
        if (prompt?.content) {
          const submitted = JSON.parse(prompt.content.split('Submitted context:\n', 2)[1]) as {
            request: { type: string }
            context: ProjectContext
          }
          const kind = submitted.request.type
          assert.equal(submitted.context.objective.title, projectTitle)
          assert(!body.toString().includes(source))
          if (options.budgetLimited) {
            assert.equal(stages.length, 0, 'Budget did not stop a second HTTP model call')
          }
          stages.push({
            kind,
            agent: submitted.context.agent.id,
            evidenceIds: submitted.context.evidence?.map(row => row.id) ?? []
          })
          const message = responseFor(kind, submitted.context, payload!, runEvidence)
          const reply = () => {
            if (response.destroyed) {
              return
            }
            assert.equal(payload!.stream, false)
            response.writeHead(200, { 'Content-Type': 'application/json' })
            response.end(
              JSON.stringify({
                id: `project-fixture-${stages.length}`,
                object: 'chat.completion',
                created: 1,
                model: 'mock-model',
                choices: [{ index: 0, message, finish_reason: 'tool_calls' in message ? 'tool_calls' : 'stop' }],
                usage: { prompt_tokens: 30, completion_tokens: 20, total_tokens: 50 }
              })
            )
          }
          if (options.holdPlan && kind === 'request.plan') {
            pendingReplies.push(reply)
          } else {
            reply()
          }
          return
        }
        const upstream = http.request(
          target,
          { method: request.method, headers: { ...request.headers, host: new URL(mock.url).host } },
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
        response.end(JSON.stringify({ error: { message: 'Project fixture invariant failed' } }))
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
    assert(address && typeof address !== 'string')
    const providerUrl = `http://127.0.0.1:${address.port}`
    const grants = ['read_file', 'patch', 'run_tests', 'integrate_source']
    const organization = {
      capabilities: ['work.inspect', 'work.edit'],
      tool_grants: grants,
      read_roots: [source],
      max_workers: 1,
      max_inflight: 1,
      max_stages: 40,
      max_context_tokens: 65536,
      max_output_tokens: 2048,
      max_model_calls: options.budgetLimited ? 1 : 40,
      project_grants: [
        {
          id: 'addition',
          files: ['root0/app.py', 'root0/test_app.py'],
          execution: { recipe: 'python_unittest', timeout_seconds: 10 }
        }
      ],
      roster: [
        {
          id: 'editor',
          name: 'Project editor',
          team: 'general',
          capabilities: ['work.inspect', 'work.edit'],
          tool_grants: grants
        }
      ]
    }
    writeMockProviderConfig(
      sandbox.hermesHome,
      providerUrl,
      undefined,
      `approvals:\n  mode: manual\norganization: ${JSON.stringify(organization)}`,
      128000,
      false
    )
    writeEnvFile(sandbox.hermesHome)
    const launched = await launchDesktop(buildAppEnv(sandbox))
    fixture = {
      ...launched,
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
    runEvidence,
    sourceHead,
    git,
    releasePlan,
    assertSourceUntouched: () => {
      assert.equal(git('rev-parse', 'HEAD'), sourceHead)
      assert.deepEqual(fs.readFileSync(path.join(source, '.git', 'index')), sourceIndex)
      assert.equal(fs.readFileSync(path.join(source, 'app.py'), 'utf8'), originalApp)
      assert.equal(fs.readFileSync(path.join(source, 'test_app.py'), 'utf8'), projectTests)
      assert.equal(git('status', '--porcelain'), '')
    },
    restart: async () => {
      await fixture.app.close()
      const launched = await launchDesktop(buildAppEnv(sandbox))
      fixture.app = launched.app
      fixture.page = launched.page
      await waitForAppReady(fixture, 120_000)
    }
  }
}
