import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway } from './runtime-adapter'
import { RuntimeToolReceipts } from './runtime-tool-receipts'
import type {
  OrganizationArtifact,
  OrganizationSnapshot,
  OrganizationToolEvidence,
  OrganizationToolReceipt
} from './types'
import { OrganizationWorkspace } from './workspace'

function receipt(status: OrganizationToolReceipt['status'], id: string = status): OrganizationToolEvidence {
  return {
    id,
    requestId: 'inspect-request',
    attempt: 1,
    toolCallId: `call-${id}`,
    toolName: 'read_file',
    arguments: { path: `root-1/${id}.md`, offset: 0, limit: 50 },
    status,
    result: status === 'completed' ? 'Full file result' : 'Tool execution was not successful',
    resultPreview: status === 'completed' ? 'File preview' : undefined,
    resultSha256: status === 'completed' ? 'result-digest' : undefined,
    reason: status === 'blocked' ? 'Path is outside the configured roots.' : undefined,
    createdAt: '2026-10-04T00:00:00Z',
    completedAt: status === 'running' ? undefined : '2026-10-04T00:00:01Z'
  }
}

function snapshot(): OrganizationSnapshot {
  return {
    source: 'runtime',
    objectives: [
      {
        id: 'objective',
        title: 'Inspect approved notes',
        description: 'Read root-1/notes.md',
        ownerId: 'manager',
        status: 'needs_input',
        source: 'runtime',
        createdAt: '2026-10-04T00:00:00Z'
      }
    ],
    agents: [],
    tasks: [],
    activity: [],
    knowledge: [
      {
        id: 'proof',
        title: 'Reviewed inspection',
        kind: 'artifact',
        objectiveId: 'objective',
        body: 'Snapshot artifact preview'
      }
    ],
    runtime: {
      state: 'ready',
      scope: 'Approved local file inspection.',
      capabilities: ['work.inspect'],
      maxWorkers: 2,
      readFileEnabled: true,
      readRoots: ['root-1']
    },
    requests: [
      {
        id: 'inspect-request',
        objectiveId: 'objective',
        taskId: 'inspect-task',
        team: 'research',
        type: 'work.inspect',
        priority: 4,
        status: 'pending_intervention',
        reason: 'No active research worker accepts work.inspect.',
        attempts: 1,
        createdAt: '2026-10-04T00:00:00Z',
        toolReceipts: ['completed', 'running', 'blocked', 'failed', 'unknown'].map(status =>
          receipt(status as OrganizationToolReceipt['status'])
        )
      }
    ]
  }
}

function adapterFor(request: (...args: Parameters<OrganizationGateway['request']>) => Promise<unknown>) {
  return createRuntimeAdapter({
    request: async <T,>(...args: Parameters<OrganizationGateway['request']>) => (await request(...args)) as T,
    getScope: () => ({ key: 'configured:profile', connected: true }),
    subscribeScope: () => () => undefined
  })
}

describe('durable tool execution evidence', () => {
  it('shows exact routing and separately counts successful, blocked, failed and unresolved calls through the runtime RPC fixture', async () => {
    const initial = snapshot()

    const request = vi
      .fn()
      .mockImplementation((method: string) =>
        Promise.resolve(method === 'organization.toolReceipts' ? initial.requests![0].toolReceipts : initial)
      )

    const adapter = adapterFor(request)

    const view = render(
      <MemoryRouter initialEntries={['/objectives/objective']}>
        <OrganizationWorkspace adapter={adapter} />
      </MemoryRouter>
    )

    const row = within(await screen.findByRole('button', { name: 'Inspect request: work.inspect' }))
    expect(row.getByText(/Routing \/ intervention reason/)).toBeTruthy()
    expect(row.getByText(initial.requests![0].reason!)).toBeTruthy()
    expect(
      row.getByText('Recent tool calls: Completed: 1 · Running: 1 · Blocked: 1 · Failed: 1 · Unknown: 1')
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Inspect request: work.inspect' }))
    const panel = within(screen.getByRole('complementary', { name: 'Request details' }))
    expect(await panel.findByText('root-1/completed.md')).toBeTruthy()
    expect(panel.getByText('Full file result')).toBeTruthy()
    expect(panel.queryByText('File preview')).toBeNull()
    expect(panel.getByText('Path is outside the configured roots.')).toBeTruthy()
    expect(panel.getAllByText('No successful result recorded.')).toHaveLength(4)
    expect(panel.getByText('result-digest')).toBeTruthy()
    expect(panel.queryByRole('button', { name: /grant|enable|approve/i })).toBeNull()

    const next = {
      ...initial,
      requests: [
        { ...initial.requests![0], toolReceipts: [receipt('completed'), receipt('completed', 'another-call')] }
      ]
    }

    request.mockImplementation((method: string) =>
      Promise.resolve(method === 'organization.toolReceipts' ? next.requests[0].toolReceipts : next)
    )
    await act(() => adapter.refresh())
    expect(await panel.findAllByText('Full file result')).toHaveLength(2)
    expect(panel.queryByText('Path is outside the configured roots.')).toBeNull()
    fireEvent.keyDown(globalThis.document, { key: 'Escape' })
    expect(screen.queryByRole('complementary', { name: 'Request details' })).toBeNull()
    expect(
      request.mock.calls.every(call => ['organization.snapshot', 'organization.toolReceipts'].includes(call[0]))
    ).toBe(true)
    view.unmount()
  })

  it('bounds request previews and treats tool text as inert content, including unrecognized status', () => {
    const hostile = '<img src=x onerror="alert(1)">'

    const receipts = Array.from({ length: 24 }, (_, index) => ({
      ...receipt('completed', `receipt-${index}`),
      resultPreview: `${hostile}${'x'.repeat(2500)}`,
      status: index === 23 ? ('unrecognized' as OrganizationToolReceipt['status']) : ('completed' as const)
    }))

    render(<RuntimeToolReceipts receipts={receipts} />)
    const section = within(screen.getByRole('region', { name: 'Tool execution receipts' }))
    expect(section.getAllByRole('listitem')).toHaveLength(20)
    expect(section.queryByText('receipt-0')).toBeNull()
    expect(section.getByText('receipt-23')).toBeTruthy()
    expect(section.getAllByText(new RegExp('^<img'))[0].textContent).toHaveLength(2000)
    expect(section.queryByRole('img')).toBeNull()
    expect(section.getByText('Unknown')).toBeTruthy()
    expect(section.getByText('No successful result recorded.')).toBeTruthy()
  })

  it('retrieves the exact artifact’s full tool result and leaves closed evidence closed after a late response', async () => {
    const initial = snapshot()

    const artifact: OrganizationArtifact = {
      id: 'proof',
      content: 'Reviewed file findings',
      sha256: 'output-digest',
      summary: 'Inspection',
      objectiveId: 'objective',
      taskId: 'inspect-task',
      createdAt: '2026-10-04T00:00:00Z',
      toolReceipts: [receipt('completed'), receipt('blocked')]
    }

    let resolve!: (value: OrganizationArtifact) => void

    const pending = new Promise<OrganizationArtifact>(yes => {
      resolve = yes
    })

    const request = vi
      .fn()
      .mockImplementation((method: string) => (method === 'organization.evidence' ? pending : Promise.resolve(initial)))

    const view = render(
      <MemoryRouter initialEntries={['/knowledge']}>
        <OrganizationWorkspace adapter={adapterFor(request)} />
      </MemoryRouter>
    )

    const opener = await screen.findByRole('button', { name: /Reviewed inspection/ })
    opener.focus()
    fireEvent.click(opener)
    await waitFor(() =>
      expect(request.mock.calls.some(call => call[0] === 'organization.evidence' && call[1].id === artifact.id)).toBe(
        true
      )
    )
    fireEvent.keyDown(globalThis.document, { key: 'Escape' })
    await act(async () => {
      resolve(artifact)
      await pending
    })
    expect(screen.queryByRole('complementary', { name: 'Artifact details' })).toBeNull()
    expect(globalThis.document.activeElement).toBe(opener)
    fireEvent.click(opener)
    const panel = within(screen.getByRole('complementary', { name: 'Artifact details' }))
    expect(await panel.findByText(artifact.content)).toBeTruthy()
    expect(panel.getByText('Full file result')).toBeTruthy()
    expect(panel.queryByText('File preview')).toBeNull()
    expect(panel.getByText('result-digest')).toBeTruthy()
    expect(panel.getByText('Path is outside the configured roots.')).toBeTruthy()
    expect(panel.getByText('No successful result recorded.')).toBeTruthy()
    expect(panel.getByText(/belong to the work request that produced this artifact/)).toBeTruthy()
    view.unmount()
  })
})

it('preserves staffing routes and loads omitted request history only on inspection, with a recoverable audit read', async () => {
  const initial = snapshot()
  initial.requests![0] = { ...initial.requests![0], toolReceipts: [], toolReceiptCount: 2, toolReceiptsTruncated: true }
  initial.requests!.push({
    id: 'hire-request',
    objectiveId: 'objective',
    type: 'request.hire',
    team: 'control',
    priority: 4,
    status: 'pending_intervention',
    reason: 'No configured worker matches the requested teams.',
    attempts: 1,
    createdAt: '2026-10-04T00:00:00Z',
    requestedWorkers: 2,
    requestedRoutes: [
      { team: 'research', type: 'work.inspect' },
      { team: 'editorial', type: 'work.draft' }
    ],
    toolReceiptCount: 0,
    toolReceipts: []
  })

  const audit = vi
    .fn()
    .mockRejectedValueOnce(new Error('Audit read interrupted'))
    .mockResolvedValue([{ ...receipt('running'), result: undefined }, receipt('completed')])

  const request = vi
    .fn()
    .mockImplementation((method: string) =>
      method === 'organization.toolReceipts' ? audit() : Promise.resolve(initial)
    )

  const view = render(
    <MemoryRouter initialEntries={['/objectives/objective']}>
      <OrganizationWorkspace adapter={adapterFor(request)} />
    </MemoryRouter>
  )

  const hire = await screen.findByRole('button', { name: 'Inspect request: request.hire' })
  expect(audit).not.toHaveBeenCalled()
  expect(
    within(hire).getByText('Requested worker routes: work.inspect → research; work.draft → editorial')
  ).toBeTruthy()
  fireEvent.click(hire)
  const hirePanel = within(screen.getByRole('complementary', { name: 'Request details' }))
  expect(hirePanel.getByText('control')).toBeTruthy()
  expect(hirePanel.getByText('work.inspect → research; work.draft → editorial')).toBeTruthy()
  expect(hirePanel.getByText('Requested worker capacity')).toBeTruthy()
  expect(audit).not.toHaveBeenCalled()
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(screen.getByText('Full request audit available · recorded calls: 2')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Inspect request: work.inspect' }))
  expect(await screen.findByText('Audit read interrupted')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Retry tool receipts' }))
  const panel = within(screen.getByRole('complementary', { name: 'Request details' }))
  expect(await panel.findByText('Full file result')).toBeTruthy()
  expect(panel.getByText('No successful result recorded.')).toBeTruthy()
  expect(
    panel.getByText('Tool execution receipts: Completed: 1 · Running: 1 · Blocked: 0 · Failed: 0 · Unknown: 0')
  ).toBeTruthy()
  expect(request.mock.calls.filter(call => call[0] === 'organization.toolReceipts').map(call => call[1])).toEqual([
    { id: 'inspect-request' },
    { id: 'inspect-request' }
  ])
  expect(
    request.mock.calls.every(call => ['organization.snapshot', 'organization.toolReceipts'].includes(call[0]))
  ).toBe(true)
  view.unmount()
})
