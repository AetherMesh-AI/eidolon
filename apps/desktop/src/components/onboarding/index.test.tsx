import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import type { HermesGateway } from '@/eidolon'
import { $gateway, setPrimaryGateway } from '@/store/gateway'
import * as notifications from '@/store/notifications'
import { $desktopOnboarding, closeManualOnboarding, type DesktopOnboardingState, type OnboardingContext, startManualOnboarding } from '@/store/onboarding'
import { $gatewayState } from '@/store/session'
import { beginSourceSwitchIntent } from '@/store/source-switch-intent'
import { makeOAuthProvider } from '@/test/oauth-provider'
import type { OAuthProvider } from '@/types/eidolon'

import { DesktopOnboardingOverlay, Picker } from '.'

function setProviders(providers: OAuthProvider[]) {
  $desktopOnboarding.set({
    configured: false,
    flow: { status: 'idle' },
    mode: 'oauth',
    providers,
    reason: null,
    requested: false,
    firstRunSkipped: false,
    manual: false,
    localEndpoint: false
  } satisfies DesktopOnboardingState)
}

const ctx: OnboardingContext = { requestGateway: async () => undefined as never }

afterEach(() => {
  cleanup()

  try {
    window.localStorage.clear()
  } catch {
    // jsdom localStorage should always be present; ignore if not.
  }

  $desktopOnboarding.set({
    configured: null,
    flow: { status: 'idle' },
    mode: 'oauth',
    providers: null,
    reason: null,
    requested: false,
    firstRunSkipped: false,
    manual: false,
    localEndpoint: false
  })
})

describe('onboarding Picker', () => {
  it('shows an informational network preview and keeps real providers in the disclosure', () => {
    setProviders([makeOAuthProvider('anthropic', 'Anthropic Claude'), makeOAuthProvider('nous', 'Nous Portal')])
    render(<Picker ctx={ctx} />)

    const network = screen.getByRole('region', { name: 'AetherMesh' })
    expect(network.textContent).toContain('Coming soon')
    expect(network.textContent).toContain('peer-to-peer AI network')
    expect(network.querySelector('button, a, input')).toBeNull()
    expect(screen.queryByText('Nous Portal')).toBeNull()
    // The preview cannot begin authentication; real providers stay available.
    fireEvent.click(network)
    expect($desktopOnboarding.get().flow.status).toBe('idle')
    expect(screen.queryByText('Fireworks AI')).toBeNull()
    expect(screen.queryByText('Anthropic API Key')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Other providers' }))

    expect(screen.getByText('Nous Portal')).toBeTruthy()
    expect(screen.getByText('Fireworks AI')).toBeTruthy()
    expect(screen.getByText('Anthropic API Key')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Collapse' }))
    expect(screen.queryByText('Nous Portal')).toBeNull()
    expect(screen.getByRole('region', { name: 'AetherMesh' })).toBeTruthy()
  })

  it('shows Fireworks first in the expanded list, ahead of other OAuth providers', () => {
    setProviders([
      makeOAuthProvider('openai-codex', 'OpenAI Codex / ChatGPT'),
      makeOAuthProvider('minimax-oauth', 'MiniMax'),
      makeOAuthProvider('nous', 'Nous Portal')
    ])
    render(<Picker ctx={ctx} />)
    fireEvent.click(screen.getByRole('button', { name: 'Other providers' }))

    const labels = screen
      .getAllByRole('button')
      .map(el => el.textContent ?? '')
      .filter(text => /Nous Portal|Fireworks AI|ChatGPT or Codex|MiniMax|OpenRouter/.test(text))

    const indexOf = (needle: string) => labels.findIndex(text => text.includes(needle))
    expect(indexOf('Nous Portal')).toBeGreaterThanOrEqual(0)
    expect(indexOf('Nous Portal')).toBeGreaterThan(indexOf('Fireworks AI'))
    expect(indexOf('ChatGPT or Codex')).toBeGreaterThan(indexOf('Fireworks AI'))
    expect(indexOf('MiniMax')).toBeGreaterThan(indexOf('ChatGPT or Codex'))
  })

  it('shows every provider directly when Nous Portal is absent', () => {
    setProviders([
      makeOAuthProvider('anthropic', 'Anthropic Claude'),
      makeOAuthProvider('openai-codex', 'OpenAI Codex / ChatGPT')
    ])
    render(<Picker ctx={ctx} />)

    expect(screen.getByText('Fireworks AI')).toBeTruthy()
    expect(screen.getByText('Anthropic API Key')).toBeTruthy()
    expect(screen.getByText('ChatGPT or Codex Subscription')).toBeTruthy()
    expect(screen.queryByText('Other sign-in options')).toBeNull()
    expect(screen.queryByText('Recommended')).toBeNull()
  })

  it('offers "choose later" on first run and persists the skip', () => {
    setProviders([makeOAuthProvider('nous', 'Nous Portal')])
    render(<Picker ctx={ctx} />)

    const skip = screen.getByRole('button', { name: "I'll choose a provider later" })

    fireEvent.click(skip)

    expect($desktopOnboarding.get().firstRunSkipped).toBe(true)
    expect(window.localStorage.getItem('hermes-onboarding-skipped-v1')).toBe('1')
  })

  it('hides "choose later" in manual (add-provider) mode', () => {
    setProviders([makeOAuthProvider('nous', 'Nous Portal')])
    $desktopOnboarding.set({ ...$desktopOnboarding.get(), manual: true })
    render(<Picker ctx={ctx} />)

    expect(screen.queryByRole('button', { name: "I'll choose a provider later" })).toBeNull()
  })
})


describe('automatic readiness source lifetime', () => {
  it.each(['disconnect', 'newer-source', 'unmount'])('does not show a stale readiness failure after %s', async retirement => {
    setProviders([])
    $desktopOnboarding.set({ ...$desktopOnboarding.get(), configured: true })
    $gatewayState.set('open')
    const notify = vi.spyOn(notifications, 'notify')
    notify.mockClear()
    let release!: () => void
    const held = new Promise<void>(resolve => { release = resolve })

    let calls = 0

    const request = vi.fn(async (method: string) => {
      if (++calls <= 2) {
        await held
        throw new Error('retired transport closed')
      }

      return (method === 'setup.status' ? { provider_configured: true } : { ok: true, provider: 'custom' }) as never
    })

    const view = render(<DesktopOnboardingOverlay enabled profile="default" requestGateway={request} />)
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(2))
    await act(async () => {
      if (retirement === 'disconnect') { $gatewayState.set('closed') }

      if (retirement === 'newer-source') { beginSourceSwitchIntent() }

      if (retirement === 'unmount') { view.unmount() }
      release()
      await Promise.allSettled(request.mock.results.map(result => result.value))
    })

    if (retirement === 'newer-source') {expect(request).toHaveBeenCalledTimes(4)}
    expect(notify).not.toHaveBeenCalled()
    expect($desktopOnboarding.get().configured).toBe(true)
    notify.mockRestore()
    $gatewayState.set('closed')
  })
})


describe('replacement readiness checks', () => {
  it.each(['source-intent', 'same-label-gateway', 'same-object-generation'])('starts a fresh read after %s while the profile stays open', async replacement => {
    setProviders([])
    $desktopOnboarding.set({ ...$desktopOnboarding.get(), configured: true })
    $gatewayState.set('open')
    const gateway = {} as HermesGateway
    setPrimaryGateway(gateway, 'default', { connectionId: 'source-a' })
    $gateway.set(gateway)
    let release!: () => void
    const held = new Promise<void>(resolve => { release = resolve })
    let calls = 0

    const request = vi.fn(async (method: string) => {
      if (++calls <= 2) {await held}

      return (method === 'setup.status' ? { provider_configured: true } : { ok: true, provider: 'custom' }) as never
    })

    const onCompleted = vi.fn()
    const view = render(<DesktopOnboardingOverlay enabled onCompleted={onCompleted} profile="default" requestGateway={request} />)
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(2))
    await act(async () => {
      if (replacement === 'source-intent') {beginSourceSwitchIntent()}
      else if (replacement === 'same-label-gateway') {
        const next = {} as HermesGateway
        setPrimaryGateway(next, 'default', { connectionId: 'source-b' })
        $gateway.set(next)
      } else {setPrimaryGateway(gateway, 'default', { connectionId: 'source-a' })}
    })
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(4))
    await vi.waitFor(() => expect(onCompleted).toHaveBeenCalledTimes(1))
    await act(async () => {release(); await Promise.allSettled(request.mock.results.map(result => result.value))})
    expect(onCompleted).toHaveBeenCalledTimes(1)
    view.unmount()
    setPrimaryGateway(null)
    $gateway.set(null)
    $gatewayState.set('closed')
  })
})


describe('provider response publication ownership', () => {
  it.each(['unmount', 'source-intent'])('does not publish an old provider response after %s', async retirement => {
    setProviders([])
    $desktopOnboarding.set({ ...$desktopOnboarding.get(), providers: null })
    $gatewayState.set('open')
    let release!: (value: { providers: OAuthProvider[] }) => void
    const held = new Promise<{ providers: OAuthProvider[] }>(resolve => { release = resolve })
    const providerApi = vi.fn().mockReturnValueOnce(held).mockResolvedValue({ providers: [makeOAuthProvider('fresh')] })
    const api = vi.fn(async ({ path }: { path: string }) => path === '/api/providers/oauth' ? providerApi() : { providers: [] })
    const previousBridge = window.hermesDesktop
    Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: { api } })

    const request = vi.fn(async (method: string) => (method === 'setup.status'
      ? { provider_configured: false } : { ok: false, error: 'Not configured' }) as never)

    const view = render(<DesktopOnboardingOverlay enabled profile="default" requestGateway={request} />)
    await vi.waitFor(() => expect(providerApi).toHaveBeenCalledTimes(1))

    if (retirement === 'unmount') {view.unmount()}
    else {
      await act(async () => {beginSourceSwitchIntent()})
      await vi.waitFor(() => expect(providerApi).toHaveBeenCalledTimes(2))
      await vi.waitFor(() => expect($desktopOnboarding.get().providers?.[0]?.id).toBe('fresh'))
    }

    const current = $desktopOnboarding.get()
    await act(async () => {release({ providers: [] }); await held})
    expect($desktopOnboarding.get()).toBe(current)
    view.unmount()
    Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: previousBridge })
    $gatewayState.set('closed')
  })
})

describe('manual provider entry publication ownership', () => {
  it.each(['success', 'failure'])('loads on first open and rejects retired manual catalog %s', async result => {
    setProviders([])
    $desktopOnboarding.set({ ...$desktopOnboarding.get(), configured: true })
    $gatewayState.set('open')
    let resolve!: (value: { providers: OAuthProvider[] }) => void
    let reject!: (reason: Error) => void
    const held = new Promise<{ providers: OAuthProvider[] }>((yes, no) => { resolve = yes; reject = no })
    const providerApi = vi.fn().mockReturnValueOnce(held).mockResolvedValue({ providers: [makeOAuthProvider('fresh')] })
    const previousBridge = window.hermesDesktop
    Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: {
      api: ({ path }: { path: string }) => path === '/api/providers/oauth' ? providerApi() : Promise.resolve({ providers: [] })
    } })
    const request = vi.fn(async () => ({ ok: true }) as never)
    const view = render(<DesktopOnboardingOverlay enabled profile="default" requestGateway={request} />)

    try {
      await act(async () => { startManualOnboarding(null, 'default') })
      await vi.waitFor(() => expect(providerApi).toHaveBeenCalledTimes(1))
      // The public entry must not compete with the mounted effect, even on the
      // same source. A reconnect then owns a genuinely new catalog request.
      await act(async () => { beginSourceSwitchIntent() })
      await vi.waitFor(() => expect(providerApi).toHaveBeenCalledTimes(2))
      await vi.waitFor(() => expect($desktopOnboarding.get().providers?.[0]?.id).toBe('fresh'))
      const current = $desktopOnboarding.get()
      await act(async () => {
        if (result === 'success') {resolve({ providers: [makeOAuthProvider('retired')] })}
        else {reject(new Error('Retired provider request failed'))}

        await held.catch(() => {})
      })
      expect($desktopOnboarding.get()).toBe(current)
      expect($desktopOnboarding.get().mode).toBe('oauth')
      await act(async () => { closeManualOnboarding() })
      await act(async () => { startManualOnboarding(null, 'default') })
      await vi.waitFor(() => expect(providerApi).toHaveBeenCalledTimes(3))
      await vi.waitFor(() => expect($desktopOnboarding.get().providers?.[0]?.id).toBe('fresh'))
    } finally {
      view.unmount()
      closeManualOnboarding()
      Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: previousBridge })
      $gatewayState.set('closed')
    }
  })
})


it('gives manual provider setup keyboard ownership and dismisses only that overlay with Escape', async () => {
  setProviders([])
  $desktopOnboarding.set({ ...$desktopOnboarding.get(), manual: true, configured: true })
  const view = render(<><button>Background action</button><DesktopOnboardingOverlay enabled={false} profile="default" requestGateway={ctx.requestGateway} /></>)
  const dialog = screen.getByRole('dialog', { name: "Let's get you setup with Eidolon" })
  await vi.waitFor(() => expect(dialog.contains(globalThis.document.activeElement)).toBe(true))
  const user = userEvent.setup()

  for (let index = 0; index < 8; index++) {
    await user.tab()
    expect(dialog.contains(globalThis.document.activeElement)).toBe(true)
  }

  fireEvent.keyDown(globalThis.document.activeElement!, { key: 'Escape' })
  expect($desktopOnboarding.get().manual).toBe(false)
  expect(screen.queryByRole('dialog')).toBeNull()
  view.unmount()
})


it('relinquishes an already mounted automatic overlay to verified workspace setup without marking readiness or suppressing explicit onboarding', async () => {
  setProviders([])
  $gatewayState.set('open')
  const request = vi.fn(async (method: string) => (method === 'setup.status' ? { provider_configured: false } : { ok: false, error: 'Separate workspace setup required' }) as never)
  const selected = vi.fn()
  const user = userEvent.setup()

  const renderSetup = (owned: boolean) => <>
    <Dialog open><DialogContent><DialogTitle>Created workspace receipt</DialogTitle><input aria-label="Workspace model draft" defaultValue="retained model" /><button onClick={selected}>Open its Keys</button></DialogContent></Dialog>
    <DesktopOnboardingOverlay enabled organizationSetupOwned={owned} profile="default" requestGateway={request} />
  </>

  const view = render(renderSetup(false))
  await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(2))
  expect(screen.queryAllByRole('dialog', { hidden: true })).toHaveLength(2)
  expect($desktopOnboarding.get().configured).toBe(false)
  view.rerender(renderSetup(true))
  await vi.waitFor(() => expect(screen.queryAllByRole('dialog', { hidden: true })).toHaveLength(1))
  await user.tab()
  expect(screen.getByRole('dialog', { name: 'Created workspace receipt' }).contains(window.document.activeElement)).toBe(true)
  await user.click(screen.getByRole('button', { name: 'Open its Keys' }))
  expect(selected).toHaveBeenCalledOnce()
  expect((screen.getByLabelText('Workspace model draft') as HTMLInputElement).value).toBe('retained model')
  expect($desktopOnboarding.get().configured).toBe(false)
  expect(localStorage.getItem('hermes-desktop-onboarded-v1')).not.toBe('1')
  await act(async () => { startManualOnboarding(null, 'default') })
  expect(screen.queryAllByRole('dialog', { hidden: true })).toHaveLength(2)
  expect($desktopOnboarding.get().manual).toBe(true)
  await act(async () => { closeManualOnboarding() })
  expect(screen.queryAllByRole('dialog', { hidden: true })).toHaveLength(1)
  view.rerender(renderSetup(false))
  expect(screen.queryAllByRole('dialog', { hidden: true })).toHaveLength(2)
})
