import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'

import { MemoryWeb } from './memory-web'
afterEach(cleanup)
it('projects an interactive 3D web with an accessible inspection list and honest provenance', () => {
  render(<MemoryWeb items={[{ id: 'a', title: 'Retained note', body: 'Local content', kind: 'memory' }]} />)
  expect(screen.getByText(/Timestamps unknown/)).toBeTruthy()
  const scene = screen.getByRole('img', { name: 'Memory relationship web' })
  const before = scene.innerHTML
  fireEvent.click(screen.getByRole('button', { name: 'Rotate right' }))
  expect(scene.innerHTML).not.toBe(before)
  fireEvent.click(screen.getByRole('button', { name: 'Retained note' }))
  expect(screen.getByText('Local content')).toBeTruthy()
  fireEvent.change(screen.getByRole('searchbox', { name: 'Search memories' }), { target: { value: 'absent' } })
  expect(screen.queryByRole('button', { name: 'Retained note' })).toBeNull()
})
