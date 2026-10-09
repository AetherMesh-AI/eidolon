import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { Command } from './command'
import { OrganizationRail } from './rail'
import { RuntimeStatus } from './runtime-detail'
import type { OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

function fixture() {
  const snapshot: OrganizationSnapshot = {
    source: 'runtime', connection: { scope: 'visual-test', state: 'ready' },
    objectives: [], agents: [], tasks: [], knowledge: [], activity: [],
    runtime: { capabilities: ['work.draft'], state: 'ready', maxWorkers: 2, scope: 'Explicit scope only.' }
  }

  const createObjective = vi.fn().mockRejectedValue(new Error('Admission paused for this test'))
  const refresh = vi.fn().mockResolvedValue(undefined)
  const adapter = { mode: 'runtime', createObjective, refresh } as unknown as RuntimeOrganizationAdapter

  return { snapshot, adapter, createObjective, refresh }
}

it('keeps optional draft values through repeated disclosure and submits their exact values', async () => {
  const { snapshot, adapter, createObjective } = fixture()
  render(<MemoryRouter><Command adapter={adapter} snapshot={snapshot} /></MemoryRouter>)
  const context = screen.getByText('Context', { selector: 'summary' }).closest('details')!
  const criteria = screen.getByText('Acceptance criteria', { selector: 'summary' }).closest('details')!
  const verification = screen.getByText('Verification and priority', { selector: 'summary' }).closest('details')!
  expect(context.open).toBe(false)
  expect(criteria.open).toBe(false)
  expect(verification.open).toBe(false)
  fireEvent.click(context.querySelector('summary')!)
  const input = screen.getByRole('textbox', { name: 'Submitted context (optional)' })
  fireEvent.change(input, { target: { value: 'Keep this context' } })

  for (let cycle = 0; cycle < 3; cycle++) {
    fireEvent.click(context.querySelector('summary')!)
    expect(context.open).toBe(false)
    fireEvent.click(context.querySelector('summary')!)
    expect(context.open).toBe(true)
    expect(screen.getByRole('textbox', { name: 'Submitted context (optional)' })).toBe(input)
    expect(input).toHaveProperty('value', 'Keep this context')
  }

  fireEvent.click(criteria.querySelector('summary')!)
  fireEvent.change(screen.getByRole('textbox', { name: 'Acceptance criteria' }), { target: { value: 'Preserve the workflow' } })
  fireEvent.click(criteria.querySelector('summary')!)
  fireEvent.change(screen.getByRole('textbox', { name: 'Objective' }), { target: { value: 'Polish the interface' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create objective' }))
  await screen.findByRole('alert')
  expect(createObjective).toHaveBeenCalledWith('Polish the interface', expect.objectContaining({ description: 'Keep this context', acceptanceCriteria: ['Preserve the workflow'], deliveryMode: 'source_project' }))
})

it('reveals invalid optional criteria without dispatching the objective', () => {
  const { snapshot, adapter, createObjective } = fixture()
  render(<MemoryRouter><Command adapter={adapter} snapshot={snapshot} /></MemoryRouter>)
  const details = screen.getByText('Acceptance criteria', { selector: 'summary' }).closest('details')!
  fireEvent.click(details.querySelector('summary')!)
  fireEvent.change(screen.getByRole('textbox', { name: 'Acceptance criteria' }), { target: { value: Array.from({ length: 13 }, (_, i) => `Criterion ${i}`).join('\n') } })
  fireEvent.click(details.querySelector('summary')!)
  fireEvent.change(screen.getByRole('textbox', { name: 'Objective' }), { target: { value: 'Review the design' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create objective' }))
  expect(details.open).toBe(true)
  expect(screen.getByRole('alert')).toBeTruthy()
  expect(createObjective).not.toHaveBeenCalled()
})

it('keeps connection errors and retry outside collapsed runtime configuration', () => {
  const { snapshot, adapter, refresh } = fixture()
  snapshot.connection = { scope: 'visual-test', state: 'error', error: 'Gateway unreachable' }
  render(<RuntimeStatus adapter={adapter} snapshot={snapshot} />)
  const region = screen.getByRole('region', { name: 'Organization runtime' })
  const details = region.querySelector('details')!
  expect(details.open).toBe(false)
  const alert = within(region).getByRole('alert')
  expect(alert.textContent).toBe('Gateway unreachable')
  expect(details.contains(alert)).toBe(false)
  const retry = within(region).getByRole('button', { name: 'Retry connection' })
  expect(details.contains(retry)).toBe(false)
  fireEvent.click(retry)
  expect(refresh).toHaveBeenCalledOnce()
})

it('keeps home history compact while preserving its mounted input when expanded', () => {
  render(<MemoryRouter initialEntries={['/home']}><OrganizationRail sessions={<input aria-label="Existing history filter" defaultValue="Retained" />} /></MemoryRouter>)
  const history = screen.getByText('History', { selector: 'summary' }).closest('details')!
  expect(history.open).toBe(false)
  const input = history.querySelector('input')!
  fireEvent.click(history.querySelector('summary')!)
  expect(history.open).toBe(true)
  expect(screen.getByRole('textbox', { name: 'Existing history filter' })).toBe(input)
  expect(input.value).toBe('Retained')
  expect(screen.getByRole('navigation', { name: 'Settings' })).toBeTruthy()
})
