import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { OrganizationFolderSetup } from './organization-folder-setup'

const state = vi.hoisted(() => ({ key: 'socket', picker: vi.fn(), request: vi.fn(), saved: vi.fn() }))
vi.mock('@/store/projects', () => ({ pickProjectFolder: state.picker }))
vi.mock('./use-organization-metadata', () => {
  const metadata = { organizationId: 'org', owner: { ownerId: 'owner' } }
  const source = () => ({ key: state.key, ownerKey: 'owner-route', eligible: true, profile: 'default', request: state.request })

  return { readOrganizationMetadataScope: source, organizationMetadataClient: { getSnapshot: () => ({ descriptor: { metadata } }) },
    useOrganizationMetadata: () => ({ scope: source(), view: { descriptor: { metadata } } }) }
})

it('chooses an empty folder and requires preview plus explicit access approval', async () => {
  state.key = 'socket'; state.picker.mockResolvedValue('/empty/notes')
  const descriptor = { capabilityVersion: 1, organizationId: 'org', ownerId: 'owner', policyGeneration: 1, sourceRevision: 'a'.repeat(64), rootAliases: [], teams: ['general'], blockers: [], inspectionEnabled: false, scope: { kind: 'served-profile', profile: 'default' } }
  state.request.mockImplementation(async (method: string, params: Record<string, unknown>) => {
    if (method === 'organization.folderSetup') { return descriptor }

    if (method === 'organization.previewProjectFolder') {
      return { ...descriptor, previewVersion: 'b'.repeat(64), candidate: { folder: params.folder, project: { id: 'notes', root: 'root0', recipe: null, team: 'general' }, directoryIdentity: { device: 1, inode: 2 } } }
    }

    return { capabilityVersion: 1, scope: descriptor.scope, receipt: { ...params, organizationId: 'org', ownerId: 'owner', policyGeneration: 2 } }
  })
  render(<OrganizationFolderSetup onSaved={state.saved} />)
  fireEvent.click(screen.getByRole('button', { name: 'Choose a new project folder' }))
  await screen.findByText('/empty/notes')
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'notes' } })
  fireEvent.submit(screen.getByRole('textbox').closest('form')!)
  const approve = await screen.findByRole('button', { name: 'Approve this folder access and save project' })
  expect(state.request.mock.calls.some(call => call[0] === 'organization.registerProjectFolder')).toBe(false)
  fireEvent.click(approve)
  await screen.findByRole('status')
  expect(state.saved).toHaveBeenCalledOnce()
})

it('discards a picker result after the owner socket changes', async () => {
  state.request.mockClear(); state.key = 'socket'
  state.picker.mockImplementation(async () => { state.key = 'different';

 return '/wrong/profile' })
  render(<OrganizationFolderSetup onSaved={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: 'Choose a new project folder' }))
  await screen.findAllByRole('alert')
  expect(screen.queryByText('/wrong/profile')).toBeNull()
  expect(state.request.mock.calls.some(call => call[0] === 'organization.previewProjectFolder')).toBe(false)
})
