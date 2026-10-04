import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it } from 'vitest'

import { demoSnapshot } from './demo'
import { WorkGraph } from './work-graph'
it('shows dependency and task inspection without claiming runtime execution', () => {
 const snapshot = demoSnapshot()
 render(<MemoryRouter><WorkGraph objectiveId="demo-identity" snapshot={snapshot} /></MemoryRouter>)
 fireEvent.click(screen.getByRole('button', { name: /Inspect task: Implement/ }))
 expect(screen.getByRole('complementary', { name: 'Task details' })).toBeTruthy()
 expect(screen.getByText('No runtime session linked')).toBeTruthy()
})
