import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { observeMasterDetailWidth } from '@/app/responsive-master-detail.test-helpers'
import { TRANSLATIONS } from '@/i18n/catalog'
import { I18nProvider } from '@/i18n/context'
import type { Locale } from '@/i18n/types'

import { Messages } from './messages'
import { $ownerChatDrafts } from './runtime-owner-chat-drafts'
import { ownerChatSnapshot, ownerChatThread } from './runtime-owner-chat.test-support'
import type { OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  $ownerChatDrafts.set({})
})

function RouteControls() {
  const location = useLocation()
  const navigate = useNavigate()

  return <>
    <output data-testid="location">{location.search}</output>
    <button onClick={() => navigate(-1)}>Browser back</button>
    <button onClick={() => navigate(1)}>Browser forward</button>
  </>
}

it('keeps the exact chat, draft, URL, filter and scroll mounted through compact Back and resizing', async () => {
  const resize = observeMasterDetailWidth()
  const snapshot = ownerChatSnapshot()
  const thread = ownerChatThread()
  thread.messages = [{ id: 'retained', role: 'agent', text: 'Retained reply', createdAt: '2026-10-08T12:00:00Z', turnId: 'retained-turn', replyToMessageId: null }]

  const adapter = {
    mode: 'runtime', getSnapshot: () => snapshot,
    openOwnerChat: vi.fn(async () => thread), readOwnerChat: vi.fn(),
    sendOwnerChat: vi.fn(), cancelOwnerChat: vi.fn(), renewOwnerChat: vi.fn()
  } as unknown as RuntimeOrganizationAdapter

  const { container } = render(<MemoryRouter initialEntries={['/messages?recipient=all']}><RouteControls /><Messages adapter={adapter} snapshot={snapshot} /></MemoryRouter>)
  resize(400)
  const list = container.querySelector<HTMLElement>('[data-pane="list"]')!
  const detail = container.querySelector<HTMLElement>('[data-pane="detail"]')!
  const search = screen.getByRole('textbox', { name: 'Search members' })
  fireEvent.change(search, { target: { value: 'Research' } })
  list.scrollTop = 45
  const row = screen.getByRole('button', { name: /Worker 1/ })
  row.focus()
  fireEvent.click(row)
  const back = screen.getByRole('button', { name: 'Back to members' })
  expect(window.document.activeElement).toBe(back)
  expect(list.hidden).toBe(true)
  expect(list.hasAttribute('inert')).toBe(true)
  expect(screen.queryByRole('textbox', { name: 'Search members' })).toBeNull()
  expect(within(screen.getByRole('region', { name: 'Member conversation' })).getByRole('heading', { name: 'Worker 1' })).toBeTruthy()
  const selectedLocation = screen.getByTestId('location').textContent
  expect(selectedLocation).toContain('member=worker')
  expect(selectedLocation).toContain('identity=identity-worker')
  expect(selectedLocation).toContain('owner=connection-a%3Adefault')
  expect(adapter.openOwnerChat).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Chat' }))
  const composer = await screen.findByRole('textbox', { name: 'Message' })
  fireEvent.change(composer, { target: { value: 'Keep this scoped draft' } })
  const transcript = container.querySelector<HTMLElement>('.eid-owner-chat-scroll')!
  transcript.scrollTop = 35
  detail.scrollTop = 25
  expect(container.querySelector('.eid-owner-chat-charge')?.textContent).toBe(TRANSLATIONS.en.organizationOwnerChat.providerUsage)
  back.focus()
  fireEvent.click(back)
  expect(window.document.activeElement).toBe(row)
  expect(detail.hidden).toBe(true)
  expect(detail.hasAttribute('inert')).toBe(true)
  expect(screen.queryByRole('textbox', { name: 'Message' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Send' })).toBeNull()
  expect(screen.getByRole('textbox', { name: 'Search members' })).toBe(search)
  expect(search).toHaveProperty('value', 'Research')
  expect(list.scrollTop).toBe(45)
  expect(screen.getByTestId('location').textContent).toBe(selectedLocation)
  fireEvent.click(row)
  expect(screen.getByRole('textbox', { name: 'Message' })).toBe(composer)
  expect(composer).toHaveProperty('value', 'Keep this scoped draft')
  expect(transcript.scrollTop).toBe(35)
  expect(detail.scrollTop).toBe(25)
  Object.defineProperty(composer.closest('.eid-owner-chat-compose-area'), 'scrollIntoView', { value: vi.fn() })
  composer.focus()
  resize(1100)
  expect(list.hidden).toBe(false)
  expect(detail.hidden).toBe(false)
  resize(400)
  expect(detail.hidden).toBe(false)
  expect(window.document.activeElement).toBe(composer)
  resize(1100)
  search.focus()
  resize(600)
  expect(list.hidden).toBe(false)
  expect(detail.hidden).toBe(true)
  expect(window.document.activeElement).toBe(search)
  fireEvent.change(search, { target: { value: 'no matching member' } })
  expect(screen.getByText('No members match this search.')).toBeTruthy()
  expect(screen.queryByRole('heading', { name: 'No recipients available' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Clear search' }))
  fireEvent.click(screen.getByRole('button', { name: /Worker 1/ }))
  expect(screen.getByRole('textbox', { name: 'Message' })).toBe(composer)
  expect(adapter.openOwnerChat).toHaveBeenCalledExactlyOnceWith({ agentId: 'worker', identityId: 'identity-worker' })
  expect(adapter.sendOwnerChat).not.toHaveBeenCalled()
  expect(adapter.cancelOwnerChat).not.toHaveBeenCalled()
  expect(adapter.renewOwnerChat).not.toHaveBeenCalled()
})

it.each<Locale>(['en', 'ja', 'zh', 'zh-hant', 'ar', 'ru'])(
  'keeps compact navigation scoped and localizes recovery for removed, replaced and empty members in %s',
  locale => {
    const resize = observeMasterDetailWidth()
    const snapshot = ownerChatSnapshot()
    snapshot.agents.push({ ...snapshot.agents[0], id: 'other', identityId: 'identity-other', name: 'Other member' })
    const copy = TRANSLATIONS[locale].organizationHome
    const mutation = vi.fn()
    const adapter = { mode: 'runtime', openOwnerChat: mutation, sendOwnerChat: mutation, renewOwnerChat: mutation } as unknown as RuntimeOrganizationAdapter

    const content = (current: OrganizationSnapshot) => <I18nProvider configClient={null} initialLocale={locale}>
      <MemoryRouter initialEntries={['/messages?owner=connection-a%3Adefault&member=worker&identity=identity-worker']}><RouteControls /><Messages adapter={adapter} snapshot={current} /></MemoryRouter>
    </I18nProvider>

    const view = render(content(snapshot))
    resize(400)
    const initialBack = screen.getByRole('button', { name: copy.memberMessages.backToMembers })
    initialBack.focus()
    fireEvent.click(initialBack)
    expect(window.document.activeElement).toBe(screen.getByRole('button', { name: /Worker 1/ }))
    fireEvent.click(screen.getByRole('button', { name: /Worker 1/ }))
    expect(screen.getByRole('button', { name: copy.memberMessages.backToMembers })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: copy.memberMessages.backToMembers }))
    fireEvent.click(screen.getByRole('button', { name: /Other member/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Browser back' }))
    const conversation = () => within(screen.getByRole('region', { name: copy.memberMessages.conversation }))
    expect(conversation().getByRole('heading', { name: 'Worker 1' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Browser forward' }))
    expect(conversation().getByRole('heading', { name: 'Other member' })).toBeTruthy()
    expect(screen.getByTestId('location').textContent).toContain('identity=identity-other')
    view.rerender(content({ ...snapshot, connection: { ...snapshot.connection!, ownerScope: 'another-owner' } }))
    expect(screen.queryByRole('region', { name: copy.memberMessages.conversation })).toBeNull()
    expect(screen.getByRole('button', { name: /Other member/ }).getAttribute('aria-pressed')).toBe('false')
    view.rerender(content({ ...snapshot, agents: [snapshot.agents[0], { ...snapshot.agents[1], identityId: 'replacement' }] }))
    expect(screen.queryByRole('region', { name: copy.memberMessages.conversation })).toBeNull()
    expect(screen.getByRole('button', { name: /Other member/ }).getAttribute('aria-pressed')).toBe('false')
    view.rerender(content({ ...snapshot, agents: [snapshot.agents[0]] }))
    expect(screen.queryByRole('button', { name: /Other member/ })).toBeNull()
    fireEvent.change(screen.getByRole('textbox', { name: copy.memberMessages.search }), { target: { value: 'no match' } })
    expect(screen.getByText(copy.memberMessages.noMatches)).toBeTruthy()
    view.rerender(content({ ...snapshot, agents: [] }))
    expect(screen.getByRole('heading', { name: copy.memberMessages.noRecipients })).toBeTruthy()
    expect(screen.queryByText(copy.memberMessages.noMatches)).toBeNull()
    expect(screen.queryByRole('heading', { name: copy.chooseAgent })).toBeNull()
    expect(screen.getByRole('link', { name: copy.reviewMessageRoster }).getAttribute('href')).toBe('/organization')
    expect(mutation).not.toHaveBeenCalled()
  }
)
