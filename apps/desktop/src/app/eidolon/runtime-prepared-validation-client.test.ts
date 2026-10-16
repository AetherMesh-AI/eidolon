import { describe, expect, it, vi } from 'vitest'

import { createPreparedValidationClient } from './runtime-prepared-validation-client'
import { normalizePreparedSelection } from './runtime-prepared-validation-contract'
import type { PreparedValidationIntent, PreparedValidationPreview, PreparedValidationRevokeIntent, PreparedValidationScope, PreparedValidationSelection } from './runtime-prepared-validation-types'
import { deferred, preparedDescriptor, preparedPreview, preparedRevokeResult, preparedSaveResult, preparedSelection } from './runtime-prepared-validation.test-fixtures'

function harness() {
  const descriptor = preparedDescriptor()
  const selection = preparedSelection()

  const request = vi.fn(async (method: string, params: Record<string, unknown>): Promise<unknown> => {
    if (method === 'organization.preparedValidationSetup') { return descriptor }

    if (method === 'organization.previewPreparedValidation') { return preparedPreview(descriptor, params.selection as PreparedValidationSelection) }

    if (method === 'organization.revokePreparedValidation') { return preparedRevokeResult(descriptor, params as unknown as PreparedValidationRevokeIntent) }

    return preparedSaveResult(descriptor, params as unknown as PreparedValidationIntent)
  })

  const scope: PreparedValidationScope = { key: 'socket', ownerKey: 'owner-route', profile: 'default', eligible: true, organizationId: 'org', ownerId: 'owner', request: request as PreparedValidationScope['request'] }
  const newKey = vi.fn(() => '00000000-0000-4000-8000-000000000001')
  const client = createPreparedValidationClient(() => scope, newKey)

  return { client, descriptor, selection, request, scope, newKey }
}

describe('prepared validation owner review contract', () => {
  it.each(['duplicate', 'unknown', 'disabled', 'revoked', 'revision', 'hash', 'extra', 'capacity'])('rejects %s registration references', async change => {
    const h = harness()
    Object.assign(h.descriptor.projects[0], { enabled: true, registrationRevision: 2, registrationHash: 'b'.repeat(64) })
    const reference = { projectId: 'desktop', revision: 2, registrationSha256: 'b'.repeat(64) }
    h.descriptor.registrationRefs = [reference]

    if (change === 'duplicate') { h.descriptor.registrationRefs.push({ ...reference }) }

    if (change === 'unknown') { reference.projectId = 'missing' }

    if (change === 'disabled') { h.descriptor.projects[0].enabled = false }

    if (change === 'revoked') { h.descriptor.projects[0].rootRevoked = true }

    if (change === 'revision') { reference.revision++ }

    if (change === 'hash') { reference.registrationSha256 = 'c'.repeat(64) }

    if (change === 'extra') { Object.assign(reference, { arbitrary: true }) }

    if (change === 'capacity') { h.descriptor.registrationRefs = Array.from({ length: 9 }, () => ({ ...reference })) }
    await expect(h.client.read()).rejects.toMatchObject({ code: 'invalid' })
  })

  it('requires exact preview, explicit enabling and confirmation; preserves legacy and policy binding', async () => {
    const h = harness()
    await h.client.read()
    expect(h.request).toHaveBeenCalledExactlyOnceWith('organization.preparedValidationSetup', { expectedOrganizationId: 'org' })
    await expect(h.client.save(true)).rejects.toMatchObject({ code: 'confirm' })
    await expect(h.client.preview({ ...h.selection, enabled: false })).rejects.toMatchObject({ code: 'input' })
    const preview = await h.client.preview(h.selection)
    expect(h.request.mock.calls[1]).toEqual(['organization.previewPreparedValidation', {
      expectedOrganizationId: h.scope.organizationId, expectedOwnerId: h.scope.ownerId, expectedGeneration: h.descriptor.policyGeneration,
      sourceRevision: h.descriptor.sourceRevision, expectedSidecarRevision: h.descriptor.sidecarRevision, selection: h.selection
    }])
    expect(preview.candidate.selection).toEqual(normalizePreparedSelection(h.selection))
    await expect(h.client.save(false)).rejects.toMatchObject({ code: 'confirm' })
    const receipt = await h.client.save(true)
    expect(receipt.policyGeneration).toBe(h.descriptor.policyGeneration)
    expect(receipt.sidecarRevision).toBe(h.descriptor.sidecarRevision + 1)
    expect(receipt.registrationRevision).toBe(h.descriptor.projects[0].registrationRevision + 1)
    expect(h.descriptor.projects[0].recipe).toBeNull()
    expect(h.request.mock.calls.map(call => call[0])).toEqual(['organization.preparedValidationSetup', 'organization.previewPreparedValidation', 'organization.savePreparedValidation'])
    await expect(h.client.save(true)).rejects.toMatchObject({ code: 'confirm' })
  })

  it('deep-copies the reviewed intent and retries only that intent after uncertain delivery', async () => {
    const h = harness()
    await h.client.read()
    const preview = await h.client.preview(h.selection)
    const expectedCandidate = structuredClone(preview.candidate)
    preview.candidate.contract.runtime.network = 'unexpected' as 'none'
    h.selection.source.files.push('not-reviewed.ts')
    h.request.mockRejectedValueOnce(new Error('Lost response'))
    await expect(h.client.save(true)).rejects.toThrow('Lost response')

    const first = structuredClone(h.request.mock.calls.at(-1)![1])

    ;(h.request.mock.calls.at(-1)![1].candidate as PreparedValidationIntent['candidate']).enabled = false
    expect(h.client.hasRetainedIntent()).toBe(true)
    expect(() => h.client.invalidatePreview()).toThrow()
    await expect(h.client.read()).rejects.toMatchObject({ code: 'pending' })
    await expect(h.client.preview(preparedSelection())).rejects.toMatchObject({ code: 'pending' })
    await h.client.save(true)
    expect(h.request.mock.calls.at(-1)![1]).toEqual(first)
    expect(first.candidate).toEqual(expectedCandidate)
    expect(h.newKey).toHaveBeenCalledOnce()
    expect(h.client.hasRetainedIntent()).toBe(false)
  })

  it('prevents repeated requests and invalidates in-flight and completed previews after edits', async () => {
    const h = harness(); await h.client.read()
    const waiting = deferred<PreparedValidationPreview>()
    h.request.mockReturnValueOnce(waiting.promise)
    const pending = h.client.preview(h.selection)
    await expect(h.client.preview(h.selection)).rejects.toMatchObject({ code: 'pending' })
    h.client.invalidatePreview()
    waiting.resolve(preparedPreview(h.descriptor, h.selection))
    await expect(pending).rejects.toMatchObject({ code: 'edited' })
    await expect(h.client.save(true)).rejects.toMatchObject({ code: 'confirm' })
    await h.client.preview(h.selection)
    h.client.invalidatePreview()
    await expect(h.client.save(true)).rejects.toMatchObject({ code: 'confirm' })
    await h.client.preview(h.selection)
    const saved = deferred<unknown>()
    h.request.mockReturnValueOnce(saved.promise)
    const firstSave = h.client.save(true)
    await expect(h.client.save(true)).rejects.toMatchObject({ code: 'pending' })
    saved.resolve(preparedSaveResult(h.descriptor, h.request.mock.calls.at(-1)![1] as unknown as PreparedValidationIntent))
    await firstSave
    expect(h.request.mock.calls.filter(call => call[0] === 'organization.savePreparedValidation')).toHaveLength(1)
  })

  it.each(['key', 'ownerKey', 'profile', 'organizationId', 'ownerId'] as const)('discards a late preview after %s changes and never writes to a replacement owner', async key => {
    const h = harness(); await h.client.read()
    const waiting = deferred<PreparedValidationPreview>(); h.request.mockReturnValueOnce(waiting.promise)
    const pending = h.client.preview(h.selection)
    h.scope[key] = 'other'
    waiting.resolve(preparedPreview(h.descriptor, h.selection))
    await expect(pending).rejects.toMatchObject({ code: 'changed' })
    await expect(h.client.save(true)).rejects.toMatchObject({ code: 'changed' })
    await expect(h.client.read()).rejects.toMatchObject({ code: 'changed' })
    expect(h.request.mock.calls).toHaveLength(2)
  })

  it('discards late read/save results and rejects forged or stale receipts without losing the exact retry', async () => {
    const h = harness()
    const waiting = deferred<unknown>(); h.request.mockReturnValueOnce(waiting.promise)
    const reading = h.client.read(); h.scope.profile = 'other'; waiting.resolve(h.descriptor)
    await expect(reading).rejects.toMatchObject({ code: 'changed' })
    const next = harness(); await next.client.read(); await next.client.preview(next.selection)
    next.request.mockImplementationOnce(async (_method, params) => {
      const result = preparedSaveResult(next.descriptor, params as unknown as PreparedValidationIntent)
      result.receipt.policyGeneration++

      return result
    })
    await expect(next.client.save(true)).rejects.toMatchObject({ code: 'uncertain' })
    const last = structuredClone(next.request.mock.calls.at(-1)![1])
    next.request.mockImplementationOnce(async (_method, params) => {
      next.scope.ownerId = 'new-owner'

      return preparedSaveResult(next.descriptor, params as unknown as PreparedValidationIntent)
    })
    await expect(next.client.save(true)).rejects.toMatchObject({ code: 'changed' })
    expect(next.request.mock.calls.at(-1)![1]).toEqual(last)
    expect(next.client.hasRetainedIntent()).toBe(true)
  })

  it.each([
    (value: PreparedValidationPreview) => { value.sidecarRevision++ },
    (value: PreparedValidationPreview) => { value.candidate.contract.root.alias = 'root1' },
    (value: PreparedValidationPreview) => { value.candidate.selection.enabled = false },
    (value: PreparedValidationPreview) => { value.candidate.contract.runtime.network = 'host' as 'none' },
    (value: PreparedValidationPreview) => { value.candidate.verification.stagingCleanupConfirmed = false as true },
    (value: PreparedValidationPreview) => { value.candidateHash = 'not-a-hash' }
  ])('rejects changed authority, scope or evidence in the preview', async mutate => {
    const h = harness(); await h.client.read()
    const value = preparedPreview(h.descriptor, h.selection); mutate(value); h.request.mockResolvedValueOnce(value)
    await expect(h.client.preview(h.selection)).rejects.toMatchObject({ code: 'invalid' })
    await expect(h.client.save(true)).rejects.toMatchObject({ code: 'confirm' })
  })

  it('fails closed on unsupported methods/platforms, blockers, arbitrary recipes and unknown projects', async () => {
    const unsupported = harness(); unsupported.request.mockRejectedValueOnce(new Error('Unsupported method'))
    await expect(unsupported.client.read()).rejects.toThrow('Unsupported method')
    expect(unsupported.request).toHaveBeenCalledTimes(1)

    for (const blocked of [false, true]) {
      const h = harness()

      if (blocked) { h.descriptor.blockers = ['source_unreadable'] } else { h.descriptor.platform.supported = false }
      await h.client.read()
      await expect(h.client.preview(h.selection)).rejects.toMatchObject({ code: 'blocked' })
      expect(h.request).toHaveBeenCalledTimes(1)
    }

    const h = harness(); await h.client.read()
    await expect(h.client.preview({ ...h.selection, projectId: 'new-project' })).rejects.toMatchObject({ code: 'input' })
    ;(h.selection.profile.steps as { recipe: string }[])[0].recipe = 'shell'
    await expect(h.client.preview(h.selection)).rejects.toMatchObject({ code: 'input' })
    expect(h.request).toHaveBeenCalledTimes(1)
  })

  it('revokes unreadable/revoked-root registrations with exact separate confirmation and immutable retry', async () => {
    const h = harness()
    h.descriptor.blockers = ['source_unreadable']
    Object.assign(h.descriptor.projects[0], { enabled: true, rootRevoked: true, registrationRevision: 2, registrationHash: 'b'.repeat(64) })
    await h.client.read()
    await expect(h.client.revoke('desktop', false)).rejects.toMatchObject({ code: 'confirm' })
    h.request.mockRejectedValueOnce(new Error('Lost revoke response'))
    await expect(h.client.revoke('desktop', true)).rejects.toThrow('Lost revoke response')
    const first = structuredClone(h.request.mock.calls.at(-1)![1])
    expect(first).not.toHaveProperty('sourceRevision')
    expect(first).toMatchObject({ projectId: 'desktop', registrationRevision: 2, registrationHash: 'b'.repeat(64), confirmed: true })
    await expect(h.client.revoke('other', true)).rejects.toMatchObject({ code: 'confirm' })
    await expect(h.client.read()).rejects.toMatchObject({ code: 'pending' })
    const receipt = await h.client.revoke('desktop', true)
    expect(h.request.mock.calls.at(-1)![1]).toEqual(first)
    expect(receipt.registrationRevision).toBe(3)
    expect(receipt.disabled).toBe(true)
    expect(h.newKey).toHaveBeenCalledOnce()
  })
})
