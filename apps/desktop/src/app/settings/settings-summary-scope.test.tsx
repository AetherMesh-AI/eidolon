import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({ read: vi.fn() }))
vi.mock('@/store/profile', () => ({ $activeGatewayProfile: atom('alpha'), $gatewaySwapTarget: atom(null), normalizeProfileKey: (name: string) => name }))
vi.mock('@/store/connections', () => ({ $pendingConnectionId: atom(null) }))
vi.mock('@/store/session', () => ({ $connection: atom({ baseUrl: 'http://fixture.invalid', connectionId: 'fixture', authenticationIdentity: 'fixture-auth' }) }))
vi.mock('@/themes/context', () => ({ useTheme: vi.fn() }))
vi.mock('@/eidolon', async () => {
  const { $activeGatewayProfile } = await import('@/store/profile')
  const { $connection } = await import('@/store/session')

  return {
    getHermesConfigRecord: (scope: unknown) => state.read(scope),
    getApiRequestProfile: () => $activeGatewayProfile.get(),
    getApiRequestConnection: () => $connection.get()?.connectionId ?? null,
    profileScopeKey: (scope: unknown) => JSON.stringify(scope)
  }
})

import { $activeGatewayProfile } from '@/store/profile'
import { $connection } from '@/store/session'
import { $settingsScopeOverride, setSettingsScope } from '@/store/settings-scope'

import { HERMES_CONFIG_KEY, hermesConfigKey } from '../hooks/use-config-record'

import { SavedSettingsSummary } from './settings-summary'

const deferred = () => {
  let resolve!: (value: { model: { provider: string } }) => void
  const promise = new Promise<{ model: { provider: string } }>(done => { resolve = done })

  return { promise, resolve }
}

let client: QueryClient
beforeEach(() => {
  $activeGatewayProfile.set('alpha')
  $settingsScopeOverride.set(null)
  $connection.set({ ...$connection.get()!, baseUrl: 'http://fixture.invalid', connectionId: 'fixture', authenticationIdentity: 'fixture-auth' })
  state.read.mockReset()
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})
afterEach(() => { cleanup(); client.clear() })

it('namespaces active and explicit editor revisions across a shared-source profile switch', async () => {
  const oldActiveA = deferred()
  const explicitA = deferred()
  const savedExplicitA = deferred()
  client.setQueryData(HERMES_CONFIG_KEY, { model: { provider: 'Initial active A' } })
  client.setQueryData(HERMES_CONFIG_KEY, { model: { provider: 'Updated active A' } })
  state.read.mockReturnValueOnce(oldActiveA.promise)
    .mockResolvedValueOnce({ model: { provider: 'Current B' } })
    .mockReturnValueOnce(explicitA.promise)
    .mockReturnValueOnce(savedExplicitA.promise)
  render(<QueryClientProvider client={client}><SavedSettingsSummary section="providers" /></QueryClientProvider>)
  await waitFor(() => expect(state.read).toHaveBeenCalledTimes(1))

  // Shared remote profiles preserve the descriptor and connection identity.
  await act(async () => { $activeGatewayProfile.set('beta') })
  await screen.findByText('Current B')
  const explicitKey = hermesConfigKey('alpha')
  client.setQueryData(explicitKey, { model: { provider: 'Explicit editor A' } })
  await act(async () => { setSettingsScope('alpha') })
  await waitFor(() => expect(state.read).toHaveBeenCalledTimes(3))
  expect(screen.queryByText('Current B')).toBeNull()
  await act(async () => { client.setQueryData(explicitKey, { model: { provider: 'Saved explicit A' } }) })
  expect(client.getQueryState(explicitKey)?.dataUpdateCount).toBe(client.getQueryState(HERMES_CONFIG_KEY)?.dataUpdateCount)
  await waitFor(() => expect(state.read).toHaveBeenCalledTimes(4))
  expect(state.read.mock.calls[3][0]).toEqual({ connectionId: 'fixture', profile: 'alpha' })

  await act(async () => { oldActiveA.resolve({ model: { provider: 'Stale active A' } }) })
  expect(screen.queryByText('Stale active A')).toBeNull()
  expect(screen.getByRole('status').textContent).toContain('Loading')
  await act(async () => { savedExplicitA.resolve({ model: { provider: 'Verified explicit A' } }) })
  await screen.findByText('Verified explicit A')
  await act(async () => { explicitA.resolve({ model: { provider: 'Pre-save explicit A' } }) })
  expect(screen.queryByText('Pre-save explicit A')).toBeNull()
  expect(screen.getByText('Verified explicit A')).toBeTruthy()
})

it('never paints previous-owner configuration during a deferred switch or late previous-owner completion', async () => {
  const lateA = deferred()
  const pendingB = deferred()
  state.read.mockResolvedValueOnce({ model: { provider: 'Provider A' } }).mockReturnValueOnce(lateA.promise).mockReturnValueOnce(pendingB.promise)
  render(<QueryClientProvider client={client}><SavedSettingsSummary section="providers" /></QueryClientProvider>)
  await screen.findByText('Provider A')
  await act(async () => { void client.invalidateQueries({ queryKey: HERMES_CONFIG_KEY }) })
  await waitFor(() => expect(state.read).toHaveBeenCalledTimes(2))
  expect(screen.getByText('Provider A')).toBeTruthy()
  await act(async () => { $activeGatewayProfile.set('beta') })
  expect(screen.queryByText('Provider A')).toBeNull()
  expect(screen.getByRole('status').textContent).toContain('Loading')
  await waitFor(() => expect(state.read).toHaveBeenCalledTimes(3))
  expect(state.read.mock.calls[2][0]).toEqual({ connectionId: 'fixture', profile: 'beta' })
  await act(async () => { lateA.resolve({ model: { provider: 'Late provider A' } }) })
  expect(screen.queryByText('Late provider A')).toBeNull()
  expect(screen.getByRole('status').textContent).toContain('Loading')
  await act(async () => { pendingB.resolve({ model: { provider: 'Provider B' } }) })
  await screen.findByText('Provider B')
  expect(screen.queryByText('Provider A')).toBeNull()
  const nextSource = deferred()
  state.read.mockReturnValueOnce(nextSource.promise)
  await act(async () => { $connection.set({ ...$connection.get()!, baseUrl: 'https://other.invalid', connectionId: 'other', authenticationIdentity: 'other-auth' }) })
  expect(screen.queryByText('Provider B')).toBeNull()
  await waitFor(() => expect(state.read).toHaveBeenCalledTimes(4))
  expect(state.read.mock.calls[3][0]).toEqual({ connectionId: 'other', profile: 'beta' })
  await act(async () => { nextSource.resolve({ model: { provider: 'Other source B' } }) })
  await screen.findByText('Other source B')
})

it('revalidates saved summaries after an existing editor cache write while retaining same-owner values', async () => {
  const refresh = deferred()
  state.read.mockResolvedValueOnce({ model: { provider: 'Saved A' } }).mockReturnValueOnce(refresh.promise)
  render(<QueryClientProvider client={client}><SavedSettingsSummary section="providers" /><SavedSettingsSummary section="safety" /><SavedSettingsSummary section="workspace" /></QueryClientProvider>)
  await screen.findByText('Saved A')
  expect(state.read).toHaveBeenCalledTimes(1)
  await act(async () => { client.setQueryData(HERMES_CONFIG_KEY, { model: { provider: 'Editor A' } }) })
  await waitFor(() => expect(state.read).toHaveBeenCalledTimes(2))
  expect(screen.getByText('Saved A')).toBeTruthy()
  expect(screen.queryByText('Editor A')).toBeNull()
  await act(async () => { refresh.resolve({ model: { provider: 'Verified A' } }) })
  await screen.findByText('Verified A')
})

it.each(['initial', 'refresh'] as const)('starts a post-save read while the %s read is pending and fences later source changes', async phase => {
  const beforeSave = deferred()
  const afterSave = deferred()

  if (phase === 'refresh') { state.read.mockResolvedValueOnce({ model: { provider: 'Cached A' } }) }
  state.read.mockReturnValueOnce(beforeSave.promise).mockReturnValueOnce(afterSave.promise)
  render(<QueryClientProvider client={client}><SavedSettingsSummary section="providers" /></QueryClientProvider>)

  if (phase === 'refresh') {
    await screen.findByText('Cached A')
    await act(async () => { void client.invalidateQueries({ queryKey: HERMES_CONFIG_KEY }) })
  }

  const readsBeforeSave = phase === 'refresh' ? 2 : 1
  await waitFor(() => expect(state.read).toHaveBeenCalledTimes(readsBeforeSave))
  await act(async () => { client.setQueryData(HERMES_CONFIG_KEY, { model: { provider: 'Saved editor A' } }) })
  await waitFor(() => expect(state.read).toHaveBeenCalledTimes(readsBeforeSave + 1))

  if (phase === 'refresh') { expect(screen.getByText('Cached A')).toBeTruthy() }
  await act(async () => { beforeSave.resolve({ model: { provider: 'Pre-save A' } }) })
  expect(screen.queryByText('Pre-save A')).toBeNull()
  await act(async () => { afterSave.resolve({ model: { provider: 'Post-save A' } }) })
  await screen.findByText('Post-save A')

  const oldRefresh = deferred()
  const oldSavedRead = deferred()
  const newSource = deferred()
  state.read.mockReturnValueOnce(oldRefresh.promise).mockReturnValueOnce(oldSavedRead.promise).mockReturnValueOnce(newSource.promise)
  await act(async () => { void client.invalidateQueries({ queryKey: HERMES_CONFIG_KEY }) })
  await waitFor(() => expect(state.read).toHaveBeenCalledTimes(readsBeforeSave + 2))
  await act(async () => { client.setQueryData(HERMES_CONFIG_KEY, { model: { provider: 'Second editor save A' } }) })
  await waitFor(() => expect(state.read).toHaveBeenCalledTimes(readsBeforeSave + 3))
  await act(async () => { $activeGatewayProfile.set('beta') })
  await waitFor(() => expect(state.read).toHaveBeenCalledTimes(readsBeforeSave + 4))
  expect(screen.queryByText('Post-save A')).toBeNull()
  await act(async () => {
    oldRefresh.resolve({ model: { provider: 'Old refresh A' } })
    oldSavedRead.resolve({ model: { provider: 'Old saved read A' } })
  })
  expect(screen.queryByText('Old refresh A')).toBeNull()
  expect(screen.queryByText('Old saved read A')).toBeNull()
  expect(screen.getByRole('status').textContent).toContain('Loading')
  await act(async () => { newSource.resolve({ model: { provider: 'Current B' } }) })
  await screen.findByText('Current B')
})
