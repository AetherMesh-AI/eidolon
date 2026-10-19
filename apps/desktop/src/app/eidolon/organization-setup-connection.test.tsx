import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { setupConnectionCopy } from '@/i18n/organization-setup-connection'
import { setupSubscriptionCopy } from '@/i18n/organization-team-setup-subscription'

import { OrganizationSetupConnectionSettings } from './organization-setup-connection'
import type { SetupConnectionSnapshot } from './organization-setup-connection-client'
import { subscriptionSetup, subscriptionStatus } from './team-setup-subscription-fixtures'

const mocked = vi.hoisted(() => ({ metadata: null as unknown, create: vi.fn() }))
vi.mock('./organization-directory-runtime', () => ({ subscribeOrganizationDirectorySource: vi.fn() }))
vi.mock('./use-organization-metadata', () => ({
  readOrganizationMetadataScope: vi.fn(),
  useOrganizationMetadata: () => mocked.metadata
}))
vi.mock('./organization-setup-connection-client', () => ({ createOrganizationSetupConnectionClient: mocked.create }))

const copy = setupConnectionCopy.en

const initial: SetupConnectionSnapshot = {
  capability: 'available',
  setup: { ...subscriptionSetup, state: 'configured' },
  status: { ...subscriptionStatus, connectionRevision: 2, connectionState: 'active' }
}

const metadata = (owner = 'owner-a') => ({
  scope: { key: owner, ownerKey: owner, profile: 'default', eligible: true },
  view: {
    busy: false,
    error: null,
    descriptor: {
      state: 'ready',
      metadata: { organizationId: subscriptionSetup.organizationId, owner: { ownerId: owner } }
    }
  },
  client: { read: vi.fn() }
})

function client(snapshot: SetupConnectionSnapshot = initial) {
  let current = true

  return {
    current: () => current,
    dispose: vi.fn(() => {
      current = false
    }),
    inspect: vi.fn(async () => snapshot),
    recover: vi.fn(async () => snapshot),
    disconnect: vi.fn(async () => ({
      ...snapshot,
      status: { ...subscriptionStatus, connectionState: 'disconnected' as const, connectionRevision: 3 }
    }))
  }
}

function tree() {
  return (
    <MemoryRouter>
      <I18nProvider configClient={null} initialLocale="en">
        <OrganizationSetupConnectionSettings />
      </I18nProvider>
    </MemoryRouter>
  )
}

beforeEach(() => {
  mocked.metadata = metadata()
  mocked.create.mockReset()
})

it('keeps management after Create, explains setup-only limits and disconnects only on an explicit click', async () => {
  const current = client()
  mocked.create.mockReturnValue(current)
  render(tree())
  const region = screen.getByRole('region', { name: copy.title })
  await within(region).findByText(copy.active)
  expect(within(region).getByText(copy.purpose)).toBeTruthy()
  expect(within(region).getByText(copy.setupFinished)).toBeTruthy()
  expect(within(region).queryByRole('link', { name: copy.openSetup })).toBeNull()
  expect(current.disconnect).not.toHaveBeenCalled()
  expect(current.recover).not.toHaveBeenCalled()
  fireEvent.click(within(region).getByRole('button', { name: copy.disconnect }))
  await within(region).findByText(copy.disconnected)
  expect(within(region).getByText(copy.remoteUnknown)).toBeTruthy()
  expect(within(region).queryByRole('button', { name: copy.disconnect })).toBeNull()
  expect(current.disconnect).toHaveBeenCalledTimes(1)
})

it('supports explicit recovery of refreshing and unknown state without an auth or model editor', async () => {
  const current = client({ ...initial, status: null })
  mocked.create.mockReturnValue(current)
  const ui = render(tree())
  await screen.findByText(copy.unknown)
  expect(screen.queryByRole('button', { name: copy.disconnect })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: copy.recover }))
  await waitFor(() => expect(current.recover).toHaveBeenCalledTimes(1))
  ui.unmount()
  const refreshing = client({ ...initial, status: { ...initial.status!, connectionState: 'refreshing' } })
  mocked.create.mockReturnValue(refreshing)
  render(tree())
  await screen.findByText(copy.refreshing)
  expect((screen.getByRole('button', { name: copy.disconnect }) as HTMLButtonElement).disabled).toBe(false)
  expect(screen.queryByRole('checkbox')).toBeNull()
  expect(screen.queryByRole('combobox')).toBeNull()
})

it('links only a freshly eligible organization into the existing setup route', async () => {
  mocked.create.mockReturnValue(client({ ...initial, setup: subscriptionSetup }))
  render(tree())
  const link = await screen.findByRole('link', { name: copy.openSetup })
  expect(link.getAttribute('href')).toBe('/home')
  expect(screen.queryByText(copy.setupFinished)).toBeNull()
})

it('retires late old-owner replies and duplicate clicks across profile/socket changes', async () => {
  const old = client()
  let finish!: (value: SetupConnectionSnapshot) => void
  old.disconnect.mockImplementation(
    () =>
      new Promise(resolve => {
        finish = resolve
      }) as never
  )
  const next = client({ ...initial, status: subscriptionStatus })
  mocked.create.mockReturnValueOnce(old).mockReturnValueOnce(next)
  const ui = render(tree())
  await screen.findByText(copy.active)
  const disconnect = screen.getByRole('button', { name: copy.disconnect })
  fireEvent.click(disconnect)
  fireEvent.click(disconnect)
  expect(old.disconnect).toHaveBeenCalledTimes(1)
  mocked.metadata = metadata('owner-b')
  ui.rerender(tree())
  await screen.findByText(copy.absent)
  await act(async () => finish({ ...initial, status: { ...subscriptionStatus, connectionState: 'disconnected' } }))
  expect(screen.queryByText(copy.disconnected)).toBeNull()
  expect(old.dispose).toHaveBeenCalledTimes(1)
  expect(next.disconnect).not.toHaveBeenCalled()
})

it('does not retain an actionable snapshot after a failed mutation and waits for an explicit status check', async () => {
  const current = client()
  current.disconnect.mockRejectedValueOnce(new Error('uncertain'))
  mocked.create.mockReturnValue(current)
  render(tree())
  await screen.findByText(copy.active)
  fireEvent.click(screen.getByRole('button', { name: copy.disconnect }))
  await screen.findByText(copy.error)
  expect(current.disconnect).toHaveBeenCalledTimes(1)
  expect(current.inspect).toHaveBeenCalledTimes(1)
  expect(screen.queryByRole('button', { name: copy.disconnect })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: copy.check }))
  await screen.findByText(copy.active)
  expect(current.inspect).toHaveBeenCalledTimes(2)
})

it('does not instantiate connection management for an unverified owner and supplies all locale copy', () => {
  mocked.metadata = { ...metadata(), scope: { ...metadata().scope, eligible: false } }
  render(tree())
  expect(screen.getByText(copy.unavailable)).toBeTruthy()
  expect(mocked.create).not.toHaveBeenCalled()

  for (const locale of Object.values(setupConnectionCopy)) {
    expect(Object.keys(locale)).toEqual(Object.keys(copy))
    expect(Object.values(locale).every(value => typeof value === 'string' && value.length > 0)).toBe(true)
  }
})

it('presents a verified unsupported platform as informational and only exposes explicit Check', async () => {
  const current = client({
    capability: 'unavailable',
    reason: 'unsupported_platform',
    setup: { ...initial.setup, setupSubscriptionState: 'unsupported_platform' },
    status: null
  })

  mocked.create.mockReturnValue(current)
  render(tree())
  const notice = await screen.findByText(setupSubscriptionCopy.en.unsupported)
  expect(notice.getAttribute('role')).toBe('status')
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.queryByRole('button', { name: copy.recover })).toBeNull()
  expect(screen.queryByRole('button', { name: copy.disconnect })).toBeNull()
  expect(screen.queryByRole('link', { name: copy.openSetup })).toBeNull()
  expect(current.inspect).toHaveBeenCalledTimes(1)
  current.inspect.mockResolvedValueOnce(initial)
  fireEvent.click(screen.getByRole('button', { name: copy.check }))
  await screen.findByText(copy.active)
  expect(screen.queryByText(setupSubscriptionCopy.en.unsupported)).toBeNull()
  expect(current.inspect).toHaveBeenCalledTimes(2)
  expect(current.recover).not.toHaveBeenCalled()
  expect(current.disconnect).not.toHaveBeenCalled()
})

it('keeps a genuine inspection failure and supported unknown connection alerting', async () => {
  const current = client()
  current.inspect.mockRejectedValueOnce(new Error('verification_failed'))
  mocked.create.mockReturnValue(current)
  render(tree())
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', copy.error)
  expect(screen.queryByText(setupSubscriptionCopy.en.unsupported)).toBeNull()
  current.inspect.mockResolvedValueOnce({ ...initial, status: null })
  fireEvent.click(screen.getByRole('button', { name: copy.check }))
  expect((await screen.findByText(copy.unknown)).getAttribute('role')).toBe('alert')
  expect(current.inspect).toHaveBeenCalledTimes(2)
})

it('retires a late unsupported-platform response when the owner changes', async () => {
  const old = client()
  let finish!: (value: SetupConnectionSnapshot) => void
  old.inspect.mockImplementationOnce(
    () =>
      new Promise(resolve => {
        finish = resolve
      })
  )
  const next = client()
  mocked.create.mockReturnValueOnce(old).mockReturnValueOnce(next)
  const ui = render(tree())
  mocked.metadata = metadata('owner-b')
  ui.rerender(tree())
  await screen.findByText(copy.active)
  await act(async () =>
    finish({
      capability: 'unavailable',
      reason: 'unsupported_platform',
      setup: { ...initial.setup, setupSubscriptionState: 'unsupported_platform' },
      status: null
    })
  )
  expect(screen.queryByText(setupSubscriptionCopy.en.unsupported)).toBeNull()
  expect(screen.queryByRole('alert')).toBeNull()
  expect(old.dispose).toHaveBeenCalledTimes(1)
  expect(next.recover).not.toHaveBeenCalled()
  expect(next.disconnect).not.toHaveBeenCalled()
})
