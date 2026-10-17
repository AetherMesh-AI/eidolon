import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { StrictMode } from 'react'
import { expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { organizationCreationCopy } from '@/i18n/organization-creation'

import { createOrganizationCreationClient, type OrganizationCreationDependencies } from './organization-creation-client'
import type {
  CreatedWorkspace,
  WorkspaceCreationCapability,
  WorkspaceCreationIntent
} from './organization-creation-contract'
import { OrganizationCreationDialog } from './organization-creation-dialog'
import type { DirectorySource } from './organization-directory'

const copy = organizationCreationCopy.en
const key = '55555555-5555-4555-8555-555555555555'

const capability: WorkspaceCreationCapability = {
  capabilityVersion: 1,
  state: 'available',
  isolationVersion: 1,
  credentialMode: 'profile-local-openai-api',
  executionMode: 'tool-free-organization',
  activation: 'explicit-client',
  supportedProviders: ['openai-api'],
  origin: {
    profile: 'default',
    organizationId: '11111111-1111-4111-8111-111111111111',
    ownerId: '22222222-2222-4222-8222-222222222222'
  }
}

function receipt(intent: WorkspaceCreationIntent): CreatedWorkspace {
  return {
    capabilityVersion: 1,
    state: 'created',
    idempotencyKey: intent.idempotencyKey,
    profile: 'org-33333333333343338333333333333333',
    organizationId: '33333333-3333-4333-8333-333333333333',
    ownerId: '44444444-4444-4444-8444-444444444444',
    isolationVersion: 1,
    credentialMode: 'profile-local-openai-api',
    executionMode: 'tool-free-organization',
    activated: false,
    descriptor: {
      organizationId: '33333333-3333-4333-8333-333333333333',
      displayName: intent.displayName,
      owner: { ownerId: '44444444-4444-4444-8444-444444444444', displayName: intent.ownerDisplayName },
      revision: 1,
      createdAt: '2026-10-16T00:00:00Z',
      updatedAt: '2026-10-16T00:00:00Z'
    }
  }
}

function harness() {
  const data = new Map<string, string>()

  const storage = {
    getItem: vi.fn((name: string) => data.get(name) ?? null),
    setItem: vi.fn((name: string, value: string) => {
      data.set(name, value)
    }),
    removeItem: vi.fn((name: string) => {
      data.delete(name)
    })
  }

  const request = vi.fn<(method: string, params: Record<string, unknown>) => Promise<unknown>>()

  const source: DirectorySource = {
    ready: true,
    connections: [{ id: 'local', label: 'Local', endpoint: null }],
    current: {
      connectionId: 'local',
      endpoint: null,
      scope: {
        key: 'original-socket',
        ownerKey: 'original-owner',
        profile: 'default',
        eligible: true,
        request: async <T,>(method: string, params: Record<string, unknown>) => (await request(method, params)) as T
      }
    }
  }

  const activate = vi.fn<OrganizationCreationDependencies['activate']>()
  const saveModel = vi.fn().mockResolvedValue({ ok: true })

  const dependencies: OrganizationCreationDependencies = {
    readSource: () => source,
    rememberCurrent: vi.fn().mockResolvedValue(undefined),
    storage,
    activate,
    saveModel,
    newKey: () => key
  }

  const close = vi.fn()
  const setup = vi.fn()

  const mount = () => {
    const client = createOrganizationCreationClient(dependencies)
    const dispose = vi.spyOn(client, 'dispose')

    const mounted = render(
      <StrictMode>
        <I18nProvider configClient={null} initialLocale="en">
          <OrganizationCreationDialog
            client={client}
            onClose={() => {
              client.dispose()
              close()
            }}
            onSetup={setup}
          />
        </I18nProvider>
      </StrictMode>
    )

    return { ...mounted, client, dispose }
  }

  return { data, storage, request, source, activate, close, setup, saveModel, mount }
}

function enterNames() {
  fireEvent.change(screen.getByLabelText(copy.organizationName), { target: { value: 'Research' } })
  fireEvent.change(screen.getByLabelText(copy.ownerName), { target: { value: 'Alex' } })
}

it('keeps names and the pending request across errors and dismissal, and only offers retry after a status read', async () => {
  const h = harness()

  let finish: (value: unknown) => void = () => {}

  const pending = new Promise(resolve => {
    finish = resolve
  })

  h.request.mockResolvedValueOnce(capability).mockReturnValueOnce(pending)
  const mounted = h.mount()
  await screen.findByRole('button', { name: copy.create })
  enterNames()
  h.storage.setItem.mockImplementationOnce(() => {
    throw new Error('Storage full')
  })
  fireEvent.click(screen.getByRole('button', { name: copy.create }))
  expect((await screen.findByRole('alert')).textContent).toContain(copy.errors.storage)
  expect((screen.getByLabelText(copy.organizationName) as HTMLInputElement).value).toBe('Research')
  expect((screen.getByLabelText(copy.ownerName) as HTMLInputElement).value).toBe('Alex')
  expect(h.request).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: copy.create }))
  expect(screen.getByRole('dialog').getAttribute('aria-busy')).toBe('true')
  expect((screen.getByLabelText(copy.organizationName) as HTMLInputElement).disabled).toBe(true)
  const original = h.request.mock.calls[1][1]
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  expect(h.close).toHaveBeenCalledOnce()
  mounted.unmount()
  expect(mounted.dispose).toHaveBeenCalledOnce()
  expect(h.storage.removeItem).not.toHaveBeenCalled()
  expect(h.data.size).toBe(1)
  await act(async () => {
    finish(receipt(original as unknown as WorkspaceCreationIntent))
  })
  expect(h.activate).not.toHaveBeenCalled()
  expect(h.setup).not.toHaveBeenCalled()

  h.request.mockResolvedValueOnce(capability).mockRejectedValueOnce(new Error('Disconnected'))
  h.mount()
  await screen.findByRole('button', { name: copy.check })
  expect(screen.queryByRole('button', { name: copy.retry })).toBeNull()
  expect((screen.getByLabelText(copy.organizationName) as HTMLInputElement).value).toBe('Research')
  expect((screen.getByLabelText(copy.ownerName) as HTMLInputElement).value).toBe('Alex')
  expect(h.request.mock.calls.filter(([method]) => method === 'organization.createWorkspace')).toHaveLength(1)
  h.request.mockResolvedValueOnce({ capabilityVersion: 1, state: 'pending' })
  fireEvent.click(screen.getByRole('button', { name: copy.check }))
  await screen.findByRole('button', { name: copy.retry })
  expect(h.request.mock.calls.at(-1)?.[0]).toBe('organization.workspaceCreationStatus')
  expect(h.request.mock.calls.filter(([method]) => method === 'organization.createWorkspace')).toHaveLength(1)
  h.request.mockResolvedValueOnce(receipt(original as unknown as WorkspaceCreationIntent))
  fireEvent.click(screen.getByRole('button', { name: copy.retry }))
  await screen.findByRole('button', { name: copy.switch })
  expect(h.request.mock.calls.at(-1)).toEqual(['organization.createWorkspace', original])
  expect(h.activate).not.toHaveBeenCalled()
})

it('requires separate creation, verified switching, and explicit setup clicks without automatic activation or probes', async () => {
  const h = harness()
  let created: CreatedWorkspace
  h.request.mockImplementation(async (method, params) => {
    if (method === 'organization.workspaceCreation') {
      return capability
    }

    if (method === 'organization.createWorkspace') {
      created = receipt(params as unknown as WorkspaceCreationIntent)

      return created
    }

    if (method === 'organization.descriptor') {
      return {
        capabilityVersion: 1,
        state: 'ready',
        metadata: created.descriptor,
        scope: { kind: 'served-profile', profile: created.profile },
        isolation: { ...created, supportedProviders: ['openai-api'] }
      }
    }

    throw new Error(`Unexpected request: ${method}`)
  })
  h.activate.mockImplementation(async (connectionId, profile, verify) => {
    const descriptor = await h.request('organization.descriptor', {})
    verify(descriptor)
    h.source.current = {
      ...h.source.current!,
      scope: { ...h.source.current!.scope, key: 'created-socket', ownerKey: 'created-owner', profile }
    }

    return {
      status: 'committed',
      reason: 'selected',
      requested: { connectionId, profile },
      observedRoute: { connectionId, profile },
      resetStarted: true,
      published: true
    }
  })
  h.mount()
  await screen.findByRole('button', { name: copy.create })
  expect(h.activate).not.toHaveBeenCalled()
  expect(h.request.mock.calls.map(([method]) => method)).toEqual(['organization.workspaceCreation'])
  enterNames()
  fireEvent.click(screen.getByRole('button', { name: copy.create }))
  await screen.findByRole('button', { name: copy.switch })
  const result = screen.getByRole('region', { name: copy.receipt })
  expect(within(result).getByText('Research')).not.toBeNull()
  expect(within(result).getByText('Alex')).not.toBeNull()
  expect(h.activate).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: copy.setupKeys })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: copy.switch }))
  await screen.findByRole('button', { name: copy.setupKeys })
  expect(h.activate).toHaveBeenCalledTimes(1)
  expect(h.setup).not.toHaveBeenCalled()
  expect(h.storage.removeItem).toHaveBeenCalledOnce()
  const requestsAfterSelection = h.request.mock.calls.length
  fireEvent.click(screen.getByRole('button', { name: copy.setupKeys }))
  fireEvent.click(screen.getByRole('button', { name: copy.setupModel }))
  expect(h.setup.mock.calls).toEqual([[created!.profile]])
  expect(h.request).toHaveBeenCalledTimes(requestsAfterSelection)
  expect(h.saveModel).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText(copy.modelId), { target: { value: 'explicit-test-model' } })
  h.saveModel.mockRejectedValueOnce(new Error('Synthetic configuration save failure'))
  fireEvent.click(screen.getByRole('button', { name: copy.saveModel }))
  await screen.findByText(copy.modelStatus.failed)
  expect((screen.getByLabelText(copy.modelId) as HTMLInputElement).value).toBe('explicit-test-model')
  fireEvent.click(screen.getByRole('button', { name: copy.saveModel }))
  await screen.findByText(copy.modelStatus.saved)
  expect(h.saveModel.mock.calls).toEqual([
    [created!.profile, 'explicit-test-model'],
    [created!.profile, 'explicit-test-model']
  ])

  h.source.current!.scope.eligible = false
  fireEvent.click(screen.getByRole('button', { name: copy.setupKeys }))
  await waitFor(() => expect(h.setup).toHaveBeenCalledTimes(1))
})
