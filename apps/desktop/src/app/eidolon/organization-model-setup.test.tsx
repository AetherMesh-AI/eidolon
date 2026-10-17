import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { expect, it, vi } from 'vitest'

import { organizationCreationCopy } from '@/i18n/organization-creation'

import { deferred } from '../../test/deferred'

import { createOrganizationModelSetupClient } from './organization-creation-client'
import type { DirectorySource } from './organization-directory'
import { OrganizationModelSetup } from './organization-model-setup'

const copy = organizationCreationCopy.en
const profile = `org-${'a'.repeat(32)}`
const organizationId = '11111111-1111-4111-8111-111111111111'
const ownerId = '22222222-2222-4222-8222-222222222222'

const descriptor = {
  capabilityVersion: 1,
  state: 'ready',
  scope: { kind: 'served-profile', profile },
  metadata: { organizationId, owner: { ownerId } },
  isolation: {
    profile,
    organizationId,
    ownerId,
    isolationVersion: 1,
    credentialMode: 'profile-local-openai-api',
    executionMode: 'tool-free-organization',
    supportedProviders: ['openai-api']
  }
}

function setup() {
  const request = vi.fn().mockResolvedValue(descriptor)
  const saveModel = vi.fn().mockResolvedValue({ ok: true })

  const source: DirectorySource = {
    ready: true,
    connections: [{ id: 'local', label: 'Local', endpoint: null }],
    current: {
      connectionId: 'local',
      endpoint: null,
      scope: { key: 'socket-one', ownerKey: 'owner-one', profile, eligible: true, request }
    }
  }

  let changed = () => {}

  const client = createOrganizationModelSetupClient({
    readSource: () => source,
    saveModel,
    subscribeSource: listener => {
      changed = listener

      return () => {}
    }
  })

  const close = vi.fn(() => client.dispose())

  const ui = render(
    <StrictMode>
      <OrganizationModelSetup client={client} onClose={close} />
    </StrictMode>
  )

  return { request, saveModel, source, client, changed: () => changed(), close, ui }
}

it('reopens a current-owner setup form, retains failed-save input and writes only after an explicit verified Save', async () => {
  const h = setup()
  const input = screen.getByLabelText(copy.modelId)
  await waitFor(() => expect((input as HTMLInputElement).disabled).toBe(false))
  expect(h.request).toHaveBeenCalledExactlyOnceWith('organization.descriptor', {})
  expect(h.saveModel).not.toHaveBeenCalled()
  fireEvent.change(input, { target: { value: 'explicit-fixture-model' } })
  h.saveModel.mockRejectedValueOnce(new Error('Synthetic save failure'))
  fireEvent.click(screen.getByRole('button', { name: copy.saveModel }))
  await screen.findByText(copy.modelStatus.failed)
  expect((input as HTMLInputElement).value).toBe('explicit-fixture-model')
  expect(h.saveModel).toHaveBeenCalledExactlyOnceWith(profile, 'explicit-fixture-model')
  fireEvent.click(screen.getByRole('button', { name: copy.saveModel }))
  await screen.findByText(copy.modelStatus.saved)
  expect(h.request.mock.calls.map(call => call[0])).toEqual(Array(3).fill('organization.descriptor'))
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  h.ui.unmount()
  const reopened = setup()
  await waitFor(() => expect((screen.getByLabelText(copy.modelId) as HTMLInputElement).disabled).toBe(false))
  expect((screen.getByLabelText(copy.modelId) as HTMLInputElement).value).toBe('')
  expect(reopened.request).toHaveBeenCalledExactlyOnceWith('organization.descriptor', {})
  expect(reopened.saveModel).not.toHaveBeenCalled()
})

it('visibly revokes a delayed descriptor on source loss and refuses legacy or changed owner proof before any write', async () => {
  const h = setup()
  await waitFor(() => expect((screen.getByLabelText(copy.modelId) as HTMLInputElement).disabled).toBe(false))
  fireEvent.change(screen.getByLabelText(copy.modelId), { target: { value: 'preserved-draft' } })
  const delayed = deferred<unknown>()
  h.request.mockReturnValueOnce(delayed.promise)
  fireEvent.click(screen.getByRole('button', { name: copy.saveModel }))
  await act(async () => {
    h.source.current = { ...h.source.current!, scope: { ...h.source.current!.scope, key: 'socket-two' } }
    h.changed()
    delayed.resolve(descriptor)
  })
  expect((screen.getByLabelText(copy.modelId) as HTMLInputElement).disabled).toBe(true)
  expect((screen.getByLabelText(copy.modelId) as HTMLInputElement).value).toBe('preserved-draft')
  expect(screen.getByRole('alert').textContent).toContain(copy.modelStatus.superseded)
  expect(h.saveModel).not.toHaveBeenCalled()
  h.client.dispose()
  h.ui.unmount()

  const legacy = setup()
  legacy.request.mockResolvedValue({ ...descriptor, isolation: undefined })
  // Initial load has already dispatched; its fresh Save proof must still refuse a missing marker.
  await waitFor(() => expect((screen.getByLabelText(copy.modelId) as HTMLInputElement).disabled).toBe(false))
  fireEvent.change(screen.getByLabelText(copy.modelId), { target: { value: 'legacy-must-not-write' } })
  fireEvent.click(screen.getByRole('button', { name: copy.saveModel }))
  await screen.findByText(copy.modelStatus.failed)
  expect(legacy.saveModel).not.toHaveBeenCalled()
})
