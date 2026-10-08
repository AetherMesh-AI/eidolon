import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'

import { Organization } from './organization'
import { createRuntimeAdapter, type OrganizationGateway, type OrganizationScope } from './runtime-adapter'
import { validProjectBinding, validProjectSetup } from './runtime-project-setup-contract'
import type { OrganizationProjectBinding, OrganizationProjectDraft, OrganizationProjectSave, OrganizationProjectSaveInput, OrganizationProjectSetup, OrganizationSnapshot } from './types'

const binding: OrganizationProjectBinding = { id: 'new_repo', root: 'root1', recipe: 'other', team: 'Engineering' }
const setup = (): OrganizationProjectSetup => ({ version: 1, revision: 'a'.repeat(64), roots: ['root0', 'root1'], recipes: [{ id: 'check', root: 'root0' }, { id: 'other', root: 'root1' }], teams: ['Engineering'], projects: [{ ...binding, id: 'existing', root: 'root0', recipe: 'check' }], blocked: false, blockers: [] })
const snapshot = (): OrganizationSnapshot => ({ source: 'runtime', objectives: [], agents: [], tasks: [], requests: [], activity: [], knowledge: [], runtime: { state: 'ready', capabilities: [], maxWorkers: 1, scope: 'Scoped' } })

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(yes => {resolve = yes})

  return { promise, resolve }
}

function harness() {
  let scope: OrganizationScope = { key: 'profile-a', connected: true }
  const listeners = new Set<() => void>()
  const read = vi.fn().mockImplementation(async () => setup())
  const write = vi.fn().mockImplementation(async ({ project, expectedRevision }) => ({ version: 1, revision: expectedRevision, project, yaml: `- id: ${project.id}\n  root: ${project.root}\n  recipe: ${project.recipe}\n  team: ${project.team}\n` }))
  const snapshots = vi.fn().mockImplementation(async () => snapshot())
  const save = vi.fn().mockImplementation(async ({ project }) => ({ version: 1, revision: 'b'.repeat(64), project, saved: true }))
  const request = vi.fn(async (method: string, params: Record<string, unknown>, _timeout?: number, signal?: AbortSignal) => method === 'organization.projectSetup' ? read() : method === 'organization.projectDraft' ? write(params) : method === 'organization.projectSave' ? save(params, signal) : snapshots())

  const gateway: OrganizationGateway = { request: request as OrganizationGateway['request'], getScope: () => scope, subscribeScope(listener) {listeners.add(listener);

 return () => {listeners.delete(listener)}} }

  const adapter = createRuntimeAdapter(gateway)

  return { adapter, read, write, save, snapshots, request, change() {scope = { key: 'profile-b', connected: true }; listeners.forEach(listener => listener())} }
}

async function open(h: ReturnType<typeof harness>) {
  await h.adapter.refresh()
  const view = render(<MemoryRouter><Organization adapter={h.adapter} snapshot={h.adapter.getSnapshot()} /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: 'Set up repositories' }))
  await screen.findByLabelText('Repository ID')

  return view
}

function fill(id = binding.id) {
  fireEvent.change(screen.getByLabelText('Repository ID'), { target: { value: id } })
  fireEvent.change(screen.getByLabelText('Configured root'), { target: { value: 'root1' } })
  fireEvent.change(screen.getByLabelText('Execution recipe'), { target: { value: 'other' } })
  fireEvent.change(screen.getByLabelText('Team'), { target: { value: 'Engineering' } })
}

describe('guided repository registry setup', () => {
  it('validates exact backend identifiers and recipe-root bindings without granting authority', () => {
    expect(validProjectSetup(setup())).toBe(true)
    expect(validProjectBinding(binding, setup())).toBe(true)

    for (const id of ['existing', 'Upper', 'with.dot', '1number', 'a'.repeat(65)]) {expect(validProjectBinding({ ...binding, id }, setup())).toBe(false)}
    expect(validProjectBinding({ ...binding, root: 'root0' }, setup())).toBe(false)
    expect(validProjectBinding({ ...binding, root: 'root0', recipe: 'check' }, setup())).toBe(false)
    expect(validProjectBinding(binding, { ...setup(), projects: Array.from({ length: 8 }, (_, index) => ({ ...binding, id: `repo${index}`, root: `root${index}` })) })).toBe(false)

    for (const team of ['', ' leading', 'trailing ', 'x'.repeat(65)]) {expect(validProjectSetup({ ...setup(), teams: [team] })).toBe(false)}
    expect(validProjectSetup({ ...setup(), teams: Array.from({ length: 66 }, (_, index) => `team${index}`) })).toBe(false)
    expect(validProjectSetup({ ...setup(), roots: Array.from({ length: 9 }, (_, index) => `root${index}`) })).toBe(false)
    expect(validProjectBinding(binding, { ...setup(), blocked: true })).toBe(false)
    expect(validProjectSetup({ ...setup(), roots: ['/private/path'] })).toBe(false)
    expect(validProjectSetup({ ...setup(), projects: [binding, binding] })).toBe(false)
  })

  it('shows existing bindings, filters recipes, rejects duplicate IDs, and cancels without writing', async () => {
    const h = harness()
    await open(h)
    expect(screen.getByText(/existing · root0 · check · Engineering/)).toBeTruthy()
    fill('existing')
    expect(screen.getByText('This repository ID is already configured.')).toBeTruthy()
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Prepare repository configuration' }).disabled).toBe(true)
    expect(screen.queryByRole('option', { name: 'check' })).toBeNull()
    expect(screen.getByRole<HTMLOptionElement>('option', { name: 'root0 · Already configured' }).disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('Configured root'), { target: { value: 'root0' } })
    expect(screen.getByText('Each repository must use a different root. Select an unused configured root.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel changes' }))
    expect(screen.queryByLabelText('Repository ID')).toBeNull()
    expect(h.write).not.toHaveBeenCalled()
  })

  it('explains the eight-repository limit and cannot prepare an additional binding', async () => {
    const h = harness()
    h.read.mockResolvedValue({ ...setup(), roots: Array.from({ length: 8 }, (_, index) => `root${index}`), recipes: Array.from({ length: 8 }, (_, index) => ({ id: `recipe${index}`, root: `root${index}` })), projects: Array.from({ length: 8 }, (_, index) => ({ ...binding, id: `repo${index}`, root: `root${index}`, recipe: `recipe${index}` })) })
    await open(h)
    expect(screen.getByText('This profile already has the maximum of eight repositories.')).toBeTruthy()
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Prepare repository configuration' }).disabled).toBe(true)
    fireEvent.submit(screen.getByRole('form', { name: 'Prepare repository configuration' }))
    expect(h.write).not.toHaveBeenCalled()
  })

  it('leaves recovery and cancellation available when an older runtime lacks project setup', async () => {
    const h = harness()
    h.read.mockRejectedValue(Object.assign(new Error('Method not found: organization.projectSetup'), { code: -32601 }))
    await h.adapter.refresh()
    render(<MemoryRouter><Organization adapter={h.adapter} snapshot={h.adapter.getSnapshot()} /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: 'Set up repositories' }))
    await screen.findByText('Method not found: organization.projectSetup')
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Refresh setup' }).disabled).toBe(false)
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Cancel changes' }).disabled).toBe(false)
    expect(screen.queryByRole('button', { name: 'Prepare repository configuration' })).toBeNull()
    expect(h.write).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel changes' }))
    expect(screen.queryByRole('complementary', { name: 'Agent details' })).toBeNull()
  })

  it('recovers read errors and keeps blocked registries read-only', async () => {
    const h = harness()
    h.read.mockRejectedValueOnce(new Error('Temporary setup failure'))
    await h.adapter.refresh()
    render(<MemoryRouter><Organization adapter={h.adapter} snapshot={h.adapter.getSnapshot()} /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: 'Set up repositories' }))
    await screen.findByText('Temporary setup failure')
    h.read.mockResolvedValue({ ...setup(), blocked: true, blockers: ['active_objectives'] })
    fireEvent.click(screen.getByRole('button', { name: 'Refresh setup' }))
    await screen.findByLabelText('Repository ID')
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Prepare repository configuration' }).disabled).toBe(true)
    expect(screen.getByText(/Repository changes are currently blocked/)).toBeTruthy()
    fireEvent.submit(screen.getByRole('form', { name: 'Prepare repository configuration' }))
    expect(h.write).not.toHaveBeenCalled()
  })

  it('preserves edits over errors and refreshes, then submits the fresh revision', async () => {
    const h = harness()
    h.write.mockRejectedValueOnce(new Error('Project configuration changed; refresh setup'))
    await open(h)
    fill()
    fireEvent.click(screen.getByRole('button', { name: 'Prepare repository configuration' }))
    await screen.findByText('Project configuration changed; refresh setup')
    expect(screen.getByLabelText<HTMLInputElement>('Repository ID').value).toBe(binding.id)
    h.read.mockResolvedValue({ ...setup(), revision: 'c'.repeat(64) })
    fireEvent.click(screen.getByRole('button', { name: 'Refresh setup' }))
    await waitFor(() => expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Prepare repository configuration' }).disabled).toBe(false))
    expect(screen.getByLabelText<HTMLInputElement>('Repository ID').value).toBe(binding.id)
    fireEvent.click(screen.getByRole('button', { name: 'Prepare repository configuration' }))
    await screen.findByText('Configuration prepared. Nothing has been saved.')
    expect(h.write).toHaveBeenLastCalledWith({ project: binding, expectedRevision: 'c'.repeat(64) })
    expect(screen.getByLabelText<HTMLTextAreaElement>('Repository configuration YAML').value).toContain('- id: new_repo')
    expect(screen.getByLabelText<HTMLInputElement>('Repository ID').value).toBe(binding.id)
    fireEvent.change(screen.getByLabelText('Repository ID'), { target: { value: 'edited' } })
    expect(screen.queryByLabelText('Repository configuration YAML')).toBeNull()
  })

  it('dismisses synchronously while preparation is pending and ignores its late UI result', async () => {
    const h = harness()
    const result = deferred<OrganizationProjectDraft>()
    h.write.mockReturnValue(result.promise)
    await open(h)
    fill()
    fireEvent.click(screen.getByRole('button', { name: 'Prepare repository configuration' }))
    await waitFor(() => expect(h.write).toHaveBeenCalledTimes(1))
    fireEvent.keyDown(globalThis.document, { key: 'Escape' })
    expect(screen.queryByLabelText('Repository ID')).toBeNull()
    await act(async () => result.resolve({ version: 1, revision: setup().revision, project: binding, yaml: '- id: new_repo' }))
    expect(screen.queryByText('Configuration prepared. Nothing has been saved.')).toBeNull()
  })

  it('drops the draft on profile switch and fences an old read response', async () => {
    const h = harness()
    const view = await open(h)
    fill()
    const result = deferred<OrganizationProjectSetup>()
    h.read.mockReturnValue(result.promise)
    fireEvent.click(screen.getByRole('button', { name: 'Refresh setup' }))
    act(() => h.change())
    view.rerender(<MemoryRouter><Organization adapter={h.adapter} snapshot={h.adapter.getSnapshot()} /></MemoryRouter>)
    expect(screen.queryByLabelText('Repository ID')).toBeNull()
    await act(async () => result.resolve(setup()))
    expect(screen.queryByLabelText('Repository ID')).toBeNull()
    expect(h.write).not.toHaveBeenCalled()
  })

  it('fences draft responses on profile switches and never publishes draft data as runtime state', async () => {
    const h = harness()
    await h.adapter.refresh()
    const before = h.adapter.getSnapshot()
    await h.adapter.prepareProjectDraft!({ project: binding, expectedRevision: setup().revision })
    expect(h.adapter.getSnapshot()).toBe(before)
    expect(h.request.mock.calls.map(call => call[0])).toEqual(['organization.snapshot', 'organization.projectDraft'])
    const result = deferred<OrganizationProjectDraft>()
    h.write.mockReturnValue(result.promise)
    const preparing = h.adapter.prepareProjectDraft!({ project: binding, expectedRevision: setup().revision })
    h.change()
    result.resolve({ version: 1, revision: setup().revision, project: binding, yaml: '- id: new_repo' })
    await expect(preparing).rejects.toThrow()
    expect(h.adapter.getSnapshot().connection?.scope).toBe('profile-b')
  })

  it('rejects a returned draft for a different revision or binding', async () => {
    const h = harness()

    for (const invalid of [
      { version: 1, revision: 'b'.repeat(64), project: binding, yaml: '- id: new_repo' },
      { version: 1, revision: setup().revision, project: { ...binding, root: 'root0' }, yaml: '- id: new_repo' }
    ]) {
      h.write.mockResolvedValueOnce(invalid)
      await expect(h.adapter.prepareProjectDraft!({ project: binding, expectedRevision: setup().revision })).rejects.toThrow('invalid repository setup')
    }
  })
})


const ledgerSetup = (): OrganizationProjectSetup => ({ ...setup(), storage: 'profile-ledger', ledgerProjects: [], registryConflicts: [], repair: null })
const saveReceipt = (): OrganizationProjectSave => ({ version: 1, revision: 'b'.repeat(64), project: binding, saved: true })
const saveInput = (): OrganizationProjectSaveInput => ({ project: binding, expectedRevision: setup().revision, idempotencyKey: 'save-reviewed-draft', confirmSave: true })

async function prepare(h: ReturnType<typeof harness>) {
  h.read.mockResolvedValue(ledgerSetup())
  const view = await open(h)
  fill()
  fireEvent.click(screen.getByRole('button', { name: 'Prepare repository configuration' }))
  await screen.findByLabelText('Repository configuration YAML')

  return view
}

function review() {
  fireEvent.click(screen.getByRole('button', { name: 'Review repository activation' }))

  return within(screen.getByRole('dialog'))
}

describe('reviewed profile-ledger repository activation', () => {
  it('requires deliberate review, saves once, and keeps the export after authoritative refresh', async () => {
    const h = harness()
    await prepare(h)
    expect(h.save).not.toHaveBeenCalled()
    const cancelled = review()
    expect(cancelled.getByText(/stores this repository identity in this profile’s organization ledger/)).toBeTruthy()
    expect(cancelled.getByText(/does not edit YAML, add permissions or start work/)).toBeTruthy()
    fireEvent.keyDown(cancelled.getByRole('button', { name: 'Cancel' }), { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByLabelText('Repository ID')).toBeTruthy()
    expect(h.save).not.toHaveBeenCalled()

    const result = deferred<OrganizationProjectSave>()
    h.save.mockReturnValue(result.promise)
    const confirm = review().getByRole('button', { name: 'Save and activate repository' })
    fireEvent.click(confirm)
    fireEvent.click(confirm)
    await waitFor(() => expect(h.save).toHaveBeenCalledTimes(1))
    expect(h.save.mock.calls[0][0]).toEqual({ ...saveInput(), idempotencyKey: expect.any(String) })
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Review repository activation' }).disabled).toBe(true)
    expect(screen.getByText(/A save already received may still complete/)).toBeTruthy()
    h.read.mockResolvedValue({ ...ledgerSetup(), revision: saveReceipt().revision, projects: [...setup().projects, binding], ledgerProjects: [binding] })
    await act(async () => result.resolve(saveReceipt()))
    await screen.findByText('Repository saved in this profile’s organization ledger.')
    await screen.findByRole('list', { name: 'Saved in this profile’s ledger' })
    expect(screen.getByLabelText<HTMLTextAreaElement>('Repository configuration YAML').value).toContain('- id: new_repo')
    expect(screen.getByRole('button', { name: 'Copy' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Review repository activation' })).toBeNull()
  })

  it('keeps an uncertain save retry idempotent, then refreshes stale drafts before accepting edits', async () => {
    const h = harness()
    h.save.mockRejectedValueOnce(new Error('Connection ended; refresh or retry'))
    h.save.mockRejectedValueOnce(new Error('Project configuration changed; refresh setup'))
    await prepare(h)
    fireEvent.click(review().getByRole('button', { name: 'Save and activate repository' }))
    await screen.findByText('Connection ended; refresh or retry')
    expect(screen.getByLabelText('Repository configuration YAML')).toBeTruthy()
    fireEvent.click(review().getByRole('button', { name: 'Save and activate repository' }))
    await screen.findByText('Project configuration changed; refresh setup')
    expect(h.save.mock.calls[0][0]).toEqual(h.save.mock.calls[1][0])
    h.read.mockResolvedValue({ ...ledgerSetup(), revision: 'c'.repeat(64) })
    fireEvent.click(screen.getByRole('button', { name: 'Refresh setup' }))
    await waitFor(() => expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Prepare repository configuration' }).disabled).toBe(false))
    expect(screen.queryByRole('button', { name: 'Review repository activation' })).toBeNull()
    expect(screen.getByLabelText<HTMLInputElement>('Repository ID').value).toBe(binding.id)
    fireEvent.click(screen.getByRole('button', { name: 'Prepare repository configuration' }))
    await screen.findByLabelText('Repository configuration YAML')
    fireEvent.change(screen.getByLabelText('Repository ID'), { target: { value: 'edited' } })
    expect(screen.queryByRole('button', { name: 'Review repository activation' })).toBeNull()
    expect(h.save).toHaveBeenCalledTimes(2)
  })

  it.each(['close', 'profile'] as const)('aborts a pending save on %s and ignores its late receipt', async reason => {
    const h = harness()
    const view = await prepare(h)
    const result = deferred<OrganizationProjectSave>()
    h.save.mockReturnValue(result.promise)
    fireEvent.click(review().getByRole('button', { name: 'Save and activate repository' }))
    await waitFor(() => expect(h.save).toHaveBeenCalledTimes(1))
    const signal = h.save.mock.calls[0][1] as AbortSignal

    if (reason === 'close') {fireEvent.click(screen.getByRole('button', { name: 'Close agent details' }))} else {
      act(() => h.change())
      view.rerender(<MemoryRouter><Organization adapter={h.adapter} snapshot={h.adapter.getSnapshot()} /></MemoryRouter>)
    }

    expect(signal.aborted).toBe(true)
    expect(screen.queryByLabelText('Repository ID')).toBeNull()
    await act(async () => result.resolve(saveReceipt()))
    expect(screen.queryByText('Repository saved in this profile’s organization ledger.')).toBeNull()
  })

  it('cancels review on profile switch and gates saves on a supported unblocked ledger', async () => {
    const h = harness()
    const view = await prepare(h)
    review()
    const unsubscribe = h.adapter.subscribe(() => {})
    act(() => h.change())
    view.rerender(<MemoryRouter><Organization adapter={h.adapter} snapshot={h.adapter.getSnapshot()} /></MemoryRouter>)
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(h.save).not.toHaveBeenCalled()
    unsubscribe()
    view.unmount()

    const older = harness()
    const oldView = await open(older)
    fill()
    fireEvent.click(screen.getByRole('button', { name: 'Prepare repository configuration' }))
    await screen.findByLabelText('Repository configuration YAML')
    expect(screen.queryByRole('button', { name: 'Review repository activation' })).toBeNull()
    oldView.unmount()

    const blocked = harness()
    blocked.read.mockResolvedValue({ ...ledgerSetup(), blocked: true, blockers: ['registry_conflict'], ledgerProjects: [binding], registryConflicts: ['new_repo: configured root changed'], repair: 'Restore the original configured root, then refresh setup.' })
    await open(blocked)
    expect(screen.getByRole('list', { name: 'Repository conflicts' })).toBeTruthy()
    expect(screen.getByText('Restore the original configured root, then refresh setup.')).toBeTruthy()
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Prepare repository configuration' }).disabled).toBe(true)
    expect(blocked.save).not.toHaveBeenCalled()
  })

  it('waits for a fresh authoritative snapshot after a pre-save poll is discarded', async () => {
    const h = harness()
    await h.adapter.refresh()
    const old = deferred<OrganizationSnapshot>()
    h.snapshots.mockReturnValueOnce(old.promise)
    const polling = h.adapter.refresh()
    await waitFor(() => expect(h.snapshots).toHaveBeenCalledTimes(2))
    const activated = { ...snapshot(), runtime: { ...snapshot().runtime!, availableProjects: [binding] } }
    h.snapshots.mockResolvedValue(activated)
    const saving = h.adapter.saveProject!(saveInput())
    await waitFor(() => expect(h.save).toHaveBeenCalledTimes(1))
    old.resolve(snapshot())
    await polling
    await saving
    expect(h.snapshots).toHaveBeenCalledTimes(3)
    expect(h.adapter.getSnapshot().runtime?.availableProjects).toEqual([binding])
  })

  it('fences unsent and obsolete writes, coalesces repeats, and rejects malformed receipts', async () => {
    const h = harness()
    await h.adapter.refresh()
    const cancelled = new AbortController()
    const unsent = h.adapter.saveProject!(saveInput(), cancelled.signal)
    cancelled.abort()
    await expect(unsent).rejects.toThrow()
    expect(h.save).not.toHaveBeenCalled()
    const result = deferred<OrganizationProjectSave>()
    h.save.mockReturnValue(result.promise)
    const first = h.adapter.saveProject!(saveInput())
    const second = h.adapter.saveProject!(saveInput())
    await waitFor(() => expect(h.save).toHaveBeenCalledTimes(1))
    h.change()
    result.resolve(saveReceipt())
    await expect(first).rejects.toThrow()
    await expect(second).rejects.toThrow()
    expect(h.adapter.getSnapshot().connection?.scope).toBe('profile-b')

    for (const invalid of [
      { ...saveReceipt(), saved: false },
      { ...saveReceipt(), revision: 'invalid' },
      { ...saveReceipt(), project: { ...binding, root: 'root0' } }
    ]) {
      h.save.mockResolvedValueOnce(invalid)
      await expect(h.adapter.saveProject!(saveInput())).rejects.toThrow('invalid repository setup')
    }

    expect(validProjectSetup(ledgerSetup())).toBe(true)
    expect(validProjectSetup({ ...ledgerSetup(), ledgerProjects: [binding, binding] })).toBe(false)
    expect(validProjectSetup({ ...ledgerSetup(), storage: 'yaml' })).toBe(false)
    expect(validProjectSetup({ ...ledgerSetup(), repair: {} })).toBe(false)
  })
})
