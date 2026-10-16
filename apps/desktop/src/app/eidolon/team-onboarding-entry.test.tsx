import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'

import { TeamOnboardingEntry } from './team-onboarding'
import type { OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'
import { OrganizationWorkspaceView } from './workspace'

const state = vi.hoisted(() => ({
  scope: { key: 'A', ownerKey: 'A', eligible: true, profile: 'default' },
  organizationId: null as string | null,
  setupState: 'available',
  saved: null as null | ((receipt: unknown) => void),
  clients: [] as { scope: string; setup: ReturnType<typeof vi.fn> }[],
  request: vi.fn()
}))
vi.mock('./use-organization-metadata', () => {
  const getSnapshot = () => ({
    descriptor: {
      state: state.organizationId ? 'ready' : 'unconfigured',
      metadata: state.organizationId ? { organizationId: state.organizationId, owner: { ownerId: `${state.organizationId}-owner` } } : null
    }
  })

  const client = { getSnapshot, subscribe: () => () => {} }

  return {
    organizationMetadataClient: client,
    readOrganizationMetadataScope: () => ({ ...state.scope, request: state.request }),
    useOrganizationMetadata: () => ({ client, scope: state.scope, view: getSnapshot() })
  }
})
vi.mock('./organization-owner-editor', () => ({
  OrganizationOwnerEditor: ({ onReturnFocus }: { onReturnFocus: (receipt: unknown) => void }) => {
    state.saved = onReturnFocus
    return <div>Identity editor</div>
  }
}))
vi.mock('./team-onboarding-client', () => ({
  createTeamOnboardingClient: () => {
    const origin = state.scope.key
    const owner = state.scope.ownerKey
    const profile = state.scope.profile
    let disposed = false
    const setup = vi.fn().mockResolvedValue({
      capabilityVersion: 1,
      state: state.setupState,
      organizationId: origin,
      presets: [],
      readiness: { status: 'unchecked', blockers: [] }
    })
    state.clients.push({ scope: origin, setup })
    return {
      setup,
      current: () =>
        !disposed &&
        state.scope.eligible &&
        state.scope.key === origin &&
        state.scope.ownerKey === owner &&
        state.scope.profile === profile,
      dispose: () => {
        disposed = true
      }
    }
  }
}))

beforeEach(() => {
  state.scope = { key: 'A', ownerKey: 'A', eligible: true, profile: 'default' }
  state.organizationId = null
  state.setupState = 'available'
  state.saved = null
  state.clients = []
  state.request.mockReset()
})
function harness(refresh = vi.fn().mockResolvedValue(undefined)) {
  const adapter = { refresh } as unknown as RuntimeOrganizationAdapter
  const component = () => (
    <MemoryRouter>
      <I18nProvider configClient={null} initialLocale="en">
        <TeamOnboardingEntry adapter={adapter} />
      </I18nProvider>
    </MemoryRouter>
  )
  const view = render(component())
  return { refresh, rerender: () => view.rerender(component()) }
}
it('reads existing organization availability without requesting snapshot initialization', async () => {
  state.organizationId = 'A'
  const h = harness()
  await screen.findByRole('button', { name: 'Generate team · Recommended' })
  expect(state.clients[0].setup).toHaveBeenCalledExactlyOnceWith('A')
  expect(h.refresh).not.toHaveBeenCalled()
  h.rerender()
  expect(state.clients[0].setup).toHaveBeenCalledTimes(1)
})
it('initializes only after the confirmed identity save before opening team setup', async () => {
  const h = harness()
  fireEvent.click(screen.getByRole('button', { name: 'Set up organization' }))
  act(() => {
    state.organizationId = 'A'
    state.saved!({ metadata: { organizationId: 'A' } })
    h.rerender()
  })
  await screen.findByRole('dialog', { name: 'Build your starter team' })
  expect(h.refresh).toHaveBeenCalledTimes(1)
  expect(state.clients.at(-1)!.setup).toHaveBeenCalledExactlyOnceWith('A')
})
it.each(['key', 'ownerKey', 'profile'] as const)(
  'does not refresh a changed %s for a late identity-save callback',
  async field => {
    const h = harness()
    fireEvent.click(screen.getByRole('button', { name: 'Set up organization' }))
    const originalCallback = state.saved!
    act(() => {
      state.scope = { ...state.scope, [field]: 'B' }
      state.organizationId = 'B'
      originalCallback({ metadata: { organizationId: 'A' } })
      h.rerender()
    })
    await screen.findByRole('button', { name: 'Generate team · Recommended' })
    expect(h.refresh).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).toBeNull()
  }
)
it('discards a confirmed initialization when its owner changes before it finishes', async () => {
  let finish!: () => void
  const refresh = vi.fn(
    () =>
      new Promise<void>(resolve => {
        finish = resolve
      })
  )
  const h = harness(refresh)
  fireEvent.click(screen.getByRole('button', { name: 'Set up organization' }))
  act(() => {
    state.organizationId = 'A'
    state.saved!({ metadata: { organizationId: 'A' } })
    h.rerender()
  })
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1))
  const oldClient = state.clients[0]
  act(() => {
    state.scope = { ...state.scope, key: 'B', ownerKey: 'B' }
    state.organizationId = 'B'
    h.rerender()
  })
  await act(async () => finish())
  await screen.findByRole('button', { name: 'Generate team · Recommended' })
  expect(oldClient.setup).not.toHaveBeenCalled()
  expect(refresh).toHaveBeenCalledTimes(1)
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('resumes failed identity initialization only on explicit retry', async () => {
  const refresh = vi.fn().mockRejectedValueOnce(new Error('read failed')).mockResolvedValue(undefined)
  const h = harness(refresh)
  fireEvent.click(screen.getByRole('button', { name: 'Set up organization' }))
  act(() => {
    state.organizationId = 'A'
    state.saved!({ metadata: { organizationId: 'A' } })
    h.rerender()
  })
  await screen.findByRole('alert')
  h.rerender()
  expect(refresh).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
  await screen.findByRole('dialog', { name: 'Build your starter team' })
  expect(refresh).toHaveBeenCalledTimes(2)
})

it('keeps the fresh runtime invitation inside the Home hero without a separate overview row', () => {
  const snapshot: OrganizationSnapshot = {
    source: 'runtime',
    objectives: [],
    requests: [],
    tasks: [],
    agents: [],
    activity: [],
    knowledge: [],
    connection: { scope: 'A', state: 'ready' }
  }
  const adapter = {
    mode: 'runtime',
    getSnapshot: () => snapshot,
    subscribe: () => () => {},
    refresh: vi.fn()
  } as unknown as RuntimeOrganizationAdapter
  render(
    <MemoryRouter initialEntries={['/home']}>
      <I18nProvider configClient={null} initialLocale="en">
        <OrganizationWorkspaceView adapter={adapter} />
      </I18nProvider>
    </MemoryRouter>
  )
  const action = screen.getByRole('button', { name: 'Set up organization' })
  expect(action.closest('.eid-home-hero')).not.toBeNull()
  expect(screen.getByRole('button', { name: 'Message organization' })).toBeTruthy()
  expect(adapter.refresh).not.toHaveBeenCalled()
  // Rendering the unconfigured owner must not request prepared availability.
  expect(state.request).not.toHaveBeenCalled()
})

it('retains explicit initialization recovery when snapshot refresh resolves without creating the schema', async () => {
  state.setupState = 'unconfigured'
  const h = harness()
  fireEvent.click(screen.getByRole('button', { name: 'Set up organization' }))
  act(() => {
    state.organizationId = 'A'
    state.saved!({ metadata: { organizationId: 'A' } })
    h.rerender()
  })
  await screen.findByRole('alert')
  h.rerender()
  expect(h.refresh).toHaveBeenCalledTimes(1)
  state.setupState = 'available'
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
  await screen.findByRole('dialog', { name: 'Build your starter team' })
  expect(h.refresh).toHaveBeenCalledTimes(2)
})

it.each(['key', 'ownerKey', 'profile'] as const)(
  'does not rearm initialization when %s changed before a retry click',
  async field => {
    const refresh = vi.fn().mockRejectedValueOnce(new Error('initialization failed')).mockResolvedValue(undefined)
    const h = harness(refresh)
    fireEvent.click(screen.getByRole('button', { name: 'Set up organization' }))
    act(() => {
      state.organizationId = 'A'
      state.saved!({ metadata: { organizationId: 'A' } })
      h.rerender()
    })
    await screen.findByRole('alert')
    state.scope = { ...state.scope, [field]: 'B' }
    state.organizationId = 'B'
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    h.rerender()
    await screen.findByRole('button', { name: 'Generate team · Recommended' })
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).toBeNull()
  }
)
