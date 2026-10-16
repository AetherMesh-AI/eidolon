import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { stubResizeObserver } from '@/test/jsdom'

import { Inspector } from './inspector'
import { preparedValidationCopy } from './runtime-prepared-validation-copy'
import { RuntimePreparedValidationSetup } from './runtime-prepared-validation-setup'
import type { PreparedValidationDescriptor, PreparedValidationIntent, PreparedValidationRevokeIntent, PreparedValidationSelection } from './runtime-prepared-validation-types'
import { deferred, preparedDescriptor, preparedPreview, preparedRevokeResult, preparedSaveResult, preparedSelection } from './runtime-prepared-validation.test-fixtures'

const copy = preparedValidationCopy.en
const state = vi.hoisted(() => ({ key: 'socket', ownerKey: 'owner-route', profile: 'default', ownerId: 'owner', organizationId: 'org', eligible: true, request: vi.fn(), saved: vi.fn() }))
vi.mock('@/i18n/context', () => ({ useI18n: () => ({ locale: 'en', t: { common: { confirm: 'Confirm', cancel: 'Cancel', loading: 'Saving', done: 'Done', close: 'Close', copied: 'Copied', failed: 'Failed' }, errors: { genericFailure: 'Failed' } } }) }))
vi.mock('./use-organization-metadata', () => {
  const metadata = () => ({ organizationId: state.organizationId, owner: { ownerId: state.ownerId } })
  const scope = () => ({ key: state.key, ownerKey: state.ownerKey, profile: state.profile, eligible: state.eligible, request: state.request })

  return { readOrganizationMetadataScope: scope, organizationMetadataClient: { getSnapshot: () => ({ descriptor: { metadata: metadata() } }) },
    useOrganizationMetadata: () => ({ scope: scope(), view: { descriptor: { metadata: metadata() } } }) }
})
let descriptor: PreparedValidationDescriptor
beforeEach(() => {
  stubResizeObserver()
  state.key = 'socket'; state.ownerKey = 'owner-route'; state.profile = 'default'; state.ownerId = 'owner'; state.organizationId = 'org'; state.eligible = true
  state.saved.mockReset(); state.request.mockReset(); descriptor = preparedDescriptor()
  state.request.mockImplementation(async (method: string, params: Record<string, unknown>) => {
    if (method === 'organization.preparedValidationSetup') { return descriptor }

    if (method === 'organization.previewPreparedValidation') { return preparedPreview(descriptor, params.selection as PreparedValidationSelection) }

    if (method === 'organization.revokePreparedValidation') {
      const result = preparedRevokeResult(descriptor, params as unknown as PreparedValidationRevokeIntent)
      descriptor = { ...descriptor, sidecarRevision: result.receipt.sidecarRevision,
        projects: descriptor.projects.map(project => project.id === result.receipt.projectId ? { ...project, enabled: false,
          registrationRevision: result.receipt.registrationRevision } : project),
        registrationRefs: descriptor.registrationRefs.filter(reference => reference.projectId !== result.receipt.projectId) }

      return result
    }

    const result = preparedSaveResult(descriptor, params as unknown as PreparedValidationIntent)
    const receipt = result.receipt
    descriptor = { ...descriptor, sidecarRevision: receipt.sidecarRevision,
      projects: descriptor.projects.map(project => project.id === receipt.projectId ? { ...project, enabled: true,
        registrationRevision: receipt.registrationRevision, registrationHash: receipt.candidateHash } : project),
      registrationRefs: [...descriptor.registrationRefs.filter(reference => reference.projectId !== receipt.projectId),
        { projectId: receipt.projectId, revision: receipt.registrationRevision, registrationSha256: receipt.candidateHash }] }

    return result
  })
})
afterEach(() => { vi.unstubAllGlobals() })
const calls = (method: string) => state.request.mock.calls.filter(call => call[0] === `organization.${method}`)
const button = (name: string) => screen.getByRole('button', { name })
const input = (name: string) => screen.getByLabelText(name)

async function read() {
  fireEvent.click(button(copy.read))
  await screen.findByLabelText(copy.project)
  fireEvent.change(input(copy.project), { target: { value: 'desktop' } })
}

async function fill() {
  await read()
  const selection = preparedSelection()

  for (const [label, value] of [[copy.files, selection.source.files.join('\n')], [copy.exceptions, JSON.stringify(selection.source.executionOnlyHashes)],
    [copy.dependencies, selection.dependencies.directory], [copy.manifest, JSON.stringify(selection.dependencies.manifest)], [copy.profile, JSON.stringify(selection.profile)]]) {
    fireEvent.change(input(label), { target: { value } })
  }
}

async function review() {
  await fill()
  fireEvent.click(screen.getByRole('checkbox', { name: copy.enable }))
  fireEvent.click(button(copy.preview))
  await screen.findByRole('region', { name: copy.review })
}

async function confirmSave() {
  fireEvent.click(button(copy.save))
  const dialog = await screen.findByRole('dialog', { name: copy.confirmTitle })
  fireEvent.click(within(dialog).getByRole('button', { name: copy.confirm }))
}

describe('reviewed prepared-validation setup UI', () => {
  it('requires existing project, explicit enable, byte preview and final confirmation without starting work', async () => {
    render(<RuntimePreparedValidationSetup onSaved={state.saved} />)
    expect(state.request).not.toHaveBeenCalled()
    expect(screen.getByText(copy.platform)).toBeTruthy()
    await fill()
    expect((button(copy.preview) as HTMLButtonElement).disabled).toBe(true)
    await act(async () => fireEvent.submit(input(copy.files).closest('form')!))
    expect(calls('previewPreparedValidation')).toHaveLength(0)
    fireEvent.click(screen.getByRole('checkbox', { name: copy.enable }))
    fireEvent.click(button(copy.preview))
    await screen.findByRole('region', { name: copy.review })
    expect(screen.getByText(copy.verified)).toBeTruthy()
    expect(screen.getByText(copy.consequences)).toBeTruthy()
    expect(screen.getByText(copy.boundaries)).toBeTruthy()
    fireEvent.click(button(copy.save))
    const dialog = await screen.findByRole('dialog', { name: copy.confirmTitle })
    expect(calls('savePreparedValidation')).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: copy.confirm }))
    await screen.findByText(copy.saved)
    expect(state.saved).toHaveBeenCalledOnce()
    expect(state.request.mock.calls.map(call => call[0])).toEqual(['organization.preparedValidationSetup', 'organization.previewPreparedValidation', 'organization.savePreparedValidation', 'organization.preparedValidationSetup'])
    expect(calls('savePreparedValidation')[0][1]).toMatchObject({ expectedOwnerId: 'owner', confirmed: true, candidate: { enabled: true } })
  })

  it('invalidates a completed review when any input changes', async () => {
    render(<RuntimePreparedValidationSetup onSaved={state.saved} />)
    await review()
    fireEvent.change(input(copy.dependencies), { target: { value: '/prepared/other' } })
    expect(screen.queryByRole('region', { name: copy.review })).toBeNull()
    expect(screen.queryByRole('button', { name: copy.save })).toBeNull()
    expect(calls('savePreparedValidation')).toHaveLength(0)
    fireEvent.click(button(copy.preview))
    await screen.findByRole('region', { name: copy.review })
    await confirmSave()
    await screen.findByText(copy.saved)
    expect(calls('savePreparedValidation')[0][1].candidate.contract.dependencies.directory).toBe('/prepared/other')
  })

  it('discards an in-flight preview after editing and coalesces repeated clicks', async () => {
    render(<RuntimePreparedValidationSetup onSaved={state.saved} />)
    await fill(); fireEvent.click(screen.getByRole('checkbox', { name: copy.enable }))
    const waiting = deferred<unknown>(); state.request.mockReturnValueOnce(waiting.promise)
    fireEvent.click(button(copy.preview)); fireEvent.click(button(copy.preview))
    const selected = structuredClone(calls('previewPreparedValidation')[0][1].selection)
    fireEvent.change(input(copy.exceptions), { target: { value: '{}' } })
    await act(async () => waiting.resolve(preparedPreview(descriptor, selected)))
    await screen.findByText(copy.edited)
    expect(calls('previewPreparedValidation')).toHaveLength(1)
    expect(screen.queryByRole('region', { name: copy.review })).toBeNull()
    expect(calls('savePreparedValidation')).toHaveLength(0)
  })

  it.each(['key', 'profile', 'ownerId'] as const)('drops a late result after %s changes', async key => {
    const mounted = render(<RuntimePreparedValidationSetup onSaved={state.saved} />)
    await fill(); fireEvent.click(screen.getByRole('checkbox', { name: copy.enable }))
    const waiting = deferred<unknown>(); state.request.mockReturnValueOnce(waiting.promise)
    fireEvent.click(button(copy.preview))
    const selected = structuredClone(calls('previewPreparedValidation')[0][1].selection)
    state[key] = 'other'; mounted.rerender(<RuntimePreparedValidationSetup onSaved={state.saved} />)
    await act(async () => waiting.resolve(preparedPreview(descriptor, selected)))
    expect(screen.getByText(copy.changed)).toBeTruthy()
    expect(screen.queryByRole('region', { name: copy.review })).toBeNull()
    expect(state.saved).not.toHaveBeenCalled()
    expect(calls('savePreparedValidation')).toHaveLength(0)
  })

  it('retains the exact save and disables edits after an uncertain outcome, then retries once', async () => {
    render(<RuntimePreparedValidationSetup onSaved={state.saved} />)
    await review()
    state.request.mockRejectedValueOnce(new Error('Lost response'))
    await confirmSave()
    await screen.findByText(copy.uncertain)
    const first = structuredClone(calls('savePreparedValidation')[0][1])
    const dialog = screen.getByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect((input(copy.dependencies) as HTMLInputElement).disabled).toBe(true)
    fireEvent.change(input(copy.dependencies), { target: { value: '/unreviewed' } })
    const waiting = deferred<unknown>(); state.request.mockReturnValueOnce(waiting.promise)
    fireEvent.click(button(copy.retry)); fireEvent.click(button(copy.retry))
    await act(async () => waiting.resolve(preparedSaveResult(descriptor, first)))
    await screen.findByText(copy.saved)
    expect(calls('savePreparedValidation')).toHaveLength(2)
    expect(calls('savePreparedValidation')[1][1]).toEqual(first)
    expect(state.saved).toHaveBeenCalledOnce()
  })

  it('keeps unsupported setup read-only without fallback, and rejects malformed advanced JSON', async () => {
    descriptor.platform.supported = false; descriptor.platform.system = 'darwin'; descriptor.platform.architecture = 'arm64'
    const mounted = render(<RuntimePreparedValidationSetup onSaved={state.saved} />)
    await read()
    expect(screen.getByText(`${copy.unsupported} (darwin, arm64)`)).toBeTruthy()
    expect(screen.queryByRole('button', { name: copy.preview })).toBeNull()
    expect(state.request).toHaveBeenCalledOnce()
    mounted.unmount()
    descriptor = preparedDescriptor(); state.request.mockClear()
    render(<RuntimePreparedValidationSetup onSaved={state.saved} />)
    await fill(); fireEvent.change(input(copy.profile), { target: { value: '[]' } }); fireEvent.click(screen.getByRole('checkbox', { name: copy.enable }))
    fireEvent.click(button(copy.preview)); await screen.findByText(copy.json)
    expect(calls('previewPreparedValidation')).toHaveLength(0)
  })

  it('revokes a registered project without reading unavailable source bytes or requiring a new preview', async () => {
    descriptor.blockers = ['source_unreadable']
    Object.assign(descriptor.projects[0], { enabled: true, rootRevoked: true, registrationRevision: 3, registrationHash: 'b'.repeat(64) })
    render(<RuntimePreparedValidationSetup onSaved={state.saved} />)
    await read()
    expect(screen.getByText(copy.blocked)).toBeTruthy()
    expect(screen.queryByRole('button', { name: copy.preview })).toBeNull()
    expect((button(copy.revoke) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('checkbox', { name: copy.revokeChoice }))
    fireEvent.click(button(copy.revoke))
    const dialog = await screen.findByRole('dialog', { name: copy.revokeTitle })
    expect(calls('revokePreparedValidation')).toHaveLength(0)
    fireEvent.click(within(dialog).getByRole('button', { name: copy.revokeConfirm }))
    await screen.findByText(copy.revoked)
    expect(calls('revokePreparedValidation')).toHaveLength(1)
    expect(calls('previewPreparedValidation')).toHaveLength(0)
    expect(calls('savePreparedValidation')).toHaveLength(0)
    expect(state.saved).toHaveBeenCalledOnce()
  })

  it('dismisses the prepared confirmation before closing its containing project inspector', async () => {
    const closed = vi.fn()

    function ProjectInspector() {
      const [cancel, setCancel] = useState<(() => void) | null>(null)

      return <Inspector kind="agent" onClose={() => { if (cancel) { cancel() } else { closed() } }} title="Project setup">
        <RuntimePreparedValidationSetup onConfirmationChange={value => setCancel(() => value)} onSaved={state.saved} />
      </Inspector>
    }

    render(<ProjectInspector />)
    await review()
    fireEvent.click(button(copy.save))
    const dialog = await screen.findByRole('dialog', { name: copy.confirmTitle })
    fireEvent.keyDown(dialog, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(closed).not.toHaveBeenCalled()
    expect(calls('savePreparedValidation')).toHaveLength(0)
    fireEvent.keyDown(screen.getByRole('complementary', { name: 'Agent details' }), { key: 'Escape' })
    expect(closed).toHaveBeenCalledOnce()
  })

  it('does not publish success after an owner changes while saving', async () => {
    const mounted = render(<RuntimePreparedValidationSetup onSaved={state.saved} />)
    await review()
    const waiting = deferred<unknown>(); state.request.mockReturnValueOnce(waiting.promise)
    await confirmSave()
    const sent = structuredClone(calls('savePreparedValidation')[0][1])
    state.ownerId = 'other'; mounted.rerender(<RuntimePreparedValidationSetup onSaved={state.saved} />)
    await act(async () => waiting.resolve(preparedSaveResult(descriptor, sent)))
    await waitFor(() => expect(screen.getAllByText(copy.changed).length).toBeGreaterThan(0))
    expect(screen.queryByText(copy.saved)).toBeNull()
    expect(state.saved).not.toHaveBeenCalled()
  })

  it('shows and copies only refreshed exact enabled references after a confirmed save', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    const mounted = render(<RuntimePreparedValidationSetup onSaved={state.saved} />)
    await review(); await confirmSave()
    const copyButton = await screen.findByRole('button', { name: `${copy.copyReference}: desktop` })
    const reference = descriptor.registrationRefs[0]
    expect(screen.getByRole('region', { name: copy.references }).querySelector('pre')?.textContent).toBe(JSON.stringify(reference, null, 2))
    fireEvent.click(copyButton)
    await waitFor(() => expect(writeText).toHaveBeenCalledExactlyOnceWith(JSON.stringify(reference, null, 2)))
    expect(reference).toEqual({ projectId: 'desktop', revision: 1, registrationSha256: calls('savePreparedValidation')[0][1].candidateHash })
    state.key = 'replacement'
    await act(async () => fireEvent.click(copyButton))
    expect(writeText).toHaveBeenCalledOnce()
    mounted.rerender(<RuntimePreparedValidationSetup onSaved={state.saved} />)
    expect(screen.queryByRole('region', { name: copy.references })).toBeNull()
  })

  it.each(['failed', 'older'])('keeps confirmed save success separate from %s reference refresh', async mode => {
    render(<RuntimePreparedValidationSetup onSaved={state.saved} />)
    await review()
    state.request.mockImplementationOnce(async (_method, params) => preparedSaveResult(descriptor, params as PreparedValidationIntent))

    if (mode === 'failed') { state.request.mockRejectedValueOnce(new Error('Refresh unavailable')) }
    await confirmSave()
    await screen.findByText(copy.referencesUnavailable)
    expect(screen.getByText(copy.saved)).toBeTruthy()
    expect(screen.queryByRole('region', { name: copy.references })).toBeNull()
    expect(screen.queryByRole('button', { name: copy.retry })).toBeNull()
    expect(screen.queryByText(copy.uncertain)).toBeNull()
    expect(calls('savePreparedValidation')).toHaveLength(1)
    expect(state.saved).toHaveBeenCalledOnce()
    fireEvent.click(button(copy.read))
    await screen.findByText(copy.invalid)
    expect(screen.queryByRole('region', { name: copy.references })).toBeNull()
    expect(calls('savePreparedValidation')).toHaveLength(1)
  })

  it('clears prior selectable references during uncertain saves and after confirmed revocation', async () => {
    Object.assign(descriptor.projects[0], { enabled: true, registrationRevision: 2, registrationHash: 'b'.repeat(64) })
    descriptor.registrationRefs = [{ projectId: 'desktop', revision: 2, registrationSha256: 'b'.repeat(64) }]
    const mounted = render(<RuntimePreparedValidationSetup onSaved={state.saved} />)
    await review()
    expect(button(`${copy.copyReference}: desktop`)).toBeTruthy()
    state.request.mockRejectedValueOnce(new Error('Lost save response'))
    await confirmSave(); await screen.findByText(copy.uncertain)
    expect(screen.queryByRole('region', { name: copy.references })).toBeNull()
    mounted.unmount()
    render(<RuntimePreparedValidationSetup onSaved={state.saved} />)
    await read()
    fireEvent.click(screen.getByRole('checkbox', { name: copy.revokeChoice }))
    fireEvent.click(button(copy.revoke))
    const dialog = await screen.findByRole('dialog', { name: copy.revokeTitle })
    fireEvent.click(within(dialog).getByRole('button', { name: copy.revokeConfirm }))
    await screen.findByText(copy.revoked)
    await screen.findByText(copy.referencesEmpty)
    expect(screen.queryByRole('button', { name: `${copy.copyReference}: desktop` })).toBeNull()
  })

  it('discards a late post-save reference refresh after the owner changes', async () => {
    const mounted = render(<RuntimePreparedValidationSetup onSaved={state.saved} />)
    await review()
    const waiting = deferred<unknown>()
    state.request.mockImplementationOnce(async (_method, params) => preparedSaveResult(descriptor, params as PreparedValidationIntent))
    state.request.mockReturnValueOnce(waiting.promise)
    await confirmSave(); await screen.findByText(copy.saved)
    const reference = { projectId: 'desktop', revision: 1, registrationSha256: '8'.repeat(64) }

    const refreshed = { ...descriptor, sidecarRevision: descriptor.sidecarRevision + 1, registrationRefs: [reference],
      projects: [{ ...descriptor.projects[0], enabled: true, registrationRevision: 1, registrationHash: reference.registrationSha256 }] }

    expect(screen.queryByRole('region', { name: copy.references })).toBeNull()
    state.ownerId = 'other'; mounted.rerender(<RuntimePreparedValidationSetup onSaved={state.saved} />)
    await act(async () => waiting.resolve(refreshed))
    expect(screen.queryByRole('region', { name: copy.references })).toBeNull()
    expect(screen.queryByText(copy.saved)).toBeNull()
    expect(calls('savePreparedValidation')).toHaveLength(1)
  })
})
