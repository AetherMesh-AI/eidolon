import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway } from './runtime-adapter'
import { RuntimeExecutionAudit } from './runtime-execution-audit'
import type { OrganizationExecutionAudit, OrganizationRequest } from './types'

const request: OrganizationRequest = {
  id: 'review',
  objectiveId: 'goal',
  type: 'request.review',
  team: 'review',
  priority: 3,
  status: 'completed',
  attempts: 1,
  createdAt: '2026-10-04T00:00:00Z'
}

function report(): OrganizationExecutionAudit {
  const createdAt = '2026-10-04T00:00:00Z'

  return {
    requestId: 'review',
    contexts: [
      {
        attemptToken: 'one',
        createdAt,
        report: {
          mode: 'hierarchical',
          status: 'complete',
          fullEvidence: false,
          sources: [{ key: 'body:source-digest', sha256: 'source-digest', fullLength: 128, unit: 'utf8-bytes' }]
        }
      }
    ],
    evidencePasses: [
      {
        attemptToken: 'one',
        createdAt,
        report: {
          sourceKey: 'body:source-digest',
          sourceSha256: 'source-digest',
          chunkSha256: 'chunk-digest',
          start: 0,
          end: 128,
          unit: 'utf8-bytes',
          approved: false,
          findings: 'Unresolved conflicting source detail',
          conflicts: ['Conflicting launch dates']
        }
      }
    ],
    modelCalls: [
      {
        id: 'call',
        request_id: 'review',
        provider: 'configured-provider',
        model: 'configured-model',
        input_limit: 1000,
        output_limit: 500,
        reserved_cost_usd: null,
        createdAt
      }
    ]
  }
}

function setup(read = vi.fn().mockResolvedValue(report())) {
  const adapter = createRuntimeAdapter({
    request: read as OrganizationGateway['request'],
    getScope: () => ({ key: 'owner-scope', connected: true }),
    subscribeScope: () => () => undefined
  })

  const view = render(<RuntimeExecutionAudit adapter={adapter} request={request} />)

  return { adapter, read, view }
}

it('loads lazily once across unchanged polls and exposes exact hashes, ranges, negative findings and unknown reservations', async () => {
  const fixture = setup()
  expect(fixture.read).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Inspect model and evidence audit' }))
  const context = await screen.findByRole('region', { name: 'Context records' })
  expect(context.textContent).toContain('Context mode: hierarchical')
  expect(context.textContent).toContain('source-digest')
  const passes = within(screen.getByRole('region', { name: 'Exact evidence-read passes' }))
  expect(passes.getByText(/Unresolved conflicting source detail/).textContent).toContain('"start": 0')
  expect(passes.getByText(/Unresolved conflicting source detail/).textContent).toContain('"end": 128')
  expect(passes.getByText(/Unresolved conflicting source detail/).textContent).toContain('"approved": false')
  expect(screen.getByText(/Configured call reservation \(USD\): Unknown/)).toBeTruthy()
  expect(screen.getByText(/does not itself mean the objective was accepted/)).toBeTruthy()
  fixture.view.rerender(<RuntimeExecutionAudit adapter={fixture.adapter} request={{ ...request }} />)
  await act(async () => {
    await Promise.resolve()
  })
  expect(fixture.read).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  await waitFor(() => expect(fixture.read).toHaveBeenCalledTimes(2))
  fixture.view.unmount()
})

it('keeps errors recoverable and fences dismissed or replaced request reads without rendering old evidence', async () => {
  let finish!: (value: OrganizationExecutionAudit) => void

  const first = new Promise<OrganizationExecutionAudit>(yes => {
    finish = yes
  })

  const fixture = setup(
    vi
      .fn()
      .mockReturnValueOnce(first)
      .mockRejectedValueOnce(new Error('Audit read interrupted'))
      .mockResolvedValue(report())
  )

  fireEvent.click(screen.getByRole('button', { name: 'Inspect model and evidence audit' }))
  fixture.view.rerender(
    <RuntimeExecutionAudit adapter={fixture.adapter} key="next-request" request={{ ...request, id: 'next-request' }} />
  )
  await act(async () => {
    finish(report())
    await Promise.resolve()
  })
  expect(screen.queryByText(/Unresolved conflicting/)).toBeNull()
  expect(fixture.read).toHaveBeenCalledTimes(1)
  fixture.view.rerender(<RuntimeExecutionAudit adapter={fixture.adapter} key="review-again" request={request} />)
  fireEvent.click(screen.getByRole('button', { name: 'Inspect model and evidence audit' }))
  expect((await screen.findByRole('alert')).textContent).toBe('Audit read interrupted')
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  await screen.findByRole('region', { name: 'Context records' })
  expect(fixture.read).toHaveBeenCalledTimes(3)
  fixture.view.unmount()
})
