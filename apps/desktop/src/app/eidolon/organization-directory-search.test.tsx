import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { organizationDirectoryCopy } from '@/i18n/organization-directory'
import { directoryActivationCopy } from '@/i18n/organization-directory-activation'

import { deferred } from '../../test/deferred'

import { createOrganizationDirectory, type DirectorySource } from './organization-directory'
import { type ActivationResult, createOrganizationDirectoryActivation } from './organization-directory-activation'
import { type OrganizationDirectoryCopy, OrganizationDirectorySearch } from './organization-directory-search'

const copy: OrganizationDirectoryCopy = {
  choose: 'Use selected organization',
  heading: 'Organizations',
  description: 'Inspect organizations already verified in this window.',
  search: 'Search organizations',
  refresh: 'Refresh current organization',
  empty: 'No verified organizations match',
  lastKnown: 'Last known',
  verified: 'Verified',
  unavailable: 'Current organization unavailable',
  unnamed: 'Unnamed',
  unconfigured: 'Not configured',
  unsupported: 'Unsupported'
}

async function setup(activation?: ReturnType<typeof createOrganizationDirectoryActivation>) {
  const request = vi.fn().mockResolvedValue({
    capabilityVersion: 1,
    state: 'ready',
    scope: { kind: 'served-profile', profile: 'default' },
    metadata: {
      organizationId: '11111111-1111-4111-8111-111111111111',
      displayName: 'Research',
      owner: { ownerId: '22222222-2222-4222-8222-222222222222', displayName: 'Owner' },
      revision: 1,
      createdAt: '2026-10-12T00:00:00Z',
      updatedAt: '2026-10-12T00:00:00Z'
    }
  })

  let notify = () => {}

  const source: DirectorySource = {
    connections: [
      { id: 'a', label: 'Laptop', endpoint: null },
      { id: 'b', label: 'Lab', endpoint: null }
    ],
    current: {
      connectionId: 'a',
      endpoint: null,
      scope: { key: 'a', ownerKey: 'a', profile: 'default', eligible: true, request }
    }
  }

  const directory = createOrganizationDirectory(
    () => source,
    changed => {
      notify = changed

      return () => {}
    }
  )

  await directory.refresh()
  source.current = {
    ...source.current!,
    connectionId: 'b',
    scope: { ...source.current!.scope, key: 'b', ownerKey: 'b' }
  }
  notify()
  await directory.refresh()
  request.mockClear()
  const close = vi.fn()
  const returnFocus = vi.fn()
  let current = true

  const ui = render(
    <OrganizationDirectorySearch
      activation={activation}
      activationCopy={directoryActivationCopy.en}
      copy={copy}
      directory={directory}
      isCurrent={() => current}
      onClose={close}
      onReturnFocus={returnFocus}
    />
  )

  return {
    request,
    directory,
    source,
    close,
    returnFocus,
    ui,
    invalidate: () => {
      current = false
      source.current = null
      notify()
    }
  }
}

it('opens, searches duplicate names, highlights and presses Enter without reading or activating', async () => {
  const h = await setup()
  expect(screen.getAllByRole('option')).toHaveLength(2)
  expect(screen.getByText(/Last known/)).toBeTruthy()
  fireEvent.change(screen.getByRole('textbox', { name: copy.search }), { target: { value: 'Laptop' } })
  const list = screen.getByRole('listbox')
  list.focus()
  fireEvent.keyDown(list, { key: 'ArrowDown' })
  expect(screen.getByRole('option').getAttribute('aria-selected')).toBe('true')
  fireEvent.keyDown(list, { key: 'Enter' })
  fireEvent.click(screen.getByRole('option'))
  expect(h.request).not.toHaveBeenCalled()
  expect(h.close).not.toHaveBeenCalled()
  expect(h.source.current?.connectionId).toBe('b')
  h.ui.unmount()
  h.directory.dispose()
})

it('refreshes only the current captured descriptor after the explicit refresh button', async () => {
  const h = await setup()
  fireEvent.click(screen.getByRole('button', { name: copy.refresh }))
  await waitFor(() => expect(h.request).toHaveBeenCalledExactlyOnceWith('organization.descriptor', {}))
  await waitFor(() =>
    expect((screen.getByRole('button', { name: copy.refresh }) as HTMLButtonElement).disabled).toBe(false)
  )
  h.ui.unmount()
  h.directory.dispose()
})

it('drops a filtered highlight and exposes no stale active descendant', async () => {
  const h = await setup()
  const list = screen.getByRole('listbox')
  fireEvent.keyDown(list, { key: 'End' })
  expect(list.getAttribute('aria-activedescendant')).toBeTruthy()
  fireEvent.change(screen.getByRole('textbox', { name: copy.search }), { target: { value: 'missing' } })
  expect(list.getAttribute('aria-activedescendant')).toBeNull()
  expect(screen.getByText(copy.empty)).toBeTruthy()
  expect(h.request).not.toHaveBeenCalled()
  h.ui.unmount()
  h.directory.dispose()
})

it.each([false, true])('flushes deferred close autofocus with owner lost = %s', async lost => {
  const h = await setup()

  if (lost) {
    act(h.invalidate)
  }

  vi.useFakeTimers()

  try {
    await act(async () => {
      h.ui.unmount()
      await vi.runOnlyPendingTimersAsync()
    })
    expect(h.returnFocus).toHaveBeenCalledTimes(lost ? 0 : 1)

    if (lost) {
      expect(h.close).toHaveBeenCalled()
    }

    expect(h.request).not.toHaveBeenCalled()
  } finally {
    vi.useRealTimers()
    h.directory.dispose()
  }
})

it('requires confirmation, cancels without selecting and restores choice focus', async () => {
  const activation = { prepare: vi.fn(() => true), cancel: vi.fn(() => true), confirm: vi.fn() }
  const h = await setup(activation)
  fireEvent.click(screen.getAllByRole('option')[0])
  fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Enter' })
  expect(activation.prepare).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: copy.choose }))
  expect(activation.prepare).toHaveBeenCalledTimes(1)
  expect(activation.confirm).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: directoryActivationCopy.en.cancel }))
  expect(activation.cancel).toHaveBeenCalledTimes(1)
  expect(activation.confirm).not.toHaveBeenCalled()
  expect(window.document.activeElement).toBe(screen.getByRole('button', { name: copy.choose }))
  h.directory.dispose()
})

it.each([
  'verified',
  'selected-unconfigured',
  'committed-unverified',
  'superseded',
  'unchanged',
  'unavailable',
  'disabled'
] as const)('keeps an admitted selection visible until its explicit %s outcome', async status => {
  const completion = deferred<ActivationResult>()

  const activation = {
    prepare: vi.fn(() => true),
    cancel: vi.fn(() => true),
    confirm: vi.fn(() => completion.promise)
  }

  const h = await setup(activation)
  fireEvent.click(screen.getAllByRole('option')[0])
  fireEvent.click(screen.getByRole('button', { name: copy.choose }))
  fireEvent.click(screen.getByRole('button', { name: directoryActivationCopy.en.confirm }))
  expect(screen.getByRole('status').textContent).toBe(directoryActivationCopy.en.pending)
  expect(screen.queryByRole('button', { name: directoryActivationCopy.en.cancel })).toBeNull()
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
  act(() => h.invalidate())
  expect(h.close).not.toHaveBeenCalled()
  expect(activation.cancel).not.toHaveBeenCalled()
  await act(async () => completion.resolve({ status }))
  expect(screen.getByRole('status').textContent).toBe(directoryActivationCopy.en[status])
  expect(activation.confirm).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: directoryActivationCopy.en.close }))
  expect(h.close).toHaveBeenCalledTimes(1)
  h.directory.dispose()
})

const descriptorFor = (state: 'ready' | 'unconfigured' | 'unsupported', replacement = false) => ({
  capabilityVersion: 1,
  state,
  scope: { kind: 'served-profile', profile: 'default' },
  metadata:
    state === 'ready'
      ? {
          organizationId: replacement ? '33333333-3333-4333-8333-333333333333' : '11111111-1111-4111-8111-111111111111',
          displayName: 'Research',
          owner: { ownerId: '22222222-2222-4222-8222-222222222222', displayName: 'Owner' },
          revision: 1,
          createdAt: '2026-10-12T00:00:00Z',
          updatedAt: '2026-10-12T00:00:00Z'
        }
      : null
})

for (const locale of ['en', 'ja', 'zh', 'zh-hant', 'ar', 'ru'] as const) {
  it.each([
    ['unconfigured', 'unconfigured', false, 'selected-unconfigured'],
    ['ready', 'ready', false, 'verified'],
    ['ready', 'ready', true, 'committed-unverified'],
    ['ready', 'unconfigured', false, 'committed-unverified'],
    ['unconfigured', 'ready', false, 'committed-unverified'],
    ['ready', 'unsupported', false, 'committed-unverified'],
    ['unsupported', 'unsupported', false, null]
  ] as const)(
    `renders the real coordinator outcome for %s → %s in ${locale}`,
    async (before, after, replacement, expected) => {
      const request = vi.fn().mockResolvedValue(descriptorFor(before))

      const source: DirectorySource = {
        connections: [
          { id: 'a', label: 'Laptop', endpoint: null },
          { id: 'b', label: 'Lab', endpoint: null }
        ],
        current: null
      }

      let changed = () => {}

      const go = (id: string) => {
        source.current = {
          connectionId: id,
          endpoint: null,
          scope: { key: id, ownerKey: id, profile: 'default', eligible: true, request }
        }
        changed()
      }

      const directory = createOrganizationDirectory(
        () => source,
        listener => {
          changed = listener

          return () => {}
        }
      )

      go('b')
      await directory.refresh()
      go('a')

      const select = vi.fn(async (connectionId: string, profile: string) => {
        go(connectionId)

        return {
          status: 'committed' as const,
          reason: 'selected' as const,
          requested: { connectionId, profile },
          observedRoute: { connectionId, profile },
          resetStarted: true,
          published: true
        }
      })

      const activation = createOrganizationDirectoryActivation({
        directory,
        readSource: () => source,
        enabled: () => true,
        select
      })

      const localCopy = organizationDirectoryCopy[locale]
      const activationCopy = directoryActivationCopy[locale]
      const close = vi.fn()

      const ui = render(
        <OrganizationDirectorySearch
          activation={activation}
          activationCopy={activationCopy}
          copy={localCopy}
          directory={directory}
          isCurrent={() => source.current?.connectionId === 'a'}
          onClose={close}
          onReturnFocus={() => {}}
        />
      )

      try {
        fireEvent.click(screen.getByRole('option'))
        const choice = screen.getByRole('button', { name: localCopy.choose }) as HTMLButtonElement

        if (expected === null) {
          expect(choice.disabled).toBe(true)
          expect(select).not.toHaveBeenCalled()

          return
        }

        fireEvent.click(choice)
        const confirmation = screen.getByRole('region', { name: activationCopy.confirm })

        if (before === 'unconfigured') {
          expect(confirmation.textContent).toContain(localCopy.unconfigured)
          expect(confirmation.textContent).not.toContain(localCopy.unnamed)
        }

        request.mockResolvedValueOnce(descriptorFor(after, replacement))
        fireEvent.click(screen.getByRole('button', { name: activationCopy.confirm }))
        await waitFor(() => expect(screen.getByRole('status').textContent).toBe(activationCopy[expected]))
        expect(select).toHaveBeenCalledExactlyOnceWith('b', 'default')
        expect(close).not.toHaveBeenCalled()
      } finally {
        ui.unmount()
        directory.dispose()
      }
    }
  )
}
