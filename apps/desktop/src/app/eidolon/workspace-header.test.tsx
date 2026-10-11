import { act, fireEvent, render, screen } from '@testing-library/react'
import { atom } from 'nanostores'
import { MemoryRouter, useLocation } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'

import { $activeGatewayProfile } from '@/store/profile'

import { Inspector } from './inspector'
import { ownerChatSnapshot } from './runtime-owner-chat.test-support'
import { WorkspaceHeader } from './workspace-header'
import { searchWorkspace } from './workspace-search'

const state = vi.hoisted(() => ({ snapshot: null as ReturnType<typeof ownerChatSnapshot> | null, scope: 'socket-a:default', commands: vi.fn() }))
vi.mock('@/store/profile', () => ({ $activeGatewayProfile: atom('default'), $gatewaySwapTarget: atom(null) }))
vi.mock('@/store/connections', () => ({ $activeConnectionId: atom('local'), $pendingConnectionId: atom(null), $connectionsRegistry: atom({ connections: [{ id: 'local', label: 'This Mac' }] }) }))
vi.mock('@/store/command-palette', () => ({ openCommandPalettePage: (...args: unknown[]) => state.commands(...args) }))
vi.mock('../chat/sidebar/connection-switcher', () => ({ ConnectionSwitcher: () => null }))
vi.mock('../contrib/organization-shell', () => ({ OrganizationWorkIndicator: () => <button>Attention picker</button> }))
vi.mock('../routes', () => ({ navigateToWorkspacePage: (navigate: (path: string) => void, path: string) => navigate(path) }))
vi.mock('./runtime-provider', () => ({ useRuntimeOrganization: () => ({ snapshot: state.snapshot }), createOrganizationScopeReader: () => () => ({ key: state.scope, connected: true }) }))

beforeEach(() => {
  state.snapshot = ownerChatSnapshot()
  state.scope = state.snapshot.connection!.scope
  state.commands.mockReset()
  $activeGatewayProfile.set('default')
})

function Location() { return <output>{useLocation().pathname}</output> }

it('inspects loaded identities without replacing the current draft or navigating', () => {
  const closeInspector = vi.fn()
  render(<MemoryRouter initialEntries={['/messages']}><WorkspaceHeader /><Inspector kind="agent" title="Current conversation" onClose={closeInspector}><textarea aria-label="Existing page draft" defaultValue="Unsubmitted intent" /></Inspector><Location /></MemoryRouter>)
  const draft = screen.getByRole('textbox', { name: 'Existing page draft' })
  fireEvent.click(screen.getByRole('button', { name: 'Search loaded records' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Search loaded records' }), { target: { value: 'Worker 1' } })
  fireEvent.click(screen.getByRole('button', { name: /Worker 1.*Member/ }))
  expect(screen.getByText('identity-worker')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Chat' })).toBeNull()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.getByRole('textbox', { name: 'Existing page draft' })).toBe(draft)
  expect((draft as HTMLTextAreaElement).value).toBe('Unsubmitted intent')
  expect(screen.getByText('/messages')).toBeTruthy()
  expect(closeInspector).not.toHaveBeenCalled()
})

it('immediately hides old search records when the real scope changes before the snapshot', () => {
  render(<MemoryRouter><WorkspaceHeader /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: 'Search loaded records' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Search loaded records' }), { target: { value: 'Worker' } })
  expect(screen.getByRole('button', { name: /Worker 1.*Member/ })).toBeTruthy()
  act(() => { state.scope = 'socket-b:other'; $activeGatewayProfile.set('other') })
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByRole('button', { name: 'Search loaded records' }).hasAttribute('disabled')).toBe(true)
})

it('checks current scope again at activation and retains real Settings search', () => {
  render(<MemoryRouter initialEntries={['/settings']}><WorkspaceHeader /><Location /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: 'Search loaded records' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Search loaded records' }), { target: { value: 'Worker' } })
  state.scope = 'changed-between-render-and-click'
  fireEvent.click(screen.getByRole('button', { name: /Worker 1.*Member/ }))
  expect(screen.queryByText('identity-worker')).toBeNull()
  expect(screen.queryByRole('dialog')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Search settings' }))
  expect(state.commands).toHaveBeenCalledExactlyOnceWith('settings')
})

it('searches all query words in loaded metadata, never retained message contents', () => {
  const snapshot = ownerChatSnapshot()
  snapshot.conversations = [{ id: 'internal', subject: 'Review release', objectiveId: 'release', projectId: null, taskId: null, participants: [{ id: 'worker', name: 'Worker 1', team: 'Research' }], status: 'answered', waitingAgentId: null, messages: [{ id: 'msg', senderId: 'worker', recipientId: 'manager', body: 'Hidden message phrase', readAt: null, replyToId: null, createdAt: '2026-10-11T00:00:00Z' }] }]
  expect(searchWorkspace(snapshot, '  REVIEW worker ')).toEqual([{ id: 'internal', kind: 'conversation', label: 'Review release' }])
  expect(searchWorkspace(snapshot, 'Hidden message')).toEqual([])
  expect(searchWorkspace(snapshot, '   ')).toEqual([])
  expect(snapshot.conversations[0].messages[0].readAt).toBeNull()
})
