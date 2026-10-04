import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it } from 'vitest'

import { demoSnapshot } from './demo'
import { OrganizationWorkspace } from './workspace'

const adapter = () => {const snapshot = demoSnapshot();

 return { mode: 'prototype' as const, getSnapshot: () => snapshot, subscribe: () => () => undefined }}

it('keeps static examples inspectable without any writable prototype controls', () => {
  const example = adapter()
  const before = JSON.stringify(example.getSnapshot())
  render(<MemoryRouter initialEntries={['/objectives/demo-identity']}><OrganizationWorkspace adapter={example} /></MemoryRouter>)
  expect(screen.queryByRole('button', { name: /create objective|record local outcome|approve locally|save local metadata|clear local data|load example/i })).toBeNull()
  expect(screen.queryByRole('combobox', { name: 'Local objective state' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Inspect objective' }))
  const panel = within(screen.getByRole('complementary', { name: 'Objective details' }))
  expect(panel.getByText(/does not enforce permissions/)).toBeTruthy()
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(screen.queryByRole('complementary')).toBeNull()
  expect(JSON.stringify(example.getSnapshot())).toBe(before)
})
it('never offers intake or mutations for a static example home', () => {
  render(<MemoryRouter initialEntries={['/home']}><OrganizationWorkspace adapter={adapter()} /></MemoryRouter>)
  expect(screen.queryByRole('textbox', { name: 'Objective' })).toBeNull()
  expect(screen.getAllByRole('link').some(link => link.getAttribute('href')?.startsWith('/objectives/'))).toBe(true)
})
