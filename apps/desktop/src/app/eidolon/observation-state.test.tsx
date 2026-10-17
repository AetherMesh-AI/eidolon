import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { observationCopy } from '@/i18n/organization-observation'
import type { Locale } from '@/i18n/types'

import { demoSnapshot } from './demo'
import { observationState } from './observation-state'
import type { OrganizationConnection, OrganizationSnapshot } from './types'
import { OrganizationWorkspace } from './workspace'

afterEach(cleanup)

function empty(state?: OrganizationConnection['state']): OrganizationSnapshot {
  return { source: 'runtime', objectives: [], agents: [], tasks: [], activity: [], knowledge: [], requests: [], connection: state ? { state, scope: 'owner-a' } : undefined }
}

function mount(snapshot: OrganizationSnapshot, route: string, locale: Locale = 'en') {
  let current = snapshot

  let listener = () => {}

  const adapter = { mode: 'prototype' as const, getSnapshot: () => current, subscribe: (next: () => void) => { listener = next;

 return () => {} } }

  render(<I18nProvider configClient={null} initialLocale={locale}><MemoryRouter initialEntries={[route]}><OrganizationWorkspace adapter={adapter} /></MemoryRouter></I18nProvider>)

  return (next: OrganizationSnapshot) => act(() => { current = next; listener() })
}

it.each(['connecting', 'error', 'disconnected', undefined] as const)('does not infer absence from %s without an observation', state => {
  expect(observationState(empty(state), 0)).toBe(state === 'connecting' ? 'loading' : 'unavailable')
  expect(observationState(empty(state), 1)).toBe('retained')
  expect(observationState({ ...empty(state), connection: { scope: 'owner-a', state: state ?? 'error', lastUpdatedAt: '2026-10-01T00:00:00Z' } }, 0)).toBe('retained')
})
it('confirms ready emptiness without a timestamp and leaves examples unchanged', () => {
  expect(observationState(empty('ready'), 0)).toBe('current')
  expect(observationState({ ...empty('error'), source: 'prototype' }, 0)).toBe('current')
})
it.each(['/objectives', '/organization'])('distinguishes loading, unavailable, retained empty and confirmed empty on %s', route => {
  const update = mount(empty('connecting'), route)
  expect(screen.getByRole('heading', { name: observationCopy.en.loading })).toBeTruthy()
  update(empty('error'))
  expect(screen.getByRole('heading', { name: observationCopy.en.unavailable })).toBeTruthy()
  expect(screen.queryByRole('heading', { name: /No objectives yet|No connected organization agents/ })).toBeNull()
  update({ ...empty('error'), connection: { scope: 'owner-a', state: 'error', lastUpdatedAt: '2026-10-01T00:00:00Z' } })
  expect(screen.getByRole('heading', { name: observationCopy.en.empty })).toBeTruthy()
  expect(screen.getByText(observationCopy.en.stale)).toBeTruthy()
  update(empty('ready'))
  expect(screen.queryByText(observationCopy.en.stale)).toBeNull()
  expect(screen.queryByText(observationCopy.en.unknown)).toBeNull()
  expect(screen.getByRole('heading', { name: route === '/objectives' ? 'No objectives yet' : 'No connected organization agents' })).toBeTruthy()
})
it('retains objective pagination and filters across same-owner interruptions', () => {
  const seed = demoSnapshot().objectives[0]
  const snapshot = { ...empty('ready'), objectives: Array.from({ length: 30 }, (_, index) => ({ ...seed, id: `goal-${index}`, title: `Goal ${index}`, source: 'runtime' as const })) }
  const update = mount(snapshot, '/objectives')
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
  update({ ...snapshot, connection: { scope: 'owner-a', state: 'error' } })
  expect(screen.getByRole('link', { name: /Goal 25/ })).toBeTruthy()
  fireEvent.change(screen.getByRole('textbox', { name: 'Search objectives' }), { target: { value: 'missing' } })
  expect(screen.getByRole('heading', { name: observationCopy.en.noMatches })).toBeTruthy()
  update({ ...snapshot, connection: { scope: 'owner-a', state: 'connecting' } })
  expect(screen.getByRole('textbox', { name: 'Search objectives' })).toHaveProperty('value', 'missing')
  fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
  expect(screen.getByRole('link', { name: /Goal 25/ })).toBeTruthy()
})
it('retains roster selection and filters while last-known rows stay inspectable', () => {
  const snapshot = { ...empty('ready'), agents: demoSnapshot().agents }
  const update = mount(snapshot, '/organization')
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Engineering' } })
  fireEvent.click(screen.getByRole('button', { name: 'Inspect Engineering' }))
  update({ ...snapshot, connection: { scope: 'owner-a', state: 'disconnected' } })
  expect(screen.getByRole('complementary', { name: 'Agent details' })).toBeTruthy()
  expect(screen.getByRole('textbox')).toHaveProperty('value', 'Engineering')
  expect(screen.getByText(observationCopy.en.stale)).toBeTruthy()
})
it.each(Object.keys(observationCopy) as Locale[])('renders observation copy in %s', locale => {
  mount(empty('error'), '/objectives', locale)
  expect(screen.getByRole('heading', { name: observationCopy[locale].unavailable })).toBeTruthy()
  expect(screen.getByText(observationCopy[locale].unknown)).toBeTruthy()

  if (locale !== 'en') { expect(observationCopy[locale].unavailable).not.toBe(observationCopy.en.unavailable) }
})
