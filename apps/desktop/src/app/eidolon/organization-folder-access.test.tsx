import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { folderAccessCopy } from '@/i18n/organization-folder-access'

import { OrganizationFolderAccess } from './organization-folder-access'
import type { FolderAccess } from './organization-folder-access-client'
import { OrganizationProjectSetupForm } from './runtime-project-setup'
import type { OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

const state = vi.hoisted(() => ({ key: 'socket', ownerKey: 'owner-route', profile: 'default', ownerId: 'owner', request: vi.fn(), changed: vi.fn() }))
vi.mock('./use-organization-metadata', () => {
  const metadata = () => ({ organizationId: 'org', owner: { ownerId: state.ownerId } })
  const source = () => ({ key: state.key, ownerKey: state.ownerKey, eligible: true, profile: state.profile, request: state.request })

  return { readOrganizationMetadataScope: source, organizationMetadataClient: { getSnapshot: () => ({ descriptor: { metadata: metadata() } }) },
    useOrganizationMetadata: () => ({ scope: source(), view: { descriptor: { metadata: metadata() } } }) }
})
vi.mock('@/store/projects', () => ({ pickProjectFolder: vi.fn() }))
const copy = folderAccessCopy.en

function access(): FolderAccess {
  return { capabilityVersion: 1, organizationId: 'org', ownerId: 'owner', policyGeneration: 2,
    grants: [{ alias: 'root0', path: '/missing/notes', sourcePrefix: [], device: 1, inode: 2, grantHash: 'a'.repeat(64), revoked: false, reviewed: false }],
    paused: false, reviewVersion: 'b'.repeat(64), executionStillStopping: false, uncertainToolReceipts: [], uncertainSourceRequests: [], unsettledProjectRuns: [], scope: { kind: 'served-profile', profile: 'default' } }
}

function backend(value = access()) {
  const receipts = new Map<string, unknown>()
  state.request.mockImplementation(async (method: string, params: Record<string, unknown>) => {
    if (method === 'organization.folderAccess') { return structuredClone(value) }

    if (method === 'organization.revokeProjectFolder') {
      const key = String(params.idempotencyKey)

      if (!receipts.has(key)) {
        value.grants[0].revoked = true; value.paused = true; value.policyGeneration++
        receipts.set(key, { capabilityVersion: 1, scope: value.scope, receipt: { idempotencyKey: key, organizationId: 'org', ownerId: 'owner', alias: params.alias, grantHash: params.grantHash, policyGeneration: Number(params.expectedGeneration) + 1, accessRevoked: true, dispatchPaused: true } })
      }

      return receipts.get(key)
    }

    if (method === 'organization.reviewFolderRevocation') {
      value.grants[0].reviewed = true; value.paused = false

      return { capabilityVersion: 1, scope: value.scope, alias: params.alias, accessRevoked: true, reviewed: true }
    }

    throw new Error('Source folder is missing')
  })

  return value
}

const mutations = () => state.request.mock.calls.filter(([method]) => method !== 'organization.folderAccess' && method !== 'organization.folderSetup')
const confirm = async (label: string) => fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: label }))

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(yes => { resolve = yes })

  return { promise, resolve }
}

beforeEach(() => {
  state.key = 'socket'; state.ownerKey = 'owner-route'; state.profile = 'default'; state.ownerId = 'owner'
  state.request.mockReset(); state.changed.mockReset()
})

describe('folder access owner controls', () => {
  it('reads on opening, cancels without mutation, and confirms the exact revoked folder', async () => {
    const value = backend()
    render(<OrganizationFolderAccess onChanged={state.changed} />)
    fireEvent.click(await screen.findByRole('button', { name: copy.revoke }))
    expect(within(await screen.findByRole('dialog')).getByText('root0 · /missing/notes')).toBeTruthy()
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(mutations()).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: copy.revoke }))
    await confirm(copy.revokeConfirm)
    await screen.findByText(copy.revoked)
    expect(value.grants[0].revoked).toBe(true)
    expect(state.changed).toHaveBeenCalledOnce()
    expect(mutations()).toHaveLength(1)
    expect(mutations()[0][1]).toEqual({ expectedOrganizationId: 'org', expectedGeneration: 2, alias: 'root0', grantHash: value.grants[0].grantHash, confirmed: true, idempotencyKey: expect.any(String) })
    expect(screen.queryByRole('button', { name: copy.revoke })).toBeNull()
    expect(screen.getByText(copy.paused)).toBeTruthy()
  })

  it('requires explicit unknown-outcome acknowledgment, retains unknowns after review, and never restores access', async () => {
    const value = access()
    value.grants[0].revoked = true; value.paused = true
    value.uncertainToolReceipts = [{ id: 'tool-receipt', request_id: 'tool-request', status: 'unknown' }]
    value.uncertainSourceRequests = [{ id: 'source-result', request_id: 'source-request' }]
    backend(value)
    render(<OrganizationFolderAccess onChanged={state.changed} />)
    const resume = await screen.findByRole<HTMLButtonElement>('button', { name: copy.resume })
    expect(resume.disabled).toBe(true)
    expect(screen.getByText('source-result · source-request')).toBeTruthy()
    fireEvent.click(screen.getByRole('checkbox', { name: copy.acknowledge }))
    fireEvent.click(resume)
    expect(mutations()).toEqual([])
    await confirm(copy.resume)
    await screen.findByText(copy.reviewed)
    expect(value.grants[0].revoked).toBe(true)
    expect(value.uncertainToolReceipts[0].status).toBe('unknown')
    expect(screen.getByText('tool-receipt · tool-request')).toBeTruthy()
    expect(screen.getByText('source-result · source-request')).toBeTruthy()
    expect(screen.getByText(copy.unknownNote)).toBeTruthy()
    expect(screen.queryByRole('button', { name: copy.resume })).toBeNull()
    expect(screen.queryByRole('button', { name: copy.revoke })).toBeNull()
    expect(mutations()).toEqual([['organization.reviewFolderRevocation', {
      expectedOrganizationId: 'org', expectedGeneration: 2, alias: 'root0', grantHash: 'a'.repeat(64), confirmed: true, reviewVersion: 'b'.repeat(64), acknowledgeUncertain: true
    }]])
  })

  it('blocks review while execution is stopping or project cleanup needs reconciliation', async () => {
    const value = access(); value.grants[0].revoked = true; value.paused = true; value.executionStillStopping = true
    backend(value)
    render(<OrganizationFolderAccess onChanged={state.changed} />)
    expect((await screen.findByRole<HTMLButtonElement>('button', { name: copy.resume })).disabled).toBe(true)
    expect(screen.getByText(copy.stopping)).toBeTruthy()
    value.executionStillStopping = false; value.unsettledProjectRuns = [{ id: 'run', request_id: 'run-request' }]
    fireEvent.click(screen.getByRole('button', { name: copy.read }))
    await screen.findByText(copy.unsettled)
    expect(screen.getByRole<HTMLButtonElement>('button', { name: copy.resume }).disabled).toBe(true)
    expect(screen.getByText('run · run-request')).toBeTruthy()
    value.unsettledProjectRuns = []
    fireEvent.click(screen.getByRole('button', { name: copy.read }))
    await waitFor(() => expect(screen.getByRole<HTMLButtonElement>('button', { name: copy.resume }).disabled).toBe(false))
    expect(mutations()).toEqual([])
  })

  it('keeps a stopping snapshot blocked until an explicit fresh clear read', async () => {
    const value = access(); value.grants[0].revoked = true; value.paused = true; value.executionStillStopping = true
    backend(value)
    render(<OrganizationFolderAccess onChanged={state.changed} />)
    const resume = await screen.findByRole<HTMLButtonElement>('button', { name: copy.resume })
    expect(resume.disabled).toBe(true)
    value.executionStillStopping = false
    fireEvent.click(resume)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(mutations()).toEqual([])
    expect(resume.disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: copy.read }))
    await waitFor(() => expect(screen.getByRole<HTMLButtonElement>('button', { name: copy.resume }).disabled).toBe(false))
    expect(screen.queryByText(copy.stopping)).toBeNull()
    expect(value.grants[0].revoked).toBe(true)
    expect(value.grants[0].reviewed).toBe(false)
    expect(mutations()).toEqual([])
  })

  it('can inspect a lost revocation result and retries the identical request without a second grant change', async () => {
    const value = backend()
    render(<OrganizationFolderAccess onChanged={state.changed} />)
    fireEvent.click(await screen.findByRole('button', { name: copy.revoke }))
    const implementation = state.request.getMockImplementation()!
    state.request.mockImplementationOnce(async (...args) => { await implementation(...args); throw new Error('Lost response') })
    await confirm(copy.revokeConfirm)
    await screen.findByText(copy.uncertain)
    expect(state.changed).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: copy.read }))
    await screen.findByText(copy.revoked)
    expect(screen.getByText(`${copy.generation}: 3`)).toBeTruthy()
    expect(screen.getByText(`${copy.expectedGeneration}: 2`)).toBeTruthy()
    expect(screen.getByRole<HTMLButtonElement>('button', { name: copy.resume }).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: copy.retry }))
    await waitFor(() => expect(state.changed).toHaveBeenCalledOnce())
    await waitFor(() => expect(screen.queryByText(copy.uncertain)).toBeNull())
    const writes = mutations()
    expect(writes).toHaveLength(2)
    expect(writes[1]).toEqual(writes[0])
    expect(value.policyGeneration).toBe(3)
  })

  it.each([
    ['revoke', 'generation_conflict'], ['review', 'review_conflict']
  ] as const)('requires refreshed evidence and a new confirmation after definite %s rejection', async (action, reason) => {
    const value = access(); value.grants[0].revoked = action === 'review'
    backend(value)
    render(<OrganizationFolderAccess onChanged={state.changed} />)
    const label = action === 'revoke' ? copy.revoke : copy.resume
    const confirmLabel = action === 'revoke' ? copy.revokeConfirm : copy.resume
    fireEvent.click(await screen.findByRole('button', { name: label }))
    state.request.mockImplementationOnce(async () => {
      value.policyGeneration++; value.reviewVersion = 'c'.repeat(64)
      throw Object.assign(new Error(reason), { code: 5074, data: { reason } })
    })
    await confirm(confirmLabel)
    await screen.findByText(copy.conflict)
    expect(screen.queryByRole('button', { name: copy.retry })).toBeNull()
    expect(screen.queryByRole('button', { name: label })).toBeNull()
    expect(state.changed).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: copy.read }))
    fireEvent.click(await screen.findByRole('button', { name: label }))
    expect(mutations()).toHaveLength(1)
    await confirm(confirmLabel)
    await waitFor(() => expect(state.changed).toHaveBeenCalledOnce())
    expect(mutations()).toHaveLength(2)
    expect(mutations()[0][1].expectedGeneration).toBe(2)
    expect(mutations()[1][1].expectedGeneration).toBe(3)
  })

  it('does not report a historical revoke receipt as a current dispatch pause', async () => {
    const value = backend()
    render(<OrganizationFolderAccess onChanged={state.changed} />)
    fireEvent.click(await screen.findByRole('button', { name: copy.revoke }))
    const implementation = state.request.getMockImplementation()!
    state.request.mockImplementationOnce(async (...args) => { await implementation(...args); throw new Error('Lost response') })
    await confirm(copy.revokeConfirm)
    await screen.findByText(copy.uncertain)
    // Another owner view has completed review before this exact receipt replay.
    value.grants[0].reviewed = true; value.paused = false
    fireEvent.click(screen.getByRole('button', { name: copy.retry }))
    await screen.findByText(copy.reviewed)
    expect(screen.queryByText(copy.paused)).toBeNull()
    expect(screen.queryByText(/paused pending review/)).toBeNull()
    expect(screen.getByText(copy.revokeSaved)).toBeTruthy()
    expect(mutations()[1]).toEqual(mutations()[0])
  })

  it('keeps a refused review revoked and paused until a fresh read and another explicit confirmation', async () => {
    const value = access(); value.grants[0].revoked = true; value.paused = true
    backend(value)
    render(<OrganizationFolderAccess onChanged={state.changed} />)
    fireEvent.click(await screen.findByRole('button', { name: copy.resume }))
    state.request.mockRejectedValueOnce(Object.assign(new Error('execution_still_stopping'), { code: 5074, data: { reason: 'execution_still_stopping' } }))
    await confirm(copy.resume)
    await screen.findByText(copy.conflict)
    expect(screen.queryByText(copy.reviewSaved)).toBeNull()
    expect(screen.queryByText(copy.uncertain)).toBeNull()
    expect(screen.queryByRole('button', { name: copy.retry })).toBeNull()
    expect(screen.queryByRole('button', { name: copy.resume })).toBeNull()
    expect(state.changed).not.toHaveBeenCalled()
    expect(mutations()).toHaveLength(1)
    expect(value.grants[0]).toMatchObject({ revoked: true, reviewed: false })
    expect(value.paused).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: copy.read }))
    await screen.findByText(copy.revoked)
    expect(screen.getByText(copy.paused)).toBeTruthy()
    expect(screen.queryByText(copy.conflict)).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(mutations()).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: copy.resume }))
    expect(mutations()).toHaveLength(1)
    await confirm(copy.resume)
    await screen.findByText(copy.reviewed)
    expect(screen.queryByText(copy.paused)).toBeNull()
    expect(value.grants[0].revoked).toBe(true)
    expect(state.changed).toHaveBeenCalledOnce()
    expect(mutations()).toHaveLength(2)
    expect(mutations()[1]).toEqual(mutations()[0])
  })

  it.each(['profile', 'ownerId', 'key'] as const)('ignores stale reads after %s changes and offers no mutation or automatic rebind', async field => {
    const delayed = deferred<FolderAccess>()
    state.request.mockReturnValue(delayed.promise)
    const view = render(<OrganizationFolderAccess onChanged={state.changed} />)
    await waitFor(() => expect(state.request).toHaveBeenCalledOnce())
    state[field] = 'other'
    view.rerender(<OrganizationFolderAccess onChanged={state.changed} />)
    await act(async () => delayed.resolve(access()))
    expect(screen.queryByText('root0 · /missing/notes')).toBeNull()
    expect(screen.queryByRole('button', { name: copy.revoke })).toBeNull()
    expect(screen.getByText(copy.changed)).toBeTruthy()
    expect(state.request).toHaveBeenCalledOnce()
    expect(state.changed).not.toHaveBeenCalled()
  })

  it('does not send a confirmation after the owner changes or publish a late successful mutation', async () => {
    backend()
    const view = render(<OrganizationFolderAccess onChanged={state.changed} />)
    fireEvent.click(await screen.findByRole('button', { name: copy.revoke }))
    state.ownerId = 'other'
    // The handler rechecks the live owner even before React receives another render.
    await confirm(copy.revokeConfirm)
    expect(mutations()).toEqual([])
    view.unmount()
    state.ownerId = 'owner'
    render(<OrganizationFolderAccess onChanged={state.changed} />)
    fireEvent.click(await screen.findByRole('button', { name: copy.revoke }))
    const delayed = deferred<unknown>()
    state.request.mockReturnValueOnce(delayed.promise)
    await confirm(copy.revokeConfirm)
    await waitFor(() => expect(mutations()).toHaveLength(1))
    state.profile = 'other'
    const params = mutations()[0][1]
    await act(async () => delayed.resolve({ capabilityVersion: 1, scope: { kind: 'served-profile', profile: 'default' }, receipt: {
      ...params, organizationId: 'org', ownerId: 'owner', policyGeneration: 3, accessRevoked: true, dispatchPaused: true
    } }))
    expect(state.changed).not.toHaveBeenCalled()
    expect(screen.queryByText(copy.revokeSaved)).toBeNull()
    expect(screen.getByText(copy.changed)).toBeTruthy()
  })

  it('keeps access controls available when source-dependent setup fails and Escape dismisses only its confirmation', async () => {
    backend()
    const snapshot = { source: 'runtime', objectives: [], agents: [], tasks: [], requests: [], activity: [], knowledge: [], runtime: { state: 'ready', capabilities: [], maxWorkers: 1, scope: 'Scoped' }, connection: { state: 'ready', scope: 'socket' } } as unknown as OrganizationSnapshot
    const adapter = { getSnapshot: () => snapshot, getProjectSetup: vi.fn().mockRejectedValue(new Error('Missing source setup')), refresh: vi.fn() } as unknown as RuntimeOrganizationAdapter
    const closed = vi.fn()
    render(<OrganizationProjectSetupForm adapter={adapter} onClose={closed} snapshot={snapshot} />)
    await screen.findByText('Missing source setup')
    fireEvent.click(screen.getByRole('button', { name: 'Choose a new project folder' }))
    await screen.findByText('Source folder is missing')
    fireEvent.click(screen.getByRole('button', { name: copy.revoke }))
    await screen.findByRole('dialog')
    fireEvent.keyDown(globalThis.document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(closed).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: copy.revoke })).toBeTruthy()
    expect(mutations()).toEqual([])
    fireEvent.keyDown(globalThis.document, { key: 'Escape' })
    expect(closed).toHaveBeenCalledOnce()
  })
})
