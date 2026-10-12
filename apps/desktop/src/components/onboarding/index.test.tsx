import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { HermesGateway } from '@/eidolon'
import { $gateway, setPrimaryGateway } from '@/store/gateway'
import * as notifications from '@/store/notifications'
import { $desktopOnboarding, type DesktopOnboardingState, type OnboardingContext } from '@/store/onboarding'
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
