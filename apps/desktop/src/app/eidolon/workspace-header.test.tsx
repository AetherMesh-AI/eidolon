import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { Link, MemoryRouter, useLocation } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'

import { PANE_TOGGLE_REVEAL_EVENT } from '@/components/pane-shell'
import { group, split } from '@/components/pane-shell/tree/model'
import { $narrowOverlayReveal } from '@/components/pane-shell/tree/narrow-overlay-state'
import { NarrowOverlays } from '@/components/pane-shell/tree/renderer/narrow-overlays'
import { $hiddenTreePanes, $layoutTree, $narrowViewport } from '@/components/pane-shell/tree/store'
import { registry } from '@/contrib/registry'
import { TRANSLATIONS } from '@/i18n/catalog'
import { ESCAPE_PRIORITY, pushEscapeLayer } from '@/lib/escape-layers'
import { $pendingConnectionId } from '@/store/connections'
import { $activeGatewayProfile } from '@/store/profile'

import type * as Routes from '../routes'

import { Inspector } from './inspector'
import { OrganizationRail } from './rail'
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
vi.mock('../routes', async importOriginal => ({ ...(await importOriginal<typeof Routes>()), navigateToWorkspacePage: (navigate: (path: string) => void, path: string) => navigate(path) }))
vi.mock('./use-organization-metadata', () => ({ useOrganizationMetadata: () => ({ client: {}, view: { descriptor: null, busy: false }, scope: { key: 'metadata-scope', eligible: false } }) }))
vi.mock('./runtime-provider', () => ({ useRuntimeOrganization: () => ({ snapshot: state.snapshot }), createOrganizationScopeReader: () => () => ({ key: state.scope, connected: true }) }))

beforeEach(() => {
  $narrowViewport.set(false)
  $narrowOverlayReveal.set(null)
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
  fireEvent.keyDown(window.document, { key: 'Escape' })
  expect(screen.getByRole('textbox', { name: 'Existing page draft' })).toBe(draft)
  expect((draft as HTMLTextAreaElement).value).toBe('Unsubmitted intent')
  expect(screen.getByText('/messages')).toBeTruthy()
  expect(closeInspector).not.toHaveBeenCalled()
  await waitFor(() => expect(window.document.activeElement).toBe(trigger))
})

it('immediately hides old search records when the real scope changes before the snapshot', async () => {
  render(<MemoryRouter><WorkspaceHeader /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: 'Search loaded records' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Search loaded records' }), { target: { value: 'Worker' } })
  expect(screen.getByRole('button', { name: /Worker 1.*Member/ })).toBeTruthy()
  act(() => { state.scope = 'socket-b:other'; $activeGatewayProfile.set('other') })
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByRole('button', { name: 'Search loaded records' }).hasAttribute('disabled')).toBe(true)
  await waitFor(() => expect(window.document.activeElement).not.toBe(screen.getByRole('button', { name: 'Search loaded records' })))
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

  for (const key of ['heading', 'navigation', 'settings', 'search', 'searchPlaceholder', 'backToResults', 'noLongerLoaded', 'refine'] as const) {
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


it('opens the compact navigation with truthful state, preserves drafts and restores focus on dismissal', () => {
  const tree = split('row', [group(['sessions']), group(['workspace'])])
  $layoutTree.set(tree)
  $hiddenTreePanes.set(new Set())
  $narrowViewport.set(true)
  const dispose = registry.register({ area: 'panes', id: 'sessions', title: 'Eidolon', data: { collapsible: true, placement: 'right', revealAliases: ['chat-sidebar'] }, render: () => <OrganizationRail sessions={<input aria-label="History filter" />} /> })

  try {
    render(<MemoryRouter initialEntries={['/messaging']}><WorkspaceHeader /><textarea aria-label="Existing draft" defaultValue="Retained draft" /><Location /><NarrowOverlays /></MemoryRouter>)
    const trigger = screen.getByRole('button', { name: 'Navigation' })
    const draft = screen.getByRole('textbox', { name: 'Existing draft' })
    expect(trigger.getAttribute('aria-expanded')).toBe('false')

    for (const dismissal of ['escape', 'close'] as const) {
      trigger.focus()
      fireEvent.click(trigger)
      expect(trigger.getAttribute('aria-expanded')).toBe('true')
      expect(window.document.getElementById(trigger.getAttribute('aria-controls')!)).not.toBeNull()
      const selected = screen.getByRole('link', { name: 'Settings' })
      expect(window.document.activeElement).toBe(selected)
      expect(screen.getByRole('textbox', { name: 'Existing draft' })).toBe(draft)

      if (dismissal === 'escape') {fireEvent.keyDown(selected, { key: 'Escape' })}
      else {fireEvent.click(screen.getByRole('button', { name: 'Close' }))}

      expect(trigger.getAttribute('aria-expanded')).toBe('false')
      expect(screen.queryByRole('complementary', { name: 'Eidolon navigation' })).toBeNull()
      expect(window.document.activeElement).toBe(trigger)
      expect((draft as HTMLTextAreaElement).value).toBe('Retained draft')
      expect($layoutTree.get()).toBe(tree)
    }

    draft.focus()

    for (const expanded of ['true', 'false']) {
      act(() => window.dispatchEvent(new CustomEvent(PANE_TOGGLE_REVEAL_EVENT, { detail: { id: 'chat-sidebar', mode: 'toggle' } })))
      expect(trigger.getAttribute('aria-expanded')).toBe(expanded)
      expect(window.document.activeElement).toBe(draft)
    }

    trigger.focus()
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('link', { name: 'Home' }))
    expect(screen.getByText('/home')).toBeTruthy()
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(window.document.activeElement).toBe(trigger)
    fireEvent.click(trigger)
    expect(window.document.activeElement).toBe(screen.getByRole('link', { name: 'Home' }))
    act(() => $narrowViewport.set(false))
    expect(screen.queryByRole('button', { name: 'Navigation' })).toBeNull()
    expect(screen.queryByRole('complementary', { name: 'Eidolon navigation' })).toBeNull()
    expect($narrowOverlayReveal.get()).toBeNull()
    expect(window.document.activeElement?.isConnected).toBe(true)
    expect(screen.getByRole('textbox', { name: 'Existing draft' })).toBe(draft)
  } finally { cleanup(); dispose(); $narrowViewport.set(false); $layoutTree.set(null) }
})

it('dismisses focused compact navigation before its underlying route overlay, with nested controls first', () => {
  $layoutTree.set(split('row', [group(['sessions']), group(['workspace'])]))
  $hiddenTreePanes.set(new Set())
  $narrowViewport.set(true)
  const dispose = registry.register({ area: 'panes', id: 'sessions', title: 'Eidolon', data: { collapsible: true, placement: 'left' }, render: () => <OrganizationRail sessions={<input aria-label="History filter" />} /> })
  const releaseRouteOverlay = pushEscapeLayer(ESCAPE_PRIORITY.overlay)

  try {
    render(<MemoryRouter initialEntries={['/settings']}><WorkspaceHeader /><NarrowOverlays /></MemoryRouter>)
    const trigger = screen.getByRole('button', { name: 'Navigation' })
    trigger.focus()
    fireEvent.click(trigger)
    fireEvent.click(screen.getByText('History', { selector: 'summary' }))
    const filter = screen.getByRole('textbox', { name: 'History filter' })
    filter.focus()
    fireEvent.keyDown(filter, { key: 'Escape' })
    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    expect(window.document.activeElement).toBe(screen.getByText('History', { selector: 'summary' }))
    fireEvent.keyDown(window.document.activeElement!, { key: 'Escape' })
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(window.document.activeElement).toBe(trigger)
  } finally { cleanup(); releaseRouteOverlay(); dispose(); $narrowViewport.set(false); $layoutTree.set(null) }
})


it.each(['hidden', 'outside-tree'])('temporarily opens %s navigation without changing saved dock visibility', state => {
  const tree = split('row', state === 'hidden' ? [group(['sessions']), group(['workspace'])] : [group(['workspace'])])
  const hidden = new Set(state === 'hidden' ? ['sessions'] : [])
  $layoutTree.set(tree)
  $hiddenTreePanes.set(hidden)
  $narrowViewport.set(true)
  const dispose = registry.register({ area: 'panes', id: 'sessions', title: 'Eidolon', data: { collapsible: true, placement: 'left', revealAliases: ['chat-sidebar'] }, render: () => <OrganizationRail sessions={null} /> })

  try {
    render(<MemoryRouter initialEntries={['/home']}><WorkspaceHeader /><NarrowOverlays /></MemoryRouter>)
    const trigger = screen.getByRole('button', { name: 'Navigation' })
    act(() => window.dispatchEvent(new CustomEvent(PANE_TOGGLE_REVEAL_EVENT, { detail: { id: 'chat-sidebar', mode: 'open' } })))
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    trigger.focus()
    fireEvent.click(trigger)
    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    expect(window.document.activeElement).toBe(screen.getByRole('link', { name: 'Home' }))
    fireEvent.keyDown(window.document.activeElement!, { key: 'Escape' })
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(window.document.activeElement).toBe(trigger)
    expect($layoutTree.get()).toBe(tree)
    expect($hiddenTreePanes.get()).toBe(hidden)
  } finally { cleanup(); dispose(); $narrowViewport.set(false); $layoutTree.set(null); $hiddenTreePanes.set(new Set()) }
})

it('clears focused-reveal ownership across a breakpoint and leaves Escape alone with no overlay', () => {
  $layoutTree.set(split('row', [group(['sessions']), group(['workspace'])]))
  $hiddenTreePanes.set(new Set())
  $narrowViewport.set(true)
  const dispose = registry.register({ area: 'panes', id: 'sessions', title: 'Eidolon', data: { collapsible: true, placement: 'left' }, render: () => <OrganizationRail sessions={null} /> })
  const releaseRouteOverlay = pushEscapeLayer(ESCAPE_PRIORITY.overlay)

  try {
    render(<MemoryRouter initialEntries={['/settings']}><WorkspaceHeader /><NarrowOverlays /></MemoryRouter>)
    const trigger = screen.getByRole('button', { name: 'Navigation' })
    trigger.focus()
    fireEvent.click(trigger)
    act(() => $narrowViewport.set(false))
    act(() => $narrowViewport.set(true))
    const emptyEscape = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true })
    act(() => window.dispatchEvent(emptyEscape))
    expect(emptyEscape.defaultPrevented).toBe(false)
    act(() => window.dispatchEvent(new CustomEvent(PANE_TOGGLE_REVEAL_EVENT, { detail: { id: 'sessions', mode: 'open' } })))
    const passiveEscape = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true })
    act(() => window.dispatchEvent(passiveEscape))
    expect(passiveEscape.defaultPrevented).toBe(false)
    expect(screen.getByRole('button', { name: 'Navigation' }).getAttribute('aria-expanded')).toBe('true')
  } finally { cleanup(); releaseRouteOverlay(); dispose(); $narrowViewport.set(false); $layoutTree.set(null) }
})


it('does not advertise an unregistered navigation pane during teardown', () => {
  $layoutTree.set(split('row', [group(['sessions']), group(['workspace'])]))
  $narrowViewport.set(true)
  let dispose: (() => void) | undefined

  try {
    render(<MemoryRouter initialEntries={['/home']}><WorkspaceHeader /><NarrowOverlays /></MemoryRouter>)
    expect(screen.queryByRole('button', { name: 'Navigation' })).toBeNull()
    act(() => { dispose = registry.register({ area: 'panes', id: 'sessions', title: 'Eidolon', data: { collapsible: true, placement: 'left' }, render: () => <OrganizationRail sessions={null} /> }) })
    const trigger = screen.getByRole('button', { name: 'Navigation' })
    trigger.focus()
    fireEvent.click(trigger)
    act(() => { dispose!(); dispose = undefined })
    expect(screen.queryByRole('button', { name: 'Navigation' })).toBeNull()
    expect($narrowOverlayReveal.get()).toBeNull()
    expect(window.document.activeElement?.isConnected).toBe(true)
  } finally { cleanup(); dispose?.(); $narrowViewport.set(false); $layoutTree.set(null) }
})
