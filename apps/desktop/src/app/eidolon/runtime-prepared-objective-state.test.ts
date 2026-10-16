import { beforeEach, expect, it, vi } from 'vitest'

import { $intakeDrafts, setIntakeDraft } from './runtime-intake-drafts'
import {
  $preparedObjectiveSelections, preparedObjectiveSelectionError, readPreparedObjectiveRegistrations,
  reconcilePreparedObjectiveSelection, selectPreparedObjectiveReference, setPreparedObjectiveMode
} from './runtime-prepared-objective-state'
import { availableDescriptor } from './runtime-prepared-objective.test-fixtures'
import type { PreparedValidationScope } from './runtime-prepared-validation-types'
import { deferred } from './runtime-prepared-validation.test-fixtures'
import type { OrganizationSnapshot } from './types'

function fixture() {
  const descriptor = availableDescriptor()
  const request = vi.fn().mockResolvedValue(descriptor)
  let scope: PreparedValidationScope = { key: 'socket', ownerKey: 'owner-profile', profile: 'default', eligible: true, organizationId: 'org', ownerId: 'owner', request }
  let snapshot: OrganizationSnapshot = { connection: { scope: 'socket', ownerScope: 'owner-profile', state: 'ready' }, objectives: [], agents: [], tasks: [], knowledge: [], activity: [], runtime: { state: 'ready', capabilities: [], scope: '', maxWorkers: 1, availableProjects: descriptor.projects } }
  const context = () => ({ scope, snapshot })
  setIntakeDraft('draft', { goal: 'Goal', criteria: '', metadata: { projectIds: ['desktop'] } })

  return { descriptor, request, context, setScope: (value: Partial<PreparedValidationScope>) => { scope = { ...scope, ...value } },
    setSnapshot: (value: Partial<OrganizationSnapshot>) => { snapshot = { ...snapshot, ...value } },
    enable: () => setPreparedObjectiveMode('draft', true), read: () => readPreparedObjectiveRegistrations('draft', context),
    choose: () => selectPreparedObjectiveReference('draft', descriptor.registrationRefs[0], true, context()),
    error: () => preparedObjectiveSelectionError('draft', $intakeDrafts.get().draft.metadata, context()) }
}

beforeEach(() => { $intakeDrafts.set({}); $preparedObjectiveSelections.set({}) })

it('never infers consent from availability and requires a deliberate exact reference choice', async () => {
  const f = fixture()
  await f.read()
  expect(f.request).not.toHaveBeenCalled()
  f.enable()
  expect(f.request).not.toHaveBeenCalled()
  await f.read()
  expect(f.request).toHaveBeenCalledWith('organization.preparedValidationSetup', { expectedOrganizationId: 'org' })
  expect($intakeDrafts.get().draft.metadata.preparedValidations).toBeUndefined()
  expect(f.error()).toBe('incomplete')
  f.choose()
  expect(f.error()).toBeUndefined()
  expect($intakeDrafts.get().draft.metadata.preparedValidations).toEqual(f.descriptor.registrationRefs)
  expect($intakeDrafts.get().draft.metadata.preparedValidations![0]).not.toBe(f.descriptor.registrationRefs[0])
})

it.each([{ key: 'other-socket' }, { ownerKey: 'other-owner' }, { eligible: false }, { organizationId: null }, { ownerId: null }])('requires both current snapshot scope and owner scope before reading: %j', async mismatch => {
  const f = fixture(); f.enable(); f.setScope(mismatch)
  await f.read()
  expect(f.request).not.toHaveBeenCalled()
  expect($preparedObjectiveSelections.get().draft.requested).toBe(true)
  expect(f.error()).toBe('changed')
})

it('deduplicates reads and discards a late owner/socket/profile response without legacy fallback', async () => {
  const f = fixture(); f.enable()
  const response = deferred<unknown>()
  f.request.mockReturnValue(response.promise)
  const first = f.read()
  await f.read()
  expect(f.request).toHaveBeenCalledTimes(1)
  f.setScope({ profile: 'other' })
  reconcilePreparedObjectiveSelection('draft', f.context())
  f.setScope({ profile: 'default' })
  response.resolve(f.descriptor)
  await first
  expect($preparedObjectiveSelections.get().draft).toMatchObject({ requested: true, error: 'changed' })
  expect($preparedObjectiveSelections.get().draft.review).toBeUndefined()
  expect(f.error()).toBe('changed')
})

it('invalidates changed project bindings without rewriting retained submission identity', async () => {
  const f = fixture(); f.enable(); await f.read(); f.choose()
  const draft = $intakeDrafts.get().draft
  const submitted = structuredClone({ goal: draft.goal, criteria: draft.criteria, metadata: draft.metadata })
  setIntakeDraft('draft', { ...draft, idempotencyKey: 'sticky', submitted })
  f.setSnapshot({ runtime: { ...f.context().snapshot.runtime!, availableProjects: [{ ...f.descriptor.projects[0], root: 'rebound' }] } })
  reconcilePreparedObjectiveSelection('draft', f.context())
  expect($intakeDrafts.get().draft.metadata.preparedValidations).toBeUndefined()
  expect($intakeDrafts.get().draft.idempotencyKey).toBe('sticky')
  expect($intakeDrafts.get().draft.submitted).toBe(submitted)
  expect(f.error()).toBe('changed')
})

it('clears old choices before rereading changed registration references', async () => {
  const f = fixture(); f.enable(); await f.read(); f.choose()
  const next = structuredClone(f.descriptor)
  next.projects[0].registrationRevision = 3
  next.projects[0].registrationHash = 'd'.repeat(64)
  next.registrationRefs[0] = { ...next.registrationRefs[0], revision: 3, registrationSha256: 'd'.repeat(64) }
  f.request.mockResolvedValue(next)
  await f.read()
  expect($intakeDrafts.get().draft.metadata.preparedValidations).toBeUndefined()
  expect(f.error()).toBe('incomplete')
  f.choose()
  expect($intakeDrafts.get().draft.metadata.preparedValidations).toBeUndefined()
  selectPreparedObjectiveReference('draft', next.registrationRefs[0], true, f.context())
  expect(f.error()).toBeUndefined()
})

it('rejects malformed, mismatched and over-limit availability without selecting anything', async () => {
  const f = fixture(); f.enable()

  for (const invalid of [
    { ...f.descriptor, ownerId: 'different' },
    { ...f.descriptor, scope: { kind: 'served-profile', profile: 'different' } },
    { ...f.descriptor, registrationRefs: [...f.descriptor.registrationRefs, ...f.descriptor.registrationRefs] },
    { ...f.descriptor, registrationRefs: Array.from({ length: 9 }, (_, i) => ({ projectId: String(i), revision: 2, registrationSha256: 'c'.repeat(64) })) }
  ]) {
    f.request.mockResolvedValue(invalid)
    await f.read()
    expect($preparedObjectiveSelections.get().draft.review).toBeUndefined()
    expect($intakeDrafts.get().draft.metadata.preparedValidations).toBeUndefined()
  }
})

it('blocks partial, tampered and cleared selections and changed delivery/check requirements', async () => {
  const f = fixture(); f.enable(); await f.read(); f.choose()
  const valid = $intakeDrafts.get().draft

  for (const metadata of [
    { ...valid.metadata, preparedValidations: [] },
    { ...valid.metadata, preparedValidations: [{ ...f.descriptor.registrationRefs[0], registrationSha256: 'e'.repeat(64) }] },
    { ...valid.metadata, projectIds: ['desktop', 'unchecked'] },
    { ...valid.metadata, projectIds: Array.from({ length: 9 }, (_, i) => String(i)) }
  ]) { setIntakeDraft('draft', { ...valid, metadata }); expect(f.error()).toBe('incomplete') }

  for (const metadata of [
    { ...valid.metadata, deliveryMode: 'source_project' as const },
    { ...valid.metadata, requiredChecks: ['project_tests' as const] },
    { ...valid.metadata, requiredChecks: ['project_typecheck' as const, 'project_tests' as const, 'source_integration' as const] }
  ]) { setIntakeDraft('draft', { ...valid, metadata }); expect(f.error()).toBe('constraints') }
})

it('keeps unsupported availability visible but cannot select or submit it', async () => {
  const f = fixture(); f.enable()
  f.descriptor.platform.supported = false
  await f.read(); f.choose()
  expect($intakeDrafts.get().draft.metadata.preparedValidations).toBeUndefined()
  expect(f.error()).toBe('unsupported')
})

it('requires explicit disable to release a stale requested mode and never alters submitted intent', async () => {
  const f = fixture(); f.enable(); await f.read(); f.choose()
  f.setScope({ key: 'changed' })
  expect(f.error()).toBe('changed')
  setPreparedObjectiveMode('draft', false)
  expect(f.error()).toBeUndefined()
  expect($intakeDrafts.get().draft.metadata.preparedValidations).toBeUndefined()
})

it('adds the v2 build only after explicit selection and clears introduced checks on reread or disable', async () => {
  const f = fixture(); f.descriptor.projects[0].preparedProfileVersion = 2
  f.enable(); await f.read()
  expect($intakeDrafts.get().draft.metadata.requiredChecks).not.toContain('project_build')
  f.choose()
  expect(f.error()).toBeUndefined()
  expect($intakeDrafts.get().draft.metadata.requiredChecks).toContain('project_build')
  const selected = $intakeDrafts.get().draft
  setIntakeDraft('draft', { ...selected, metadata: { ...selected.metadata, requiredChecks: ['project_typecheck', 'project_tests'] } })
  expect(f.error()).toBe('constraints')
  await f.read()
  expect($intakeDrafts.get().draft.metadata.preparedValidations).toBeUndefined()
  expect($intakeDrafts.get().draft.metadata.requiredChecks).not.toContain('project_build')
  f.choose(); setPreparedObjectiveMode('draft', false)
  expect($intakeDrafts.get().draft.metadata.requiredChecks).not.toContain('project_build')
  expect(f.error()).toBeUndefined()

  const draft = $intakeDrafts.get().draft
  setIntakeDraft('draft', { ...draft, metadata: { ...draft.metadata, requiredChecks: ['project_build'] } })
  f.enable(); await f.read(); f.choose(); setPreparedObjectiveMode('draft', false)
  expect($intakeDrafts.get().draft.metadata.requiredChecks).toContain('project_build')
})

it('fails closed for mixed selected versions, unknown versions, and a disabled late v2 read', async () => {
  const f = fixture()
  const v2 = { ...f.descriptor.projects[0], id: 'renderer', preparedProfileVersion: 2 as const }
  f.descriptor.projects.push(v2)
  const reference = { ...f.descriptor.registrationRefs[0], projectId: v2.id }
  f.descriptor.registrationRefs.push(reference)
  const draft = $intakeDrafts.get().draft
  setIntakeDraft('draft', { ...draft, metadata: { projectIds: ['desktop', 'renderer'] } })
  f.enable(); await f.read(); f.choose()
  selectPreparedObjectiveReference('draft', reference, true, f.context())
  expect(f.error()).toBe('versions')
  Object.assign(v2, { preparedProfileVersion: 3 })
  await f.read()
  expect($preparedObjectiveSelections.get().draft.review).toBeUndefined()
  expect($intakeDrafts.get().draft.metadata.preparedValidations).toBeUndefined()
  Object.assign(v2, { preparedProfileVersion: 2 })
  const response = deferred<unknown>(); f.request.mockReturnValueOnce(response.promise)
  const reading = f.read()
  setPreparedObjectiveMode('draft', false)
  response.resolve(f.descriptor); await reading
  expect($preparedObjectiveSelections.get().draft.requested).toBe(false)
  expect($preparedObjectiveSelections.get().draft.review).toBeUndefined()
  expect($intakeDrafts.get().draft.metadata.preparedValidations).toBeUndefined()
})
