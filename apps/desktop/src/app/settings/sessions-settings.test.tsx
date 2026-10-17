import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ listAllProfileSessions: vi.fn(), deleteSession: vi.fn(), setSessionArchived: vi.fn(), getHermesConfigRecord: vi.fn(), saveHermesConfig: vi.fn() }))
vi.mock('@/eidolon', () => api)
vi.mock('@/store/session', () => ({ $connection: atom({ connectionId: 'synthetic-a' }), setSessions: vi.fn(), applyConfiguredDefaultProjectDir: vi.fn(), ensureDefaultWorkspaceCwd: vi.fn() }))
vi.mock('@/store/profile', () => ({ $activeGatewayProfile: atom('alpha'), normalizeProfileKey: (name?: string) => name || 'default' }))
vi.mock('@/store/session-removal', () => ({ untombstoneSessions: vi.fn() }))
vi.mock('@/store/session-unread', () => ({ forgetSessionUnread: vi.fn() }))
vi.mock('@/store/notifications', () => ({ notify: vi.fn(), notifyError: vi.fn() }))
vi.mock('@/lib/haptics', () => ({ triggerHaptic: vi.fn() }))

import { TRANSLATIONS } from '@/i18n'
import { $connection } from '@/store/session'

import { SessionsSettings } from './sessions-settings'

beforeEach(() => { vi.clearAllMocks() })
afterEach(cleanup)

it('never claims archives are empty after initial or repeated read failure and retries without restoring or deleting', async () => {
  api.listAllProfileSessions.mockRejectedValue(new Error('private synthetic detail'))
  render(<MemoryRouter><SessionsSettings /></MemoryRouter>)
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', TRANSLATIONS.en.settings.sessions.failedLoad)
  expect(screen.queryByText(TRANSLATIONS.en.settings.sessions.emptyArchivedTitle)).toBeNull()
  api.listAllProfileSessions.mockResolvedValue({ sessions: [], errors: [{ profile: 'synthetic', error: 'private partial read failure' }] })
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  await waitFor(() => expect(api.listAllProfileSessions).toHaveBeenCalledTimes(2))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh' })).toHaveProperty('disabled', false))
  expect(screen.queryByText(TRANSLATIONS.en.settings.sessions.emptyArchivedTitle)).toBeNull()
  expect(screen.queryByText('private partial read failure')).toBeNull()
  api.listAllProfileSessions.mockResolvedValue({ sessions: [] })
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  await screen.findByText(TRANSLATIONS.en.settings.sessions.emptyArchivedTitle)
  expect(api.deleteSession).not.toHaveBeenCalled()
  expect(api.setSessionArchived).not.toHaveBeenCalled()

  const rows = [
    { id: 'readable', profile: 'alpha', title: 'Available archive', message_count: 2 },
    { id: 'temporarily-unreadable', profile: 'beta', title: 'Retained archive', message_count: 2 }
  ]

  api.listAllProfileSessions.mockResolvedValue({ sessions: rows })
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  await screen.findByText('Retained archive')
  api.listAllProfileSessions.mockResolvedValue({ sessions: [rows[0]], errors: [{ profile: 'beta', error: 'private unreadable profile detail' }] })
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  await screen.findByText(TRANSLATIONS.en.settings.readState.incomplete)
  expect(screen.getByText('Available archive')).toBeTruthy()
  expect(screen.getByText('Retained archive')).toBeTruthy()
  expect(screen.queryByText('private unreadable profile detail')).toBeNull()

  cleanup()
  render(<MemoryRouter><SessionsSettings /></MemoryRouter>)
  await screen.findByText('Available archive')
  expect(screen.getByText(TRANSLATIONS.en.settings.readState.incomplete)).toBeTruthy()
  expect(screen.queryByText(TRANSLATIONS.en.settings.sessions.emptyArchivedTitle)).toBeNull()
})

it('pins all-profile archive reads to their connection and discards late old-source results', async () => {
  let complete!: (value: { sessions: object[] }) => void
  api.listAllProfileSessions.mockReturnValueOnce(new Promise(resolve => { complete = resolve }))
    .mockResolvedValueOnce({ sessions: [] })
  render(<MemoryRouter><SessionsSettings /></MemoryRouter>)
  await waitFor(() => expect(api.listAllProfileSessions).toHaveBeenCalledTimes(1))
  expect(api.listAllProfileSessions.mock.calls[0].slice(2)).toEqual(['only', 'recent', 'all', {}, { connectionId: 'synthetic-a', profile: 'alpha' }])
  act(() => { $connection.set({ ...$connection.get()!, connectionId: 'synthetic-b' }) })
  await screen.findByText(TRANSLATIONS.en.settings.sessions.emptyArchivedTitle)
  await act(async () => { complete({ sessions: [{ id: 'old-owner', title: 'Old owner archive', message_count: 2, archived: true, cwd: '/synthetic' }] }) })
  expect(api.listAllProfileSessions).toHaveBeenCalledTimes(2)
  expect(screen.queryByText('Old owner archive')).toBeNull()
  expect(screen.queryByRole('alert')).toBeNull()
})
