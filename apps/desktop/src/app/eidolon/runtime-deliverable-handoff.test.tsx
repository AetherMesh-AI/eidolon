import { createHash, webcrypto } from 'node:crypto'

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { RuntimeArtifact } from './runtime-artifact'
import type { OrganizationArtifact, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

const content = '\uFEFF# Résumé\r\n<script>alert(1)</script>\r\n😀\n'

const artifact: OrganizationArtifact = { id: `outcome_${'a'.repeat(32)}`, objectiveId: `obj_${'b'.repeat(32)}`, round: 2,
  kind: 'integrated_deliverable', content, sha256: createHash('sha256').update(content).digest('hex'), summary: 'Result', createdAt: '2026-10-10T00:00:00Z', taskId: null }

function fixture() {
  vi.stubGlobal('crypto', webcrypto)
  let snapshot = { tasks: [], connection: { state: 'ready', scope: 'socket', ownerScope: 'owner' }, runtime: { profile: 'alpha' } } as unknown as OrganizationSnapshot
  const getEvidence = vi.fn().mockResolvedValue(artifact)
  const adapter = { getSnapshot: () => snapshot, getEvidence } as unknown as RuntimeOrganizationAdapter
  const view = () => <RuntimeArtifact adapter={adapter} evidenceId={artifact.id} onClose={() => {}} snapshot={snapshot} title="Result" />

  return { adapter, getEvidence, view, changeScope: () => { snapshot = { ...snapshot, connection: { ...snapshot.connection!, scope: 'other', ownerScope: 'other' }, runtime: { ...snapshot.runtime!, profile: 'beta' } } } }
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('exports only explicit actions with exact UTF-8 bytes, fixed safe filename, and no rendered HTML', async () => {
  const f = fixture()
  const clipboard = vi.fn().mockResolvedValue(true)
  vi.stubGlobal('hermesDesktop', undefined)
  Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: { writeClipboard: clipboard } })
  const blobs: Blob[] = []

  const create = vi.fn((blob: Blob) => { blobs.push(blob);

 return `blob:test-${blobs.length}` })

  const revoke = vi.fn()
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }))
  const downloads: string[] = []
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { downloads.push(this.download) })
  const rendered = render(f.view())
  await screen.findByRole('button', { name: 'Copy deliverable' })
  expect(create).not.toHaveBeenCalled(); expect(clipboard).not.toHaveBeenCalled()
  expect(rendered.container.querySelector('script')).toBeNull()
  expect(screen.getByText(/Objective ID:/).textContent).toContain(artifact.objectiveId)
  expect(screen.getByText(/Retained revision:/).textContent).toContain(artifact.id)
  fireEvent.click(screen.getByRole('button', { name: 'Copy deliverable' }))
  await screen.findByText('Deliverable copied.')
  expect(clipboard).toHaveBeenCalledWith(content)
  const button = screen.getByRole('button', { name: 'Download deliverable (.txt)' })
  fireEvent.click(button); fireEvent.click(button)
  expect(downloads).toEqual([`eidolon-${artifact.id}.txt`, `eidolon-${artifact.id}.txt`])

  for (const blob of blobs) {
    const bytes = await new Promise<ArrayBuffer>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result as ArrayBuffer); reader.onerror = reject; reader.readAsArrayBuffer(blob) })
    expect(Buffer.from(bytes)).toEqual(Buffer.from(content, 'utf8'))
  }

  rendered.unmount()
  expect(revoke).toHaveBeenCalledTimes(2)
  expect(f.getEvidence).toHaveBeenCalledTimes(1)
})

it('fences stale reads, profile changes and failed integrity before an export can happen', async () => {
  const f = fixture()
  let resolve!: (value: OrganizationArtifact) => void
  f.getEvidence.mockImplementationOnce(() => new Promise<OrganizationArtifact>(yes => { resolve = yes }))
  const rendered = render(f.view())
  f.changeScope(); f.getEvidence.mockResolvedValue({ ...artifact, content: 'tampered' })
  rendered.rerender(f.view())
  await act(async () => resolve(artifact))
  await screen.findByRole('alert')
  expect(screen.queryByRole('button', { name: 'Copy deliverable' })).toBeNull()
  f.getEvidence.mockResolvedValue(artifact)
  fireEvent.click(screen.getByRole('button', { name: 'Retry artifact' }))
  await waitFor(() => expect((screen.getByRole('button', { name: 'Copy deliverable' }) as HTMLButtonElement).disabled).toBe(false))
  let finishCopy!: () => void
  const clipboard = vi.fn(() => new Promise<boolean>(resolve => { finishCopy = () => resolve(true) }))
  Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: { writeClipboard: clipboard } })
  fireEvent.click(screen.getByRole('button', { name: 'Copy deliverable' }))
  fireEvent.click(screen.getByRole('button', { name: 'Copy deliverable' }))
  expect(clipboard).toHaveBeenCalledTimes(1)
  f.changeScope()
  rendered.unmount()
  await act(async () => finishCopy())
  expect(screen.queryByText('Deliverable copied.')).toBeNull()
})
