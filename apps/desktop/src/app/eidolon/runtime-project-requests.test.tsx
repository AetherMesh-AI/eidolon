import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway } from './runtime-adapter'
import type { OrganizationSnapshot } from './types'
import { OrganizationWorkspace } from './workspace'

it('keeps every controlled stage filterable and a failed project execution actionable only through backend-owned resolutions', async () => {
  const stages = ['request.project_test', 'request.test_review', 'request.source_integrate', 'request.project_failed']

  const snapshot: OrganizationSnapshot = {
    source: 'runtime',
    agents: [],
    tasks: [],
    activity: [],
    knowledge: [],
    objectives: [
      {
        id: 'objective',
        title: 'Run bounded tests',
        description: 'Exact files',
        status: 'needs_input',
        source: 'runtime',
        ownerId: 'executive',
        createdAt: '2026-10-05T00:00:00Z'
      }
    ],
    runtime: { state: 'ready', capabilities: ['work.inspect'], maxWorkers: 2, scope: 'Explicitly granted files' },
    requests: stages.map((type, index) => ({
      id: `stage-${index}`,
      objectiveId: 'objective',
      type,
      team: 'engineering',
      priority: 3,
      attempts: 1,
      createdAt: '2026-10-05T00:00:00Z',
      status: index === 3 ? 'pending_intervention' : 'completed',
      reason: index === 3 ? 'Required isolation is unavailable; no project test success was recorded.' : undefined,
      allowedResolutions:
        index === 3
          ? [{ action: 'request_replan', label: 'Request bounded replan', requiresText: true, requiresEvidence: false }]
          : []
    }))
  }

  const request = vi.fn().mockResolvedValue(snapshot)

  const adapter = createRuntimeAdapter({
    request: request as OrganizationGateway['request'],
    getScope: () => ({ key: 'current-profile', connected: true }),
    subscribeScope: () => () => undefined
  })

  const view = render(
    <MemoryRouter initialEntries={['/requests']}>
      <OrganizationWorkspace adapter={adapter} />
    </MemoryRouter>
  )

  await screen.findByRole('button', { name: 'Inspect request: request.project_failed' })
  expect(screen.getByText('Required isolation is unavailable; no project test success was recorded.')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Inspect request: request.project_test' })).toBeNull()
  fireEvent.change(screen.getByRole('combobox', { name: 'Status' }), { target: { value: 'all' } })

  for (const type of stages) {
    expect(screen.getByRole('button', { name: `Inspect request: ${type}` })).toBeTruthy()
  }

  fireEvent.change(screen.getByRole('combobox', { name: 'Type' }), { target: { value: 'request.project_failed' } })
  expect(screen.queryByRole('button', { name: 'Inspect request: request.test_review' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Respond to request' }))
  const details = within(screen.getByRole('complementary', { name: 'Request details' }))
  expect(details.getByRole('option', { name: 'Request bounded replan' })).toBeTruthy()
  expect(details.queryByRole('option', { name: /approve|retry/i })).toBeNull()
  expect(details.getByRole('button', { name: 'Submit response' })).toHaveProperty('disabled', true)
  fireEvent.change(details.getByRole('textbox', { name: 'Response' }), {
    target: { value: 'Review the host limitation before replanning.' }
  })
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(screen.queryByRole('complementary')).toBeNull()
  expect(request.mock.calls.every(call => call[0] === 'organization.snapshot')).toBe(true)
  expect(screen.queryByRole('button', { name: /run tests|mark complete|grant/i })).toBeNull()
  view.unmount()
})
