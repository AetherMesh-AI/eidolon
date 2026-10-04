import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it } from 'vitest'

import { demoSnapshot } from './demo'
import { Organization } from './organization'
import { WorkGraph } from './work-graph'

it('finds responsibility and shows honest contextual agent details', () => {
 const s = demoSnapshot()
 s.agents[1].responsibilities = ['Release assurance']
 render(<MemoryRouter><Organization snapshot={s} /></MemoryRouter>)
 fireEvent.change(screen.getByPlaceholderText('Name or responsibility…'), { target: { value: 'Release assurance' } })
 fireEvent.click(screen.getByRole('button', { name: 'Inspect Engineering' }))
 const panel = within(screen.getByRole('complementary', { name: 'Agent details' }))

 for (const label of ['Status', 'Reports to', 'Direct reports', 'Model', 'Context usage / capacity', 'Tools']) {expect(panel.getByText(label)).toBeTruthy()}
 expect(panel.getAllByText('Unavailable · No runtime connection').length).toBeGreaterThanOrEqual(3)
 expect(panel.getByLabelText('Engineering avatar')).toBeTruthy()
})

it('exposes task provenance, progress and explicit unknown runtime fields', () => {
 const s = demoSnapshot()
 render(<WorkGraph objectiveId="demo-identity" snapshot={s} />)
 fireEvent.click(screen.getByRole('button', { name: 'Inspect task: Implement account adapter' }))
 const panel = within(screen.getByRole('complementary', { name: 'Task details' }))

 for (const label of ['Assigned by', 'Priority', 'Review', 'Dependency completion', 'Inputs', 'Results']) {expect(panel.getByText(label)).toBeTruthy()}
 expect(panel.getByText('2 / 2 complete')).toBeTruthy()
 expect(screen.getByRole('button', { name: 'Inspect task: Review token policy' }).className).toContain('eid-task-blocked')
})

it('provides coherent fictional objectives across active, waiting and completed states', () => {
 const s = demoSnapshot()
 expect(s.objectives).toHaveLength(3)
 expect(new Set(s.objectives.map(o => o.status))).toEqual(new Set(['active', 'waiting', 'completed']))

 for (const task of s.tasks) {expect(s.objectives.some(o => o.id === task.objectiveId)).toBe(true)}
})
