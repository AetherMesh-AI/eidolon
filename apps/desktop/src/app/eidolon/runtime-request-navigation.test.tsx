import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { RuntimeRequests } from './runtime-requests'
import type { OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

function setup() {
  let current: OrganizationSnapshot = {
    source: 'runtime',
    connection: { state: 'ready', scope: 'socket-a', ownerScope: 'owner-a' },
    objectives: [],
    agents: [],
    activity: [],
    knowledge: [],
    tasks: [
      {
        id: 'task',
        objectiveId: 'goal',
        title: 'Draft the board brief',
        ownerId: 'writer',
        status: 'blocked',
        dependsOn: [],
        inputs: ['Preserve the exact technical appendix.']
      }
    ],
    requests: [
      {
        id: 'question',
        objectiveId: 'goal',
        type: 'request.question',
        team: 'general',
        priority: 3,
        status: 'pending_intervention',
        attempts: 1,
        createdAt: '2026-10-09T00:00:00Z',
        parentRequestId: 'draft',
        requestedOutcome: 'Which audience?',
        allowedResolutions: [
          { action: 'answer_request', label: 'Answer request', requiresText: true, requiresEvidence: false }
        ]
      },
      {
        id: 'draft',
        objectiveId: 'goal',
        taskId: 'task',
        type: 'work.draft',
        team: 'general',
        priority: 3,
        status: 'waiting_response',
        attempts: 1,
        createdAt: '2026-10-09T00:00:00Z',
        requestedOutcome: 'Draft the board brief'
      }
    ]
  }

  const mutation = vi.fn()

  const adapter: RuntimeOrganizationAdapter = {
    mode: 'runtime',
    getSnapshot: () => current,
    subscribe: () => () => undefined,
    refresh: mutation,
    cancelObjective: mutation,
    retryRequest: mutation,
    resolveRequest: mutation,
    respondRequest: mutation,
    configureOrganization: mutation,
    createObjective: mutation,
    getEvidence: mutation,
    getToolReceipts: async () => []
  }

  const tree = () => (
    <MemoryRouter>
      <RuntimeRequests adapter={adapter} snapshot={current} />
    </MemoryRouter>
  )

  const view = render(tree())

  return {
    view,
    mutation,
    snapshot: () => current,
    update(next: OrganizationSnapshot) {
      current = next
      view.rerender(tree())
    }
  }
}

const details = () => within(screen.getByRole('complementary', { name: 'Request details' }))

it('opens the parent assignment outside queue filters, preserves the response on Back, and closes the full trail', () => {
  const fixture = setup()
  fireEvent.click(screen.getByRole('button', { name: 'Inspect request: request.question' }))
  fireEvent.change(details().getByRole('textbox', { name: 'Response' }), {
    target: { value: 'Board and technical leads.' }
  })
  const parent = details().getByRole('button', { name: 'Open parent request' })
  parent.focus()
  fireEvent.click(parent)
  expect(screen.getAllByRole('complementary', { name: 'Request details' })).toHaveLength(1)
  expect(details().getByText('Preserve the exact technical appendix.')).toBeTruthy()
  expect(details().queryByRole('textbox', { name: 'Response' })).toBeNull()
  fireEvent.click(details().getByRole('button', { name: 'Back' }))
  expect(details().getByRole('textbox', { name: 'Response' })).toHaveProperty('value', 'Board and technical leads.')
  expect(fixture.view.container.ownerDocument.activeElement).toBe(
    details().getByRole('button', { name: 'Open parent request' })
  )
  fireEvent.click(details().getByRole('button', { name: 'Open parent request' }))
  fireEvent.click(details().getByRole('button', { name: 'Close request details' }))
  expect(screen.queryByRole('complementary', { name: 'Request details' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Inspect request: request.question' }))
  expect(details().getByRole('textbox', { name: 'Response' })).toHaveProperty('value', '')
  fireEvent.click(details().getByRole('button', { name: 'Open parent request' }))
  fireEvent.keyDown(fixture.view.container.ownerDocument, { key: 'Escape' })
  expect(screen.queryByRole('complementary', { name: 'Request details' })).toBeNull()
  expect(fixture.mutation).not.toHaveBeenCalled()
})

it.each(['removed', 'cross-objective', 'changed-parent', 'profile', 'cycle'])(
  'fences %s navigation without reopening stale details',
  mode => {
    const fixture = setup()
    const initial = fixture.snapshot()
    fireEvent.click(screen.getByRole('button', { name: 'Inspect request: request.question' }))

    if (mode === 'cycle') {
      fixture.update({
        ...initial,
        requests: initial.requests!.map(row => (row.id === 'question' ? { ...row, parentRequestId: row.id } : row))
      })
      expect(details().queryByRole('button', { name: 'Open parent request' })).toBeNull()
    } else {
      fireEvent.click(details().getByRole('button', { name: 'Open parent request' }))
      fixture.update({
        ...initial,
        connection:
          mode === 'profile' ? { state: 'ready', scope: 'socket-b', ownerScope: 'owner-b' } : initial.connection,
        requests: initial.requests!.flatMap(row => {
          if (row.id === 'draft' && mode === 'removed') {
            return []
          }

          if (row.id === 'draft' && mode === 'cross-objective') {
            return [{ ...row, objectiveId: 'other-goal' }]
          }

          if (row.id === 'question' && mode === 'changed-parent') {
            return [{ ...row, parentRequestId: 'different-parent' }]
          }

          return [row]
        })
      })
      expect(screen.queryByRole('complementary', { name: 'Request details' })).toBeNull()
      fixture.update(initial)
      expect(screen.queryByRole('complementary', { name: 'Request details' })).toBeNull()
    }

    expect(fixture.mutation).not.toHaveBeenCalled()
  }
)

it.each(['unchanged', 'ancestor', 'back', 'unrelated', 'reopened'])(
  'records a delayed response without dismissing newer %s navigation',
  async mode => {
    const fixture = setup()
    const initial = fixture.snapshot()
    fixture.update({
      ...initial,
      requests: [
        ...initial.requests!,
        { ...initial.requests![0], id: 'other', type: 'request.decision', requestedOutcome: 'Another decision' }
      ]
    })
    let finish!: () => void
    fixture.mutation.mockImplementation(
      () =>
        new Promise<void>(resolve => {
          finish = resolve
        })
    )
    fireEvent.click(screen.getByRole('button', { name: 'Inspect request: request.question' }))
    fireEvent.change(details().getByRole('textbox', { name: 'Response' }), { target: { value: 'Board audience.' } })
    fireEvent.click(details().getByRole('button', { name: 'Submit response' }))
    expect(fixture.mutation).toHaveBeenCalledTimes(1)
    expect(fixture.mutation).toHaveBeenCalledWith({ id: 'question', text: 'Board audience.', decision: 'answered' })

    if (mode !== 'unchanged') {
      fireEvent.click(details().getByRole('button', { name: 'Open parent request' }))

      if (mode === 'back') {
        fireEvent.click(details().getByRole('button', { name: 'Back' }))
      } else if (mode === 'unrelated') {
        fireEvent.click(screen.getByRole('button', { name: 'Inspect request: request.decision' }))
        fireEvent.change(details().getByRole('textbox', { name: 'Response' }), {
          target: { value: 'New decision draft.' }
        })
      } else if (mode === 'reopened') {
        fireEvent.click(details().getByRole('button', { name: 'Close request details' }))
        fireEvent.click(screen.getByRole('button', { name: 'Inspect request: request.question' }))
        fireEvent.change(details().getByRole('textbox', { name: 'Response' }), {
          target: { value: 'New question draft.' }
        })
      }
    }

    await act(async () => {
      finish()
    })

    if (mode === 'unchanged') {
      expect(screen.queryByRole('complementary', { name: 'Request details' })).toBeNull()
    } else if (mode === 'ancestor') {
      expect(details().getByText('Preserve the exact technical appendix.')).toBeTruthy()
    } else {
      const expected =
        mode === 'back' ? 'Board audience.' : mode === 'unrelated' ? 'New decision draft.' : 'New question draft.'

      expect(details().getByRole('textbox', { name: 'Response' })).toHaveProperty('value', expected)
    }

    // The original operation still completes authoritatively after navigation.
    fixture.update({
      ...fixture.snapshot(),
      requests: fixture
        .snapshot()
        .requests!.map(row =>
          row.id === 'question'
            ? {
                ...row,
                status: 'completed',
                allowedResolutions: [],
                response: {
                  text: 'Board audience.',
                  decision: 'answered',
                  responderId: 'owner',
                  createdAt: '2026-10-09T01:00:00Z'
                }
              }
            : row
        )
    })

    if (mode === 'ancestor') {
      fireEvent.click(details().getByRole('button', { name: 'Back' }))
      expect(details().getByText('Board audience.')).toBeTruthy()
      expect(details().queryByRole('button', { name: 'Submit response' })).toBeNull()
    }

    expect(fixture.mutation).toHaveBeenCalledTimes(1)
  }
)
