import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider, LOCALE_OPTIONS } from '@/i18n'
import { getOrganizationSelectionCopy } from '@/i18n/organization-selection'
import { $desktopBoot } from '@/store/boot'
import { $desktopOnboarding } from '@/store/onboarding'
import {
  $organizationSelectionRecovery,
  openStartupWorkspace,
  retryOrganizationSelection
} from '@/store/organization-selection'

import { BootFailureOverlay } from './boot-failure-overlay'

vi.mock('@/store/organization-selection', async importOriginal => ({
  ...await importOriginal<Record<string, unknown>>(),
  openStartupWorkspace: vi.fn(),
  retryOrganizationSelection: vi.fn()
}))

// Remote-backend users hit a hard boot failure that isn't OAuth reauth (token
// auth, wrong URL, unreachable host). The recovery screen must let them fix the
// remote connection in place — the "Connection settings" action swaps the card
// to an in-line connect form — instead of stranding them (the old bug forced a
// hand-edit of connection.json).

function failBoot() {
  $desktopBoot.set({
    error: 'Could not connect to Hermes gateway',
    fakeMode: false,
    message: 'boot failed',
    phase: 'renderer.error',
    progress: 40,
    running: false,
    timestamp: Date.now(),
    visible: true
  })
}

function stubDesktop(config: Record<string, unknown>, overrides: Record<string, unknown> = {}) {
  const original = window.hermesDesktop
  Object.defineProperty(window, 'hermesDesktop', {
    configurable: true,
    value: { getRecentLogs: async () => ({ lines: [] }), getConnectionConfig: async () => config, ...overrides }
  })

  return () => Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: original })
}

const remoteToken = {
  envOverride: false,
  mode: 'remote',
  profile: null,
  remoteAuthMode: 'token',
  remoteOauthConnected: false,
  remoteTokenPreview: null,
  remoteTokenSet: true,
  remoteUrl: 'http://100.116.104.53:9191',
  cloudOrg: ''
}

beforeEach(() => {
  $organizationSelectionRecovery.set(null)
  vi.mocked(openStartupWorkspace).mockReset()
  vi.mocked(retryOrganizationSelection).mockReset()
  $desktopOnboarding.set({
    configured: true,
    flow: { status: 'idle' },
    mode: 'oauth',
    providers: null,
    reason: null,
    requested: false,
    firstRunSkipped: false,
    manual: false,
    localEndpoint: false
  })
  failBoot()
})

describe('organization selection recovery', () => {
  it.each(['pending', 'unavailable', 'storage', 'invalid'] as const)(
    'keeps %s recovery visible without probing or falling back to a gateway',
    async reason => {
      const copy = getOrganizationSelectionCopy('en')

      const calls = {
        getRecentLogs: vi.fn().mockResolvedValue({ lines: [] }),
        getConnectionConfig: vi.fn().mockResolvedValue({ ...remoteToken, remoteAuthMode: 'oauth' }),
        probeConnectionConfig: vi.fn(),
        resetBootstrap: vi.fn(),
        repairBootstrap: vi.fn(),
        applyConnectionConfig: vi.fn()
      }

      const restore = stubDesktop(remoteToken, calls)
      $organizationSelectionRecovery.set({ reason, profile: 'saved-profile', canRetry: reason !== 'invalid' })
      // Legacy effects would still run for this boot error even when its
      // presentation is suppressed by onboarding.
      $desktopOnboarding.set({
        ...$desktopOnboarding.get(),
        flow: { status: 'confirming_model', currentModel: '', label: '', providerSlug: '', saving: false }
      })

      try {
        render(<BootFailureOverlay />)
        const dialog = screen.getByRole('dialog', { name: copy.title })

        expect(dialog.getAttribute('aria-modal')).toBe('true')
        expect(screen.getByText(copy.description)).toBeTruthy()
        expect(screen.getByText(copy.reasons[reason])).toBeTruthy()
        expect(screen.getByText('saved-profile').closest('code')?.textContent).toBe('“saved-profile”')
        expect((screen.getByRole('button', { name: copy.retry }) as HTMLButtonElement).disabled).toBe(reason === 'invalid')
        expect(screen.getByRole('button', { name: copy.openStartup })).toBeTruthy()
        expect(screen.queryByRole('button', { name: /repair|gateway settings|sign in/i })).toBeNull()
        await act(async () => {})

        for (const call of Object.values(calls)) {
          expect(call).not.toHaveBeenCalled()
        }

        expect(retryOrganizationSelection).not.toHaveBeenCalled()
        expect(openStartupWorkspace).not.toHaveBeenCalled()
        act(() => { $desktopBoot.set({ ...$desktopBoot.get(), error: null, running: true }) })
        expect(screen.getByRole('dialog', { name: copy.title })).toBe(dialog)
      } finally {
        restore()
      }
    }
  )

  it.each(['pending', 'unavailable', 'storage', 'invalid'] as const)(
    'disables unavailable Retry for %s while preserving the explicit startup action',
    reason => {
      $organizationSelectionRecovery.set({ reason, profile: null, canRetry: false })
      render(<BootFailureOverlay />)
      const retry = screen.getByRole('button', { name: 'Retry' }) as HTMLButtonElement
      const startup = screen.getByRole('button', { name: 'Open startup workspace' }) as HTMLButtonElement

      expect(retry.disabled).toBe(true)
      expect(startup.disabled).toBe(false)
      expect(screen.getByText(getOrganizationSelectionCopy('en').retryUnavailable)).toBeTruthy()
      expect(screen.queryByText(getOrganizationSelectionCopy('en').retryHint)).toBeNull()
      fireEvent.click(retry)
      expect(retryOrganizationSelection).not.toHaveBeenCalled()
    }
  )

  it.each([
    ['Retry', retryOrganizationSelection, openStartupWorkspace],
    ['Open startup workspace', openStartupWorkspace, retryOrganizationSelection]
  ] as const)('guards pending %s against duplicate and competing clicks, then retains a safe error', async (label, action, otherAction) => {
    let reject!: (error: Error) => void
    vi.mocked(action).mockImplementation(() => new Promise<void>((_resolve, rejectPromise) => { reject = rejectPromise }))
    $organizationSelectionRecovery.set({ reason: 'pending', profile: 'saved-profile', canRetry: true })
    const savedSelection = $organizationSelectionRecovery.get()
    render(<BootFailureOverlay />)
    const retry = screen.getByRole('button', { name: 'Retry' }) as HTMLButtonElement
    const startup = screen.getByRole('button', { name: 'Open startup workspace' }) as HTMLButtonElement

    act(() => {
      fireEvent.click(screen.getByRole('button', { name: label }))
      fireEvent.click(retry)
      fireEvent.click(startup)
    })
    expect(action).toHaveBeenCalledTimes(1)
    expect(otherAction).not.toHaveBeenCalled()
    expect(retry.disabled).toBe(true)
    expect(startup.disabled).toBe(true)
    expect(screen.getByRole('dialog').getAttribute('aria-busy')).toBe('true')

    await act(async () => { reject(new Error('secret-token-do-not-render')) })
    expect(screen.getByRole('alert').textContent).toBe(getOrganizationSelectionCopy('en').actionFailed)
    expect(screen.queryByText(/secret-token-do-not-render/)).toBeNull()
    expect($organizationSelectionRecovery.get()).toBe(savedSelection)
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(retry.disabled).toBe(false)
    expect(startup.disabled).toBe(false)
    expect(screen.getByRole('dialog').getAttribute('aria-busy')).toBe('false')

    vi.mocked(action).mockResolvedValue(undefined)
    fireEvent.click(screen.getByRole('button', { name: label }))
    await waitFor(() => expect(screen.getByRole('dialog').getAttribute('aria-busy')).toBe('false'))
    expect(action).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('alert')).toBeNull()
    // The action/store owns success; the card cannot dismiss itself.
    expect($organizationSelectionRecovery.get()).toBe(savedSelection)
    expect(screen.getByRole('dialog')).toBeTruthy()
  })

  it('keeps keyboard focus inside recovery and cannot dismiss through Escape or outside interaction', async () => {
    const user = userEvent.setup()
    $organizationSelectionRecovery.set({ reason: 'pending', profile: null, canRetry: true })
    render(<><button type="button">Outside action</button><BootFailureOverlay /></>)
    const dialog = screen.getByRole('dialog')
    const retry = screen.getByRole('button', { name: 'Retry' })
    const startup = screen.getByRole('button', { name: 'Open startup workspace' })

    act(() => { retry.focus() })
    await user.tab()
    expect(window.document.activeElement).toBe(startup)
    await user.tab()
    expect(window.document.activeElement).toBe(retry)
    await user.keyboard('{Escape}')
    fireEvent.pointerDown(window.document.body)
    expect(screen.getByRole('dialog')).toBe(dialog)
    expect(screen.queryByRole('button', { name: /close/i })).toBeNull()
    expect(retryOrganizationSelection).not.toHaveBeenCalled()
    expect(openStartupWorkspace).not.toHaveBeenCalled()
  })

  it('returns ownership to the unchanged legacy overlay only when the recovery store clears', async () => {
    const getConnectionConfig = vi.fn().mockResolvedValue(remoteToken)
    const restore = stubDesktop(remoteToken, { getConnectionConfig })
    $organizationSelectionRecovery.set({ reason: 'unavailable', profile: 'saved-profile', canRetry: true })

    try {
      render(<BootFailureOverlay />)
      expect(screen.getByRole('dialog')).toBeTruthy()
      expect(getConnectionConfig).not.toHaveBeenCalled()
      act(() => { $organizationSelectionRecovery.set(null) })
      expect(screen.queryByRole('dialog')).toBeNull()
      await waitFor(() => expect(getConnectionConfig).toHaveBeenCalledTimes(1))
      expect(screen.getByRole('button', { name: /gateway settings/i })).toBeTruthy()
      expect(screen.getByRole('button', { name: /use local gateway/i })).toBeTruthy()
      expect(retryOrganizationSelection).not.toHaveBeenCalled()
      expect(openStartupWorkspace).not.toHaveBeenCalled()
    } finally {
      restore()
    }
  })

  it.each(LOCALE_OPTIONS)('renders recovery reasons and controls in $id', ({ id }) => {
    const copy = getOrganizationSelectionCopy(id)
    $organizationSelectionRecovery.set({ reason: 'pending', profile: null, canRetry: true })
    render(<I18nProvider configClient={null} initialLocale={id}><BootFailureOverlay /></I18nProvider>)

    expect(screen.getByRole('dialog', { name: copy.title })).toBeTruthy()
    expect(screen.getByText(copy.description)).toBeTruthy()
    expect(screen.getByText(copy.startupHint)).toBeTruthy()
    expect(screen.getByRole('button', { name: copy.retry })).toBeTruthy()
    expect(screen.getByRole('button', { name: copy.openStartup })).toBeTruthy()

    for (const reason of ['pending', 'unavailable', 'storage', 'invalid'] as const) {
      act(() => { $organizationSelectionRecovery.set({ reason, profile: null, canRetry: reason !== 'invalid' }) })
      expect(screen.getByText(copy.reasons[reason])).toBeTruthy()
    }
  })
})

afterEach(cleanup)

describe('BootFailureOverlay', () => {
  it('swaps to the in-place gateway settings view (no route nav) and back', async () => {
    render(<BootFailureOverlay />)

    fireEvent.click(screen.getByRole('button', { name: /gateway settings/i }))
    // Recovery actions give way to the embedded panel (behind a Back control).
    expect(await screen.findByRole('button', { name: /back/i })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /back/i }))
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /back/i })).toBeNull()
  })

  it('drops local-only Repair and Use-local-gateway on a local failure', () => {
    render(<BootFailureOverlay />)
    // No connection config stub → treated as a local failure.
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /repair/i })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /use local gateway/i })).toBeNull()
  })

  it('leads with Gateway settings and drops Repair for a remote (token) failure', async () => {
    const restore = stubDesktop(remoteToken)

    try {
      render(<BootFailureOverlay />)
      await waitFor(() => expect(screen.queryByRole('button', { name: /repair/i })).toBeNull())
      expect(screen.getByRole('button', { name: /gateway settings/i })).toBeTruthy()
      expect(screen.getByRole('button', { name: /use local gateway/i })).toBeTruthy()
    } finally {
      restore()
    }
  })

  it('opens gateway settings with a partial persisted remote config', async () => {
    const restore = stubDesktop({ mode: 'remote', remoteAuthMode: undefined, remoteUrl: undefined })

    try {
      render(<BootFailureOverlay />)
      fireEvent.click(screen.getByRole('button', { name: /gateway settings/i }))

      expect(await screen.findByRole('button', { name: /back/i })).toBeTruthy()
      expect(screen.queryByRole('button', { name: /retry/i })).toBeNull()
    } finally {
      restore()
    }
  })

  it('clears and signs in only the failed gateway once', async () => {
    const gatewayUrl = 'http://100.116.104.53:9191'
    const logout = vi.fn().mockResolvedValue({ ok: true, connected: false })
    const login = vi.fn().mockResolvedValue({ ok: true, connected: false })

    const restore = stubDesktop(
      {
        ...remoteToken,
        remoteAuthMode: 'oauth',
        remoteOauthConnected: false,
        remoteTokenSet: false,
        remoteUrl: gatewayUrl
      },
      {
        oauthLoginConnectionConfig: login,
        oauthLogoutConnectionConfig: logout,
        probeConnectionConfig: vi.fn().mockResolvedValue({ providers: [{ id: 'basic', type: 'password' }] })
      }
    )

    try {
      render(<BootFailureOverlay />)
      fireEvent.click(await screen.findByRole('button', { name: /sign out & sign in/i }))

      await waitFor(() => expect(login).toHaveBeenCalledWith(gatewayUrl))
      expect(logout).toHaveBeenCalledTimes(1)
      expect(logout).toHaveBeenCalledWith(gatewayUrl)
      expect(login).toHaveBeenCalledTimes(1)
    } finally {
      restore()
    }
  })

  it('recovers a cloud connection through the portal cascade instead of native OAuth', async () => {
    const gatewayUrl = 'https://agent-1.agents.nousresearch.com'
    const logout = vi.fn().mockResolvedValue({ ok: true, connected: false })
    const nativeLogin = vi.fn().mockResolvedValue({ ok: true, connected: false })
    const cloudStatus = vi.fn().mockResolvedValue({ portalBaseUrl: 'https://portal.nousresearch.com', signedIn: false })

    const cloudLogin = vi.fn().mockResolvedValue({
      ok: true,
      portalBaseUrl: 'https://portal.nousresearch.com',
      signedIn: true
    })

    const cloudAgentSignIn = vi.fn().mockResolvedValue({ baseUrl: gatewayUrl, connected: false })

    const restore = stubDesktop(
      {
        ...remoteToken,
        mode: 'cloud',
        remoteAuthMode: 'oauth',
        remoteOauthConnected: false,
        remoteTokenSet: false,
        remoteUrl: gatewayUrl
      },
      {
        cloud: { status: cloudStatus, login: cloudLogin, agentSignIn: cloudAgentSignIn },
        oauthLoginConnectionConfig: nativeLogin,
        oauthLogoutConnectionConfig: logout,
        probeConnectionConfig: vi.fn().mockResolvedValue({ providers: [{ id: 'nous', type: 'oauth' }] })
      }
    )

    try {
      render(<BootFailureOverlay />)
      fireEvent.click(await screen.findByRole('button', { name: /sign in/i }))

      await waitFor(() => expect(cloudAgentSignIn).toHaveBeenCalledWith(gatewayUrl))
      expect(logout).toHaveBeenCalledWith(gatewayUrl)
      expect(cloudStatus).toHaveBeenCalledTimes(1)
      expect(cloudLogin).toHaveBeenCalledTimes(1)
      expect(nativeLogin).not.toHaveBeenCalled()
    } finally {
      restore()
    }
  })

  it('shows the Nous Cloud down recovery when the backend flags isCloudBackendDown', async () => {
    const restore = stubDesktop(remoteToken)
    $desktopBoot.set({
      error: 'Nous Cloud agent ares-3009.agents.nousresearch.com is down (HTTP 503: server-side fault).',
      fakeMode: false,
      isCloudBackendDown: true,
      message: 'boot failed',
      phase: 'renderer.error',
      progress: 40,
      running: false,
      statusCode: 503,
      timestamp: Date.now(),
      visible: true
    })

    try {
      render(<BootFailureOverlay />)
      // Cloud-specific title + actionable recovery instead of the generic
      // remote-failure copy.
      expect(await screen.findByText(/Nous Cloud agent is down/i)).toBeTruthy()
      // Portal and Discord are dedicated action buttons (localized labels
      // can't drift the URLs, which live in code).
      expect(screen.getByRole('button', { name: /check portal status/i })).toBeTruthy()
      expect(screen.getByRole('button', { name: /get help from nous on discord/i })).toBeTruthy()
      // Cloud-down is a remote failure: local-only Repair is dropped; the
      // actionable paths are Gateway settings + Use local gateway.
      expect(screen.queryByRole('button', { name: /repair/i })).toBeNull()
      expect(screen.getByRole('button', { name: /gateway settings/i })).toBeTruthy()
      expect(screen.getByRole('button', { name: /use local gateway/i })).toBeTruthy()
      // The electron-built error message (portal / local mode / Discord) is
      // still surfaced in the error box.
      expect(screen.getByText(/ares-3009\.agents\.nousresearch\.com/i)).toBeTruthy()
    } finally {
      restore()
    }
  })
})
