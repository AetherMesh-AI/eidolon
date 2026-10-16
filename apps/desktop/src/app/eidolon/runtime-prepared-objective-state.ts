import { atom } from 'nanostores'

import { policyEqual } from './organization-owner-policy-contract'
import { $intakeDrafts, emptyIntake, setIntakeDraft } from './runtime-intake-drafts'
import { createPreparedValidationClient } from './runtime-prepared-validation-client'
import type { PreparedValidationDescriptor, PreparedValidationScope } from './runtime-prepared-validation-types'
import type { ObjectiveMetadata, OrganizationProjectBinding, OrganizationSnapshot, PreparedValidationReference } from './types'

interface PreparedObjectiveReview {
  descriptor: PreparedValidationDescriptor
  identity: unknown[]
  projects: OrganizationProjectBinding[]
}
export interface PreparedObjectiveSelection {
  requested: boolean
  buildCheckWasPresent?: boolean
  review?: PreparedObjectiveReview
  busy?: boolean
  reading?: Omit<PreparedObjectiveReview, 'descriptor'>
  error?: 'changed' | 'failed' | 'unavailable'
}
export interface PreparedObjectiveContext {
  scope: PreparedValidationScope
  snapshot: OrganizationSnapshot
}
export const $preparedObjectiveSelections = atom<Record<string, PreparedObjectiveSelection>>({})
const pending = new Set<string>()
const identity = (scope: PreparedValidationScope) => [scope.key, scope.ownerKey, scope.profile, scope.eligible, scope.organizationId, scope.ownerId]
const projectBindings = (snapshot: OrganizationSnapshot) => (snapshot.runtime?.availableProjects ?? []).map(({ id, root, recipe, team }) => ({ id, root, recipe, team }))

export function preparedObjectiveOwnerMatches({ scope, snapshot }: PreparedObjectiveContext) {
  return scope.eligible && !!scope.organizationId && !!scope.ownerId && !!scope.ownerKey &&
    snapshot.connection?.state === 'ready' && snapshot.connection.scope === scope.key && snapshot.connection.ownerScope === scope.ownerKey
}

function publish(key: string, value: PreparedObjectiveSelection) {
  const previous = $preparedObjectiveSelections.get()[key]

  if (value.requested && value.buildCheckWasPresent === undefined) { value.buildCheckWasPresent = previous?.buildCheckWasPresent }
  $preparedObjectiveSelections.set({ ...$preparedObjectiveSelections.get(), [key]: value })
}

function clearReferences(key: string) {
  const draft = $intakeDrafts.get()[key]

  if (!draft?.metadata.preparedValidations) { return }
  const { preparedValidations: _refs, ...metadata } = draft.metadata
  // A stale live choice must never rewrite the retained submitted intent/key.
  setIntakeDraft(key, { ...draft, metadata: { ...metadata, requiredChecks: $preparedObjectiveSelections.get()[key]?.buildCheckWasPresent
    ? metadata.requiredChecks : metadata.requiredChecks?.filter(check => check !== 'project_build') } })
}

export function setPreparedObjectiveMode(key: string, requested: boolean) {
  const state = $preparedObjectiveSelections.get()[key]
  const draft = $intakeDrafts.get()[key] ?? emptyIntake
  const { preparedValidations: _refs, ...metadata } = draft.metadata
  publish(key, { requested, busy: pending.has(key), ...(requested ? { buildCheckWasPresent: metadata.requiredChecks?.includes('project_build') ?? false } : {}) })
  setIntakeDraft(key, { ...draft, metadata: requested ? { ...metadata, deliveryMode: 'managed_artifact',
    requiredChecks: [...new Set([...(metadata.requiredChecks ?? []).filter(check => check !== 'source_integration'), 'project_typecheck', 'project_tests'] as const)] } : { ...metadata, requiredChecks: metadata.requiredChecks?.filter(check => check !== 'project_typecheck' && (check !== 'project_build' || state?.buildCheckWasPresent)) } })
}

export function discardPreparedObjectiveSelection(key: string) {
  const states = { ...$preparedObjectiveSelections.get() }
  delete states[key]
  $preparedObjectiveSelections.set(states)
}

export function preparedObjectiveReviewCurrent(state: PreparedObjectiveSelection | undefined, context: PreparedObjectiveContext) {
  return !!state?.review && preparedObjectiveOwnerMatches(context) &&
    policyEqual(state.review.identity, identity(context.scope)) && policyEqual(state.review.projects, projectBindings(context.snapshot))
}

export function reconcilePreparedObjectiveSelection(key: string, context: PreparedObjectiveContext) {
  const state = $preparedObjectiveSelections.get()[key]

  const capture = state?.review ?? state?.reading

  if (state && capture && (!preparedObjectiveOwnerMatches(context) || !policyEqual(capture.identity, identity(context.scope)) ||
    !policyEqual(capture.projects, projectBindings(context.snapshot)))) {
    publish(key, { requested: state.requested, busy: state.busy, error: 'changed' })
    clearReferences(key)
  }
}

export async function readPreparedObjectiveRegistrations(key: string, context: () => PreparedObjectiveContext) {
  if (pending.has(key)) { return }
  const state = $preparedObjectiveSelections.get()[key]

  if (!state?.requested) { return }
  const captured = context()
  clearReferences(key)

  if (!preparedObjectiveOwnerMatches(captured)) {
    publish(key, { requested: true, error: 'unavailable' })

    return
  }

  pending.add(key)
  const reading = { requested: true, busy: true, reading: { identity: identity(captured.scope), projects: projectBindings(captured.snapshot) } } satisfies PreparedObjectiveSelection
  publish(key, reading)
  const client = createPreparedValidationClient(() => context().scope)

  try {
    const descriptor = await client.read()
    const current = context()

    if ($preparedObjectiveSelections.get()[key] !== reading) { return }

    if (!client.current() || !preparedObjectiveOwnerMatches(current) || !policyEqual(reading.reading.identity, identity(current.scope)) ||
      !policyEqual(reading.reading.projects, projectBindings(current.snapshot))) {
      publish(key, { requested: true, error: 'changed' })

      return
    }

    publish(key, { requested: true, review: { descriptor, ...reading.reading } })
  } catch {
    if ($preparedObjectiveSelections.get()[key] === reading) {
      publish(key, { requested: true, error: client.current() && preparedObjectiveOwnerMatches(context()) ? 'failed' : 'changed' })
    }
  } finally {
    pending.delete(key)
    const current = $preparedObjectiveSelections.get()[key]

    if (current?.busy) { publish(key, { ...current, busy: false }) }
  }
}

/** History capacity blocks saving new registrations, not selecting existing exact references. */
export function preparedObjectiveBlockingReasons(descriptor: PreparedValidationDescriptor) {
  return descriptor.blockers.filter(blocker => blocker !== 'prepared_registration_capacity')
}

export function preparedObjectiveProjectMatches(descriptor: PreparedValidationDescriptor, projectId: string, snapshot: OrganizationSnapshot) {
  const registered = descriptor.projects.find(project => project.id === projectId)
  const configured = snapshot.runtime?.availableProjects?.find(project => project.id === projectId)

  return !!registered && !!configured && ['id', 'root', 'recipe', 'team'].every(key => registered[key as keyof typeof registered] === configured[key as keyof typeof configured])
}

export function selectPreparedObjectiveReference(key: string, reference: PreparedValidationReference, selected: boolean, context: PreparedObjectiveContext) {
  const state = $preparedObjectiveSelections.get()[key]
  const draft = $intakeDrafts.get()[key]

  if (!state?.requested || state.busy || !draft || !preparedObjectiveReviewCurrent(state, context)) {
    reconcilePreparedObjectiveSelection(key, context)

    return
  }

  const descriptor = state.review!.descriptor

  if (!descriptor.platform.supported || preparedObjectiveBlockingReasons(descriptor).length || !draft.metadata.projectIds?.includes(reference.projectId) ||
    !descriptor.registrationRefs.some(ref => policyEqual(ref, reference)) ||
    !preparedObjectiveProjectMatches(descriptor, reference.projectId, context.snapshot)) { return }

  const refs = (draft.metadata.preparedValidations ?? []).filter(ref => ref.projectId !== reference.projectId)

  if (selected && refs.length < 8) { refs.push(structuredClone(reference)) }
  const requiresBuild = refs.some(ref => descriptor.projects.find(project => project.id === ref.projectId)?.preparedProfileVersion === 2)
  const checks = draft.metadata.requiredChecks ?? []

  const requiredChecks = requiresBuild ? [...new Set([...checks, 'project_build'] as const)]
    : state.buildCheckWasPresent ? checks : checks.filter(check => check !== 'project_build')

  setIntakeDraft(key, { ...draft, metadata: { ...draft.metadata, requiredChecks, preparedValidations: refs } })
}

/** A requested but stale/partial mode never degrades silently to legacy admission. */
export function preparedObjectiveSelectionError(key: string, metadata: ObjectiveMetadata, context: PreparedObjectiveContext): 'changed' | 'incomplete' | 'constraints' | 'unsupported' | 'pending' | 'versions' | undefined {
  const state = $preparedObjectiveSelections.get()[key]

  if (!state?.requested) { return metadata.preparedValidations?.length ? 'changed' : undefined }

  if (state.busy) { return 'pending' }

  if (!preparedObjectiveReviewCurrent(state, context)) { return 'changed' }
  const descriptor = state.review!.descriptor

  if (!descriptor.platform.supported || preparedObjectiveBlockingReasons(descriptor).length) { return 'unsupported' }
  const refs = metadata.preparedValidations ?? []
  const projects = metadata.projectIds ?? []

  if (!projects.length || projects.length > 8 || refs.length !== projects.length || new Set(projects).size !== projects.length ||
    new Set(refs.map(ref => ref.projectId)).size !== refs.length ||
    !refs.every(ref => projects.includes(ref.projectId) && preparedObjectiveProjectMatches(descriptor, ref.projectId, context.snapshot) && descriptor.registrationRefs.some(available => policyEqual(available, ref)))) { return 'incomplete' }

  const versions = new Set(refs.map(ref => descriptor.projects.find(project => project.id === ref.projectId)?.preparedProfileVersion ?? 1))

  if (versions.size !== 1) { return 'versions' }

  if (metadata.deliveryMode !== 'managed_artifact' || !metadata.requiredChecks?.includes('project_typecheck') ||
    !metadata.requiredChecks.includes('project_tests') || metadata.requiredChecks.includes('source_integration') ||
    metadata.requiredChecks.includes('project_build') !== versions.has(2)) { return 'constraints' }
}
