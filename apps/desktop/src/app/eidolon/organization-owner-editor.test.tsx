import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'

import { createOrganizationMetadataClient, type MetadataDescriptor } from './organization-metadata-client'
import { OrganizationOwnerEditor } from './organization-owner-editor'

const stored = (name = 'Research', revision = 1): MetadataDescriptor => ({
  capabilityVersion: 1,
  state: 'ready',
  metadata: {
    organizationId: '11111111-1111-4111-8111-111111111111',
    displayName: name,
    owner: { ownerId: '22222222-2222-4222-8222-222222222222', displayName: 'Alex' },
    revision,
    createdAt: '2026-10-11T00:00:00Z',
    updatedAt: '2026-10-11T00:00:00Z'
  },
  scope: { kind: 'served-profile', profile: 'default' }
})

async function setup(draftKey: string) {
  const request = vi.fn().mockResolvedValue(stored())

  const client = createOrganizationMetadataClient(
    () => ({ key: 'socket', ownerKey: draftKey, profile: 'default', eligible: true, request }),
    () => 'one-intent'
  )

  await client.read()
  const close = vi.fn()

  const ui = () => (
    <I18nProvider configClient={null} initialLocale="en">
      <OrganizationOwnerEditor client={client} draftKey={draftKey} onClose={close} onReturnFocus={vi.fn()} />
    </I18nProvider>
  )

  return { request, client, close, ui }
}

it('shows saved organization and separate owner, then saves only on explicit submit', async () => {
  const h = await setup('save')
  render(h.ui())
  expect((screen.getByLabelText('Organization name') as HTMLInputElement).value).toBe('Research')
  expect((screen.getByLabelText('Your display name') as HTMLInputElement).value).toBe('Alex')
  expect(h.request).toHaveBeenCalledTimes(1)
  fireEvent.change(screen.getByLabelText('Organization name'), { target: { value: 'Updated' } })
  h.request.mockResolvedValueOnce(stored('Updated', 2))
  fireEvent.click(screen.getByRole('button', { name: 'Save names' }))
  await waitFor(() => expect(h.close).toHaveBeenCalledTimes(1))
  expect(h.request.mock.calls.map(call => call[0])).toEqual(['organization.descriptor', 'organization.saveMetadata'])
  expect(h.request.mock.calls[1][1]).toMatchObject({
    displayName: 'Updated',
    ownerDisplayName: 'Alex',
    expectedRevision: 1
  })
})
it('cancel does not send or create anything', async () => {
  const h = await setup('cancel')
  render(h.ui())
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(h.close).toHaveBeenCalledTimes(1)
  expect(h.request).toHaveBeenCalledTimes(1)
})
it('keeps an uncertain original Save across dismissal without automatic retry', async () => {
  const h = await setup('uncertain')
  const first = render(h.ui())
  fireEvent.change(screen.getByLabelText('Organization name'), { target: { value: 'Uncertain name' } })
  h.request.mockRejectedValueOnce(new Error('timeout'))
  fireEvent.click(screen.getByRole('button', { name: 'Save names' }))
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('result is unknown'))
  first.unmount()
  render(h.ui())
  expect(h.request).toHaveBeenCalledTimes(2)
  expect((screen.getByLabelText('Organization name') as HTMLInputElement).disabled).toBe(true)
  expect((screen.getByLabelText('Organization name') as HTMLInputElement).value).toBe('Uncertain name')
  h.request.mockResolvedValueOnce(stored('Uncertain name', 2))
  fireEvent.click(screen.getByRole('button', { name: 'Check original Save' }))
  await waitFor(() => expect(h.close).toHaveBeenCalledTimes(1))
  expect(h.request.mock.calls[2][1]).toEqual(h.request.mock.calls[1][1])
})

it('restores distinct A and B drafts and never carries a configured draft into missing metadata', async () => {
  const h = await setup('replacement-route')
  const a = stored('Organization A')
  const b = stored('Organization B')
  b.metadata!.organizationId = '33333333-3333-4333-8333-333333333333'
  b.metadata!.owner.ownerId = '44444444-4444-4444-8444-444444444444'

  const ui = (key: string) => (
    <I18nProvider configClient={null} initialLocale="en">
      <OrganizationOwnerEditor client={h.client} draftKey={key} onClose={h.close} onReturnFocus={vi.fn()} />
    </I18nProvider>
  )

  h.request.mockResolvedValueOnce(a)
  await h.client.read()
  const mounted = render(ui('replacement-A'))
  fireEvent.change(screen.getByLabelText('Organization name'), { target: { value: 'A draft' } })
  h.request.mockResolvedValueOnce(b)
  await act(async () => {
    await h.client.read()
  })
  mounted.rerender(ui('replacement-B'))
  expect((screen.getByLabelText('Organization name') as HTMLInputElement).value).toBe('Organization B')
  fireEvent.change(screen.getByLabelText('Organization name'), { target: { value: 'B draft' } })
  h.request.mockResolvedValueOnce(a)
  await act(async () => {
    await h.client.read()
  })
  mounted.rerender(ui('replacement-A'))
  expect((screen.getByLabelText('Organization name') as HTMLInputElement).value).toBe('A draft')
  h.request.mockResolvedValueOnce(stored('A draft', 2))
  fireEvent.click(screen.getByRole('button', { name: 'Save names' }))
  await waitFor(() => expect(h.close).toHaveBeenCalledTimes(1))
  expect(h.request.mock.calls.at(-1)?.[1]).toMatchObject({
    displayName: 'A draft',
    expectedOrganizationId: a.metadata!.organizationId,
    expectedRevision: 1
  })
  h.request.mockResolvedValueOnce({ ...a, state: 'unconfigured', metadata: null })
  await act(async () => {
    await h.client.read()
  })
  mounted.rerender(ui('replacement-missing'))
  expect((screen.getByLabelText('Organization name') as HTMLInputElement).value).toBe('')
  expect(h.request.mock.calls.filter(call => call[0] === 'organization.saveMetadata')).toHaveLength(1)
})
