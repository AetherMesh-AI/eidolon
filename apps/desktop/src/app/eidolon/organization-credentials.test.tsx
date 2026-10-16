import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useSyncExternalStore } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { organizationCredentialsCopy } from '@/i18n/organization-credentials'

import { OrganizationCredentialsSettings } from './organization-credentials'
import { useOrganizationCredentials } from './organization-credentials-hook'
import { credentialsHarness, deferred, entryFixture, identity } from './organization-credentials-test-support'

vi.mock('./organization-credentials-hook', () => ({ useOrganizationCredentials: vi.fn() }))
const copy = organizationCredentialsCopy.en
type Harness = Awaited<ReturnType<typeof credentialsHarness>>
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  HTMLElement.prototype.scrollIntoView = vi.fn()
  HTMLElement.prototype.hasPointerCapture = () => false
  HTMLElement.prototype.setPointerCapture = vi.fn()
  HTMLElement.prototype.releasePointerCapture = vi.fn()
})
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals() })

function mount(h: Harness) {
  vi.mocked(useOrganizationCredentials).mockImplementation(function useFixture() {
    const view = useSyncExternalStore(h.client.subscribe, h.client.getSnapshot)

    return { client: h.client, view, scope: h.scope, identity: JSON.stringify([h.scope.key, h.scope.profile, h.scope.ownerId, h.scope.eligible]),
      metadata: { busy: false, error: null, intent: null, descriptor: { capabilityVersion: 1, state: 'ready', scope: { kind: 'served-profile', profile: h.scope.profile },
        metadata: { organizationId: identity.organizationId, displayName: 'Synthetic organization', owner: { ownerId: identity.ownerId, displayName: 'Test owner' }, revision: 1, createdAt: '2026-10-16T00:00:00Z', updatedAt: '2026-10-16T00:00:00Z' } } },
      refresh: h.client.read }
  })
  const tree = () => <I18nProvider configClient={null} initialLocale="en"><OrganizationCredentialsSettings /></I18nProvider>
  const view = render(tree())

  return { ...view, repaint: () => view.rerender(tree()) }
}

it('browses status-only data and creates metadata only after valid explicit submission', async () => {
  const h = await credentialsHarness()
  mount(h)
  expect(screen.getByText(entryFixture().name)).toBeTruthy()
  expect(screen.getByText('identity-1')).toBeTruthy()
  expect(h.request.mock.calls).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: copy.create }))
  const dialog = within(screen.getByRole('dialog'))
  const submit = dialog.getByRole('button', { name: copy.create }) as HTMLButtonElement
  expect(submit.disabled).toBe(true)
  fireEvent.change(dialog.getByLabelText(copy.service), { target: { value: ' ssh ' } })
  fireEvent.change(dialog.getByLabelText(copy.label), { target: { value: 'dl380' } })
  fireEvent.change(dialog.getByLabelText(copy.type), { target: { value: 'Username' } })
  fireEvent.change(dialog.getByLabelText(copy.description), { target: { value: 'Synthetic host username' } })
  expect(submit.disabled).toBe(true)
  fireEvent.change(dialog.getByLabelText(copy.type), { target: { value: 'username' } })
  expect(submit.disabled).toBe(false)
  fireEvent.click(submit)
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  const writes = h.request.mock.calls.filter(call => call[0] === 'organization.credentials.mutate')
  expect(writes).toHaveLength(1)
  expect(writes[0][1]).toMatchObject({ action: 'create', entryId: null, expectedRevision: null, metadata: {
    service: 'ssh', label: 'dl380', type: 'username', description: 'Synthetic host username', kind: 'secret', expiresAt: null
  }, inputHandle: null, confirmed: true })
  expect(h.request.mock.calls.some(call => call[0] === 'organization.credentials.secureInput')).toBe(false)
})

it('clears secret DOM input on Escape, disconnect, owner/profile change and unmount without submitting', async () => {
  for (const reason of ['escape', 'disconnect', 'owner', 'profile', 'unmount']) {
    const h = await credentialsHarness()
    const mounted = mount(h)
    fireEvent.click(screen.getByRole('button', { name: copy.replaceValue }))
    const field = screen.getByLabelText(copy.value) as HTMLInputElement
    fireEvent.change(field, { target: { value: 'SYNTHETIC-PRIVATE-VALUE' } })
    expect(field.type).toBe('password')

    if (reason === 'escape') {
      fireEvent.keyDown(field, { key: 'Escape' })
    } else if (reason === 'unmount') {
      mounted.unmount()
    } else {
      if (reason === 'disconnect') { h.scope.eligible = false }

      if (reason === 'owner') { h.scope.ownerId = 'other-owner' }

      if (reason === 'profile') { h.scope.profile = 'other-profile' }
      mounted.repaint()
    }

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(field.value).toBe('')
    expect(h.request.mock.calls).toHaveLength(1)
    expect(h.values.size).toBe(0)
    cleanup()
  }
})

it('clears a submitted value immediately and keeps its bytes out of the UI, storage and errors', async () => {
  const h = await credentialsHarness()
  mount(h)
  fireEvent.click(screen.getByRole('button', { name: copy.replaceValue }))
  const field = screen.getByLabelText(copy.value) as HTMLInputElement
  fireEvent.change(field, { target: { value: 'SYNTHETIC-PRIVATE-VALUE' } })
  const pending = deferred<unknown>()
  h.request.mockImplementationOnce(() => pending.promise)
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: copy.replaceValue }))
  expect(field.value).toBe('')
  expect(screen.queryByRole('dialog')).toBeNull()
  expect([...h.values.values()].join()).not.toContain('SYNTHETIC-PRIVATE-VALUE')
  await act(async () => { pending.reject(new Error('SYNTHETIC-PRIVATE-VALUE')) })
  expect(screen.getByRole('alert').textContent).toContain(copy.secureInput)
  expect(screen.getByRole('region', { name: copy.title }).textContent).not.toContain('SYNTHETIC-PRIVATE-VALUE')
})

it('requires explicit review of independent grant permissions and resets review after any edit', async () => {
  const h = await credentialsHarness()
  mount(h)
  fireEvent.click(screen.getByRole('button', { name: copy.manageGrants }))
  const dialog = within(screen.getByRole('dialog'))
  const save = dialog.getByRole('button', { name: copy.grantSave }) as HTMLButtonElement
  expect(save.disabled).toBe(true)
  fireEvent.click(dialog.getByRole('combobox', { name: copy.actor }))
  fireEvent.click(await screen.findByRole('option', { name: /Synthetic manager/ }))
  fireEvent.click(dialog.getByRole('combobox', { name: copy.scope }))
  fireEvent.click(await screen.findByRole('option', { name: /github_eidolonReleases_token/ }))
  expect(dialog.queryByRole('checkbox', { name: 'use' })).toBeNull()
  expect(dialog.getByRole('checkbox', { name: 'discover' }).getAttribute('aria-checked')).toBe('true')
  fireEvent.click(dialog.getByRole('checkbox', { name: 'edit_metadata' }))
  fireEvent.click(dialog.getByRole('checkbox', { name: copy.grantReviewed }))
  expect(save.disabled).toBe(false)
  fireEvent.click(dialog.getByRole('checkbox', { name: 'revoke' }))
  expect(save.disabled).toBe(true)
  fireEvent.click(dialog.getByRole('checkbox', { name: copy.grantReviewed }))
  fireEvent.click(save)
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  const writes = h.request.mock.calls.filter(call => call[0] === 'organization.credentials.configureGrant')
  expect(writes).toHaveLength(1)
  expect(writes[0][1]).toEqual({ expectedOrganizationId: identity.organizationId, expectedOwnerId: identity.ownerId,
    actorId: 'identity-1', scope: entryFixture().id, permissions: ['discover', 'edit_metadata', 'revoke'], bindings: [],
    expectedRevision: 2, operationId: h.newKey.mock.results[0].value, confirmed: true })
})

it('reviews only an exact creation scope and never creates or fills the credential as a side effect', async () => {
  const h = await credentialsHarness()
  mount(h)
  fireEvent.click(screen.getByRole('button', { name: copy.manageCreation }))
  const dialog = within(screen.getByRole('dialog'))
  fireEvent.click(dialog.getByRole('combobox', { name: copy.actor }))
  fireEvent.click(await screen.findByRole('option', { name: /Synthetic manager/ }))

  for (const [label, value] of [[copy.service, 'discord'], [copy.label, 'statusAlerts'], [copy.type, 'webhookUrl']]) {
    fireEvent.change(dialog.getByLabelText(label), { target: { value } })
  }

  fireEvent.click(dialog.getByRole('checkbox', { name: copy.allowCreate }))
  fireEvent.click(dialog.getByRole('checkbox', { name: copy.grantReviewed }))
  const save = dialog.getByRole('button', { name: copy.grantSave }) as HTMLButtonElement
  expect(save.disabled).toBe(false)
  fireEvent.change(dialog.getByLabelText(copy.label), { target: { value: '*' } })
  expect(save.disabled).toBe(true)
  fireEvent.change(dialog.getByLabelText(copy.label), { target: { value: 'statusAlerts' } })
  expect(save.disabled).toBe(true)
  fireEvent.click(dialog.getByRole('checkbox', { name: copy.grantReviewed }))
  fireEvent.click(save)
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  const writes = h.request.mock.calls.filter(call => call[0] === 'organization.credentials.configureGrant')
  expect(writes).toHaveLength(1)
  expect(writes[0][1]).toMatchObject({ scope: 'create:discord:statusAlerts:webhookUrl:secret', permissions: ['create'], bindings: [], expectedRevision: 0 })
  expect(h.request.mock.calls.some(call => call[0] === 'organization.credentials.mutate' || call[0] === 'organization.credentials.secureInput')).toBe(false)
})

it('keeps exact expiry on metadata edit and asks before local revocation and recoverable removal', async () => {
  const h = await credentialsHarness()
  h.data.entries[0].expiresAt = 1893456017
  await h.client.read()
  mount(h)
  fireEvent.click(screen.getByRole('button', { name: copy.edit }))
  const dialog = within(screen.getByRole('dialog'))
  expect((dialog.getByLabelText(copy.expiresAt) as HTMLInputElement).disabled).toBe(true)
  fireEvent.change(dialog.getByLabelText(copy.description), { target: { value: 'Revised synthetic context' } })
  fireEvent.click(dialog.getByRole('button', { name: copy.save }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(h.request.mock.calls.find(call => call[0] === 'organization.credentials.mutate')?.[1].metadata).toMatchObject({ expiresAt: 1893456017, kind: 'secret' })
  expect((screen.getByRole('button', { name: copy.remove }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: copy.revoke }))
  expect(within(screen.getByRole('dialog')).getByText(copy.revokeNote)).toBeTruthy()
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: copy.cancel }))
  expect(h.request.mock.calls.filter(call => call[0] === 'organization.credentials.mutate')).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: copy.revoke }))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: copy.revoke }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  fireEvent.click(screen.getByRole('button', { name: copy.remove }))
  expect(within(screen.getByRole('dialog')).getByText(copy.removeNote)).toBeTruthy()
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: copy.remove }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(h.client.getSnapshot().catalog?.entries).toHaveLength(0)
  expect(h.client.getSnapshot().receipt).toMatchObject({ action: 'remove', providerRevoked: false, storedValueDeleted: false })
})

it('reviews exact pending-operation cancellation and requires server fencing before enabling new changes', async () => {
  const h = await credentialsHarness()
  mount(h)
  h.request.mockRejectedValueOnce(new Error('Synthetic unknown metadata outcome'))
  await act(async () => { await h.client.mutate('revoke', entryFixture().id, 3) })
  const operationId = h.client.getSnapshot().intent!.operationId
  expect((screen.getByRole('button', { name: copy.create }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: copy.cancelPending }))
  let dialog = within(screen.getByRole('dialog'))
  expect(dialog.getByText(copy.cancelPendingNote)).toBeTruthy()
  expect(dialog.getByText(operationId)).toBeTruthy()
  fireEvent.click(dialog.getByRole('button', { name: copy.cancel }))
  expect(h.request.mock.calls.some(call => call[0] === 'organization.credentials.cancelOperation')).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: copy.cancelPending }))
  dialog = within(screen.getByRole('dialog'))
  fireEvent.click(dialog.getByRole('button', { name: copy.cancelPending }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(screen.getByText(`${copy.cancelled}:`, { exact: false })).toBeTruthy()
  expect(h.client.getSnapshot().intent).toBeNull()
  expect((screen.getByRole('button', { name: copy.create }) as HTMLButtonElement).disabled).toBe(false)
  expect(h.request.mock.calls.filter(call => call[0] === 'organization.credentials.cancelOperation')).toHaveLength(1)
  expect(h.request.mock.calls.filter(call => call[0] === 'organization.credentials.mutate')).toHaveLength(1)
})

it.each(['metadata', 'grant', 'createGrant'])('exposes recovery after an uncertain %s write without replaying or dropping its intent', async kind => {
  const h = await credentialsHarness()
  mount(h)
  fireEvent.click(screen.getByRole('button', { name: kind === 'metadata' ? copy.edit : kind === 'grant' ? copy.manageGrants : copy.manageCreation }))
  const dialog = within(screen.getByRole('dialog'))

  if (kind === 'metadata') {
    fireEvent.change(dialog.getByLabelText(copy.description), { target: { value: 'Synthetic uncertain metadata edit' } })
  } else {
    fireEvent.click(dialog.getByRole('combobox', { name: copy.actor }))
    fireEvent.click(await screen.findByRole('option', { name: /Synthetic manager/ }))

    if (kind === 'grant') {
      fireEvent.click(dialog.getByRole('combobox', { name: copy.scope }))
      fireEvent.click(await screen.findByRole('option', { name: /github_eidolonReleases_token/ }))
      fireEvent.click(dialog.getByRole('checkbox', { name: 'edit_metadata' }))
    } else {
      for (const [label, value] of [[copy.service, 'discord'], [copy.label, 'statusAlerts'], [copy.type, 'webhookUrl']]) {
        fireEvent.change(dialog.getByLabelText(label), { target: { value } })
      }

      fireEvent.click(dialog.getByRole('checkbox', { name: copy.allowCreate }))
    }

    fireEvent.click(dialog.getByRole('checkbox', { name: copy.grantReviewed }))
  }

  const pending = deferred<unknown>()
  h.request.mockImplementationOnce(() => pending.promise)
  fireEvent.click(dialog.getByRole('button', { name: kind === 'metadata' ? copy.save : copy.grantSave }))
  expect(screen.getByRole('dialog')).toBeTruthy()
  expect((dialog.getByRole('button', { name: copy.cancel }) as HTMLButtonElement).disabled).toBe(true)
  const intent = h.client.getSnapshot().intent
  const retained = [...h.values.entries()]
  await act(async () => { pending.reject(new Error('Synthetic lost write acknowledgement')) })
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(h.client.getSnapshot().intent).toEqual(intent)
  expect([...h.values.entries()]).toEqual(retained)
  expect((screen.getByRole('button', { name: copy.reconcile }) as HTMLButtonElement).disabled).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: copy.cancelPending }))
  expect(within(screen.getByRole('dialog')).getByText(intent!.operationId)).toBeTruthy()
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: copy.cancelPending }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(h.client.getSnapshot().intent).toBeNull()
  expect(h.values.size).toBe(0)
  expect((screen.getByRole('button', { name: copy.create }) as HTMLButtonElement).disabled).toBe(false)
  expect(h.request.mock.calls.filter(call => ['organization.credentials.mutate', 'organization.credentials.configureGrant'].includes(call[0]))).toHaveLength(1)
})

it('keeps a populated description field label independent of its existing value', async () => {
  const h = await credentialsHarness()
  mount(h)
  fireEvent.click(screen.getByRole('button', { name: copy.edit }))
  const dialog = within(screen.getByRole('dialog'))
  const description = dialog.getByLabelText(copy.description) as HTMLTextAreaElement
  expect(description.value).toBe(entryFixture().description)
  expect([...description.labels!].map(label => label.textContent?.trim())).toEqual([copy.description])
  fireEvent.change(description, { target: { value: 'Updated synthetic description' } })
  expect([...description.labels!].map(label => label.textContent?.trim())).toEqual([copy.description])
  expect(h.request.mock.calls).toHaveLength(1)
})
