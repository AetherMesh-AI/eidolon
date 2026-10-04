import { fireEvent, render, screen } from '@testing-library/react'
import { atom } from 'nanostores'
import { beforeEach, expect, it, vi } from 'vitest'

import type { ComposerStatusItem } from '@/store/composer-status'

import type { TerminalEntry } from './terminals'

const state = vi.hoisted(() => ({ terminal: null as unknown, processes: null as unknown }))
vi.mock('./terminals', () => {
  state.terminal = atom<TerminalEntry | null>(null)
  return { $activeTerminal: state.terminal, $terminals: atom<TerminalEntry[]>([]) }
})
vi.mock('@/store/composer-status', () => {
  state.processes = atom<Record<string, ComposerStatusItem[]>>({})
  return { $backgroundStatusBySession: state.processes }
})
vi.mock('./persistent', () => ({ TerminalSlot: () => <div>Terminal body</div> }))
vi.mock('./rail', () => ({ TerminalRail: () => <div>Terminal rail</div> }))
import { $backgroundStatusBySession } from '@/store/composer-status'

import { TerminalPaneChrome } from './chrome'
import { $activeTerminal } from './terminals'
const terminal = () => $activeTerminal as ReturnType<typeof atom<TerminalEntry | null>>
const processes = () => $backgroundStatusBySession
beforeEach(() => { terminal().set(null); processes().set({}) })

it('inspects an agent process using reported runtime status and output', () => {
  terminal().set({ id: 'mirror', title: 'Build mirror', kind: 'agent', auto: false, cwd: '/example', procId: 'proc-7' })
  processes().set({ 'runtime-owner': [{ id: 'proc-7', title: 'Build package', type: 'background', state: 'failed', exitCode: 2, output: 'Build rejected' }] })
  render(<TerminalPaneChrome />)
  fireEvent.click(screen.getByRole('button', { name: 'Inspect process' }))
  expect(screen.getByRole('complementary', { name: 'Process details' })).toBeTruthy()
  expect(screen.getByText('runtime-owner')).toBeTruthy()
  expect(screen.getByText('failed')).toBeTruthy()
  expect(screen.getByText('2', { selector: 'dd' })).toBeTruthy()
  expect(screen.getByText('Build rejected')).toBeTruthy()
})

it.each([false, true])('does not invent process ownership for missing/ambiguous registry entries (%s)', ambiguous => {
  terminal().set({ id: 'mirror', title: 'Build mirror', kind: 'agent', auto: false, cwd: '/example', procId: 'proc-7' })
  if (ambiguous) {
    const item: ComposerStatusItem = { id: 'proc-7', title: 'Build', type: 'background', state: 'running' }
    processes().set({ first: [item], second: [item] })
  }
  render(<TerminalPaneChrome />)
  const opener = screen.getByRole('button', { name: 'Inspect process' })
  opener.focus()
  fireEvent.click(opener)
  expect(screen.getByText('Unavailable')).toBeTruthy()
  expect(screen.getByText('No captured output available.')).toBeTruthy()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('complementary')).toBeNull()
  expect(document.activeElement).toBe(opener)
})

it('inspects the selected terminal without creating a shell or inventing a PID', () => {
  terminal().set({ id: 'tab-1', title: 'Shell', kind: 'user', auto: true, cwd: '/example', restoreCwd: '/example/sub' })
  render(<TerminalPaneChrome />)
  const opener = screen.getByRole('button', { name: 'Inspect terminal' })
  opener.focus()
  fireEvent.click(opener)
  expect(screen.getByRole('complementary', { name: 'Terminal details' })).toBeTruthy()
  expect(screen.getByText('/example/sub')).toBeTruthy()
  expect(screen.getByText(/not an operating-system PID/)).toBeTruthy()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('complementary')).toBeNull()
  expect(document.activeElement).toBe(opener)
})
