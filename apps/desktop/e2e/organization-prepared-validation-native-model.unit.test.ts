import { describe, expect, it } from 'vitest'

import { createPreparedNativeModel, PREPARED_NATIVE_ERRORS, PREPARED_NATIVE_ORGANIZATION_ID,
  PREPARED_NATIVE_OWNER_ID, PREPARED_NATIVE_TITLE, type PreparedNativeReference } from './organization-prepared-validation-native-model'

type Model = ReturnType<typeof createPreparedNativeModel>

const read = <T = Record<string, unknown>>(model: Model, method: string, params = {}): T => {
  const reply = model.reply(method, params)
  expect(reply).not.toBeNull()
  expect(reply).toHaveProperty('result')

  return (reply as { result: T }).result
}

const payload = (model: Model, idempotencyKey = 'synthetic-create-1'): Record<string, unknown> => ({
  title: PREPARED_NATIVE_TITLE, description: 'Synthetic owner supplied context.', priority: 'P3',
  executiveId: 'executive', managerId: 'manager', projectIds: ['frontend', 'backend'],
  requiredChecks: ['project_typecheck', 'project_tests'], deliveryMode: 'managed_artifact',
  preparedValidations: model.currentReferences(), idempotencyKey
})

const create = (model: Model, params = payload(model)) => read<{ objective: Record<string, unknown>; snapshot: { objectives: Record<string, unknown>[] } }>(model, 'organization.create', params)
interface Descriptor {
  organizationId: string
  ownerId: string
  policyGeneration: number
  sourceRevision: string
  sidecarRevision: number
  registrationRefs: PreparedNativeReference[]
  projects: Array<Record<string, unknown>>
  scope: { kind: string; profile: string }
  platform: Record<string, unknown>
}
const descriptor = (model: Model) => read<Descriptor>(model, 'organization.preparedValidationSetup', { expectedOrganizationId: PREPARED_NATIVE_ORGANIZATION_ID })

describe('synthetic prepared-validation native RPC model', () => {
  it('starts with deterministic UUID metadata, two null-recipe bindings, and no admitted work', () => {
    const model = createPreparedNativeModel()
    const metadata = read<{ state: string; metadata: { organizationId: string; owner: { ownerId: string }; revision: number } }>(model, 'organization.descriptor')
    const setup = descriptor(model)

    const snapshot = read<{ runtime: { availableProjects: Record<string, unknown>[]; setup: { backgroundOptIn: boolean } }; agents: Record<string, unknown>[];
      objectives: unknown[]; tasks: unknown[]; requests: unknown[] }>(model, 'organization.snapshot')

    const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
    expect(metadata.state).toBe('ready')
    expect(metadata.metadata.organizationId).toMatch(uuid)
    expect(metadata.metadata.owner.ownerId).toMatch(uuid)
    expect(metadata.metadata.owner.ownerId).not.toBe(metadata.metadata.organizationId)
    expect(setup).toMatchObject({ organizationId: metadata.metadata.organizationId, ownerId: metadata.metadata.owner.ownerId,
      scope: { kind: 'served-profile', profile: 'default' }, platform: { supported: true, system: 'linux', architecture: 'x86_64',
        backend: 'linux-docker-pinned', runtimeReadiness: 'not_probed' } })
    expect(setup.registrationRefs.map(reference => reference.projectId)).toEqual(['backend', 'frontend'])
    expect(snapshot.runtime.availableProjects).toEqual([
      { id: 'frontend', root: 'root0', recipe: null, team: 'general' },
      { id: 'backend', root: 'root1', recipe: null, team: 'general' }
    ])
    expect(snapshot.agents).toMatchObject([
      { id: 'executive', persistent: true, lifecycle: 'active', capabilities: ['request.accept', 'request.decompose'] },
      { id: 'manager', persistent: true, lifecycle: 'active', managerId: 'executive', capabilities: ['request.plan', 'request.integrate'] }
    ])
    expect(snapshot.runtime.setup.backgroundOptIn).toBe(false)
    expect([snapshot.objectives, snapshot.tasks, snapshot.requests]).toEqual([[], [], []])
    expect(model.admittedCount).toBe(0)
    expect(model.creates).toEqual([])
    expect(descriptor(createPreparedNativeModel())).toEqual(setup)
  })

  it('admits exactly the selected metadata while retaining project order and canonical references', () => {
    const model = createPreparedNativeModel()
    const params = payload(model)
    const result = create(model, params)
    expect(model.creates).toEqual([params])
    expect(model.creates[0].projectIds).toEqual(['frontend', 'backend'])
    expect(model.creates[0].preparedValidations).toEqual(model.currentReferences())
    expect(result.objective).toMatchObject({ title: params.title, description: params.description, status: 'active', source: 'runtime',
      deliveryMode: 'managed_artifact', requiredChecks: params.requiredChecks, preparedValidations: params.preparedValidations,
      projectExecution: null, dispatchControl: { paused: true, runningCount: 0 } })
    expect(result.snapshot.objectives).toEqual([result.objective])
    expect(model.admittedCount).toBe(1)
    expect(model.unexpectedMethods).toEqual([])
  })

  it('records two complete scalar sets and four units without verifying or authorizing any claim', () => {
    const model = createPreparedNativeModel()
    const result = create(model)

    const status = result.objective.preparedValidation as {
      current: Record<string, { setId: string; projectId: string; generation: number; steps: Array<Record<string, unknown>> }>
      history: Array<{ setId: string; projectId: string; generation: number; steps: Array<Record<string, unknown>> }>
      blockers: { entries: unknown[] }
    }

    expect(status).toMatchObject({ objectiveId: result.objective.id, storageState: 'present', legacyUnits: 0, preparedUnits: 4, totalUnits: 4,
      totalSets: 2, totalHistory: 2, historyTruncated: false, scalarMetadataOnly: true, accountingVerified: false,
      sourceBytesVerified: false, runtimeReceiptsVerified: false, executionAuthorized: false, acceptanceAuthorized: false, sourceDeliveryAuthorized: false,
      blockers: { blocked: true, totalBlockers: 4, truncated: false } })
    expect(Object.keys(status.current)).toEqual(['backend', 'frontend'])
    expect(status.history).toHaveLength(2)
    const runs = new Set<unknown>()
    const steps = new Set<unknown>()

    for (const set of status.history) {
      expect(status.current[set.projectId]).toEqual(set)
      expect(set).toMatchObject({ reservedUnits: 2, state: 'runtime_unverified', aggregateClaimPresent: true, reviewClaimPresent: true, pairReviewRecorded: true })
      expect(set.steps.map(step => step.recipe)).toEqual(['workspace_typescript', 'workspace_vitest_ui'])

      for (const step of set.steps) {
        expect(step).toMatchObject({ reservedUnits: 1, dispatched: true, resourceClaimed: true, resultClaimStatus: 'passed', cleanupClaimSettled: true,
          latestCleanupSequence: 1, latestRuntimeEventSequence: 2, latestRuntimeEventKind: 'receipt', blockerReasons: ['runtime_receipt_unverified'] })
        expect(step.runId).not.toBe(set.setId)
        runs.add(step.runId); steps.add(step.stepId)
      }
    }

    expect(runs.size).toBe(4)
    expect(steps.size).toBe(4)
    expect(status.blockers.entries).toEqual(status.history.flatMap(set => set.steps.map(step => ({ setId: set.setId,
      projectId: set.projectId, generation: set.generation, runId: step.runId, reasons: step.blockerReasons }))))
  })

  it('replays one committed synthetic receipt after one lost acknowledgement and rejects changed intent', () => {
    const model = createPreparedNativeModel()
    const params = payload(model)
    model.loseNextCreateReply()
    expect(model.reply('organization.create', params)).toEqual({ error: { code: 5076, message: PREPARED_NATIVE_ERRORS.lostCreateReply } })
    expect(model.admittedCount).toBe(1)
    const replay = create(model, params)
    expect(create(model, structuredClone(params))).toEqual(replay)
    expect(model.creates).toEqual([params, params, params])
    expect(model.reply('organization.create', { ...params, title: `${PREPARED_NATIVE_TITLE} changed` })).toEqual({
      error: { code: 5076, message: PREPARED_NATIVE_ERRORS.idempotencyConflict }
    })
    expect(model.admittedCount).toBe(1)
    model.replaceRegistration('frontend')
    expect(create(model, params)).toEqual(replay)
    expect(create(model, payload(model, 'synthetic-create-2')).objective.id).not.toBe(replay.objective.id)
    expect(model.admittedCount).toBe(2)
  })

  it('replaces one registration without changing another and rejects unadmitted stale references', () => {
    const model = createPreparedNativeModel()
    const old = descriptor(model)
    const params = payload(model)
    model.replaceRegistration('frontend')
    const next = descriptor(model)
    expect(next.registrationRefs.find(reference => reference.projectId === 'backend')).toEqual(old.registrationRefs.find(reference => reference.projectId === 'backend'))
    expect(next.registrationRefs.find(reference => reference.projectId === 'frontend')).not.toEqual(old.registrationRefs.find(reference => reference.projectId === 'frontend'))
    expect(next.sidecarRevision).toBe(old.sidecarRevision + 1)
    expect(model.reply('organization.create', params)).toEqual({ error: { code: 5076, message: PREPARED_NATIVE_ERRORS.staleRegistration } })
    expect(model.admittedCount).toBe(0)
    create(model, payload(model))
    expect(model.admittedCount).toBe(1)
    expect(() => model.replaceRegistration('missing')).toThrow('Unknown synthetic project')
  })

  it('rotates owner metadata and reference identities, blocking old-owner receipts and stale reads', () => {
    const model = createPreparedNativeModel()
    const original = descriptor(model)
    const oldParams = payload(model)
    const originalObjective = create(model, oldParams).objective
    const ownerId = model.replaceOwner()
    const next = descriptor(model)
    expect(ownerId).not.toBe(PREPARED_NATIVE_OWNER_ID)
    expect(next.ownerId).toBe(ownerId)
    expect(next.policyGeneration).toBe(original.policyGeneration + 1)
    expect(next.sourceRevision).not.toBe(original.sourceRevision)
    expect(next.registrationRefs.every(reference => !original.registrationRefs.some(previous => previous.registrationSha256 === reference.registrationSha256))).toBe(true)
    expect(read<{ metadata: { owner: { ownerId: string } } }>(model, 'organization.descriptor').metadata.owner.ownerId).toBe(ownerId)
    expect(model.reply('organization.create', oldParams)).toEqual({ error: { code: 5076, message: PREPARED_NATIVE_ERRORS.identity } })
    expect(model.reply('organization.create', { ...oldParams, idempotencyKey: 'synthetic-new-stale-key' })).toEqual({
      error: { code: 5076, message: PREPARED_NATIVE_ERRORS.staleRegistration }
    })
    expect(model.reply('organization.preparedValidationSetup', { expectedOrganizationId: 'another' })).toEqual({
      error: { code: 5076, message: PREPARED_NATIVE_ERRORS.identity }
    })
    expect(read<{ objectives: unknown[] }>(model, 'organization.snapshot').objectives).toEqual([originalObjective])
    expect(model.admittedCount).toBe(1)
  })

  it.each([
    ['partial references', (params: Record<string, unknown>) => { params.preparedValidations = (params.preparedValidations as unknown[]).slice(0, 1) }, 'staleRegistration'],
    ['duplicate project', (params: Record<string, unknown>) => { params.projectIds = ['frontend', 'frontend'] }, 'staleRegistration'],
    ['extra project', (params: Record<string, unknown>) => { params.projectIds = ['frontend', 'backend', 'other'] }, 'staleRegistration'],
    ['tampered hash', (params: Record<string, unknown>) => { (params.preparedValidations as PreparedNativeReference[])[0].registrationSha256 = 'a'.repeat(64) }, 'staleRegistration'],
    ['extra reference authority', (params: Record<string, unknown>) => { Object.assign((params.preparedValidations as unknown[])[0]!, { executionAuthorized: true }) }, 'staleRegistration'],
    ['source delivery', (params: Record<string, unknown>) => { params.deliveryMode = 'source_project' }, 'intent'],
    ['source integration', (params: Record<string, unknown>) => { params.requiredChecks = ['project_typecheck', 'project_tests', 'source_integration'] }, 'intent'],
    ['missing tests', (params: Record<string, unknown>) => { params.requiredChecks = ['project_typecheck'] }, 'intent'],
    ['missing key', (params: Record<string, unknown>) => { delete params.idempotencyKey }, 'invalid'],
    ['unknown command', (params: Record<string, unknown>) => { params.command = 'never execute' }, 'invalid']
  ] as const)('rejects %s without admission', (_name, mutate, errorKey) => {
    const model = createPreparedNativeModel()
    const params = payload(model)
    mutate(params)
    expect(model.reply('organization.create', params)).toEqual({ error: { code: 5076, message: PREPARED_NATIVE_ERRORS[errorKey] } })
    expect(model.admittedCount).toBe(0)
    expect(model.creates).toEqual([params])
  })

  it('deep-copies inbound payloads, audit observations, descriptors, immutable objectives, and replays', () => {
    const model = createPreparedNativeModel()
    const params = payload(model)
    const retained = structuredClone(params)
    const original = create(model, params)

    const expected = structuredClone(original)

    ;(params.preparedValidations as PreparedNativeReference[])[0].revision = 999
    ;(model.creates[0].preparedValidations as PreparedNativeReference[])[0].revision = 777
    ;(original.objective.preparedValidations as PreparedNativeReference[])[0].revision = 555
    const observed = descriptor(model)
    observed.registrationRefs[0].revision = 333
    observed.projects[0].root = 'tampered'
    const references = model.currentReferences()
    references[0].revision = 111
    const snapshot = read<{ objectives: Record<string, unknown>[] }>(model, 'organization.snapshot')
    snapshot.objectives[0].title = 'tampered'
    expect(create(model, retained)).toEqual(expected)
    expect(descriptor(model).projects[0].root).toBe('root0')
    expect(model.currentReferences().every(reference => reference.revision === 1)).toBe(true)
    expect(model.admittedCount).toBe(1)
  })

  it('forwards only non-organization messages and explicitly rejects unknown organization mutations', () => {
    const model = createPreparedNativeModel()
    expect(model.reply('setup.status')).toBeNull()

    for (const method of ['organization.saveMetadata', 'organization.previewPreparedValidation', 'organization.savePreparedValidation',
      'organization.revokePreparedValidation', 'organization.configure', 'organization.setPaused', 'organization.unrecognizedRead']) {
      expect(model.reply(method, { synthetic: true })).toEqual({ error: { code: -32601, message: PREPARED_NATIVE_ERRORS.unexpected } })
    }

    expect(model.methods).toEqual(model.unexpectedMethods)
    expect(model.unexpectedMethods).toHaveLength(7)
    expect(model.admittedCount).toBe(0)
    expect(read(model, 'organization.outcomes')).toMatchObject({ items: [], unread: 0, total: 0, hasMore: false, nextCursor: null })
  })
})
