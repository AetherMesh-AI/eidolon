import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it, vi } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'
import { I18nProvider } from '@/i18n/context'

import { RuntimeObjectiveDetail } from './runtime-detail'
import type { Objective, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

const copy = TRANSLATIONS.en.organizationWork
const detail = TRANSLATIONS.en.organizationHome.detail

function setup(locale: 'en' | 'ar' = 'en') {
  const objective: Objective = { id: 'goal-a', title: 'Goal A', description: 'Recorded work', source: 'runtime', status: 'active', ownerId: 'executive', createdAt: '2026-10-09T00:00:00Z' }

  let snapshot: OrganizationSnapshot = {
    source: 'runtime', connection: { state: 'ready', scope: 'socket-a', ownerScope: 'owner-a' },
    objectives: [objective], agents: [], tasks: [], requests: [], activity: [],
    knowledge: [{ id: 'evidence-a', objectiveId: objective.id, title: 'Evidence A', kind: 'artifact', body: 'Recorded evidence' }]
  }

  const mutation = vi.fn()

  const adapter: RuntimeOrganizationAdapter = {
    mode: 'runtime', getSnapshot: () => snapshot, subscribe: () => () => undefined,
    refresh: mutation, cancelObjective: mutation, retryRequest: mutation, resolveRequest: mutation,
    respondRequest: mutation, configureOrganization: mutation, createObjective: mutation,
    getEvidence: mutation, getToolReceipts: async () => []
  }

  const draw = () => <I18nProvider configClient={null} initialLocale={locale}><MemoryRouter initialEntries={['/objectives/goal-a?from=%2Fmessages%3Fquery%3Dbrief&originOwner=owner-a']}><RuntimeObjectiveDetail adapter={adapter} objective={snapshot.objectives[0]} snapshot={snapshot} /></MemoryRouter></I18nProvider>
  const view = render(draw())

  return { view, mutation, update(change: Partial<OrganizationSnapshot>) { snapshot = { ...snapshot, ...change }; view.rerender(draw()) }, objective }
}

it('associates tabs and panels and supports manual keyboard activation', async () => {
  const f = setup()
  const tabs = screen.getAllByRole('tab')
  expect(screen.getByRole('tablist').tabIndex).toBe(0)
  expect(tabs.filter(tab => tab.tabIndex === 0).length).toBeLessThanOrEqual(1)

  for (const tab of tabs) {
    const panel = window.document.getElementById(tab.getAttribute('aria-controls')!)!
    expect(panel.getAttribute('aria-labelledby')).toBe(tab.id)
    expect(panel.getAttribute('role')).toBe('tabpanel')
  }

  act(() => tabs[0].focus())
  fireEvent.keyDown(tabs[0], { key: 'ArrowRight' })
  await waitFor(() => expect(window.document.activeElement).toBe(tabs[1]))
  expect(tabs[0].getAttribute('aria-selected')).toBe('true')
  fireEvent.keyDown(tabs[1], { key: 'Enter' })
  expect(tabs[1].getAttribute('aria-selected')).toBe('true')
  expect(screen.getAllByRole('tabpanel')).toHaveLength(1)
  fireEvent.keyDown(tabs[1], { key: 'End' })
  await waitFor(() => expect(window.document.activeElement).toBe(tabs[3]))
  fireEvent.keyDown(tabs[3], { key: ' ' })
  expect(tabs[3].getAttribute('aria-selected')).toBe('true')
  fireEvent.keyDown(tabs[3], { key: 'Home' })
  await waitFor(() => expect(window.document.activeElement).toBe(tabs[0]))
  expect(tabs[3].getAttribute('aria-selected')).toBe('true')
  expect(f.mutation).not.toHaveBeenCalled()
})

it('keeps visited evidence search mounted but hidden and inert, preserves Back and reveals work', () => {
  const f = setup()
  expect(screen.queryByRole('textbox', { name: copy.evidence })).toBeNull()
  fireEvent.mouseDown(screen.getByRole('tab', { name: copy.artifacts }), { button: 0, ctrlKey: false })
  const search = screen.getByRole('textbox', { name: copy.evidence }) as HTMLInputElement
  fireEvent.change(search, { target: { value: 'Evidence' } })
  fireEvent.mouseDown(screen.getByRole('tab', { name: copy.activity }), { button: 0, ctrlKey: false })
  expect(search.isConnected).toBe(true)
  expect(search.closest('[role="tabpanel"]')?.hasAttribute('hidden')).toBe(true)
  expect(search.closest('[role="tabpanel"]')?.hasAttribute('inert')).toBe(true)
  expect(screen.queryByRole('textbox', { name: copy.evidence })).toBeNull()
  fireEvent.mouseDown(screen.getByRole('tab', { name: copy.artifacts }), { button: 0, ctrlKey: false })
  expect(screen.getByRole('textbox', { name: copy.evidence })).toBe(search)
  expect(search.value).toBe('Evidence')
  const scroll = vi.fn()
  const work = screen.getByRole('region', { name: detail.workDetails })
  work.scrollIntoView = scroll
  fireEvent.click(screen.getByRole('button', { name: detail.inspectWork }))
  expect(screen.getByRole('tab', { name: copy.work }).getAttribute('aria-selected')).toBe('true')
  expect(window.document.activeElement).toBe(work)
  expect(scroll).toHaveBeenCalledOnce()
  expect(f.view.container.querySelector('a.eid-back')?.getAttribute('href')).toBe('/messages?query=brief')
  expect(f.mutation).not.toHaveBeenCalled()
})

it.each(['objective', 'owner', 'connection'] as const)('discards retained pane state when %s identity changes', identity => {
  const f = setup()
  fireEvent.mouseDown(screen.getByRole('tab', { name: copy.artifacts }), { button: 0, ctrlKey: false })
  const previous = screen.getByRole('textbox', { name: copy.evidence })
  fireEvent.change(previous, { target: { value: 'Private draft' } })

  if (identity === 'objective') { f.update({ objectives: [{ ...f.objective, id: 'goal-b', title: 'Goal B' }] }) }
  else { f.update({ connection: { state: 'ready', scope: identity === 'connection' ? 'socket-b' : 'socket-a', ownerScope: identity === 'owner' ? 'owner-b' : 'owner-a' } }) }

  expect(previous.isConnected).toBe(false)
  expect(screen.getByRole('tab', { name: copy.work }).getAttribute('aria-selected')).toBe('true')
  fireEvent.mouseDown(screen.getByRole('tab', { name: copy.artifacts }), { button: 0, ctrlKey: false })
  expect((screen.getByRole('textbox', { name: copy.evidence }) as HTMLInputElement).value).toBe('')

  if (identity === 'owner') { expect(f.view.container.querySelector('a.eid-back')?.getAttribute('href')).toBe('/objectives') }
  expect(f.mutation).not.toHaveBeenCalled()
})

it('uses right-to-left arrow navigation without activating on focus', async () => {
  setup('ar')
  const tabs = screen.getAllByRole('tab')
  act(() => tabs[0].focus())
  fireEvent.keyDown(tabs[0], { key: 'ArrowLeft' })
  await waitFor(() => expect(window.document.activeElement).toBe(tabs[1]))
  expect(tabs[0].getAttribute('aria-selected')).toBe('true')
})
