import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'

import { createRuntimeAdapter, type OrganizationGateway, type OrganizationScope } from './runtime-adapter'
import { RuntimeArtifact } from './runtime-artifact'
import { RuntimeCapabilities } from './runtime-capabilities'
import { RuntimeEditProposal } from './runtime-edit-proposal'
import type { OrganizationArtifact, OrganizationEditProposal, OrganizationSnapshot } from './types'
import { OrganizationWorkspace } from './workspace'

const baseContent = '\ufeffBefore\r\nSecond line\r\n'
const newContent = '\ufeffAfter 🧭\r\nSecond line\r\n'
const diff = '--- root-1/notes.md\n+++ root-1/notes.md\n@@ -1,2 +1,2 @@\n-Before\n+After 🧭\n Second line\n'

function proposal(overrides: Partial<OrganizationEditProposal> = {}): OrganizationEditProposal {
  return {
    id: 'proposal-1',
    workspaceId: 'workspace-1',
    sourcePath: 'root-1/notes.md',
    baseRevision: 1,
    currentRevision: 1,
    baseSha256: 'a'.repeat(64),
    newSha256: 'b'.repeat(64),
    proposalSha256: 'c'.repeat(64),
    baseContent,
    newContent,
    diff,
    status: 'proposed',
    reviewStatus: 'pending',
    ...overrides
  }
}

function artifact(edits = proposal()): OrganizationArtifact {
  return {
    id: 'proof',
    content: 'The worker proposed one file change.',
    summary: 'Edit proposal',
    sha256: 'd'.repeat(64),
    objectiveId: 'objective',
    taskId: 'edit-task',
    createdAt: '2026-10-04T00:00:00Z',
    editProposal: edits
  }
}

function snapshot(): OrganizationSnapshot {
  return {
    source: 'runtime',
    runtime: {
      capabilities: ['work.edit'],
      scope: 'Managed workspace only.',
      state: 'ready',
      maxWorkers: 2,
      supportsWorkspaceEdits: true,
      workspaceApplyEnabled: false
    },
    objectives: [
      {
        id: 'objective',
        title: 'Update the approved notes',
        description: 'Edit root-1/notes.md.',
        ownerId: 'manager',
        status: 'active',
        source: 'runtime',
        createdAt: '2026-10-04T00:00:00Z'
      }
    ],
    agents: [],
    tasks: [],
    activity: [],
    knowledge: [
      { id: 'proof', objectiveId: 'objective', title: 'Retained edit proposal', body: 'Preview only', kind: 'artifact' }
    ],
    requests: [
      {
        id: 'review-request',
        objectiveId: 'objective',
        taskId: 'edit-task',
        type: 'request.review',
        team: 'review',
        priority: 3,
        status: 'queued',
        attempts: 0,
        createdAt: '2026-10-04T00:00:00Z'
      }
    ]
  }
}

function harness(request: (...args: Parameters<OrganizationGateway['request']>) => Promise<unknown>) {
  let scope: OrganizationScope = { key: 'connection-a:default:socket', connected: true }
  const listeners = new Set<() => void>()

  return {
    adapter: createRuntimeAdapter({
      request: async <T,>(...args: Parameters<OrganizationGateway['request']>) => (await request(...args)) as T,
      getScope: () => scope,
      subscribeScope: listener => {
        listeners.add(listener)

        return () => {
          listeners.delete(listener)
        }
      }
    }),
    change(next: OrganizationScope) {
      scope = next
      listeners.forEach(listener => listener())
    }
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(yes => {
    resolve = yes
  })

  return { promise, resolve }
}

function blobBytes(blob: Blob): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer))
    reader.onerror = reject
    reader.readAsArrayBuffer(blob)
  })
}

afterEach(() => vi.restoreAllMocks())

describe('reviewed managed workspace edit evidence', () => {
  it('keeps proposal, rejection, grant failure and application distinct through the actual artifact RPC path', async () => {
    let currentSnapshot = snapshot()
    let currentArtifact = artifact()

    const request = vi.fn(async (method: string) =>
      method === 'organization.evidence' ? currentArtifact : currentSnapshot
    )

    const { adapter } = harness(request)

    const view = render(
      <MemoryRouter initialEntries={['/objectives/objective']}>
        <OrganizationWorkspace adapter={adapter} />
      </MemoryRouter>
    )

    await screen.findByText('No patch grant configured')
    fireEvent.click(screen.getByRole('tab', { name: 'Artifacts' }))
    fireEvent.click(await screen.findByRole('button', { name: /Retained edit proposal/ }))
    const panel = within(screen.getByRole('complementary', { name: 'Artifact details' }))
    expect(
      await panel.findByText('Proposed only. Review and application are not recorded; the source project is unchanged.')
    ).toBeTruthy()
    expect(panel.getByLabelText('Full proposed diff').textContent).toBe(diff)
    expect(panel.getByText('root-1/notes.md')).toBeTruthy()
    expect(panel.getByText(currentArtifact.editProposal!.baseSha256)).toBeTruthy()
    expect(panel.getByText(currentArtifact.editProposal!.newSha256)).toBeTruthy()
    expect(panel.getByText(currentArtifact.editProposal!.proposalSha256)).toBeTruthy()
    expect(panel.queryByRole('button', { name: 'Download reviewed file' })).toBeNull()
    expect(panel.queryByText('Applied to managed workspace; source project unchanged.')).toBeNull()

    currentArtifact = artifact(
      proposal({ reviewStatus: 'rejected', reviewReason: 'The replacement omits a required paragraph.' })
    )
    currentSnapshot = {
      ...currentSnapshot,
      requests: [
        {
          ...currentSnapshot.requests![0],
          status: 'pending_intervention',
          reason: 'The replacement omits a required paragraph.'
        }
      ]
    }
    await act(() => adapter.refresh())
    expect(await panel.findByText('Rejected')).toBeTruthy()
    expect(panel.getByText('The replacement omits a required paragraph.')).toBeTruthy()
    expect(panel.queryByRole('button', { name: 'Download reviewed file' })).toBeNull()
    expect(panel.queryByText('Applied to managed workspace; source project unchanged.')).toBeNull()

    currentArtifact = artifact(
      proposal({
        status: 'approved',
        reviewStatus: 'approved',
        applicationReason: 'No active worker has the configured workspace_patch grant.'
      })
    )
    currentSnapshot = {
      ...currentSnapshot,
      requests: [
        {
          ...currentSnapshot.requests![0],
          type: 'request.apply',
          reason: 'No active worker has the configured workspace_patch grant.'
        }
      ]
    }
    await act(() => adapter.refresh())
    expect(
      await panel.findByText(
        'The exact proposal was approved. It has not been applied to the managed workspace; the source project is unchanged.'
      )
    ).toBeTruthy()
    expect(panel.getByText(currentArtifact.editProposal!.applicationReason!)).toBeTruthy()
    expect(panel.getByRole('button', { name: 'Download reviewed file' })).toBeTruthy()
    expect(panel.queryByText('Applied to managed workspace; source project unchanged.')).toBeNull()

    currentArtifact = artifact(
      proposal({
        status: 'applied',
        reviewStatus: 'approved',
        currentRevision: 2,
        appliedRevision: 2,
        appliedAt: '2026-10-04T00:03:00Z'
      })
    )
    currentSnapshot = {
      ...currentSnapshot,
      requests: [
        {
          ...currentSnapshot.requests![0],
          type: 'request.merge',
          reason: 'Source merging requires manual intervention.'
        }
      ]
    }
    await act(() => adapter.refresh())
    expect(await panel.findByText('Applied to managed workspace; source project unchanged.')).toBeTruthy()
    expect(panel.getByText('Applied workspace revision').nextElementSibling?.textContent).toBe('2')
    expect(screen.getByRole('button', { name: 'Inspect request: request.merge' })).toBeTruthy()
    expect(screen.getByText(/Source merge pending intervention/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Retry request.merge' })).toBeNull()
    expect(screen.queryByRole('button', { name: /^(grant|mark.*review|approve|apply|merge)/i })).toBeNull()
    expect(request.mock.calls.every(call => ['organization.snapshot', 'organization.evidence'].includes(call[0]))).toBe(
      true
    )
    view.unmount()
  })

  it('downloads the immutable reviewed bytes and complete patch, sanitizes only the filename, and releases URLs on close', async () => {
    const edits = proposal({
      status: 'approved',
      reviewStatus: 'approved',
      sourcePath: 'root-1/nested/notes:final.md',
      diff: diff + '+<img src=x onerror="alert(1)">' + '+long line\n'.repeat(500)
    })

    const createObjectURL = vi
      .spyOn(URL, 'createObjectURL')
      .mockReturnValueOnce('blob:reviewed-file')
      .mockReturnValueOnce('blob:reviewed-patch')

    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
    const downloads: { href: string; name: string }[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      downloads.push({ href: this.href, name: this.download })
    })
    const view = render(<RuntimeEditProposal proposal={edits} />)
    expect(screen.getByLabelText('Full proposed diff').textContent).toBe(edits.diff)
    expect(screen.queryByRole('img')).toBeNull()
    expect(createObjectURL).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Download reviewed file' }))
    fireEvent.click(screen.getByRole('button', { name: 'Download proposed patch' }))
    expect(downloads).toEqual([
      { href: 'blob:reviewed-file', name: 'notes_final.md' },
      { href: 'blob:reviewed-patch', name: 'notes_final.md.patch' }
    ])
    expect(Array.from(await blobBytes(createObjectURL.mock.calls[0][0] as Blob))).toEqual(
      Array.from(new TextEncoder().encode(newContent))
    )
    expect(Array.from(await blobBytes(createObjectURL.mock.calls[1][0] as Blob))).toEqual(
      Array.from(new TextEncoder().encode(edits.diff))
    )
    expect(revokeObjectURL).not.toHaveBeenCalled()
    expect(globalThis.document.querySelector('a[download]')).toBeNull()
    view.unmount()
    expect(revokeObjectURL.mock.calls.map(call => call[0])).toEqual(['blob:reviewed-file', 'blob:reviewed-patch'])
  })

  it('shows stale base and later workspace revisions without relabeling the reviewed output as current', () => {
    const edits = proposal({
      status: 'approved',
      reviewStatus: 'approved',
      currentRevision: 3,
      applicationReason: 'Workspace revision is stale.'
    })

    const view = render(<RuntimeEditProposal proposal={edits} />)
    expect(screen.getByText(/This proposal has a stale base/)).toBeTruthy()
    expect(screen.getByText('Workspace revision is stale.')).toBeTruthy()
    expect(screen.queryByText('Applied to managed workspace; source project unchanged.')).toBeNull()
    expect(screen.getByText('Current workspace revision').nextElementSibling?.textContent).toBe('3')
    view.rerender(
      <RuntimeEditProposal
        proposal={{
          ...edits,
          status: 'applied',
          appliedRevision: 2,
          appliedAt: '2026-10-04T00:03:00Z',
          applicationReason: undefined
        }}
      />
    )
    expect(screen.getByText(/The managed workspace has advanced since this application/)).toBeTruthy()
    expect(screen.getByText('Applied workspace revision').nextElementSibling?.textContent).toBe('2')
    expect(screen.queryByText(/This proposal has a stale base/)).toBeNull()
  })

  it('keeps download failures recoverable without changing review or application state', () => {
    vi.spyOn(URL, 'createObjectURL').mockImplementation(() => {
      throw new Error('Blob unavailable')
    })
    render(<RuntimeEditProposal proposal={proposal({ status: 'approved', reviewStatus: 'approved' })} />)
    fireEvent.click(screen.getByRole('button', { name: 'Download reviewed file' }))
    expect(screen.getByRole('alert').textContent).toBe('The download could not be prepared. Try again.')
    expect(screen.getByRole('button', { name: 'Download reviewed file' })).toBeTruthy()
    expect(screen.queryByText('Applied to managed workspace; source project unchanged.')).toBeNull()
  })

  it('ignores earlier evidence after a newer refresh and never reopens a dismissed inspector', async () => {
    const initial = snapshot()
    const old = deferred<OrganizationArtifact>()
    const current = artifact(proposal({ status: 'approved', reviewStatus: 'approved' }))
    const request = vi.fn().mockReturnValueOnce(old.promise).mockResolvedValue(current)
    const { adapter } = harness(request)
    const close = vi.fn()

    const view = render(
      <RuntimeArtifact adapter={adapter} evidenceId="proof" onClose={close} snapshot={initial} title="Edit evidence" />
    )

    view.rerender(
      <RuntimeArtifact
        adapter={adapter}
        evidenceId="proof"
        onClose={close}
        snapshot={{ ...initial, requests: [{ ...initial.requests![0], status: 'completed' }] }}
        title="Edit evidence"
      />
    )
    await screen.findByText('Approved')
    await act(async () => {
      old.resolve(artifact())
      await old.promise
    })
    expect(screen.getByText('Approved')).toBeTruthy()
    expect(screen.queryByText('Pending review')).toBeNull()
    fireEvent.keyDown(globalThis.document, { key: 'Escape' })
    expect(close).toHaveBeenCalledOnce()
    view.unmount()
  })

  it('clears prior claims on failed refresh and rejects malformed full proposals until a valid read succeeds', async () => {
    const current = artifact(
      proposal({
        status: 'applied',
        reviewStatus: 'approved',
        currentRevision: 2,
        appliedRevision: 2,
        appliedAt: '2026-10-04T00:03:00Z'
      })
    )

    const request = vi
      .fn()
      .mockResolvedValueOnce(current)
      .mockResolvedValueOnce(artifact({ ...current.editProposal!, appliedRevision: undefined }))
      .mockResolvedValue(current)

    const { adapter } = harness(request)
    render(
      <RuntimeArtifact
        adapter={adapter}
        evidenceId="proof"
        onClose={vi.fn()}
        snapshot={snapshot()}
        title="Edit evidence"
      />
    )
    await screen.findByText('Applied to managed workspace; source project unchanged.')
    fireEvent.click(screen.getByRole('button', { name: 'Refresh artifact' }))
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText('The runtime returned an invalid edit proposal.')).toBeTruthy()
    expect(screen.queryByText('Applied to managed workspace; source project unchanged.')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Download reviewed file' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Retry artifact' }))
    expect(await screen.findByText('Applied to managed workspace; source project unchanged.')).toBeTruthy()
  })

  it('rejects malformed or contradictory proposal bytes and fences a late read across profile connections', async () => {
    const malformed = [
      { newContent: undefined },
      { diff: undefined },
      { baseSha256: 'invalid' },
      { currentRevision: 0 },
      {
        status: 'applied',
        reviewStatus: 'pending',
        currentRevision: 2,
        appliedRevision: 2,
        appliedAt: '2026-10-04T00:03:00Z'
      },
      { status: 'approved', reviewStatus: 'rejected' }
    ]

    for (const override of malformed) {
      const { adapter } = harness(async () => artifact({ ...proposal(), ...override } as OrganizationEditProposal))
      await expect(adapter.getEvidence('proof')).rejects.toThrow('invalid edit proposal')
    }

    const ordinary = { ...artifact(), editProposal: null }
    const ordinaryAdapter = harness(async () => ordinary).adapter
    await expect(ordinaryAdapter.getEvidence('proof')).resolves.toEqual(ordinary)

    const pending = deferred<OrganizationArtifact>()
    const request = vi.fn().mockReturnValue(pending.promise)
    const h = harness(request)
    const read = h.adapter.getEvidence('proof')
    const rejected = expect(read).rejects.toThrow('connection or profile changed')
    h.change({ key: 'connection-b:default:socket', connected: true })
    expect(request.mock.calls[0][3].aborted).toBe(true)
    pending.resolve(artifact())
    await rejected
  })

  it('distinguishes support from explicit grants and localizes read-only proposal controls', () => {
    const runtime = snapshot().runtime!

    const view = render(
      <RuntimeCapabilities
        runtime={{ ...runtime, supportsWorkspaceEdits: undefined, workspaceApplyEnabled: undefined }}
      />
    )

    expect(screen.getByText('Workspace-edit support not reported')).toBeTruthy()
    expect(screen.getByText('Patch grant not reported')).toBeTruthy()
    view.rerender(<RuntimeCapabilities runtime={runtime} />)
    expect(screen.getByText('Supported')).toBeTruthy()
    expect(screen.getByText('No patch grant configured')).toBeTruthy()
    view.rerender(<RuntimeCapabilities runtime={{ ...runtime, workspaceApplyEnabled: true }} />)
    expect(screen.getByText('Configured · staff grants still required')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /grant|enable|approve/i })).toBeNull()
    view.rerender(
      <I18nProvider configClient={null} initialLocale="ja">
        <RuntimeEditProposal proposal={proposal({ status: 'approved', reviewStatus: 'approved' })} />
      </I18nProvider>
    )
    expect(screen.getByRole('region', { name: '単一ファイルの編集提案' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'レビュー済みファイルをダウンロード' })).toBeTruthy()
    expect(screen.queryByText('Applied to managed workspace; source project unchanged.')).toBeNull()
  })
})
