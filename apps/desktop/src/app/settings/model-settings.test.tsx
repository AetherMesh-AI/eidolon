import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

// Radix Select calls scrollIntoView on its items when the content opens; jsdom
// doesn't implement it (nor hasPointerCapture / releasePointerCapture), so stub
// them to let the dropdown open in tests.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

const getGlobalModelInfo = vi.fn()
const getGlobalModelOptions = vi.fn()
const getAuxiliaryModels = vi.fn()
const getMoaModels = vi.fn()
const setModelAssignment = vi.fn()
const getRecommendedDefaultModel = vi.fn()
const saveMoaModels = vi.fn()
const setEnvVar = vi.fn()
const getHermesConfigRecord = vi.fn()
const saveHermesConfig = vi.fn()
const startManualLocalEndpoint = vi.fn()
const startManualOnboarding = vi.fn()
const startManualProviderOAuth = vi.fn()
let profileSwitchHandler: (() => void) | null = null

vi.mock('@/eidolon', () => ({
  getGlobalModelInfo: (profile?: null | string) => getGlobalModelInfo(profile),
  getGlobalModelOptions: (opts?: unknown, profile?: null | string) => getGlobalModelOptions(opts, profile),
  getAuxiliaryModels: (profile?: null | string) => getAuxiliaryModels(profile),
  getApiRequestProfile: () => 'default',
  getMoaModels: (profile?: null | string) => getMoaModels(profile),
  profileScopeKey: (scope?: null | string) => (scope ?? '').trim() || 'default',
  setModelAssignment: (body: unknown) => setModelAssignment(body),
  getRecommendedDefaultModel: (slug: string) => getRecommendedDefaultModel(slug),
  saveMoaModels: (body: unknown) => saveMoaModels(body),
  setEnvVar: (key: string, value: string) => setEnvVar(key, value),
  getHermesConfigRecord: () => getHermesConfigRecord(),
  saveHermesConfig: (config: unknown) => saveHermesConfig(config),
  setApiRequestProfile: () => {}
}))

vi.mock('@/store/onboarding', () => ({
  startManualLocalEndpoint: () => startManualLocalEndpoint(),
  startManualOnboarding: () => startManualOnboarding(),
  startManualProviderOAuth: (slug: string) => startManualProviderOAuth(slug)
}))

vi.mock('../hooks/use-on-profile-switch', () => ({
  useOnProfileSwitch: (handler: () => void) => {
    profileSwitchHandler = handler
  }
}))

beforeEach(() => {
  getGlobalModelInfo.mockResolvedValue({ provider: 'nous', model: 'hermes-4' })
  getGlobalModelOptions.mockResolvedValue({
    providers: [
      {
        name: 'Nous',
        slug: 'nous',
        models: ['hermes-4', 'hermes-4-mini'],
        authenticated: true,
        capabilities: { 'hermes-4': { reasoning: true, fast: true } }
      }
    ]
  })
  getAuxiliaryModels.mockResolvedValue({
    main: { provider: 'nous', model: 'hermes-4' },
    tasks: [{ task: 'vision', provider: 'auto', model: '', base_url: '' }]
  })
  getMoaModels.mockResolvedValue(null)
  setModelAssignment.mockResolvedValue({ ok: true, provider: 'nous', model: 'hermes-4', gateway_tools: [] })
  getRecommendedDefaultModel.mockResolvedValue({ provider: 'nous', model: 'hermes-4', free_tier: null })
  setEnvVar.mockResolvedValue({ ok: true })
  getHermesConfigRecord.mockResolvedValue({ agent: { reasoning_effort: 'medium', service_tier: 'normal' } })
  saveHermesConfig.mockResolvedValue({ ok: true })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  profileSwitchHandler = null
})

async function renderModelSettings(scopeProfile?: null | string, compactOverview = false) {
  const { ModelSettings } = await import('./model-settings')
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return render(
    // The aux-task deep-link highlight reads useSearchParams, so the page
    // needs a router context in tests (the app provides HashRouter at root).
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <ModelSettings compactOverview={compactOverview} scopeProfile={scopeProfile} />
      </QueryClientProvider>
    </MemoryRouter>
  )
}

it('announces catalog loading and exposes a rejected load without showing Apply as ready', async () => {
  let rejectCatalog!: (reason: Error) => void
  getGlobalModelOptions.mockReturnValueOnce(new Promise((_, reject) => { rejectCatalog = reject }))
  await renderModelSettings()

  const pending = screen.getByRole('status', { name: /loading/i })
  expect(pending.getAttribute('aria-busy')).toBe('true')
  expect(screen.queryByRole('button', { name: 'Apply' })).toBeNull()

  await act(async () => rejectCatalog(new Error('Catalog temporarily unavailable')))
  const failure = await screen.findByRole('alert')
  expect(failure.textContent).toContain('Could not load models')
  expect(failure.textContent).not.toContain('Catalog temporarily unavailable')
  expect(screen.queryByRole('status', { name: /loading/i })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Apply' })).toBeNull()
  expect(screen.queryByRole('combobox')).toBeNull()
  expect(screen.getByRole('button', { name: 'Refresh' })).toBeTruthy()
})

it.each([false, true])('keeps read recovery beside the model explanation without replacing draft controls (overview: %s)', async compactOverview => {
  await renderModelSettings(undefined, compactOverview)
  const model = (await screen.findAllByRole('combobox'))[1]
  const picker = model.closest('.eid-settings-model-picker')!
  const description = screen.getByText(/Applies to new sessions/)
  const refresh = screen.getByRole('button', { name: 'Refresh' })
  const status = refresh.closest<HTMLElement>('[data-settings-read-state]')!

  expect(status.parentElement).toBe(description.parentElement)
  expect(status.parentElement!.nextElementSibling).toBe(picker)
  expect(status.closest('section')).toBe(picker.closest('section'))
  fireEvent.click(model)
  fireEvent.click(await screen.findByRole('option', { name: 'hermes-4-mini' }))
  let rejectRefresh!: (reason: Error) => void
  getGlobalModelOptions.mockReturnValueOnce(new Promise((_, reject) => { rejectRefresh = reject }))
  fireEvent.click(refresh)
  expect(status.getAttribute('data-settings-read-state')).toBe('refreshing')
  expect(refresh).toHaveProperty('disabled', true)
  await act(async () => rejectRefresh(new Error('synthetic-private-error-detail')))
  expect(status.getAttribute('data-settings-read-state')).toBe('stale')
  expect(within(status).getByRole('alert').textContent).toContain('Could not load models')
  expect(status.textContent).not.toContain('synthetic-private-error-detail')
  expect(screen.getByRole('button', { name: 'Refresh' })).toBe(refresh)
  expect(screen.getAllByRole('combobox')[1]).toBe(model)
  expect(model.textContent).toContain('hermes-4-mini')
  fireEvent.click(refresh)
  await waitFor(() => expect(status.getAttribute('data-settings-read-state')).toBe('ready'))
  expect(status.parentElement).toBe(description.parentElement)
  expect(screen.getAllByRole('combobox')[1]).toBe(model)
  expect(model.textContent).toContain('hermes-4-mini')

  for (const write of [setModelAssignment, saveHermesConfig, saveMoaModels, setEnvVar]) { expect(write).not.toHaveBeenCalled() }
})

describe('ModelSettings profile scope', () => {
  // #90549: the API helpers treat `null` as "deliberately target the
  // primary/default profile". A page following the active profile must pass
  // `undefined`, or every read repaints the primary's model and the user's
  // change looks reverted.
  it('follows the active profile (undefined, never null) when unscoped', async () => {
    await renderModelSettings()

    await waitFor(() => expect(getGlobalModelInfo).toHaveBeenCalledWith(undefined))
    expect(getGlobalModelOptions).toHaveBeenCalledWith(undefined, undefined)
    expect(getAuxiliaryModels).toHaveBeenCalledWith(undefined)
    expect(getMoaModels).toHaveBeenCalledWith(undefined)
  })

  it('reads through the explicit scope override when one is set', async () => {
    await renderModelSettings('research')

    await waitFor(() => expect(getGlobalModelInfo).toHaveBeenCalledWith('research'))
    expect(getGlobalModelOptions).toHaveBeenCalledWith(undefined, 'research')
    expect(getAuxiliaryModels).toHaveBeenCalledWith('research')
    expect(getMoaModels).toHaveBeenCalledWith('research')
  })
})

describe('ModelSettings', () => {
  it('loads the current main model and lists configured providers only', async () => {
    await renderModelSettings()

    await waitFor(() => expect(getGlobalModelInfo).toHaveBeenCalled())
    await waitFor(() => expect(getGlobalModelOptions).toHaveBeenCalled())

    // Open the provider Select — only configured providers should be listed.
    const triggers = await screen.findAllByRole('combobox')
    fireEvent.click(triggers[0])

    // "Nous" shows in both the trigger and the open list.
    expect((await screen.findAllByText('Nous')).length).toBeGreaterThan(0)
    expect(screen.queryByText(/DeepSeek/)).toBeNull()
  })

  it.each(['custom', 'local', 'custom:lab'])(
    'opens local endpoint setup when %s has no inventory row',
    async provider => {
      getGlobalModelInfo.mockResolvedValueOnce({ provider, model: '' })
      getGlobalModelOptions.mockResolvedValueOnce({ providers: [] })

      await renderModelSettings()

      const providerSelect = (await screen.findAllByRole('combobox'))[0]

      expect(providerSelect.textContent).toContain(provider)
      expect(screen.queryByText(/undefined/)).toBeNull()
      expect(screen.queryByText(/signs in through your browser/)).toBeNull()

      fireEvent.click(await screen.findByRole('button', { name: 'Set up provider' }))

      expect(startManualLocalEndpoint).toHaveBeenCalledOnce()
      expect(startManualOnboarding).not.toHaveBeenCalled()
      expect(startManualProviderOAuth).not.toHaveBeenCalled()
    }
  )

  it('opens the generic provider picker for an unknown provider with no inventory row', async () => {
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'retired-provider', model: '' })
    getGlobalModelOptions.mockResolvedValueOnce({ providers: [] })

    await renderModelSettings()

    fireEvent.click(await screen.findByRole('button', { name: 'Set up provider' }))

    expect(startManualOnboarding).toHaveBeenCalledOnce()
    expect(startManualLocalEndpoint).not.toHaveBeenCalled()
    expect(startManualProviderOAuth).not.toHaveBeenCalled()
  })

  it('deep-links a known OAuth provider row into its setup flow', async () => {
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'anthropic', model: '' })
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        {
          name: 'Anthropic',
          slug: 'anthropic',
          models: [],
          authenticated: false,
          auth_type: 'oauth'
        }
      ]
    })

    await renderModelSettings()

    fireEvent.click(await screen.findByRole('button', { name: 'Set up Anthropic' }))

    expect(startManualProviderOAuth).toHaveBeenCalledWith('anthropic')
    expect(startManualLocalEndpoint).not.toHaveBeenCalled()
    expect(startManualOnboarding).not.toHaveBeenCalled()
  })

  it('replaces the selected provider and model when the active profile changes', async () => {
    getGlobalModelInfo
      .mockResolvedValueOnce({ provider: 'custom', model: 'local-a' })
      .mockResolvedValueOnce({ provider: 'nous', model: 'hermes-4' })
    getGlobalModelOptions
      .mockResolvedValueOnce({
        providers: [
          {
            name: 'Custom A',
            slug: 'custom',
            models: ['local-a'],
            authenticated: true
          }
        ]
      })
      .mockResolvedValueOnce({
        providers: [
          {
            name: 'Nous',
            slug: 'nous',
            models: ['hermes-4'],
            authenticated: true,
            capabilities: { 'hermes-4': { reasoning: true, fast: true } }
          }
        ]
      })

    await renderModelSettings()
    expect((await screen.findAllByRole('combobox'))[0].textContent).toContain('Custom A')

    await act(async () => {
      profileSwitchHandler?.()
    })

    await waitFor(() => expect(getGlobalModelInfo).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getAllByRole('combobox')[0].textContent).toContain('Nous'))
    expect(screen.queryByRole('button', { name: 'Set up provider' })).toBeNull()
  })

  it('preserves a user-defined provider endpoint when applying the main model', async () => {
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        {
          name: 'Nous',
          slug: 'nous',
          models: ['hermes-4'],
          authenticated: true
        },
        {
          name: 'Ollama',
          slug: 'local-ollama',
          models: ['qwen3:latest'],
          authenticated: true,
          is_user_defined: true,
          api_url: 'http://localhost:11434/v1'
        }
      ]
    })
    setModelAssignment.mockResolvedValueOnce({
      ok: true,
      provider: 'local-ollama',
      model: 'qwen3:latest',
      gateway_tools: []
    })

    await renderModelSettings()

    const providerSelect = (await screen.findAllByRole('combobox'))[0]
    fireEvent.click(providerSelect)
    fireEvent.click(await screen.findByRole('option', { name: 'Ollama' }))

    const modelSelect = (await screen.findAllByRole('combobox'))[1]
    fireEvent.click(modelSelect)
    fireEvent.click(await screen.findByRole('option', { name: 'qwen3:latest' }))

    fireEvent.click(await screen.findByRole('button', { name: 'Apply' }))

    await waitFor(() =>
      expect(setModelAssignment).toHaveBeenCalledWith({
        model: 'qwen3:latest',
        provider: 'local-ollama',
        scope: 'main',
        base_url: 'http://localhost:11434/v1'
      })
    )
  })

  it('writes the profile default speed (service_tier) when the fast switch is toggled', async () => {
    await renderModelSettings()
    await waitFor(() => expect(getHermesConfigRecord).toHaveBeenCalled())

    const fastSwitch = await screen.findByRole('switch')
    fireEvent.click(fastSwitch)

    await waitFor(() =>
      expect(saveHermesConfig).toHaveBeenCalledWith(
        expect.objectContaining({ agent: expect.objectContaining({ service_tier: 'fast' }) })
      )
    )
  })

  it('hides the reasoning/speed defaults when the main model reports no capabilities', async () => {
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        {
          name: 'Nous',
          slug: 'nous',
          models: ['hermes-4'],
          authenticated: true,
          capabilities: { 'hermes-4': { reasoning: false, fast: false } }
        }
      ]
    })

    await renderModelSettings()
    await waitFor(() => expect(getHermesConfigRecord).toHaveBeenCalled())

    expect(screen.queryByRole('switch')).toBeNull()
  })

  it('renders the auxiliary task rows', async () => {
    await renderModelSettings()

    expect(await screen.findByText('Vision')).toBeTruthy()
    expect(screen.getAllByText('auto · use main model').length).toBeGreaterThan(0)
  })

  it('assigns an auxiliary task to the main model via setModelAssignment', async () => {
    await renderModelSettings()

    // One "Set to main" button per task slot; the first is Vision.
    const setToMainButtons = await screen.findAllByRole('button', { name: 'Set to main' })
    fireEvent.click(setToMainButtons[0])

    await waitFor(() =>
      expect(setModelAssignment).toHaveBeenCalledWith({
        model: 'hermes-4',
        provider: 'nous',
        scope: 'auxiliary',
        task: 'vision'
      })
    )
  })

  it('carries the user-defined endpoint when an aux slot is set to a local main model', async () => {
    getGlobalModelOptions.mockResolvedValueOnce({
      providers: [
        {
          name: 'Ollama',
          slug: 'local-ollama',
          models: ['qwen3:latest'],
          authenticated: true,
          is_user_defined: true,
          api_url: 'http://localhost:11434/v1'
        }
      ]
    })
    getGlobalModelInfo.mockResolvedValueOnce({ provider: 'local-ollama', model: 'qwen3:latest' })
    getAuxiliaryModels.mockResolvedValueOnce({
      main: { provider: 'local-ollama', model: 'qwen3:latest' },
      tasks: [{ task: 'vision', provider: 'auto', model: '', base_url: '' }]
    })

    await renderModelSettings()

    const setToMainButtons = await screen.findAllByRole('button', { name: 'Set to main' })
    fireEvent.click(setToMainButtons[0])

    await waitFor(() =>
      expect(setModelAssignment).toHaveBeenCalledWith({
        model: 'qwen3:latest',
        provider: 'local-ollama',
        scope: 'auxiliary',
        task: 'vision',
        base_url: 'http://localhost:11434/v1'
      })
    )
  })

  it('warns when a main switch leaves auxiliary tasks pinned to another provider', async () => {
    setModelAssignment.mockResolvedValueOnce({
      ok: true,
      provider: 'openrouter',
      model: 'anthropic/claude-opus-4.7',
      gateway_tools: [],
      stale_aux: [{ task: 'compression', provider: 'nous', model: 'hermes-4' }]
    })

    await renderModelSettings()
    await waitFor(() => expect(getGlobalModelInfo).toHaveBeenCalled())

    const applyButton = await screen.findByRole('button', { name: 'Apply' })
    fireEvent.click(applyButton)

    // The switch-time notice names the pinned provider and offers a reset.
    expect(await screen.findByText(/still run on/)).toBeTruthy()
    expect(screen.getByText('nous')).toBeTruthy()
  })

  it.each([false, true])('shows a persistent aux mismatch banner with compact overview %s', async compactOverview => {
    getAuxiliaryModels.mockResolvedValueOnce({
      main: { provider: 'nous', model: 'hermes-4' },
      tasks: [{ task: 'curator', provider: 'openrouter', model: 'anthropic/claude-opus-4.7', base_url: '' }]
    })

    const view = await renderModelSettings(undefined, compactOverview)

    // Banner present on load, no switch required.
    const warning = await screen.findByText(/still run on/)
    expect(warning).toBeTruthy()

    if (compactOverview) {
      const disclosure = view.container.querySelector('.eid-settings-model-advanced') as HTMLDetailsElement
      expect(disclosure.open).toBe(false)
      expect(disclosure.contains(warning)).toBe(false)
    }
  })
})

describe('ModelSettings MoA preset editor', () => {
  const moaConfig = () => ({
    default_preset: 'default',
    active_preset: '',
    presets: {
      default: {
        reference_models: [
          { provider: 'nous', model: 'hermes-4' },
          { provider: 'openrouter', model: 'deepseek/deepseek-v4-pro' }
        ],
        aggregator: { provider: 'openrouter', model: 'anthropic/claude-opus-4.8' },
        reference_temperature: 0,
        aggregator_temperature: 0,

        enabled: true
      }
    },
    reference_models: [
      { provider: 'nous', model: 'hermes-4' },
      { provider: 'openrouter', model: 'deepseek/deepseek-v4-pro' }
    ],
    aggregator: { provider: 'openrouter', model: 'anthropic/claude-opus-4.8' },
    reference_temperature: 0,
    aggregator_temperature: 0,

    enabled: true
  })

  beforeEach(() => {
    getGlobalModelOptions.mockResolvedValue({
      providers: [
        {
          name: 'Nous',
          slug: 'nous',
          models: ['hermes-4', 'hermes-4-mini'],
          authenticated: true,
          capabilities: { 'hermes-4': { reasoning: true, fast: true } }
        },
        {
          name: 'OpenRouter',
          slug: 'openrouter',
          models: ['deepseek/deepseek-v4-pro', 'anthropic/claude-opus-4.8'],
          authenticated: true
        }
      ]
    })
    getMoaModels.mockResolvedValue(moaConfig())
    saveMoaModels.mockImplementation((body: unknown) => Promise.resolve(body))
  })

  async function openReferenceEditor() {
    await renderModelSettings()
    expect(await screen.findByText('Reference 1')).toBeTruthy()
  }

  function slotSelects() {
    // Combobox order in the MoA section (last 7 on the page): preset select,
    // then provider+model per reference (2 refs), then aggregator
    // provider+model. Reference 1's pair is therefore at -6 / -5.
    const all = screen.getAllByRole('combobox')

    return { ref1Provider: all.at(-6)!, ref1Model: all.at(-5)! }
  }

  it('holds the autosave while a slot is half-filled (provider changed, model pending)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    try {
      await openReferenceEditor()

      fireEvent.click(slotSelects().ref1Provider)
      fireEvent.click(await screen.findByRole('option', { name: 'OpenRouter' }))

      // Model was cleared by the provider change → config incomplete → the
      // debounced autosave must NOT fire, even well past the 600ms window.
      await vi.advanceTimersByTimeAsync(2000)
      expect(saveMoaModels).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('saves once the model pick completes the slot', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    try {
      await openReferenceEditor()

      fireEvent.click(slotSelects().ref1Provider)
      fireEvent.click(await screen.findByRole('option', { name: 'OpenRouter' }))
      await vi.advanceTimersByTimeAsync(700)

      fireEvent.click(slotSelects().ref1Model)
      fireEvent.click(await screen.findByRole('option', { name: 'anthropic/claude-opus-4.8' }))
      await vi.advanceTimersByTimeAsync(700)

      expect(saveMoaModels).toHaveBeenCalledTimes(1)
      const sent = saveMoaModels.mock.calls[0][0] as ReturnType<typeof moaConfig>
      expect(sent.presets.default.reference_models[0]).toEqual({
        provider: 'openrouter',
        model: 'anthropic/claude-opus-4.8'
      })
      // The untouched slots ride along unchanged — nothing reverts to defaults.
      expect(sent.presets.default.reference_models[1]).toEqual({
        provider: 'openrouter',
        model: 'deepseek/deepseek-v4-pro'
      })
      expect(sent.presets.default.aggregator).toEqual({
        provider: 'openrouter',
        model: 'anthropic/claude-opus-4.8'
      })
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not clear the model or save when the same provider is re-selected', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    try {
      await openReferenceEditor()

      fireEvent.click(slotSelects().ref1Provider)
      fireEvent.click(await screen.findByRole('option', { name: 'Nous' }))
      await vi.advanceTimersByTimeAsync(700)

      // Radix treats re-picking the current value as a no-op (no
      // onValueChange), so nothing changes: no save, model still shown.
      expect(saveMoaModels).not.toHaveBeenCalled()
      expect(screen.getByText('nous · hermes-4')).toBeTruthy()
    } finally {
      vi.useRealTimers()
    }
  })

  it('autosaves the selected preset when its enabled switch is toggled', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    try {
      await openReferenceEditor()

      fireEvent.click(screen.getByRole('switch', { name: 'Enabled' }))
      await vi.advanceTimersByTimeAsync(700)

      expect(saveMoaModels).toHaveBeenCalledWith(
        expect.objectContaining({
          presets: expect.objectContaining({
            default: expect.objectContaining({ enabled: false })
          })
        })
      )
    } finally {
      vi.useRealTimers()
    }
  })

  it('saves a disabled reference model without removing it (per-slot enabled toggle)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    try {
      await openReferenceEditor()

      fireEvent.click(screen.getByRole('switch', { name: 'Disable reference 1' }))
      await vi.advanceTimersByTimeAsync(700)

      expect(saveMoaModels).toHaveBeenCalledWith(
        expect.objectContaining({
          presets: expect.objectContaining({
            default: expect.objectContaining({
              reference_models: [
                expect.objectContaining({ provider: 'nous', model: 'hermes-4', enabled: false }),
                expect.objectContaining({ provider: 'openrouter', model: 'deepseek/deepseek-v4-pro' })
              ]
            })
          })
        })
      )
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('ModelSettings code-skew 503', () => {
  const skewError = new Error(
    'Error invoking remote method \'hermes:api\': Error: 503: {"detail":"Restart required: This process is running code from 08b4875f4a but the checkout on disk is now 48d2528066. The model picker would risk a stale-module crash — restart the Desktop-owned backend to load the new code (use Restart backend in Hermes Desktop, or quit and reopen the app)"}'
  )

  afterEach(() => {
    delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
  })

  it('unwraps the stale-backend 503 instead of dumping IPC JSON', async () => {
    getGlobalModelOptions.mockRejectedValueOnce(skewError)

    await renderModelSettings()

    await waitFor(() => {
      expect(screen.getByText(/running old code after an update/i)).toBeTruthy()
    })
    expect(screen.getByRole('button', { name: 'Restart backend' })).toBeTruthy()
    expect(screen.queryByText(/hermes:api/)).toBeNull()
    expect(screen.queryByText(/systemctl/)).toBeNull()
  })

  it('recycles the Desktop-owned backend and reloads the catalog', async () => {
    const recycleBackend = vi.fn().mockResolvedValue({ ok: true })

    ;(window as unknown as { hermesDesktop: { recycleBackend: typeof recycleBackend } }).hermesDesktop = {
      recycleBackend
    }

    getGlobalModelOptions.mockRejectedValueOnce(skewError)

    await renderModelSettings()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Restart backend' })).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: 'Restart backend' }))

    await waitFor(() => expect(recycleBackend).toHaveBeenCalledWith(undefined))
    await waitFor(() => expect(getGlobalModelOptions.mock.calls.length).toBeGreaterThan(1))
  })
})


it('retains real provider, model and auxiliary drafts through overview/detail and keeps Apply explicit', async () => {
  const { ModelSettings } = await import('./model-settings')
  const { SettingsOverview } = await import('./settings-overview')
  const { NotificationsSettings } = await import('./notifications-settings')
  getGlobalModelOptions.mockResolvedValue({ providers: [
    { name: 'Nous', slug: 'nous', models: ['hermes-4', 'hermes-4-mini'], authenticated: true },
    { name: 'Lab', slug: 'lab', models: ['lab-model'], authenticated: true }
  ] })
  getRecommendedDefaultModel.mockResolvedValue({ provider: 'lab', model: 'lab-model', free_tier: null })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  function Workspace() {
    const [editorOnly, setModelOnly] = useState(false)

    return <><button onClick={() => setModelOnly(false)}>Return overview</button><SettingsOverview editor={<ModelSettings compactOverview={!editorOnly} />} editorOnly={editorOnly} notifications={<NotificationsSettings compactOverview />} onSelect={() => setModelOnly(true)} /></>
  }

  const view = render(<MemoryRouter><QueryClientProvider client={client}><Workspace /></QueryClientProvider></MemoryRouter>)
  const modelRegion = within(screen.getByRole('region', { name: 'Model' }))
  const mainProvider = (await modelRegion.findAllByRole('combobox'))[0]
  fireEvent.click(mainProvider)
  fireEvent.click(await screen.findByRole('option', { name: 'Lab' }))
  const mainModel = modelRegion.getAllByRole('combobox')[1]
  fireEvent.click(mainModel)
  fireEvent.click(await screen.findByRole('option', { name: 'lab-model' }))
  const disclosure = view.container.querySelector('.eid-settings-model-advanced') as HTMLDetailsElement
  expect(disclosure.open).toBe(false)
  fireEvent.click(disclosure.querySelector('summary')!)
  expect(disclosure.open).toBe(true)
  fireEvent.click(screen.getAllByRole('button', { name: 'Change' })[0])
  const aux = view.container.querySelector('#aux-task-vision')!
  const auxSelects = aux.querySelectorAll('[role=combobox]')
  fireEvent.click(auxSelects[0])
  fireEvent.click(await screen.findByRole('option', { name: 'Nous' }))
  fireEvent.click(auxSelects[1])
  fireEvent.click(await screen.findByRole('option', { name: 'hermes-4-mini' }))
  fireEvent.click(screen.getByRole('button', { name: 'Open Model' }))
  expect(modelRegion.getAllByRole('combobox')[0]).toBe(mainProvider)
  expect(mainProvider.textContent).toContain('Lab')
  expect(mainModel.textContent).toContain('lab-model')
  expect(auxSelects[1].textContent).toContain('hermes-4-mini')
  fireEvent.click(screen.getByRole('button', { name: 'Return overview' }))
  expect(modelRegion.getAllByRole('combobox')[0]).toBe(mainProvider)
  expect(auxSelects[1].textContent).toContain('hermes-4-mini')
  const notificationSwitch = screen.getByRole('region', { name: 'Notifications' }).querySelector('[role=switch]')!
  fireEvent.click(notificationSwitch)
  expect(mainProvider.textContent).toContain('Lab')
  expect(mainModel.textContent).toContain('lab-model')
  expect(auxSelects[1].textContent).toContain('hermes-4-mini')
  expect(setModelAssignment).not.toHaveBeenCalled()
  expect(saveHermesConfig).not.toHaveBeenCalled()
  fireEvent.click(notificationSwitch)
  expect(disclosure.open).toBe(false)
  fireEvent.click(disclosure.querySelector('summary')!)
  expect(disclosure.open).toBe(true)
  fireEvent.click(screen.getAllByRole('button', { name: 'Apply' })[1])
  await waitFor(() => expect(setModelAssignment).toHaveBeenCalledWith(expect.objectContaining({ provider: 'nous', model: 'hermes-4-mini' })))
})

it('retries an unavailable catalog once and never writes configuration', async () => {
  getGlobalModelOptions.mockRejectedValueOnce(new Error('unavailable'))
  await renderModelSettings()
  await screen.findByRole('alert')
  let resolveCatalog!: (value: unknown) => void
  getGlobalModelOptions.mockReturnValueOnce(new Promise(resolve => { resolveCatalog = resolve }))
  const retry = screen.getByRole('button', { name: 'Refresh' })
  fireEvent.click(retry)
  fireEvent.click(retry)
  expect(getGlobalModelOptions).toHaveBeenCalledTimes(2)
  expect(retry.hasAttribute('disabled')).toBe(true)
  await act(async () => resolveCatalog({ providers: [{ name: 'Nous', slug: 'nous', models: ['hermes-4'], authenticated: true }] }))
  await screen.findByRole('button', { name: 'Apply' })
  expect(screen.queryByRole('alert')).toBeNull()
  expect(setModelAssignment).not.toHaveBeenCalled()
  expect(saveHermesConfig).not.toHaveBeenCalled()
  expect(setEnvVar).not.toHaveBeenCalled()
})

it('retains the last catalog and unsaved selection across a failed refresh and retry', async () => {
  await renderModelSettings()
  const model = (await screen.findAllByRole('combobox'))[1]
  fireEvent.click(model)
  fireEvent.click(await screen.findByRole('option', { name: 'hermes-4-mini' }))
  getGlobalModelOptions.mockRejectedValueOnce(new Error('private backend detail'))
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  expect((await screen.findByRole('alert')).textContent).toContain('Could not load models')
  expect(screen.queryByText('private backend detail')).toBeNull()
  expect(model.textContent).toContain('hermes-4-mini')
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  expect(model.textContent).toContain('hermes-4-mini')
  expect(setModelAssignment).not.toHaveBeenCalled()
  expect(saveHermesConfig).not.toHaveBeenCalled()
})

it.each(['resolve', 'reject'] as const)('ignores a late %s from the previous connection', async outcome => {
  const { $connection } = await import('@/store/session')
  const previous = $connection.get()
  let resolveOld!: (value: unknown) => void
  let rejectOld!: (error: Error) => void
  getGlobalModelOptions.mockReturnValueOnce(new Promise((resolve, reject) => { resolveOld = resolve; rejectOld = reject }))
  const view = await renderModelSettings()

  try {
    await act(async () => { $connection.set(previous ? { ...previous } : { baseUrl: 'http://synthetic.test', wsUrl: 'ws://synthetic.test', token: 'synthetic', isFullscreen: false, nativeOverlayWidth: 0, logs: [], windowButtonPosition: null }) })
    await screen.findByRole('button', { name: 'Apply' })
    await act(async () => {
      if (outcome === 'resolve') { resolveOld({ providers: [{ name: 'Foreign', slug: 'foreign', models: ['foreign'], authenticated: true }] }) }
      else { rejectOld(new Error('old owner failed')) }
    })
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getAllByRole('combobox')[0].textContent).toContain('Nous')
    expect(screen.queryByText('Foreign')).toBeNull()
    expect(setModelAssignment).not.toHaveBeenCalled()
  } finally {
    view.unmount()
    $connection.set(previous)
  }
})

it('retires pending reads on unmount without publishing their failure', async () => {
  let rejectOld!: (error: Error) => void
  getGlobalModelInfo.mockReturnValueOnce(new Promise((_, reject) => { rejectOld = reject }))
  const view = await renderModelSettings()
  view.unmount()
  await act(async () => rejectOld(new Error('retired read')))
  await renderModelSettings()
  await screen.findByRole('button', { name: 'Apply' })
  expect(screen.queryByRole('alert')).toBeNull()
  expect(setModelAssignment).not.toHaveBeenCalled()
})

it('permits retry after a synchronously failed read', async () => {
  getGlobalModelInfo.mockImplementationOnce(() => { throw new Error('synchronous read failure') })
  await renderModelSettings()
  await screen.findByRole('alert')
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  await screen.findByRole('button', { name: 'Apply' })
  expect(getGlobalModelInfo).toHaveBeenCalledTimes(2)
  expect(screen.queryByRole('alert')).toBeNull()
})

it('clears foreign drafts and ignores a pending old-profile catalog', async () => {
  const { $activeGatewayProfile } = await import('@/store/profile')
  const previous = $activeGatewayProfile.get()
  const view = await renderModelSettings()

  try {
    const model = (await screen.findAllByRole('combobox'))[1]
    fireEvent.click(model)
    fireEvent.click(await screen.findByRole('option', { name: 'hermes-4-mini' }))
    let rejectOld!: (error: Error) => void
    getGlobalModelOptions.mockReturnValueOnce(new Promise((_, reject) => { rejectOld = reject }))
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await act(async () => { $activeGatewayProfile.set('synthetic-other-owner') })
    await waitFor(() => expect(screen.getAllByRole('combobox')[1].textContent).toContain('hermes-4'))
    expect(screen.getAllByRole('combobox')[1].textContent).not.toContain('mini')
    await act(async () => rejectOld(new Error('old profile failure')))
    expect(screen.queryByRole('alert')).toBeNull()
    expect(getGlobalModelOptions).toHaveBeenCalledTimes(3)
    expect(setModelAssignment).not.toHaveBeenCalled()
  } finally {
    view.unmount()
    $activeGatewayProfile.set(previous)
  }
})

it('keeps explicit primary scope pinned while the ambient profile changes', async () => {
  const { $activeGatewayProfile } = await import('@/store/profile')
  const previous = $activeGatewayProfile.get()
  const view = await renderModelSettings(null)

  try {
    const model = (await screen.findAllByRole('combobox'))[1]
    fireEvent.click(model)
    fireEvent.click(await screen.findByRole('option', { name: 'hermes-4-mini' }))
    let resolvePrimary!: (value: unknown) => void
    getGlobalModelOptions.mockReturnValueOnce(new Promise(resolve => { resolvePrimary = resolve }))
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await act(async () => { $activeGatewayProfile.set('synthetic-ambient-profile') })
    expect(model.textContent).toContain('hermes-4-mini')
    expect(getGlobalModelOptions).toHaveBeenCalledTimes(2)
    await act(async () => resolvePrimary({ providers: [{ name: 'Primary Nous', slug: 'nous', models: ['hermes-4', 'hermes-4-mini'], authenticated: true }] }))
    expect(screen.getAllByRole('combobox')[0].textContent).toContain('Primary Nous')
    expect(model.textContent).toContain('hermes-4-mini')
    expect(getGlobalModelOptions).toHaveBeenLastCalledWith(undefined, null)
    expect(screen.queryByRole('alert')).toBeNull()
    expect(setModelAssignment).not.toHaveBeenCalled()
  } finally {
    view.unmount()
    $activeGatewayProfile.set(previous)
  }
})
