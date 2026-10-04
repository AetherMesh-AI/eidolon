import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it } from 'vitest'

import { createPrototypeAdapter } from '../../../test-fixtures/organization-prototype'

import { Activity } from './activity'

it('offers the brief categories with opt-in fictional events and observable details', () => {
 const adapter = createPrototypeAdapter()
 expect(adapter.getSnapshot().activity).toEqual([])
 adapter.createObjective('Local planning only')
 expect(adapter.getSnapshot().activity.some(event => event.id.startsWith('demo-'))).toBe(false)
 adapter.loadDemo()
 const snapshot = adapter.getSnapshot()
 render(<MemoryRouter><Activity snapshot={snapshot} /></MemoryRouter>)
 const filter = screen.getByRole('combobox', { name: 'Event type' })
 const expected = { Assignments: 'delegation', Decisions: 'decision', Tools: 'tool', Files: 'file', Reviews: 'approval', System: 'system' }

 for (const [label, kind] of Object.entries(expected)) {
  const option = within(filter).getByRole('option', { name: label }) as HTMLOptionElement
  fireEvent.change(filter, { target: { value: option.value } })
  const example = snapshot.activity.find(event => event.kind === kind && event.id.startsWith('demo-'))!
  expect(example).toBeTruthy()
  const otherCategory = snapshot.activity.find(event => event.kind === (kind === 'tool' ? 'file' : 'tool'))!
  expect(screen.queryByRole('button', { name: `Inspect event: ${otherCategory.text}` })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: `Inspect event: ${example.text}` }))
  const inspector = screen.getByRole('complementary', { name: 'Event details' })
  expect(within(inspector).getByText('Fictional example · Not live')).toBeTruthy()
  expect(within(inspector).getByText(example.description!)).toBeTruthy()
  expect(within(inspector).getByRole('link', { name: 'Open objective' }).getAttribute('href')).toBe(`/objectives/${example.objectiveId}`)
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
 }
})

it('keeps event inspectors inside the current objective scope', () => {
 const adapter = createPrototypeAdapter()
 adapter.loadDemo()
 const snapshot = adapter.getSnapshot()
 const example = snapshot.activity.find(event => event.kind === 'tool')!
 const { rerender } = render(<MemoryRouter><Activity objectiveId="demo-identity" snapshot={snapshot} /></MemoryRouter>)
 fireEvent.change(screen.getByRole('combobox', { name: 'Event type' }), { target: { value: 'tool' } })
 expect(screen.getAllByRole('button', { name: /Inspect event:/ })).toHaveLength(1)
 fireEvent.click(screen.getByRole('button', { name: `Inspect event: ${example.text}` }))
 rerender(<MemoryRouter><Activity objectiveId="another-objective" snapshot={snapshot} /></MemoryRouter>)
 expect(screen.queryByRole('complementary', { name: 'Event details' })).toBeNull()
 expect(screen.queryByRole('button', { name: /Inspect event:/ })).toBeNull()
})

it('filters organization events and opens scoped details without dispatching', () => {
 const adapter = createPrototypeAdapter()
 adapter.createObjective('Prepare release notes')
 render(<MemoryRouter><Activity snapshot={adapter.getSnapshot()} /></MemoryRouter>)
 fireEvent.click(screen.getAllByRole('button', { name: /Inspect event:/ })[0])
 expect(screen.getByRole('complementary', { name: 'Event details' })).toBeTruthy()
 expect(screen.getByRole('link', { name: 'Open objective' })).toBeTruthy()
 fireEvent.keyDown(globalThis.document, { key: 'Escape' })
 fireEvent.change(screen.getByRole('searchbox', { name: 'Search activity' }), { target: { value: 'no-match' } })
 expect(screen.getByText('No matching events')).toBeTruthy()
})
