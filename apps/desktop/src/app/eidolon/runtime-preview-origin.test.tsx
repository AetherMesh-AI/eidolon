import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { RuntimeObjectiveDetail } from './runtime-detail'
import type { Objective, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

function setup() {
  const objective: Objective = { id: 'goal', ownerId: 'executive', title: 'Goal', description: '', source: 'runtime', status: 'completed', createdAt: '2026-10-16T00:00:00Z', acceptance: { status: 'accepted', round: 1, maxReplans: 2, criteria: [], summary: 'Accepted', deliverableId: 'evidence' } }
  let snapshot: OrganizationSnapshot = { source: 'runtime', connection: { state: 'ready', scope: 'socket', ownerScope: 'owner' }, objectives: [objective], agents: [], tasks: [], requests: [], activity: [], knowledge: [] }
  const adapter = { mode: 'runtime', getEvidence: vi.fn(() => new Promise(() => {})), getToolReceipts: async () => [] } as unknown as RuntimeOrganizationAdapter
  const draw = () => <MemoryRouter><section aria-label="Scroll owner"><RuntimeObjectiveDetail adapter={adapter} objective={snapshot.objectives[0]} snapshot={snapshot} /></section><button>Outside</button></MemoryRouter>
  const view = render(draw())
  const root = screen.getByRole('region', { name: 'Scroll owner' })
  let top = 2083
  let anchored = false
  Object.defineProperty(root, 'scrollTop', { configurable: true, get() {
    // Native layout anchors the existing row when a preview is inserted above it.
    if (!anchored && root.querySelector('.eid-inspector')) { top = 2291; anchored = true }

    return top
  }, set(value) { top = value } })
  const opener = screen.getByRole('button', { name: 'Read final deliverable' })
  opener.focus()

  return { root, opener, view, update(change: Partial<OrganizationSnapshot>) { snapshot = { ...snapshot, ...change }; view.rerender(draw()) } }
}

it('restores the action-time preview origin after insertion anchors the scroll position', () => {
  const f = setup()
  fireEvent.click(f.opener)
  expect(f.root.scrollTop).toBe(2291)
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(globalThis.document.activeElement).toBe(f.opener)
  expect(f.root.scrollTop).toBe(2083)
})

it.each(['owner', 'objective'] as const)('discards captured preview origin when %s changes', changed => {
  const f = setup()
  fireEvent.click(f.opener)
  expect(f.root.scrollTop).toBe(2291)
  f.update(changed === 'owner' ? { connection: { state: 'ready', scope: 'socket', ownerScope: 'replacement' } } : { objectives: [{ id: 'next', ownerId: 'executive', title: 'Next', description: '', source: 'runtime', status: 'active', createdAt: '2026-10-16T00:00:00Z' }] })
  expect(screen.queryByRole('complementary', { name: 'Artifact details' })).toBeNull()
  expect(globalThis.document.activeElement).not.toBe(f.opener)
  expect(f.root.scrollTop).toBe(2291)
})

it('does not override newer outside focus when the preview closes', () => {
  const f = setup()
  fireEvent.click(f.opener)
  expect(f.root.scrollTop).toBe(2291)
  const outside = screen.getByRole('button', { name: 'Outside' })
  outside.focus()
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(globalThis.document.activeElement).toBe(outside)
  expect(f.root.scrollTop).toBe(2291)
})

it('returns a replacement evidence preview to its durable WorkGraph row', () => {
  const f = setup()
  f.update({ tasks: [{ id: 'task', objectiveId: 'goal', title: 'Recorded task', ownerId: 'executive', status: 'completed', dependsOn: [], evidence: [{ id: 'task-evidence', kind: 'note', content: 'Evidence' }] }] })
  const row = screen.getByRole('button', { name: 'Inspect task: Recorded task' })
  row.focus()
  fireEvent.click(row)
  const retainedTop = f.root.scrollTop
  const read = screen.getByRole('button', { name: 'Read full evidence' })
  read.focus()
  f.root.scrollTop = 2400
  fireEvent.click(read)
  expect(read.isConnected).toBe(false)
  expect(screen.queryByRole('complementary', { name: 'Task details' })).toBeNull()
  expect(screen.getByRole('complementary', { name: 'Artifact details' })).toBeTruthy()
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(globalThis.document.activeElement).toBe(row)
  expect(f.root.scrollTop).toBe(retainedTop)
})
