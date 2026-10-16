import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { stubResizeObserver } from '@/test/jsdom'

import { preparedValidationCopy } from './runtime-prepared-validation-copy'
import type { PreparedValidationIntent, PreparedValidationRevokeIntent, PreparedValidationSelection } from './runtime-prepared-validation-types'
import { preparedDescriptor, preparedPreview, preparedRevokeResult, preparedSaveResult, preparedSelection } from './runtime-prepared-validation.test-fixtures'
import { OrganizationProjectSetupForm } from './runtime-project-setup'
import type { OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

const state = vi.hoisted(() => ({ request: vi.fn() }))
vi.mock('./use-organization-metadata', () => {
  const metadata = { organizationId: 'org', owner: { ownerId: 'owner' } }
  const scope = () => ({ key: 'socket', ownerKey: 'owner-route', profile: 'default', eligible: true, request: state.request })

  return { readOrganizationMetadataScope: scope, organizationMetadataClient: { getSnapshot: () => ({ descriptor: { metadata } }) },
    useOrganizationMetadata: () => ({ scope: scope(), view: { descriptor: { metadata } } }) }
})
vi.mock('./organization-folder-setup', () => ({ OrganizationFolderSetup: () => null }))
vi.mock('./organization-folder-access', () => ({ OrganizationFolderAccess: () => null }))

describe('prepared owner setup inside the project inspector', () => {
  it.each(['save', 'revoke'])('%s refreshes only the inert owner descriptor, never the service snapshot', async action => {
    stubResizeObserver()
    const copy = preparedValidationCopy.en
    let descriptor = preparedDescriptor()

    if (action === 'revoke') { Object.assign(descriptor.projects[0], { enabled: true, registrationRevision: 2, registrationHash: 'b'.repeat(64) }) }
    state.request.mockReset().mockImplementation(async (method, params) => {
      if (method === 'organization.preparedValidationSetup') { return descriptor }

      if (method === 'organization.previewPreparedValidation') { return preparedPreview(descriptor, params.selection as PreparedValidationSelection) }

      const result = method === 'organization.savePreparedValidation' ? preparedSaveResult(descriptor, params as PreparedValidationIntent)
        : preparedRevokeResult(descriptor, params as PreparedValidationRevokeIntent)

      descriptor = { ...descriptor, sidecarRevision: result.receipt.sidecarRevision, registrationRefs: [] }

      return result
    })
    const snapshot = { connection: { state: 'ready', scope: 'socket' } } as OrganizationSnapshot
    const refresh = vi.fn(async () => { throw new Error('Service snapshot must not be requested') })
    const getProjectSetup = vi.fn(async () => ({ version: 1, revision: 'a'.repeat(64), roots: [], recipes: [], teams: [], projects: [], blocked: false, blockers: [] }))
    const adapter = { mode: 'runtime', getSnapshot: () => snapshot, refresh, getProjectSetup } as unknown as RuntimeOrganizationAdapter
    render(<OrganizationProjectSetupForm adapter={adapter} onClose={() => {}} snapshot={snapshot} />)
    fireEvent.click(screen.getByRole('button', { name: copy.read }))
    await screen.findByLabelText(copy.project)
    fireEvent.change(screen.getByLabelText(copy.project), { target: { value: 'desktop' } })

    if (action === 'save') {
      const selection = preparedSelection()

      for (const [label, value] of [[copy.files, selection.source.files.join('\n')], [copy.exceptions, '{}'],
        [copy.dependencies, selection.dependencies.directory], [copy.manifest, JSON.stringify(selection.dependencies.manifest)], [copy.profile, JSON.stringify(selection.profile)]]) {
        fireEvent.change(screen.getByLabelText(label), { target: { value } })
      }

      fireEvent.click(screen.getByRole('checkbox', { name: copy.enable }))
      fireEvent.click(screen.getByRole('button', { name: copy.preview }))
      await screen.findByRole('region', { name: copy.review })
      fireEvent.click(screen.getByRole('button', { name: copy.save }))
    } else {
      fireEvent.click(screen.getByRole('checkbox', { name: copy.revokeChoice }))
      fireEvent.click(screen.getByRole('button', { name: copy.revoke }))
    }

    const dialog = await screen.findByRole('dialog', { name: action === 'save' ? copy.confirmTitle : copy.revokeTitle })
    fireEvent.click(within(dialog).getByRole('button', { name: action === 'save' ? copy.confirm : copy.revokeConfirm }))
    await screen.findByText(action === 'save' ? copy.saved : copy.revoked)
    await waitFor(() => expect(state.request.mock.calls.filter(call => call[0] === 'organization.preparedValidationSetup')).toHaveLength(2))
    expect(state.request.mock.calls.map(call => call[0])).toEqual(action === 'save'
      ? ['organization.preparedValidationSetup', 'organization.previewPreparedValidation', 'organization.savePreparedValidation', 'organization.preparedValidationSetup']
      : ['organization.preparedValidationSetup', 'organization.revokePreparedValidation', 'organization.preparedValidationSetup'])
    expect(getProjectSetup).toHaveBeenCalledOnce()
    expect(refresh).not.toHaveBeenCalled()
  })
})
