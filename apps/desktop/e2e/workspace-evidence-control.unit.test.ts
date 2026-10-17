import { expect, it, vi } from 'vitest'

import { createGalleryEvidenceControl } from './workspace-evidence-control'

it('binds held synthetic evidence to the requested identity and served owner', () => {
  const seam = createGalleryEvidenceControl('default')
  const artifact = { id: 'proof', content: 'Synthetic', summary: 'Synthetic', sha256: 'digest', createdAt: '2026-10-16T00:00:00Z', objectiveId: 'goal', taskId: null }
  seam.state.artifacts.proof = artifact
  const send = vi.fn()
  seam.control.pause()
  expect(seam.handle({ id: 1, method: 'organization.observeRead', params: { method: 'organization.evidence', params: { id: 'proof' } } }, send, () => true)).toBe(true)
  expect(send).not.toHaveBeenCalled()
  seam.control.release()
  expect(send).toHaveBeenCalledWith({ jsonrpc: '2.0', id: 1, result: { capabilityVersion: 1, state: 'available', scope: { kind: 'served-profile', profile: 'default' }, data: artifact } })
  expect(seam.reads).toEqual([{ profile: 'default', id: 'proof', wrapped: true }])
  seam.handle({ id: 2, method: 'organization.evidence', params: { id: 'missing' } }, send, () => true)
  expect(send.mock.calls.at(-1)?.[0].error.message).toContain('missing')
})

it('rejects wrapped mutation attempts and discards disconnected or cleaned-up replies', () => {
  const seam = createGalleryEvidenceControl('default')
  const send = vi.fn()
  expect(seam.handle({ id: 1, method: 'organization.observeRead', params: { method: 'organization.start' } }, send, () => true)).toBe(true)
  expect(seam.rejected).toEqual(['organization.start'])
  expect(seam.handle({ id: 2, method: 'organization.snapshot' }, send, () => true)).toBe(false)
  send.mockClear()
  seam.control.pause()
  seam.handle({ id: 3, method: 'organization.evidence', params: { id: 'proof' } }, send, () => false)
  seam.control.release()
  expect(send).not.toHaveBeenCalled()
  seam.control.pause()
  seam.handle({ id: 4, method: 'organization.evidence', params: { id: 'proof' } }, send, () => true)
  seam.control.discard()
  seam.control.release()
  expect(send).not.toHaveBeenCalled()
})
