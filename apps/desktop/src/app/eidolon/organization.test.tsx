import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it } from 'vitest'

import { createPrototypeAdapter } from '../../../test-fixtures/organization-prototype'

import { demoSnapshot } from './demo'
import { Organization } from './organization'
import { OrganizationWorkspace } from './workspace'

it('opens an example agent inspector without inventing a runtime profile', () => {
  const adapter = createPrototypeAdapter()
  adapter.loadDemo()
  render(<MemoryRouter initialEntries={['/organization']}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  expect(screen.getByText(/Static fictional example/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /Inspect Engineering/ }))
  expect(screen.getByRole('complementary', { name: 'Agent details' })).toBeTruthy()
  expect(screen.getByText('No runtime profile linked')).toBeTruthy()
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(screen.queryByRole('complementary', { name: 'Agent details' })).not.toBeTruthy()
})

it.each(['ltr', 'rtl'])('wraps complete recorded activity in the selected member inspector without clipping (%s)', dir => {
  const snapshot = demoSnapshot()
  const member = snapshot.agents[0]
  const text = 'Synthetic contextual event ' + 'LongUnbrokenEventTitle'.repeat(8) + '\nRetained second line'
  snapshot.activity = [{ ...snapshot.activity[0], agentId: member.id, text }]
  const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'eidolon.css'), 'utf8')
  render(<MemoryRouter><style>{css}</style><div className="eidolon" dir={dir}><Organization snapshot={snapshot} /></div></MemoryRouter>)
  const row = screen.getByRole('button', { name: `Inspect ${member.name}` })
  row.focus()
  fireEvent.click(row)
  const paragraph = screen.getByText(text, { normalizer: value => value })
  expect(paragraph.textContent).toBe(text)
  const style = getComputedStyle(paragraph)
  expect(style.overflowWrap).toBe('anywhere')
  expect(style.whiteSpace).toBe('pre-wrap')
  expect(['hidden', 'clip']).not.toContain(style.overflowX)
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  expect(globalThis.document.activeElement).toBe(row)
})
