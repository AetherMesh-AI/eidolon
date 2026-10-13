import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { Link, MemoryRouter, useLocation } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'
import { $pendingConnectionId } from '@/store/connections'
import { $activeGatewayProfile } from '@/store/profile'

import { Inspector } from './inspector'
import { ownerChatSnapshot } from './runtime-owner-chat.test-support'
import { WorkspaceHeader } from './workspace-header'
import { searchWorkspace } from './workspace-search'

const state = vi.hoisted(() => ({ snapshot: null as ReturnType<typeof ownerChatSnapshot> | null, scope: 'socket-a:default', commands: vi.fn(), connection: vi.fn() }))
vi.mock('./organization-directory-control', () => ({ OrganizationDirectoryControl: () => null }))
vi.mock('@/store/profile', () => ({ $activeGatewayProfile: atom('default'), $gatewaySwapTarget: atom(null) }))
vi.mock('@/store/connections', () => ({ $activeConnectionId: atom('local'), $pendingConnectionId: atom(null), $connectionsRegistry: atom({ connections: [{ id: 'local', label: 'This Mac' }] }) }))
vi.mock('@/store/command-palette', () => ({ openCommandPalettePage: (...args: unknown[]) => state.commands(...args) }))
vi.mock('../chat/sidebar/connection-switcher', () => ({ ConnectionSwitcher: (props: unknown) => { state.connection(props);

 return <div aria-label="Connection selector" role="group" /> } }))
vi.mock('../contrib/organization-shell', () => ({ OrganizationWorkIndicator: () => <button>Attention picker</button> }))
vi.mock('../routes', async importOriginal => ({ ...(await importOriginal<typeof import('../routes')>()), navigateToWorkspacePage: (navigate: (path: string) => void, path: string) => navigate(path) }))
vi.mock('./use-organization-metadata', () => ({ useOrganizationMetadata: () => ({ client: {}, view: { descriptor: null, busy: false }, scope: { key: 'metadata-scope', eligible: false } }) }))
vi.mock('./runtime-provider', () => ({ useRuntimeOrganization: () => ({ snapshot: state.snapshot }), createOrganizationScopeReader: () => () => ({ key: state.scope, connected: true }) }))

beforeEach(() => {
  state.snapshot = ownerChatSnapshot()
  state.scope = state.snapshot.connection!.scope
  state.commands.mockReset()
  state.connection.mockReset()
  $activeGatewayProfile.set('default')
  $pendingConnectionId.set(null)
})

function Location() { return <output>{useLocation().pathname}</output> }

it('mounts the registry-owning switcher before a second connection exists', () => {
  render(<MemoryRouter><WorkspaceHeader /></MemoryRouter>)
  expect(screen.getByRole('group', { name: 'Connection selector' })).toBeTruthy()
})

it('inspects loaded identities without replacing the current draft or navigating', async () => {
  const closeInspector = vi.fn()
  render(<MemoryRouter initialEntries={['/messages']}><WorkspaceHeader /><Inspector kind="agent" onClose={closeInspector} title="Current conversation"><textarea aria-label="Existing page draft" defaultValue="Unsubmitted intent" /></Inspector><Location /></MemoryRouter>)
  const draft = screen.getByRole('textbox', { name: 'Existing page draft' })
  const trigger = screen.getByRole('button', { name: 'Search loaded records' })
  trigger.focus()
  fireEvent.click(trigger)
  fireEvent.change(screen.getByRole('textbox', { name: 'Search loaded records' }), { target: { value: 'Worker 1' } })
  fireEvent.click(screen.getByRole('button', { name: /Worker 1.*Member/ }))
  expect(screen.getByText('identity-worker')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Chat' })).toBeNull()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.getByRole('textbox', { name: 'Existing page draft' })).toBe(draft)
  expect((draft as HTMLTextAreaElement).value).toBe('Unsubmitted intent')
  expect(screen.getByText('/messages')).toBeTruthy()
  expect(closeInspector).not.toHaveBeenCalled()
  await waitFor(() => expect(document.activeElement).toBe(trigger))
})

it('immediately hides old search records when the real scope changes before the snapshot', async () => {
  render(<MemoryRouter><WorkspaceHeader /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: 'Search loaded records' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Search loaded records' }), { target: { value: 'Worker' } })
  expect(screen.getByRole('button', { name: /Worker 1.*Member/ })).toBeTruthy()
  act(() => { state.scope = 'socket-b:other'; $activeGatewayProfile.set('other') })
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByRole('button', { name: 'Search loaded records' }).hasAttribute('disabled')).toBe(true)
  await waitFor(() => expect(document.activeElement).not.toBe(screen.getByRole('button', { name: 'Search loaded records' })))
})

it('checks current scope again at activation and retains real Settings search', () => {
  render(<MemoryRouter initialEntries={['/settings']}><WorkspaceHeader /><Location /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: 'Search loaded records' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Search loaded records' }), { target: { value: 'Worker' } })
  state.scope = 'changed-between-render-and-click'
  fireEvent.click(screen.getByRole('button', { name: /Worker 1.*Member/ }))
  expect(screen.queryByText('identity-worker')).toBeNull()
  expect(screen.queryByRole('dialog')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Search' }))
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


it('closes search while a connection selection is pending without reusing its results', () => {
  render(<MemoryRouter><WorkspaceHeader /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: 'Search loaded records' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Search loaded records' }), { target: { value: 'Worker' } })
  act(() => $pendingConnectionId.set('other-connection'))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByRole('button', { name: 'Search loaded records' }).hasAttribute('disabled')).toBe(true)
  act(() => $pendingConnectionId.set(null))
  expect(screen.queryByRole('dialog')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Search loaded records' }))
  expect((screen.getByRole('textbox', { name: 'Search loaded records' }) as HTMLInputElement).value).toBe('')
})


it.each(['ja', 'zh', 'zh-hant', 'ar', 'ru'] as const)('provides the complete new workspace vocabulary in %s', locale => {
  const copy = TRANSLATIONS[locale].organizationHome.workspace
  const english = TRANSLATIONS.en.organizationHome.workspace

  for (const key of ['heading', 'settings', 'search', 'searchPlaceholder', 'backToResults', 'noLongerLoaded', 'refine'] as const) {
    expect(copy[key]).not.toBe(english[key])
    expect(copy[key].trim()).not.toBe('')
  }

  expect(copy.profile('scope-token')).toContain('scope-token')
  expect(copy.searchNote('scope-token')).toContain('scope-token')
  expect(copy.searchNote('scope-token')).not.toBe(english.searchNote('scope-token'))
  expect(copy.resultCount(7)).toContain('7')

  for (const kind of ['agent', 'objective', 'conversation'] as const) {expect(copy.kinds[kind]).not.toBe(english.kinds[kind])}
})


it('lets an in-flight header connection pick observe later page navigation', () => {
  render(<MemoryRouter><WorkspaceHeader /><Link to="/home">Go home</Link><Link to="/retained-session">Go chat</Link></MemoryRouter>)
  const preserveRoute = state.connection.mock.calls.at(-1)![0].preserveRoute as () => boolean
  expect(preserveRoute()).toBe(false)
  fireEvent.click(screen.getByRole('link', { name: 'Go home' }))
  expect(preserveRoute()).toBe(true)
  fireEvent.click(screen.getByRole('link', { name: 'Go chat' }))
  expect(preserveRoute()).toBe(false)
})
