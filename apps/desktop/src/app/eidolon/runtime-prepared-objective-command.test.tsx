import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'

import { $activeGatewayProfile } from '@/store/profile'
import { stubResizeObserver } from '@/test/jsdom'

import { Command } from './command'
import { createRuntimeAdapter, type OrganizationGateway } from './runtime-adapter'
import { $intakeDrafts, setIntakeDraft } from './runtime-intake-drafts'
import { $preparedObjectiveSelections } from './runtime-prepared-objective-state'
import { availableDescriptor } from './runtime-prepared-objective.test-fixtures'
import type { PreparedValidationDescriptor, PreparedValidationScope } from './runtime-prepared-validation-types'
import { deferred } from './runtime-prepared-validation.test-fixtures'
import type { OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

const environment = vi.hoisted(() => ({ scope: null as PreparedValidationScope | null }))
vi.mock('./runtime-prepared-validation-scope', () => ({ readPreparedValidationScope: () => environment.scope }))
vi.mock('./organization-request-intake', () => ({ OrganizationRequestIntake: () => <div>Assisted intake stays separate</div> }))

const key = JSON.stringify(['intake', 'owner-profile'])
const goal = () => screen.getByRole('textbox', { name: 'Objective' })
const enable = () => screen.getByRole('checkbox', { name: 'Use explicitly selected prepared validation registrations' })
const create = () => screen.getByRole('button', { name: 'Create objective' })
const select = (id = 'desktop') => fireEvent.click(screen.getByRole('checkbox', { name: new RegExp(`^${id} · root`) }))
const refChoice = (id = 'desktop') => screen.getByRole('checkbox', { name: new RegExp(`Use this exact registration: ${id} ·`) })

function fixture(descriptor: PreparedValidationDescriptor = availableDescriptor()) {
  const request = vi.fn().mockResolvedValue(descriptor)
  environment.scope = { key: 'socket', ownerKey: 'owner-profile', profile: 'default', eligible: true, organizationId: 'org', ownerId: 'owner', request }
  let snapshot: OrganizationSnapshot = { source: 'runtime', connection: { scope: 'socket', ownerScope: 'owner-profile', state: 'ready' }, objectives: [], agents: [], tasks: [], knowledge: [], activity: [], requests: [], runtime: { state: 'ready', capabilities: [], scope: 'Configured projects', maxWorkers: 1, availableProjects: descriptor.projects } }
  const submit = vi.fn().mockRejectedValue(new Error('Admission paused'))
  const adapter = { mode: 'runtime', getSnapshot: () => snapshot, createObjective: submit } as unknown as RuntimeOrganizationAdapter
  const content = () => <MemoryRouter><Command adapter={adapter} snapshot={snapshot} /></MemoryRouter>
  const view = render(content())
  fireEvent.click(screen.getByText('Manual setup'))
  fireEvent.change(goal(), { target: { value: 'Run prepared validation' } })

  return { request, submit, descriptor, view, render: () => view.rerender(content()),
    changeSnapshot: (value: Partial<OrganizationSnapshot>) => { snapshot = { ...snapshot, ...value }; view.rerender(content()) },
    async review() {
      fireEvent.click(enable())
      fireEvent.click(screen.getByRole('button', { name: 'Read available prepared registrations' }))
      await screen.findByRole('checkbox', { name: /Use this exact registration:/ })
    } }
}

beforeEach(() => { $intakeDrafts.set({}); $preparedObjectiveSelections.set({}); stubResizeObserver() })

it('does not infer prepared mode from goal text or read availability automatically', async () => {
  const f = fixture()
  expect(enable()).toHaveProperty('checked', false)
  expect(f.request).not.toHaveBeenCalled()
  fireEvent.change(screen.getByRole('combobox', { name: 'Delivery scope' }), { target: { value: 'managed_artifact' } })
  fireEvent.click(create())
  await screen.findByText('Admission paused')
  expect(f.submit.mock.calls[0][1]).not.toHaveProperty('preparedValidations')
  expect(f.request).not.toHaveBeenCalled()
})

it('requires explicit mode, availability read and per-project choice before recipe-null test admission', async () => {
  const f = fixture(); select()
  fireEvent.click(screen.getByRole('checkbox', { name: /Source project integration/ }))
  await f.review()
  expect(screen.getByRole('combobox', { name: 'Delivery scope' })).toHaveProperty('value', 'managed_artifact')
  expect(screen.getByRole('checkbox', { name: /Source project integration/ })).toHaveProperty('checked', false)
  expect(screen.getByRole('checkbox', { name: 'Project TypeScript check' })).toHaveProperty('checked', true)
  expect(screen.getByRole('checkbox', { name: /Project tests/ })).toHaveProperty('checked', true)
  expect(refChoice()).toHaveProperty('checked', false)
  expect(screen.getByText(new RegExp(f.descriptor.registrationRefs[0].registrationSha256))).toBeTruthy()
  fireEvent.click(create())
  expect(f.submit).not.toHaveBeenCalled()
  fireEvent.click(refChoice())
  fireEvent.click(create())
  await screen.findByText('Admission paused')
  expect(f.submit.mock.calls[0][1]).toMatchObject({ preparedValidations: f.descriptor.registrationRefs, projectIds: ['desktop'], deliveryMode: 'managed_artifact', requiredChecks: ['project_typecheck', 'project_tests'] })
  expect(f.request.mock.calls.map(call => call[0])).toEqual(['organization.preparedValidationSetup'])
})

it('preserves recipe-null managed-validation denial even after exact prepared selection', async () => {
  const f = fixture(); select(); await f.review(); fireEvent.click(refChoice())
  fireEvent.click(screen.getByRole('checkbox', { name: /Managed workspace validation/ }))
  fireEvent.click(create())
  expect(f.submit).not.toHaveBeenCalled()
  expect(screen.getByRole('alert').textContent).toContain('artifact')
})

it('blocks partial project coverage and clears selections after project edits', async () => {
  const descriptor = availableDescriptor()
  descriptor.projects.push({ ...descriptor.projects[0], id: 'second', root: 'root1' })
  descriptor.registrationRefs.push({ ...descriptor.registrationRefs[0], projectId: 'second' })
  const f = fixture(descriptor); select(); select('second')
  fireEvent.click(enable()); fireEvent.click(screen.getByRole('button', { name: 'Read available prepared registrations' }))
  await screen.findByRole('checkbox', { name: /Use this exact registration: desktop/ })
  fireEvent.click(refChoice())
  fireEvent.click(create())
  expect(f.submit).not.toHaveBeenCalled()
  fireEvent.click(refChoice('second')); select('second')
  expect($intakeDrafts.get()[key].metadata.preparedValidations).toBeUndefined()
  fireEvent.click(create())
  expect(f.submit).not.toHaveBeenCalled()
  fireEvent.click(refChoice()); fireEvent.click(create())
  await screen.findByText('Admission paused')
  expect(f.submit.mock.calls[0][1].preparedValidations).toEqual([descriptor.registrationRefs[0]])
})

it('blocks submit on a stale live socket even before the component receives a render', async () => {
  const f = fixture(); select(); await f.review(); fireEvent.click(refChoice())
  environment.scope = { ...environment.scope!, key: 'replacement-socket' }
  fireEvent.submit(goal().closest('form')!)
  expect(f.submit).not.toHaveBeenCalled()
  expect($intakeDrafts.get()[key].metadata.preparedValidations).toBeUndefined()
  expect(enable()).toHaveProperty('checked', true)
  expect(screen.getAllByText(/Prepared mode is still requested/).length).toBeGreaterThan(0)
})

it('preserves submitted intent and key while clearing stale owner selections, then requires rereview', async () => {
  const f = fixture(); select(); await f.review(); fireEvent.click(refChoice()); fireEvent.click(create())
  await screen.findByText('Admission paused')
  const original = structuredClone($intakeDrafts.get()[key])
  environment.scope = { ...environment.scope!, ownerId: 'replacement-owner' }
  f.render()
  await waitFor(() => expect($intakeDrafts.get()[key].metadata.preparedValidations).toBeUndefined())
  expect($intakeDrafts.get()[key].submitted).toEqual(original.submitted)
  expect($intakeDrafts.get()[key].idempotencyKey).toBe(original.idempotencyKey)
  fireEvent.click(create())
  expect(f.submit).toHaveBeenCalledTimes(1)
  environment.scope = { ...environment.scope!, ownerId: 'owner' }; f.render()
  fireEvent.click(screen.getByRole('button', { name: 'Read available prepared registrations' }))
  await screen.findByRole('checkbox', { name: /Use this exact registration:/ })
  expect(refChoice()).toHaveProperty('checked', false)
  fireEvent.click(refChoice()); fireEvent.click(create())
  await waitFor(() => expect(f.submit).toHaveBeenCalledTimes(2))
  expect(f.submit.mock.calls[1]).toEqual(f.submit.mock.calls[0])
})

it('deduplicates repeated submissions and retains exact intent for an uncertain retry', async () => {
  const f = fixture(); select(); await f.review(); fireEvent.click(refChoice())
  const admission = deferred<never>(); f.submit.mockReturnValueOnce(admission.promise)
  fireEvent.submit(goal().closest('form')!); fireEvent.submit(goal().closest('form')!)
  expect(f.submit).toHaveBeenCalledTimes(1)
  const submitted = structuredClone($intakeDrafts.get()[key].submitted)
  await act(async () => admission.reject(new Error('Response lost')))
  fireEvent.click(create())
  await screen.findByText('Admission paused')
  expect(f.submit.mock.calls[1]).toEqual(f.submit.mock.calls[0])
  expect($intakeDrafts.get()[key].submitted).toEqual(submitted)
})

it('cleared checks cannot silently fall back to legacy submission', async () => {
  const f = fixture(); select(); await f.review(); fireEvent.click(refChoice())
  await act(async () => {
    const draft = $intakeDrafts.get()[key]
    setIntakeDraft(key, { ...draft, metadata: { ...draft.metadata, requiredChecks: [] } })
  })
  fireEvent.click(create())
  expect(f.submit).not.toHaveBeenCalled()
  expect(screen.getByRole('alert').textContent).toContain('requires managed-artifact')
})

it('explicitly disabling mode removes its references and TypeScript check but retains legacy choices', async () => {
  const f = fixture(); select(); await f.review(); fireEvent.click(refChoice()); fireEvent.click(enable())
  expect($intakeDrafts.get()[key].metadata.preparedValidations).toBeUndefined()
  expect($intakeDrafts.get()[key].metadata.requiredChecks).toEqual(['project_tests'])
  expect(screen.getByRole('combobox', { name: 'Delivery scope' })).toHaveProperty('disabled', false)
  fireEvent.click(screen.getByRole('checkbox', { name: /Project tests/ })); fireEvent.click(create())
  await screen.findByText('Admission paused')
  expect(f.submit.mock.calls[0][1]).not.toHaveProperty('preparedValidations')
})

it('permits existing references at registration-history capacity and keeps other blockers fail-closed', async () => {
  const descriptor = availableDescriptor(); descriptor.blockers = ['prepared_registration_capacity']
  const f = fixture(descriptor); select(); await f.review()
  expect(screen.getByText(/New registrations cannot be saved at capacity/)).toBeTruthy()
  fireEvent.click(refChoice()); fireEvent.click(create())
  await screen.findByText('Admission paused')
  expect(f.submit).toHaveBeenCalledTimes(1)
  f.request.mockResolvedValue({ ...descriptor, blockers: ['prepared_registration_capacity', 'source_unreadable'] })
  fireEvent.click(screen.getByRole('button', { name: 'Reread and review prepared registrations' }))
  await screen.findByText(/unavailable on this backend platform or is blocked/)
  fireEvent.click(refChoice()); fireEvent.click(create())
  expect(f.submit).toHaveBeenCalledTimes(1)
})

it('discards late availability after disabling mode and never auto-selects on reopening', async () => {
  const f = fixture(); select(); const response = deferred<unknown>(); f.request.mockReturnValue(response.promise)
  fireEvent.click(enable()); fireEvent.click(screen.getByRole('button', { name: 'Read available prepared registrations' }))
  fireEvent.click(enable())
  await act(async () => response.resolve(f.descriptor))
  expect($intakeDrafts.get()[key].metadata.preparedValidations).toBeUndefined()
  fireEvent.click(enable())
  expect(screen.queryByRole('checkbox', { name: /Use this exact registration:/ })).toBeNull()
  fireEvent.click(create()); expect(f.submit).not.toHaveBeenCalled()
})


it('renders and submits exactly eight independently checked project references', async () => {
  const descriptor = availableDescriptor()
  descriptor.projects = Array.from({ length: 8 }, (_, index) => ({ ...descriptor.projects[0], id: `project${index}`, root: `root${index}` }))
  descriptor.registrationRefs = descriptor.projects.map(project => ({ ...descriptor.registrationRefs[0], projectId: project.id }))
  const f = fixture(descriptor)

  for (const project of descriptor.projects) { select(project.id) }
  fireEvent.click(enable()); fireEvent.click(screen.getByRole('button', { name: 'Read available prepared registrations' }))
  await screen.findByRole('checkbox', { name: /Use this exact registration: project0/ })
  expect(screen.getAllByRole('checkbox', { name: /Use this exact registration:/ })).toHaveLength(8)

  for (const reference of descriptor.registrationRefs) { fireEvent.click(refChoice(reference.projectId)) }
  fireEvent.click(create()); await screen.findByText('Admission paused')
  expect(f.submit.mock.calls[0][1].preparedValidations).toEqual(descriptor.registrationRefs)
})

it('observes live identity store notifications without a parent snapshot render', async () => {
  const f = fixture(); select(); await f.review(); fireEvent.click(refChoice())
  await act(async () => {
    environment.scope = { ...environment.scope!, profile: 'changed-profile' }
    $activeGatewayProfile.set('changed-profile')
  })
  expect($intakeDrafts.get()[key].metadata.preparedValidations).toBeUndefined()
  expect(enable()).toHaveProperty('checked', true)
  expect(screen.getByText(/Prepared mode is still requested/)).toBeTruthy()
  await act(async () => { $activeGatewayProfile.set('default') })
})

it('keeps the original key and submitted snapshot after deliberately selecting a replacement revision', async () => {
  const f = fixture(); select(); await f.review(); fireEvent.click(refChoice()); fireEvent.click(create())
  await screen.findByText('Admission paused')
  const original = structuredClone($intakeDrafts.get()[key])
  const next = structuredClone(f.descriptor)
  next.projects[0].registrationRevision = 3; next.projects[0].registrationHash = 'd'.repeat(64)
  next.registrationRefs[0] = { projectId: 'desktop', revision: 3, registrationSha256: 'd'.repeat(64) }
  f.request.mockResolvedValue(next)
  fireEvent.click(screen.getByRole('button', { name: 'Reread and review prepared registrations' }))
  await screen.findByRole('checkbox', { name: /Registration revision: 3/ })
  fireEvent.click(refChoice()); fireEvent.click(create())
  await waitFor(() => expect(f.submit).toHaveBeenCalledTimes(2))
  expect(f.submit.mock.calls[1][1].preparedValidations).toEqual(next.registrationRefs)
  expect(f.submit.mock.calls[1][2]).toBe(original.idempotencyKey)
  expect($intakeDrafts.get()[key].submitted).toEqual(original.submitted)
})

it('preserves mode and exact choices across unmount and clears them only after confirmed draft discard', async () => {
  const f = fixture(); select(); await f.review(); fireEvent.click(refChoice())
  const retained = structuredClone($intakeDrafts.get()[key])
  f.view.unmount()
  const next = fixture()
  expect(enable()).toHaveProperty('checked', true)
  expect(refChoice()).toHaveProperty('checked', true)
  expect($intakeDrafts.get()[key]).toEqual(retained)
  expect(next.request).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Discard draft' }))
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(enable()).toHaveProperty('checked', true)
  fireEvent.click(screen.getByRole('button', { name: 'Discard draft' }))
  fireEvent.click(screen.getAllByRole('button', { name: 'Discard draft' }).at(-1)!)
  await waitFor(() => expect($intakeDrafts.get()[key]).toBeUndefined())
  expect($preparedObjectiveSelections.get()[key]).toBeUndefined()
})

it('passes the manual selection through the real owner-read client and runtime create adapter without extra mutations', async () => {
  const descriptor = availableDescriptor()
  const snapshot: OrganizationSnapshot = { source: 'runtime', objectives: [], agents: [], tasks: [], knowledge: [], activity: [], requests: [], runtime: { state: 'ready', capabilities: [], scope: 'Configured projects', maxWorkers: 1, availableProjects: descriptor.projects } }

  const request = vi.fn().mockImplementation((method: string) => {
    if (method === 'organization.preparedValidationSetup') { return Promise.resolve(descriptor) }

    if (method === 'organization.create') { return Promise.reject(new Error('Wire response lost')) }

    return Promise.resolve(snapshot)
  })

  environment.scope = { key: 'socket', ownerKey: 'owner-profile', profile: 'default', eligible: true, organizationId: 'org', ownerId: 'owner', request }
  const adapter = createRuntimeAdapter({ request: request as OrganizationGateway['request'], getScope: () => ({ key: 'socket', ownerKey: 'owner-profile', connected: true }), subscribeScope: () => () => {} })
  await adapter.refresh()
  expect(adapter.getSnapshot().connection?.state).toBe('ready')
  request.mockClear()
  render(<MemoryRouter><Command adapter={adapter} snapshot={adapter.getSnapshot()} /></MemoryRouter>)
  fireEvent.click(screen.getByText('Manual setup')); select()
  fireEvent.change(goal(), { target: { value: 'Exact prepared objective' } })
  fireEvent.click(enable()); fireEvent.click(screen.getByRole('button', { name: 'Read available prepared registrations' }))
  await screen.findByRole('checkbox', { name: /Use this exact registration:/ })
  fireEvent.click(refChoice()); fireEvent.click(create()); await screen.findByText('Wire response lost')
  fireEvent.click(create()); await waitFor(() => expect(request.mock.calls.filter(call => call[0] === 'organization.create')).toHaveLength(2))
  const creates = request.mock.calls.filter(call => call[0] === 'organization.create')
  expect(creates[0][1]).toMatchObject({ title: 'Exact prepared objective', preparedValidations: descriptor.registrationRefs, projectIds: ['desktop'], requiredChecks: ['project_typecheck', 'project_tests'], deliveryMode: 'managed_artifact' })
  expect(creates[1][1]).toEqual(creates[0][1])
  expect(request.mock.calls.map(call => call[0])).toEqual(['organization.preparedValidationSetup', 'organization.create', 'organization.create'])
})

it('reviews a v2 registration explicitly, adds the renderer build, and submits its unchanged reference triple', async () => {
  const descriptor = availableDescriptor()
  descriptor.projects[0].preparedProfileVersion = 2
  const f = fixture(descriptor); select(); await f.review()
  expect(screen.queryByRole('checkbox', { name: 'Project renderer build check' })).toBeNull()
  expect(screen.getByText(/Profile v2: TypeScript, UI tests, and renderer build/)).toBeTruthy()
  fireEvent.click(refChoice())
  expect(screen.getByRole('checkbox', { name: 'Project renderer build check' })).toHaveProperty('checked', true)
  expect(screen.getByRole('checkbox', { name: 'Project renderer build check' })).toHaveProperty('disabled', true)
  fireEvent.click(create()); await screen.findByText('Admission paused')
  expect(f.submit.mock.calls[0][1]).toMatchObject({ preparedValidations: descriptor.registrationRefs,
    requiredChecks: ['project_typecheck', 'project_tests', 'project_build'], deliveryMode: 'managed_artifact' })
})


it('removes an introduced v2 build check on deselection and stale-owner reconciliation without silently enabling legacy admission', async () => {
  const descriptor = availableDescriptor(); descriptor.projects[0].preparedProfileVersion = 2
  const f = fixture(descriptor); select(); await f.review(); fireEvent.click(refChoice())
  expect(screen.getByRole('checkbox', { name: 'Project renderer build check' })).toHaveProperty('checked', true)
  fireEvent.click(refChoice())
  expect(screen.queryByRole('checkbox', { name: 'Project renderer build check' })).toBeNull()
  fireEvent.click(create()); expect(f.submit).not.toHaveBeenCalled()
  fireEvent.click(refChoice())
  environment.scope = { ...environment.scope!, key: 'changed' }; f.render()
  await waitFor(() => expect($intakeDrafts.get()[key].metadata.preparedValidations).toBeUndefined())
  expect(screen.queryByRole('checkbox', { name: 'Project renderer build check' })).toBeNull()
  expect(enable()).toHaveProperty('checked', true)
  fireEvent.click(create()); expect(f.submit).not.toHaveBeenCalled()
})
