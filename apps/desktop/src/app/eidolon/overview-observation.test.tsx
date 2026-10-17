import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useSyncExternalStore } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'
import { I18nProvider } from '@/i18n/context'
import { observationCopy } from '@/i18n/organization-observation'
import type { Locale } from '@/i18n/types'

import { Activity } from './activity'
import { demoSnapshot } from './demo'
import { Home } from './home'
import { Messages } from './messages'
import { createRuntimeAdapter, type OrganizationGateway } from './runtime-adapter'
import { RuntimeStatus } from './runtime-detail'
import { $intakeDrafts } from './runtime-intake-drafts'
import { $ownerChatDrafts } from './runtime-owner-chat-drafts'
import { ownerChatSnapshot, ownerChatThread } from './runtime-owner-chat.test-support'
import type { OrganizationAdapter, OrganizationConnection, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'
import { WorkOverview } from './work-overview'
import { OrganizationWorkspace } from './workspace'

afterEach(() => { cleanup(); $intakeDrafts.set({}); $ownerChatDrafts.set({}); vi.unstubAllGlobals() })
const routes = ['/home', '/work', '/messages', '/activity'] as const
type Route = typeof routes[number]

it.each(['ltr', 'rtl'])('reserves complete Retry outlines in Messages without padding its ready layout (%s)', dir => {
  const stylesheet = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'eidolon.css'), 'utf8')
  const { container } = render(<><style>{stylesheet}</style>
    <main className="eidolon eid-workspace" data-testid="messages-error" dir={dir}><div className="eid-page">
      <div className="eid-runtime-status"><button>Retry connection</button></div><section className="eid-messages-view" />
    </div></main>
    <main className="eidolon eid-workspace" data-testid="messages-ready" dir={dir}><div className="eid-page"><section className="eid-messages-view" /></div></main>
    <main className="eidolon eid-workspace" data-testid="other-error" dir={dir}><div className="eid-page">
      <div className="eid-runtime-status"><button>Retry connection</button></div><section />
    </div></main>
  </>)
  const error = container.querySelector('[data-testid=messages-error]')!
  const status = getComputedStyle(error.querySelector('.eid-runtime-status')!)
  // Native evidence measured a 3px outline plus 3px offset at the scroll edge.
  for (const value of [status.paddingTop, status.paddingLeft, status.paddingRight]) {
    expect(parseFloat(value)).toBeGreaterThan(6)
  }
  for (const id of ['messages-error', 'other-error']) {
    const workspace = container.querySelector(`[data-testid=${id}]`)!
    expect(parseFloat(getComputedStyle(workspace).scrollPadding)).toBeGreaterThan(6)
    expect(parseFloat(getComputedStyle(workspace.querySelector('button')!).scrollMargin)).toBeGreaterThan(6)
  }
  const ready = container.querySelector('[data-testid=messages-ready]')!
  expect(getComputedStyle(ready.querySelector('.eid-page')!).padding).toBe('0px')
  expect(parseFloat(getComputedStyle(ready).scrollPadding) || 0).toBe(0)
  expect(parseFloat(getComputedStyle(container.querySelector('[data-testid=other-error] .eid-runtime-status')!).paddingTop) || 0).toBe(0)
})

function empty(state: OrganizationConnection['state'] = 'ready'): OrganizationSnapshot {
  return { ...ownerChatSnapshot(), agents: [], connection: { scope: 'socket-a', ownerScope: 'owner-a', state }, outcomes: { generation: 'empty', total: 0, unread: 0, hasMore: false, nextCursor: null, items: [] } }
}

function Surface({ route, snapshot, adapter }: { route: Route; snapshot: OrganizationSnapshot; adapter: OrganizationAdapter }) {
  if (route === '/home') { return <Home adapter={adapter} snapshot={snapshot} /> }

  if (route === '/work') { return <WorkOverview snapshot={snapshot} /> }

  if (route === '/messages') { return <Messages adapter={adapter.mode === 'runtime' ? adapter : undefined} snapshot={snapshot} /> }

  return <Activity snapshot={snapshot} />
}

function mount(route: Route, initial: OrganizationSnapshot, locale: Locale = 'en', adapter?: RuntimeOrganizationAdapter) {
  const content = (snapshot: OrganizationSnapshot) => <I18nProvider configClient={null} initialLocale={locale}><MemoryRouter initialEntries={[route]}><Surface adapter={adapter ?? { mode: 'prototype', getSnapshot: () => snapshot, subscribe: () => () => {} }} route={route} snapshot={snapshot} /></MemoryRouter></I18nProvider>
  const view = render(content(initial))

  return (snapshot: OrganizationSnapshot) => view.rerender(content(snapshot))
}

function confirmedEmpty(route: Route, locale: Locale) {
  const t = TRANSLATIONS[locale]

  return route === '/messages' ? t.organizationHome.memberMessages.noRecipients : route === '/activity' ? t.organizationFoundation.noEvents : t.organizationHome.noWork
}

it.each(routes.flatMap(route => (Object.keys(observationCopy) as Locale[]).map(locale => ({ route, locale }))))('keeps unobserved, retained and current emptiness distinct on $route in $locale', ({ route, locale }) => {
  const copy = observationCopy[locale]
  const update = mount(route, empty('connecting'), locale)
  expect(screen.getAllByText(copy.loading).length).toBeGreaterThan(0)
  expect(screen.queryByText(confirmedEmpty(route, locale))).toBeNull()
  update(empty('error'))
  expect(screen.getAllByText(copy.unavailable).length).toBeGreaterThan(0)
  expect(screen.queryByText(confirmedEmpty(route, locale))).toBeNull()
  update({ ...empty('error'), connection: { ...empty('error').connection!, lastUpdatedAt: '2026-10-01T00:00:00Z' } })
  expect(screen.getAllByText(copy.stale).length).toBeGreaterThan(0)
  expect(screen.getAllByText(copy.empty).length).toBeGreaterThan(0)
  expect(screen.queryByText(confirmedEmpty(route, locale))).toBeNull()
  update(empty())
  expect(screen.queryByText(copy.stale)).toBeNull()
  expect(screen.queryByText(copy.unknown)).toBeNull()
  expect(screen.getByText(confirmedEmpty(route, locale))).toBeTruthy()
})

it('does not turn initial Home requests, deliverables or activity into confirmed absence', () => {
  const copy = TRANSLATIONS.en.organizationHome
  const update = mount('/home', empty('error'))

  for (const text of [copy.noNeeds, copy.noReady, copy.noActivity]) { expect(screen.queryByText(text)).toBeNull() }
  update({ ...empty(), requests: undefined, outcomes: undefined })
  expect(screen.getByText(copy.requestsUnavailable)).toBeTruthy()
  expect(screen.getByText(copy.outcomesUnavailable)).toBeTruthy()
  expect(screen.queryByText(copy.noNeeds)).toBeNull()
  expect(screen.queryByText(copy.noReady)).toBeNull()
})

it.each(['/work', '/activity'] as const)('preserves activity filters and selection through retained errors on %s without claiming zero observed events', route => {
  const copy = TRANSLATIONS.en.organizationFoundation
  const update = mount(route, empty('error'))
  expect(screen.queryByText(copy.eventCount(0, '0'))).toBeNull()
  const snapshot = { ...empty(), activity: demoSnapshot().activity.slice(0, 1) }
  const event = snapshot.activity[0]
  update(snapshot)
  const search = screen.getByRole('searchbox', { name: copy.searchActivity })
  fireEvent.change(search, { target: { value: event.text } })
  fireEvent.click(screen.getByRole('button', { name: copy.inspectEvent(event.text) }))
  const inspector = screen.getByRole('complementary', { name: 'Event details' })
  update({ ...snapshot, connection: { ...snapshot.connection!, state: 'error' } })
  expect(screen.getByRole('complementary', { name: 'Event details' })).toBe(inspector)
  expect(screen.getByRole('searchbox', { name: copy.searchActivity })).toBe(search)
  expect(search).toHaveProperty('value', event.text)
  update({ ...snapshot, connection: { ...snapshot.connection!, scope: 'socket-b', state: 'connecting' } })
  expect(screen.getByRole('searchbox', { name: copy.searchActivity })).toBe(search)
  expect(screen.getByRole('complementary', { name: 'Event details' })).toBe(inspector)
  fireEvent.keyDown(globalThis.document, { key: 'Escape' })
  fireEvent.change(search, { target: { value: 'no matching recorded event' } })
  expect(screen.getByText(observationCopy.en.noMatches)).toBeTruthy()
})

it('keeps a selected member, chat draft and search mounted during a same-owner error', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} unobserve() {} })
  let snapshot = ownerChatSnapshot()
  const adapter = { mode: 'runtime', getSnapshot: () => snapshot, openOwnerChat: vi.fn(async () => ownerChatThread()), readOwnerChat: vi.fn(), sendOwnerChat: vi.fn(), cancelOwnerChat: vi.fn(), renewOwnerChat: vi.fn() } as unknown as RuntimeOrganizationAdapter
  const update = mount('/messages', snapshot, 'en', adapter)
  const search = screen.getByRole('textbox', { name: 'Search members' })
  fireEvent.click(screen.getByRole('button', { name: /Worker 1/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
  const composer = await screen.findByRole('textbox', { name: 'Message' })
  fireEvent.change(composer, { target: { value: 'Keep this unsent draft' } })
  fireEvent.change(search, { target: { value: 'unmatched' } })
  snapshot = { ...snapshot, connection: { ...snapshot.connection!, state: 'error' } }
  update(snapshot)
  expect(screen.getByText(observationCopy.en.stale)).toBeTruthy()
  expect(screen.getByText(observationCopy.en.noMatches)).toBeTruthy()
  expect(screen.getByRole('textbox', { name: 'Message' })).toBe(composer)
  expect(composer).toHaveProperty('value', 'Keep this unsent draft')
  expect(search).toHaveProperty('value', 'unmatched')
  expect(adapter.openOwnerChat).toHaveBeenCalledTimes(1)
  expect(adapter.sendOwnerChat).not.toHaveBeenCalled()
  expect(adapter.renewOwnerChat).not.toHaveBeenCalled()
})

it('keeps the Home intake and draft mounted beside last-known work', () => {
  const seed = demoSnapshot().objectives[0]
  const snapshot = { ...empty(), objectives: [{ ...seed, source: 'runtime' as const }] }
  const adapter = { mode: 'runtime', getSnapshot: () => snapshot, createObjective: vi.fn() } as unknown as RuntimeOrganizationAdapter
  const update = mount('/home', snapshot, 'en', adapter)
  const intake = globalThis.document.getElementById('eid-home-intake') as HTMLDetailsElement
  intake.open = true
  const manual = globalThis.document.getElementById('eid-manual-intake') as HTMLDetailsElement
  manual.open = true
  const input = screen.getByRole('textbox', { name: 'Objective' })
  fireEvent.change(input, { target: { value: 'Unsubmitted objective' } })
  update({ ...snapshot, connection: { ...snapshot.connection!, state: 'error' } })
  expect(screen.getByText(observationCopy.en.stale)).toBeTruthy()
  expect(screen.getByRole('textbox', { name: 'Objective' })).toBe(input)
  expect(input).toHaveProperty('value', 'Unsubmitted objective')
  expect(intake.open).toBe(true)
  expect(manual.open).toBe(true)
  expect(screen.getByRole('link', { name: seed.title })).toBeTruthy()
  expect(adapter.createObjective).not.toHaveBeenCalled()
})

it.each(routes)('uses the existing single-flight runtime Retry without mutation on %s', async route => {
  let resolve!: (snapshot: OrganizationSnapshot) => void
  const pending = new Promise<OrganizationSnapshot>(yes => { resolve = yes })
  const request = vi.fn().mockRejectedValueOnce(new Error('Synthetic initial observation unavailable')).mockReturnValue(pending)
  const gateway: OrganizationGateway = { request, getScope: () => ({ key: 'socket-a', ownerKey: 'owner-a', connected: true }), subscribeScope: () => () => {} }
  const adapter = createRuntimeAdapter(gateway)

  function ConnectedSurface() {
    const snapshot = useSyncExternalStore(adapter.subscribe, adapter.getSnapshot)

    return <><RuntimeStatus adapter={adapter} snapshot={snapshot} warningsOnly /><Surface adapter={adapter} route={route} snapshot={snapshot} /></>
  }

  render(<MemoryRouter initialEntries={[route]}><ConnectedSurface /></MemoryRouter>)
  const retry = await screen.findByRole('button', { name: 'Retry connection' })
  expect(screen.queryByText(confirmedEmpty(route, 'en'))).toBeNull()
  fireEvent.click(retry)
  fireEvent.click(retry)
  await act(async () => { await Promise.resolve() })
  expect(request.mock.calls.map(call => call[0])).toEqual(['organization.snapshot', 'organization.snapshot'])
  expect(screen.queryByText(confirmedEmpty(route, 'en'))).toBeNull()
  await act(async () => { resolve(empty()); await pending })
  expect(screen.getByText(confirmedEmpty(route, 'en'))).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Retry connection' })).toBeNull()
  expect(request.mock.calls.every(call => call[0] === 'organization.snapshot')).toBe(true)
})


it('keeps direct Activity mounted across same-owner transports and clears selection for a different owner', () => {
  const copy = TRANSLATIONS.en.organizationFoundation
  let snapshot: OrganizationSnapshot = { ...empty(), activity: demoSnapshot().activity.slice(0, 1) }
  const event = snapshot.activity[0]

  let listener = () => {}

  const adapter = {
    mode: 'prototype' as const,
    getSnapshot: () => snapshot,
    subscribe: (next: () => void) => {
      listener = next

      return () => {}
    }
  }

  render(<MemoryRouter initialEntries={['/activity']}><OrganizationWorkspace adapter={adapter} /></MemoryRouter>)
  const search = screen.getByRole('searchbox', { name: copy.searchActivity })
  fireEvent.change(search, { target: { value: event.text } })
  fireEvent.click(screen.getByRole('button', { name: copy.inspectEvent(event.text) }))
  const inspector = screen.getByRole('complementary', { name: 'Event details' })
  act(() => { snapshot = { ...snapshot, connection: { ...snapshot.connection!, scope: 'new-socket', state: 'connecting' } }; listener() })
  expect(screen.getByRole('searchbox', { name: copy.searchActivity })).toBe(search)
  expect(screen.getByRole('complementary', { name: 'Event details' })).toBe(inspector)
  act(() => { snapshot = { ...empty('connecting'), connection: { scope: 'other-socket', ownerScope: 'owner-b', state: 'connecting' } }; listener() })
  expect(screen.queryByRole('complementary', { name: 'Event details' })).toBeNull()
  expect(screen.queryByRole('button', { name: copy.inspectEvent(event.text) })).toBeNull()
  expect(screen.getByRole('searchbox', { name: copy.searchActivity })).toHaveProperty('value', '')
  expect(screen.getByText(observationCopy.en.loading)).toBeTruthy()
  expect(screen.queryByText(copy.noEvents)).toBeNull()
})
