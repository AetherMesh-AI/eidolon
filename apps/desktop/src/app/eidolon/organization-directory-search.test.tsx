import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { createOrganizationDirectory, type DirectorySource } from './organization-directory'
import { type OrganizationDirectoryCopy, OrganizationDirectorySearch } from './organization-directory-search'

const copy: OrganizationDirectoryCopy = {
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

async function setup() {
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

it('closes on owner loss and never returns focus into the former owner', async () => {
  const h = await setup()
  act(h.invalidate)
  expect(h.close).toHaveBeenCalled()
  h.ui.unmount()
  await waitFor(() => expect(h.returnFocus).not.toHaveBeenCalled())
  expect(h.request).not.toHaveBeenCalled()
  h.ui.unmount()
  h.directory.dispose()
})
