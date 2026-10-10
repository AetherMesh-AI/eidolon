import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Link, MemoryRouter, useNavigate } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'

import { createRuntimeAdapter, type OrganizationGateway, type OrganizationScope } from './runtime-adapter'
import { $intakeDrafts } from './runtime-intake-drafts'
import type { OrganizationSnapshot } from './types'
import { OrganizationWorkspace } from './workspace'

beforeEach(() => $intakeDrafts.set({}))

const initialSnapshot = (): OrganizationSnapshot => ({
  source: 'runtime',
  objectives: [],
  agents: [],
  tasks: [],
  knowledge: [],
  activity: [],
  requests: [],
  runtime: {
    capabilities: ['work.draft'],
    scope: 'Submitted text',
    state: 'ready',
    maxWorkers: 2,
    setup: {
      version: 1,
      provider: { status: 'unchecked', blockers: [], inheritedMembers: 0, overriddenMembers: 0 },
      backgroundOptIn: false
    }
  }
})

function Navigation() {
  const navigate = useNavigate()

  return (
    <>
      <Link to="/home">Return to Command</Link>
      <button onClick={() => navigate(-1)}>Back</button>
    </>
  )
}

async function fixture() {
  let snapshot = initialSnapshot()
  let scope: OrganizationScope = { key: 'socket-a', ownerKey: 'connection-a/profile-a', connected: true }
  const listeners = new Set<() => void>()
  const request = vi.fn().mockImplementation(() => Promise.resolve(snapshot))

  const adapter = createRuntimeAdapter({
    request: request as OrganizationGateway['request'],
    getScope: () => scope,
    subscribeScope: listener => {
      listeners.add(listener)

      return () => {
        listeners.delete(listener)
      }
    }
  })

  const mount = () =>
    render(
      <MemoryRouter initialEntries={['/home']}>
        <Navigation />
        <OrganizationWorkspace adapter={adapter} />
      </MemoryRouter>
    )

  const view = mount()
  await waitFor(() => expect(adapter.getSnapshot().connection?.state).toBe('ready'))

  return {
    adapter,
    request,
    view,
    mount,
    get snapshot() {
      return snapshot
    },
    async update(next: OrganizationSnapshot) {
      snapshot = next
      await act(async () => {
        await adapter.refresh()
      })
    },
    async switchScope(next: OrganizationScope) {
      scope = next
      await act(async () => {
        listeners.forEach(listener => listener())
        await adapter.refresh()
      })
    }
  }
}

const goal = () => screen.getByRole('textbox', { name: 'Objective' })
const context = () => screen.getByRole('textbox', { name: 'Submitted context (optional)' })
const criteria = () => screen.getByRole('textbox', { name: 'Acceptance criteria' })
const delivery = () => screen.getByRole('combobox', { name: 'Delivery scope' })
const create = () => screen.getByRole('button', { name: 'Create objective' })
const change = (element: HTMLElement, value: string) => fireEvent.change(element, { target: { value } })
const leave = () => fireEvent.click(screen.getByRole('link', { name: 'Review organization roster' }))
const back = () => fireEvent.click(screen.getByRole('button', { name: 'Back' }))

const creates = (request: ReturnType<typeof vi.fn>) =>
  request.mock.calls.filter(call => call[0] === 'organization.create')

it('retains all intake fields through roster/back and unmount, without automatic submission or disk persistence', async () => {
  const { request, view, mount } = await fixture()
  const disk = vi.spyOn(Storage.prototype, 'setItem')
  change(goal(), 'Prepare my first evidence-backed brief')
  change(context(), 'Keep these supplied facts')
  change(criteria(), 'Cite the supplied facts')
  change(delivery(), 'managed_artifact')
  fireEvent.click(screen.getByRole('checkbox', { name: 'Priority' }))
  fireEvent.click(screen.getByRole('checkbox', { name: /Project tests/ }))
  leave()
  await screen.findByRole('heading', { name: 'Organization' })
  back()
  view.unmount()
  mount()
  expect(goal()).toHaveProperty('value', 'Prepare my first evidence-backed brief')
  expect(context()).toHaveProperty('value', 'Keep these supplied facts')
  expect(criteria()).toHaveProperty('value', 'Cite the supplied facts')
  expect(delivery()).toHaveProperty('value', 'managed_artifact')
  expect(screen.getByRole('checkbox', { name: 'Priority' })).toHaveProperty('checked', true)
  expect(screen.getByRole('checkbox', { name: /Project tests/ })).toHaveProperty('checked', true)
  expect(creates(request)).toHaveLength(0)
  expect(disk).not.toHaveBeenCalled()
  disk.mockRestore()
})

it('isolates connections/profiles, hides drafts during switching, and restores only the verified owner', async () => {
  const f = await fixture()
  change(goal(), 'Owner A private draft')
  await f.switchScope({ key: 'socket-b', ownerKey: 'connection-a/profile-b', connected: true })
  expect(goal()).toHaveProperty('value', '')
  change(goal(), 'Owner B private draft')
  await f.switchScope({ key: 'socket-other', ownerKey: 'connection-b/profile-a', connected: true })
  expect(goal()).toHaveProperty('value', '')
  await f.switchScope({ key: 'socket-a-new', ownerKey: 'connection-a/profile-a', connected: false, switching: true })
  expect(goal()).toHaveProperty('value', '')
  expect(goal()).toHaveProperty('disabled', true)
  await f.switchScope({ key: 'socket-a-new', ownerKey: 'connection-a/profile-a', connected: true })
  expect(goal()).toHaveProperty('value', 'Owner A private draft')
  expect(creates(f.request)).toHaveLength(0)
})

it('requires explicit discard and cancels it without losing content', async () => {
  await fixture()
  change(goal(), 'Keep unless explicitly discarded')
  fireEvent.click(screen.getByRole('button', { name: 'Discard draft' }))
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(goal()).toHaveProperty('value', 'Keep unless explicitly discarded')
  fireEvent.click(screen.getByRole('button', { name: 'Discard draft' }))
  fireEvent.click(screen.getAllByRole('button', { name: 'Discard draft' }).at(-1)!)
  await waitFor(() => expect(goal()).toHaveProperty('value', ''))
  expect(Object.keys($intakeDrafts.get())).toHaveLength(0)
})

it('revalidates retired projects on return without silently changing delivery', async () => {
  const f = await fixture()
  await f.update({
    ...f.snapshot,
    runtime: {
      ...f.snapshot.runtime!,
      availableProjects: [{ id: 'retired', root: 'root0', recipe: 'tests', team: 'team' }]
    }
  })
  change(goal(), 'Change the selected project')
  fireEvent.click(screen.getByRole('checkbox', { name: /retired/ }))
  leave()
  await f.update({ ...f.snapshot, runtime: { ...f.snapshot.runtime!, availableProjects: [] } })
  back()
  expect(delivery()).toHaveProperty('value', 'source_project')
  expect(screen.getByRole('checkbox', { name: 'retired' })).toHaveProperty('checked', true)
  fireEvent.click(create())
  expect(creates(f.request)).toHaveLength(0)
  expect(screen.getAllByText(/A selected repository is no longer configured/).length).toBeGreaterThan(0)
  fireEvent.click(screen.getByRole('checkbox', { name: 'retired' }))
  expect(screen.queryByRole('checkbox', { name: 'retired' })).toBeNull()
  expect(delivery()).toHaveProperty('value', 'source_project')
})

it('keeps one request key through navigation, duplicate clicks, lost responses and edits, then clears only on confirmation', async () => {
  const f = await fixture()
  let reject!: (reason: Error) => void
  f.request.mockImplementation((method: string) =>
    method === 'organization.create'
      ? new Promise((_resolve, fail) => {
          reject = fail
        })
      : Promise.resolve(f.snapshot)
  )
  change(goal(), 'Original objective')
  change(delivery(), 'managed_artifact')
  fireEvent.click(create())
  await waitFor(() => expect(creates(f.request)).toHaveLength(1))
  leave()
  back()
  expect(screen.getByRole('button', { name: 'Submitting objective…' })).toHaveProperty('disabled', true)
  fireEvent.submit(goal().closest('form')!)
  expect(creates(f.request)).toHaveLength(1)
  leave()
  await act(async () => reject(new Error('Response lost')))
  back()
  expect(screen.getByRole('alert').textContent).toBe('Response lost')
  change(goal(), 'Edited after lost response')
  f.request.mockImplementation((method: string) =>
    method === 'organization.create'
      ? Promise.reject(new Error('Idempotency key already belongs to a different objective'))
      : Promise.resolve(f.snapshot)
  )
  fireEvent.click(create())
  await screen.findByText('Idempotency key already belongs to a different objective')
  const first = creates(f.request)[0][1]
  expect(creates(f.request)[1][1].idempotencyKey).toBe(first.idempotencyKey)
  fireEvent.click(screen.getByRole('button', { name: 'Restore submitted draft' }))
  expect(goal()).toHaveProperty('value', 'Original objective')
  f.request.mockImplementation((method: string) =>
    Promise.resolve(
      method === 'organization.create'
        ? { objective: { id: 'confirmed', title: 'Original objective' }, snapshot: f.snapshot }
        : f.snapshot
    )
  )
  fireEvent.click(create())
  await waitFor(() => expect(Object.keys($intakeDrafts.get())).toHaveLength(0))
  expect(creates(f.request)[2][1]).toEqual(first)
})

it('acknowledges success after leaving without navigating away from the roster', async () => {
  const f = await fixture()
  let resolve!: (value: unknown) => void
  f.request.mockImplementation((method: string) =>
    method === 'organization.create'
      ? new Promise(done => {
          resolve = done
        })
      : Promise.resolve(f.snapshot)
  )
  change(goal(), 'Complete while away')
  fireEvent.click(create())
  await waitFor(() => expect(creates(f.request)).toHaveLength(1))
  leave()
  await act(async () => resolve({ objective: { id: 'done', title: 'Complete while away' }, snapshot: f.snapshot }))
  expect(screen.getByRole('heading', { name: 'Organization' })).toBeTruthy()
  back()
  expect(goal()).toHaveProperty('value', '')
})

it('does not clear supplied context after a malformed creation receipt', async () => {
  const f = await fixture()
  f.request.mockImplementation((method: string) =>
    Promise.resolve(method === 'organization.create' ? { objective: {}, snapshot: f.snapshot } : f.snapshot)
  )
  change(goal(), 'Keep this draft')
  change(context(), 'Sensitive source facts')
  fireEvent.click(create())
  await screen.findByRole('alert')
  expect(goal()).toHaveProperty('value', 'Keep this draft')
  expect(context()).toHaveProperty('value', 'Sensitive source facts')
  expect(Object.values($intakeDrafts.get())[0].idempotencyKey).toBeTruthy()
})

it('revalidates persistent leader permissions after a roster detour and preserves the selected owners', async () => {
  const f = await fixture()

  const leader = (id: string, role: string, capabilities: string[], managerId?: string) => ({
    id,
    name: id,
    role,
    capabilities,
    managerId,
    persistent: true,
    lifecycle: 'active' as const,
    status: 'idle' as const,
    responsibilities: [],
    summary: id
  })

  const executive = leader('chosen-executive', 'Executive', ['request.accept'])
  const manager = leader('chosen-manager', 'Manager', ['request.plan', 'request.integrate'], executive.id)
  await f.update({ ...f.snapshot, agents: [executive, manager] })
  change(goal(), 'Keep selected ownership')
  change(screen.getByRole('combobox', { name: 'Executive owner' }), executive.id)
  change(screen.getByRole('combobox', { name: 'Responsible manager' }), manager.id)
  leave()
  await f.update({ ...f.snapshot, agents: [executive, { ...manager, capabilities: ['request.plan'] }] })
  back()
  expect(screen.getByRole('combobox', { name: 'Executive owner' })).toHaveProperty('value', executive.id)
  expect(screen.getByRole('combobox', { name: 'Responsible manager' })).toHaveProperty('value', '')
  fireEvent.click(create())
  expect(screen.getByRole('alert').textContent).toBe('Choose an active executive and one of their active managers.')
  expect(creates(f.request)).toHaveLength(0)
  expect(Object.values($intakeDrafts.get())[0].metadata.managerId).toBe(manager.id)
})

it('warns that discarding an unconfirmed submission cannot cancel work, and cancel preserves its retry key', async () => {
  const f = await fixture()
  f.request.mockImplementation((method: string) =>
    method === 'organization.create' ? Promise.reject(new Error('Response lost')) : Promise.resolve(f.snapshot)
  )
  change(goal(), 'Potentially admitted objective')
  fireEvent.click(create())
  await screen.findByText('Response lost')
  const key = Object.values($intakeDrafts.get())[0].idempotencyKey
  fireEvent.click(screen.getByRole('button', { name: 'Discard draft' }))
  expect(screen.getByRole('dialog').textContent).toContain(
    'Discarding does not cancel work and a later submission may create a duplicate.'
  )
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(Object.values($intakeDrafts.get())[0].idempotencyKey).toBe(key)
  expect(goal()).toHaveProperty('value', 'Potentially admitted objective')
})

it('keeps an in-flight receipt with its original profile when a different owner becomes foreground', async () => {
  const f = await fixture()
  let resolve!: (value: unknown) => void
  f.request.mockImplementation((method: string) =>
    method === 'organization.create'
      ? new Promise(done => {
          resolve = done
        })
      : Promise.resolve(f.snapshot)
  )
  change(goal(), 'Owner A submission')
  fireEvent.click(create())
  await waitFor(() => expect(creates(f.request)).toHaveLength(1))
  const key = creates(f.request)[0][1].idempotencyKey
  await f.switchScope({ key: 'socket-b', ownerKey: 'connection-a/profile-b', connected: true })
  change(goal(), 'Owner B draft')
  await act(async () =>
    resolve({ objective: { id: 'owner-a-objective', title: 'Owner A submission' }, snapshot: f.snapshot })
  )
  expect(goal()).toHaveProperty('value', 'Owner B draft')
  expect(screen.queryByRole('alert')).toBeNull()
  await f.switchScope({ key: 'socket-a-new', ownerKey: 'connection-a/profile-a', connected: true })
  expect(goal()).toHaveProperty('value', 'Owner A submission')
  expect(Object.values($intakeDrafts.get()).find(draft => draft.goal === 'Owner A submission')?.idempotencyKey).toBe(
    key
  )
  expect(creates(f.request)).toHaveLength(1)
})
