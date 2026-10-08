import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { RuntimeAcceptance } from './runtime-acceptance'
import { createRuntimeAdapter, type OrganizationGateway } from './runtime-adapter'
import type { Objective, OrganizationArtifact, OrganizationProjectExecution, OrganizationSnapshot } from './types'
import { OrganizationWorkspace } from './workspace'

function objective(): Objective {
  return {
    id: 'objective',
    title: 'Test reviewed project',
    description: 'Verify selected files',
    ownerId: 'executive',
    status: 'active',
    source: 'runtime',
    createdAt: '2026-10-05T00:00:00Z',
    requiredChecks: ['project_tests', 'source_integration'],
    acceptance: { status: 'pending', criteria: ['Independent evidence'], round: 1, maxReplans: 2, summary: '', deliverableId: null },
    projectValidation: {
      id: 'syntax-validation',
      status: 'passed',
      scope: 'latest_managed_project_heads',
      resultSha256: 'a'.repeat(64),
      filesCount: 2,
      checksCount: 4,
      notExecuted: ['functional_tests']
    }
  }
}

function execution(): OrganizationProjectExecution {
  return {
    id: 'exact-run',
    requestId: 'test-request',
    round: 1,
    snapshotSha256: 'b'.repeat(64),
    status: 'passed',
    receipt: {
      status: 'passed',
      exitCode: 0,
      testCount: 3,
      durationSeconds: 0.012345,
      command: ['/usr/bin/python3', '-I', '-S', '-B', '/runner.py', 'argument with spaces'],
      reason: 'Nonempty unittest suite completed successfully.',
      isolation: {
        backend: 'linux-bubblewrap-seccomp',
        established: true,
        network: 'none',
        sourceReadOnly: true,
        runtimeReadOnly: true,
        scratchMode: 'single_file',
        processLimit: 1
      }
    },
    review: null,
    sourceIntegration: null,
    files: [
      { path: 'root0/app.py', sha256: 'c'.repeat(64), revision: 1 },
      { path: 'root0/test_app.py', sha256: 'd'.repeat(64), revision: 0 }
    ]
  }
}

it('keeps syntax checks, a real run, independent review and branch delivery separate and opens their exact retained evidence', async () => {
  const initial: OrganizationSnapshot = {
    source: 'runtime',
    objectives: [objective()],
    agents: [],
    tasks: [],
    requests: [],
    activity: [],
    knowledge: [],
    runtime: { state: 'ready', maxWorkers: 2, capabilities: ['work.inspect'], scope: 'Selected project files' }
  }

  let current = initial
  const request = vi.fn().mockImplementation(() => Promise.resolve(current))

  const adapter = createRuntimeAdapter({
    request: request as OrganizationGateway['request'],
    getScope: () => ({ key: 'current-profile', connected: true }),
    subscribeScope: () => () => undefined
  })

  const view = render(
    <MemoryRouter initialEntries={['/objectives/objective']}>
      <OrganizationWorkspace adapter={adapter} />
    </MemoryRouter>
  )

  await screen.findByText('No project test execution receipt recorded.')
  expect(within(screen.getByRole('region', { name: 'Final managed validation' })).getByText('Passed')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Read exact test evidence' })).toBeNull()
  expect(screen.getByText(/Deterministic content and syntax checks are distinct/)).toBeTruthy()

  const run = execution()
  current = { ...current, objectives: [{ ...initial.objectives[0], projectExecution: run }] }
  await act(() => adapter.refresh())
  const panel = within(screen.getByRole('region', { name: 'Latest project test execution' }))
  expect(panel.getByText('Passed')).toBeTruthy()
  expect(panel.getByText('Reported test count').nextElementSibling?.textContent).toBe('3')
  expect(panel.getByText('Exit code').nextElementSibling?.textContent).toBe('0')
  expect(panel.getByText('Duration (seconds)').nextElementSibling?.textContent).toBe('0.012345')
  expect(panel.getByText('Exact command arguments').nextElementSibling?.textContent).toBe(
    JSON.stringify(run.receipt.command)
  )
  expect(panel.getByText('linux-bubblewrap-seccomp · Established')).toBeTruthy()
  expect(panel.getByText('Selected snapshot files (2)').closest('details')?.open).toBe(false)
  fireEvent.click(panel.getByText('Selected snapshot files (2)'))
  expect(panel.getByText('root0/test_app.py')).toBeTruthy()
  expect(within(panel.getByRole('region', { name: 'Independent test review' })).getByText('Not recorded')).toBeTruthy()
  expect(panel.getByText('No automatic source integration receipt recorded.')).toBeTruthy()
  expect(screen.getByText('No accepted final result yet.')).toBeTruthy()

  const artifact: OrganizationArtifact = {
    id: run.id,
    kind: 'project_execution',
    objectiveId: 'objective',
    taskId: null,
    createdAt: '2026-10-05T00:01:00Z',
    summary: 'Exact run',
    sha256: 'e'.repeat(64),
    content: JSON.stringify({
      snapshot: [{ path: 'root0/test_app.py', content: 'assert result == 3' }],
      execution: run.receipt,
      sourceBase: { commit: 'reviewed-base' },
      grant: { recipe: 'python_unittest' }
    })
  }

  request.mockImplementation((method: string) =>
    method === 'organization.evidence'
      ? Promise.reject(new Error('Evidence temporarily unavailable'))
      : Promise.resolve(current)
  )
  fireEvent.click(panel.getByRole('button', { name: 'Read exact test evidence' }))
  await screen.findByText('Evidence temporarily unavailable')
  expect(request.mock.calls.find(call => call[0] === 'organization.evidence')?.[1]).toEqual({ id: run.id })
  request.mockImplementation((method: string) =>
    Promise.resolve(method === 'organization.evidence' ? artifact : current)
  )
  fireEvent.click(screen.getByRole('button', { name: 'Retry artifact' }))
  const detail = within(screen.getByRole('complementary', { name: 'Artifact details' }))
  expect(await detail.findByText(artifact.content)).toBeTruthy()
  expect(detail.getByText('Retained test snapshot and execution receipt')).toBeTruthy()
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(screen.queryByRole('complementary', { name: 'Artifact details' })).toBeNull()

  current = {
    ...current,
    objectives: [
      {
        ...initial.objectives[0],
        projectExecution: {
          ...run,
          review: {
            approved: true,
            reviewerId: 'independent-reviewer',
            summary: 'Checked test coverage and exact bytes',
            requestId: 'review-request'
          },
          sourceIntegration: {
            status: 'integrated',
            evidenceId: 'exact-source-receipt',
            sourceBaseCommit: '1'.repeat(40),
            commit: '2'.repeat(40),
            tree: '3'.repeat(40),
            ref: 'refs/heads/eidolon/reviewed-project',
            manifestSha256: '4'.repeat(64),
            sourceWritesPerformed: true,
            workingTreeWritesPerformed: false
          }
        }
      }
    ]
  }
  await act(() => adapter.refresh())
  expect(within(panel.getByRole('region', { name: 'Independent test review' })).getByText('Approved')).toBeTruthy()
  expect(panel.getByText('independent-reviewer')).toBeTruthy()
  const integration = within(panel.getByRole('region', { name: 'Reviewed source branch' }))
  expect(integration.getByText('refs/heads/eidolon/reviewed-project')).toBeTruthy()
  expect(integration.getByText('2'.repeat(40))).toBeTruthy()
  expect(integration.getByText(/original index and working tree were left unchanged/)).toBeTruthy()

  const sourceArtifact = {
    ...artifact,
    id: 'exact-source-receipt',
    kind: 'source_integration',
    content: JSON.stringify((current.objectives[0].projectExecution as OrganizationProjectExecution).sourceIntegration)
  }

  request.mockImplementation((method: string) =>
    Promise.resolve(method === 'organization.evidence' ? sourceArtifact : current)
  )
  fireEvent.click(integration.getByRole('button', { name: 'Read exact source integration evidence' }))
  expect(await screen.findByText(sourceArtifact.content)).toBeTruthy()
  expect(screen.getByText('Retained source integration receipt')).toBeTruthy()
  expect(request.mock.calls.filter(call => call[0] === 'organization.evidence').at(-1)?.[1]).toEqual({
    id: sourceArtifact.id
  })
  fireEvent.click(screen.getByRole('button', { name: 'Close artifact details' }))
  expect(screen.getByText('No accepted final result yet.')).toBeTruthy()
  expect(screen.queryByRole('button', { name: /approve|run tests|merge|push|mark complete/i })).toBeNull()
  view.unmount()
})

it('reports unavailable, failed, timed-out and unknown outcomes without inventing a passing receipt or granting retries', () => {
  const run = execution()
  const onOpenEvidence = vi.fn()

  const view = render(
    <RuntimeAcceptance
      objective={{
        ...objective(),
        projectExecution: {
          ...run,
          status: 'unsupported',
          receipt: {
            status: 'unsupported',
            exitCode: null,
            testCount: 0,
            durationSeconds: 0,
            command: [],
            isolation: { backend: 'linux-bubblewrap-seccomp', established: false },
            reason: 'macOS cannot establish the required OS isolation.'
          }
        }
      }}
      onOpenEvidence={onOpenEvidence}
    />
  )

  const panel = within(screen.getByRole('region', { name: 'Latest project test execution' }))
  expect(panel.getByText('Unavailable on this runtime')).toBeTruthy()
  expect(panel.getByText('macOS cannot establish the required OS isolation.')).toBeTruthy()
  expect(panel.getByText('linux-bubblewrap-seccomp · Not established')).toBeTruthy()
  expect(panel.getByText('Reported test count').nextElementSibling?.textContent).toBe('0')
  expect(panel.getByText('Exit code').nextElementSibling?.textContent).toBe('Not recorded')
  expect(panel.getByText('Exact command arguments').nextElementSibling?.textContent).toBe('Not recorded')
  expect(panel.queryByText('Passed')).toBeNull()
  fireEvent.click(panel.getByRole('button', { name: 'Read exact test evidence' }))
  expect(onOpenEvidence).toHaveBeenCalledWith(run.id)

  for (const [status, label] of [
    ['failed', 'Failed'],
    ['timed_out', 'Timed out'],
    ['unknown', 'Unknown']
  ] as const) {
    view.rerender(
      <RuntimeAcceptance
        objective={{
          ...objective(),
          projectExecution: {
            ...run,
            status,
            receipt: { status },
            review: {
              approved: false,
              reviewerId: 'reviewer',
              requestId: 'review-request',
              summary: 'Required evidence missing'
            }
          }
        }}
        onOpenEvidence={onOpenEvidence}
      />
    )
    expect(panel.getByText('Status').nextElementSibling?.textContent).toBe(label)
    expect(panel.getByText('Reported test count').nextElementSibling?.textContent).toBe('Not recorded')
    expect(panel.queryByText('Passed')).toBeNull()
    expect(within(panel.getByRole('region', { name: 'Independent test review' })).getByText('Denied')).toBeTruthy()
    expect(panel.queryByRole('button', { name: 'Read exact test evidence' }) !== null).toBe(status !== 'unknown')
  }

  expect(panel.getByText(/No terminal receipt is available/)).toBeTruthy()
  expect(screen.queryByRole('button', { name: /retry|grant|approve|complete/i })).toBeNull()
})

it('keeps each bound repository outcome and pending execution separate', () => {
  const open = vi.fn()
  const frontend = execution()
  render(<RuntimeAcceptance objective={{ ...objective(), projectExecution: { projects: [
    { projectId: 'frontend', execution: frontend },
    { projectId: 'backend', execution: null }
  ] } }} onOpenEvidence={open} />)
  const completed = within(screen.getByRole('region', { name: 'Latest project test execution: frontend' }))
  const pending = within(screen.getByRole('region', { name: 'Latest project test execution: backend' }))
  expect(completed.getByText('Passed')).toBeTruthy()
  expect(pending.getByText('No project test execution receipt recorded.')).toBeTruthy()
  expect(pending.queryByText('Passed')).toBeNull()
  expect(pending.queryByRole('button', { name: 'Read exact test evidence' })).toBeNull()
  fireEvent.click(completed.getByRole('button', { name: 'Read exact test evidence' }))
  expect(open).toHaveBeenCalledWith(frontend.id)
  expect(screen.getByText('No accepted final result yet.')).toBeTruthy()
})
