import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { createRef } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getHermesConfigRecord = vi.fn()
const getHermesConfigSchema = vi.fn()
const saveHermesConfig = vi.fn()
const getElevenLabsVoices = vi.fn()
const notifyError = vi.fn()
vi.mock('@/store/notifications', () => ({ notifyError: (...args: unknown[]) => notifyError(...args), notify: vi.fn() }))

vi.mock('./model-settings', () => ({ ModelSettings: () => <div>Model editor</div>, ModelSettingsSkeleton: () => null }))

vi.mock('@/eidolon', () => ({
  getHermesConfigRecord: () => getHermesConfigRecord(),
  getHermesConfigSchema: () => getHermesConfigSchema(),
  saveHermesConfig: (config: unknown, profile?: string) => saveHermesConfig(config, profile),
  getElevenLabsVoices: () => getElevenLabsVoices(),
  setApiRequestProfile: () => {},
  profileScopeKey: (scope: string) => scope
}))

vi.mock('../hooks/use-on-profile-switch', () => ({
  useOnProfileSwitch: () => {}
}))

// The real stores pull in the gateway/profile stack, which needs a live
// backend connection. This page only reads the "applies to" scope override
// and the repo-discovery signature, neither of which this test touches.
vi.mock('@/store/settings-scope', () => ({
  $settingsRequestProfile: atom<string | undefined>(undefined),
  $settingsScopeOverride: atom<null | string>(null)
}))

vi.mock('@/store/projects', () => ({
  repoDiscoveryPolicyFromConfig: () => ({ enabled: true, roots: [], exclude_paths: [] }),
  repoDiscoveryPolicySignature: (policy: unknown) => JSON.stringify(policy),
  scanAndRecordRepos: vi.fn().mockResolvedValue(undefined)
}))

beforeEach(() => {
  getElevenLabsVoices.mockResolvedValue({ available: false })
  getHermesConfigSchema.mockResolvedValue({ fields: {} })
  saveHermesConfig.mockResolvedValue({ ok: true })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderConfigSettings() {
  const { ConfigSettings } = await import('./config-settings')
  const { SettingsOverview } = await import('./settings-overview')
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const importInputRef = createRef<HTMLInputElement>()

  const view = render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <SettingsOverview editor={<ConfigSettings activeSectionId="safety" importInputRef={importInputRef} />} editorOnly notifications={null} onSelect={() => {}} />
      </QueryClientProvider>
    </MemoryRouter>
  )

  return { importInputRef, navigateSection: (activeSectionId: string) => view.rerender(<MemoryRouter><QueryClientProvider client={client}><SettingsOverview editor={<ConfigSettings activeSectionId={activeSectionId === 'overview' ? 'model' : activeSectionId} compactOverview={activeSectionId === 'overview'} importInputRef={importInputRef} />} editorOnly={activeSectionId !== 'overview'} notifications={null} onSelect={() => {}} /></QueryClientProvider></MemoryRouter>) }
}

describe('ConfigSettings autosave', () => {
  it('sends a later revert instead of diffing it away against the stale page-load baseline', async () => {
    getHermesConfigRecord.mockResolvedValue({ checkpoints: { enabled: false }, other: 'untouched' })

    vi.useFakeTimers({ shouldAdvanceTime: true })

    try {
      await renderConfigSettings()

      const toggle = await screen.findByRole('switch')

      // Edit: flip checkpoints.enabled on, let the debounced autosave fire.
      toggle.click()
      await vi.advanceTimersByTimeAsync(700)

      await waitFor(() => expect(saveHermesConfig).toHaveBeenCalledTimes(1))
      expect(saveHermesConfig.mock.calls[0][0]).toEqual({ checkpoints: { enabled: true } })

      // Revert: flip it back to its original value and let autosave fire again.
      toggle.click()
      await vi.advanceTimersByTimeAsync(700)

      await waitFor(() => expect(saveHermesConfig).toHaveBeenCalledTimes(2))
      // Must still explicitly send the reverted value — diffing against the
      // never-advanced page-load baseline would produce an empty patch here
      // (the field is back to its original value) and leave disk stuck at
      // `enabled: true` from the first save.
      expect(saveHermesConfig.mock.calls[1][0]).toEqual({ checkpoints: { enabled: false } })
    } finally {
      vi.useRealTimers()
    }
  })
})


it('serializes a reverted edit behind an in-flight save while navigating configuration sections', async () => {
  getHermesConfigRecord.mockResolvedValue({ checkpoints: { enabled: false }, other: 'untouched' })
  let finish!: (value: { ok: boolean }) => void
  saveHermesConfig.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  vi.useFakeTimers({ shouldAdvanceTime: true })

  try {
    const { navigateSection } = await renderConfigSettings()
    const toggle = await screen.findByRole('switch')
    toggle.click()
    await vi.advanceTimersByTimeAsync(700)
    await waitFor(() => expect(saveHermesConfig).toHaveBeenCalledTimes(1))
    toggle.click()
    await vi.advanceTimersByTimeAsync(700)
    navigateSection('chat')
    expect(saveHermesConfig).toHaveBeenCalledTimes(1)
    finish({ ok: true })
    await waitFor(() => expect(saveHermesConfig).toHaveBeenCalledTimes(2))
    expect(saveHermesConfig.mock.calls.map(call => call[0])).toEqual([{ checkpoints: { enabled: true } }, { checkpoints: { enabled: false } }])
  } finally {
    vi.useRealTimers()
  }
})


it('retains pending configuration autosave when entering Model before debounce', async () => {
  getHermesConfigRecord.mockResolvedValue({ checkpoints: { enabled: false }, other: 'untouched' })
  vi.useFakeTimers({ shouldAdvanceTime: true })

  try {
    const view = await renderConfigSettings()
    fireEvent.click(await screen.findByRole('switch'))
    view.navigateSection('model')
    expect(saveHermesConfig).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(700)
    await waitFor(() => expect(saveHermesConfig).toHaveBeenCalledTimes(1))
    expect(saveHermesConfig.mock.calls[0][0]).toEqual({ checkpoints: { enabled: true } })
  } finally {
    vi.useRealTimers()
  }
})


it('reports a failed pending save after navigation without writing an explicit Model draft', async () => {
  getHermesConfigRecord.mockResolvedValue({ checkpoints: { enabled: false } })
  saveHermesConfig.mockRejectedValueOnce(new Error('Scripted save failure'))
  vi.useFakeTimers({ shouldAdvanceTime: true })

  try {
    const view = await renderConfigSettings()
    fireEvent.click(await screen.findByRole('switch'))
    view.navigateSection('model')
    await vi.advanceTimersByTimeAsync(700)
    await waitFor(() => expect(notifyError).toHaveBeenCalled())
    expect(saveHermesConfig).toHaveBeenCalledTimes(1)
    expect(saveHermesConfig.mock.calls[0][0]).toEqual({ checkpoints: { enabled: true } })
  } finally {
    vi.useRealTimers()
  }
})

it('fences a pending configuration edit when the explicit profile scope changes', async () => {
  const { $settingsRequestProfile } = await import('@/store/settings-scope')
  const fixtureScope = $settingsRequestProfile as ReturnType<typeof atom<string | undefined>>
  getHermesConfigRecord.mockResolvedValue({ checkpoints: { enabled: false } })
  vi.useFakeTimers({ shouldAdvanceTime: true })

  try {
    await renderConfigSettings()
    fireEvent.click(await screen.findByRole('switch'))
    await act(async () => { fixtureScope.set('research') })
    await vi.advanceTimersByTimeAsync(700)
    expect(saveHermesConfig).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByRole('switch').getAttribute('aria-checked')).toBe('false'))
  } finally {
    await act(async () => { fixtureScope.set(undefined) })
    vi.useRealTimers()
  }
})


it('retains pending configuration autosave when returning to Overview before debounce', async () => {
  getHermesConfigRecord.mockResolvedValue({ checkpoints: { enabled: false } })
  vi.useFakeTimers({ shouldAdvanceTime: true })

  try {
    const view = await renderConfigSettings()
    view.navigateSection('model')
    view.navigateSection('safety')
    fireEvent.click(await screen.findByRole('switch'))
    view.navigateSection('overview')
    expect(saveHermesConfig).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(700)
    await waitFor(() => expect(saveHermesConfig).toHaveBeenCalledTimes(1))
    expect(saveHermesConfig.mock.calls[0][0]).toEqual({ checkpoints: { enabled: true } })
  } finally {
    vi.useRealTimers()
  }
})


it('keeps context drafts and the same field mounted while the overview disclosure closes', async () => {
  getHermesConfigRecord.mockResolvedValue({ model: 'fixture', model_context_length: 4096 })
  getHermesConfigSchema.mockResolvedValue({ fields: { model_context_length: { type: 'number', default: 0, description: 'Context window' } } })
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    const view = await renderConfigSettings()
    view.navigateSection('overview')
    const summary = await screen.findByText('Context and fallback models')
    const disclosure = summary.closest('details')!
    expect(disclosure.open).toBe(false)
    fireEvent.click(summary)
    expect(disclosure.open).toBe(true)
    const input = screen.getByRole('spinbutton')
    fireEvent.change(input, { target: { value: '8192' } })
    fireEvent.click(summary)
    expect(disclosure.open).toBe(false)
    view.navigateSection('model')
    expect(screen.getByRole('spinbutton')).toBe(input)
    expect((input as HTMLInputElement).value).toBe('8192')
    await vi.advanceTimersByTimeAsync(700)
    await waitFor(() => expect(saveHermesConfig).toHaveBeenCalledTimes(1))
    expect(saveHermesConfig.mock.calls[0][0]).toEqual({ model_context_length: 8192 })
  } finally { vi.useRealTimers() }
})
