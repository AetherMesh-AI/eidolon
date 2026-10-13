import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, it } from 'vitest'

import { TRANSLATIONS } from '@/i18n/catalog'
import { I18nProvider, useI18n } from '@/i18n/context'

import { demoSnapshot } from './demo'
import { OrganizationWorkspace } from './workspace'

function LanguageControl() {
  const { setLocale } = useI18n()

  return <button onClick={() => void setLocale('en')}>Use English</button>
}

it.each(['en', 'ja', 'zh', 'zh-hant', 'ar', 'ru'] as const)('localizes objective navigation without changing records or filters (%s)', locale => {
  const snapshot = demoSnapshot()
  const source = snapshot.objectives[0]
  snapshot.objectives = Array.from({ length: 26 }, (_, index) => ({ ...source, id: `goal-${index}`, title: `Owner 佐藤 ${index}`, description: 'Keep my description العربية', milestone: 'Recorded milestone 日本語', status: index === 25 ? 'paused' as const : 'active' as const, progress: 42.5 }))
  snapshot.agents = []
  const before = JSON.stringify(snapshot)
  const adapter = { mode: 'prototype' as const, getSnapshot: () => snapshot, subscribe: () => () => undefined }
  const copy = TRANSLATIONS[locale].organizationSurfaces.objectives
  const { container } = render(<I18nProvider configClient={null} initialLocale={locale}><MemoryRouter initialEntries={['/objectives']}><LanguageControl /><OrganizationWorkspace adapter={adapter} /></MemoryRouter></I18nProvider>)
  const pages = within(screen.getByRole('navigation', { name: copy.objectivePages }))
  expect((pages.getByRole('button', { name: copy.previousPage }) as HTMLButtonElement).disabled).toBe(true)
  expect(container.textContent).toContain(new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 2 }).format(0.425))
  expect(screen.getAllByText('Keep my description العربية')).toHaveLength(25)
  expect(screen.getAllByText(new RegExp(copy.unknownTeam))).toHaveLength(25)
  fireEvent.click(pages.getByRole('button', { name: copy.nextPage }))
  expect(screen.getByRole('link', { name: /Owner 佐藤 25/ }).getAttribute('href')).toBe('/objectives/goal-25')
  fireEvent.change(screen.getByRole('combobox', { name: copy.status }), { target: { value: 'paused' } })
  fireEvent.change(screen.getByRole('textbox', { name: copy.searchObjectives }), { target: { value: 'No such owner objective' } })
  expect(screen.getByRole('heading', { name: copy.noMatches })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: copy.clearFilters }))
  expect((screen.getByRole('combobox', { name: copy.status }) as HTMLSelectElement).value).toBe('all')
  fireEvent.change(screen.getByRole('combobox', { name: copy.status }), { target: { value: 'paused' } })
  fireEvent.change(screen.getByRole('textbox', { name: copy.searchObjectives }), { target: { value: 'Owner 佐藤 25' } })
  fireEvent.click(screen.getByRole('button', { name: 'Use English' }))
  expect((screen.getByRole('textbox', { name: 'Search objectives' }) as HTMLInputElement).value).toBe('Owner 佐藤 25')
  expect((screen.getByRole('combobox', { name: 'Status' }) as HTMLSelectElement).value).toBe('paused')
  expect(screen.getByRole('link', { name: /Owner 佐藤 25/ }).getAttribute('href')).toBe('/objectives/goal-25')
  expect(screen.queryByRole('navigation', { name: copy.objectivePages })).toBeNull()
  expect(JSON.stringify(snapshot)).toBe(before)
})
