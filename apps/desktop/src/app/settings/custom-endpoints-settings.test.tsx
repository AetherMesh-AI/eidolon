import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { I18nProvider, TRANSLATIONS, useI18n } from '@/i18n'
import type { Locale } from '@/i18n'
import { $activeGatewayProfile } from '@/store/profile'
import { $settingsScopeOverride } from '@/store/settings-scope'

import { CustomEndpointsSettings } from './custom-endpoints-settings'

const api = vi.hoisted(() => ({
  activateCustomEndpoint: vi.fn(), deleteCustomEndpoint: vi.fn(), getCustomEndpoints: vi.fn(),
  saveCustomEndpoint: vi.fn(), validateCustomEndpoint: vi.fn()
}))

const confirm = vi.hoisted(() => vi.fn())
vi.mock('@/eidolon', () => api)
vi.mock('@/store/profile', () => ({ $activeGatewayProfile: atom('alpha'), normalizeProfileKey: (name?: string) => name || 'default' }))
vi.mock('@/store/session', () => ({ $connection: atom({ connectionId: 'synthetic-a' }) }))
vi.mock('@/lib/haptics', () => ({ triggerHaptic: vi.fn() }))
vi.mock('@/store/confirm', () => ({ confirm }))
vi.mock('@/store/notifications', () => ({ notify: vi.fn(), notifyError: vi.fn() }))

beforeEach(() => {
  $activeGatewayProfile.set('alpha')
  $settingsScopeOverride.set(null)
  api.getCustomEndpoints.mockResolvedValue({ endpoints: [] })
  api.saveCustomEndpoint.mockResolvedValue({ endpoints: [], id: 'local-provider' })
  confirm.mockResolvedValue(false)
})

it('retains an endpoint draft through failed and deduplicated successful same-owner refreshes', async () => {
  render(<I18nProvider configClient={null}><CustomEndpointsSettings /></I18nProvider>)
  const field = await screen.findByLabelText('Name', { exact: true })
  fireEvent.change(field, { target: { value: 'Keep this synthetic draft' } })
  api.getCustomEndpoints.mockRejectedValueOnce(new Error('private synthetic refresh detail'))
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  await screen.findByRole('alert')
  expect(field).toHaveProperty('value', 'Keep this synthetic draft')
  expect(screen.getByText(TRANSLATIONS.en.settings.readState.stale)).toBeTruthy()
  let complete!: (value: { endpoints: [] }) => void
  api.getCustomEndpoints.mockReturnValueOnce(new Promise(resolve => { complete = resolve }))
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  expect(api.getCustomEndpoints).toHaveBeenCalledTimes(3)
  await act(async () => { complete({ endpoints: [] }) })
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByLabelText('Name', { exact: true })).toBe(field)
  expect(field).toHaveProperty('value', 'Keep this synthetic draft')
  expect(api.saveCustomEndpoint).not.toHaveBeenCalled()
  expect(api.validateCustomEndpoint).not.toHaveBeenCalled()
  expect(api.activateCustomEndpoint).not.toHaveBeenCalled()
  expect(api.deleteCustomEndpoint).not.toHaveBeenCalled()
})

it('follows the active profile rather than the Settings override and never shows old-owner endpoint drafts or late errors', async () => {
  render(<I18nProvider configClient={null}><CustomEndpointsSettings /></I18nProvider>)
  const field = await screen.findByLabelText('Name', { exact: true })
  fireEvent.change(field, { target: { value: 'Only for owner A' } })
  act(() => { $settingsScopeOverride.set('settings-only') })
  expect(api.getCustomEndpoints).toHaveBeenCalledTimes(1)
  let rejectOld!: (error: Error) => void
  api.getCustomEndpoints.mockReturnValueOnce(new Promise((_, reject) => { rejectOld = reject }))
    .mockResolvedValueOnce({ endpoints: [] })
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  act(() => { $activeGatewayProfile.set('beta') })
  await waitFor(() => expect(screen.getByLabelText('Name', { exact: true })).toHaveProperty('value', ''))
  await act(async () => { rejectOld(new Error('private old-owner detail')) })
  expect(screen.queryByRole('alert')).toBeNull()
  expect(api.getCustomEndpoints).toHaveBeenCalledTimes(3)
  expect(api.getCustomEndpoints.mock.calls.every(args => args.length === 0)).toBe(true)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('keeps a failed endpoint read distinct from an empty endpoint list or add form', async () => {
  api.getCustomEndpoints.mockRejectedValue(new Error('synthetic private read detail'))
  render(<I18nProvider configClient={null}><CustomEndpointsSettings /></I18nProvider>)
  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(screen.queryByText(TRANSLATIONS.en.customEndpoints.emptyHeading)).toBeNull()
  expect(screen.queryByLabelText(TRANSLATIONS.en.customEndpoints.name, { exact: true })).toBeNull()
  expect(screen.queryByText('synthetic private read detail')).toBeNull()
  api.getCustomEndpoints.mockResolvedValue({ endpoints: [] })
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  await screen.findByText(TRANSLATIONS.en.customEndpoints.emptyHeading)
  expect(api.getCustomEndpoints).toHaveBeenCalledTimes(2)
  expect(api.saveCustomEndpoint).not.toHaveBeenCalled()
})

function SwitchLocale() {
  const { setLocale } = useI18n()

  return <button onClick={() => void setLocale('ru')} type="button">Switch test locale</button>
}

it.each(['en', 'ja', 'zh', 'zh-hant', 'ar', 'ru'] as Locale[])('keeps endpoint writes explicit in %s', async locale => {
  const m = TRANSLATIONS[locale].customEndpoints
  render(<I18nProvider configClient={null} initialLocale={locale}><CustomEndpointsSettings /></I18nProvider>)
  await screen.findByText(m.emptyHeading)
  expect(screen.getByRole('button', { name: m.save })).toHaveProperty('disabled', true)
  fireEvent.change(screen.getByLabelText(m.name, { exact: true }), { target: { value: 'My authored endpoint' } })
  fireEvent.change(screen.getByLabelText(m.providerId), { target: { value: 'local-provider' } })
  fireEvent.change(screen.getByLabelText(m.url), { target: { value: 'http://localhost:9119/v1' } })
  fireEvent.change(screen.getByLabelText(m.model, { exact: true }), { target: { value: 'vendor/model-original' } })
  expect(api.saveCustomEndpoint).not.toHaveBeenCalled()
  expect(api.validateCustomEndpoint).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: m.save }))
  await waitFor(() => expect(api.saveCustomEndpoint).toHaveBeenCalledWith({
    id: 'local-provider', name: 'My authored endpoint', base_url: 'http://localhost:9119/v1',
    model: 'vendor/model-original', api_key: undefined, context_length: undefined,
    discover_models: true, make_default: true, models: undefined
  }))
  expect(api.validateCustomEndpoint).not.toHaveBeenCalled()
  expect(api.activateCustomEndpoint).not.toHaveBeenCalled()
  expect(api.deleteCustomEndpoint).not.toHaveBeenCalled()
})

it('retains the same unsaved inputs when the interface language changes', async () => {
  render(<I18nProvider configClient={null}><SwitchLocale /><CustomEndpointsSettings /></I18nProvider>)
  const field = await screen.findByLabelText('Name', { exact: true })
  fireEvent.change(field, { target: { value: 'Keep my endpoint draft' } })
  fireEvent.click(screen.getByRole('button', { name: 'Switch test locale' }))
  await screen.findByText('Добавить эндпоинт')
  expect(screen.getByLabelText('Название', { exact: true })).toBe(field)
  expect(field).toHaveProperty('value', 'Keep my endpoint draft')
  expect(api.getCustomEndpoints).toHaveBeenCalledTimes(1)
  expect(api.saveCustomEndpoint).not.toHaveBeenCalled()
  expect(api.validateCustomEndpoint).not.toHaveBeenCalled()
})

it('keeps deleting a named endpoint behind the existing destructive confirmation', async () => {
  api.getCustomEndpoints.mockResolvedValue({ endpoints: [{
    id: 'retained-provider', name: 'Authored $& service', base_url: 'https://example.invalid/v1',
    model: 'vendor/model', models: [], discover_models: false, is_current: false, has_api_key: true
  }] })
  render(<I18nProvider configClient={null} initialLocale="ar"><CustomEndpointsSettings /></I18nProvider>)
  fireEvent.click(await screen.findByRole('button', { name: 'حذف نقطة الاتصال' }))
  await waitFor(() => expect(confirm).toHaveBeenCalledWith({ destructive: true, title: 'حذف Authored $& service؟' }))
  expect(api.deleteCustomEndpoint).not.toHaveBeenCalled()
  expect(screen.getByLabelText('مفتاح API')).toHaveProperty('value', '')
})
