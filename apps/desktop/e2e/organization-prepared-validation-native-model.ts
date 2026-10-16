/** Synthetic owner RPC records only. No filesystem, provider, Docker, or execution work. */
import { createHash } from 'node:crypto'

export const PREPARED_NATIVE_ORGANIZATION_ID = '11111111-1111-4111-8111-111111111111'
export const PREPARED_NATIVE_OWNER_ID = '22222222-2222-4222-8222-000000000001'
export const PREPARED_NATIVE_TITLE = 'Synthetic explicitly prepared validation objective'
export const PREPARED_NATIVE_CREATED_AT = '2026-10-16T00:00:00Z'
export const PREPARED_NATIVE_ERRORS = {
  staleRegistration: 'Synthetic prepared registration is stale or does not exactly cover both projects.',
  intent: 'Synthetic admission requires managed-artifact delivery and exactly TypeScript and project tests.',
  identity: 'Synthetic organization or owner identity changed.',
  idempotencyConflict: 'Synthetic idempotency conflict: the original key belongs to a different payload.',
  lostCreateReply: 'Synthetic acknowledgement loss after admission. Retry the unchanged objective.',
  invalid: 'Synthetic objective payload is invalid.',
  unexpected: 'Unexpected organization method is forbidden by the synthetic prepared-validation fixture.'
} as const

export interface PreparedNativeReference {
  projectId: string
  revision: number
  registrationSha256: string
}

export type PreparedNativeReply = { result: unknown } | { error: { code: number; message: string } }

const bindings = [
  { id: 'frontend', root: 'root0', recipe: null, team: 'general' },
  { id: 'backend', root: 'root1', recipe: null, team: 'general' }
]

const hash = (text: string) => createHash('sha256').update(text).digest('hex')
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)

const canonical = (value: unknown): string => JSON.stringify(value, (_key, item: unknown) =>
  record(item) ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item)

const fail = (message: string, code = 5076): PreparedNativeReply => ({ error: { code, message } })

function scalarStatus(objectiveId: string, references: PreparedNativeReference[]) {
  const history = references.map(reference => {
    const setId = `${objectiveId}-${reference.projectId}-set`

    return { setId, requestId: `${setId}-request`, projectId: reference.projectId, round: 0, generation: 1,
      reservedUnits: 2, snapshotSha256: hash(`synthetic-snapshot:${setId}`),
      steps: ['workspace_typescript', 'workspace_vitest_ui'].map((recipe, index) => ({
        runId: `${setId}-run-${index}`, stepId: `${setId}-step-${index}`, recipe, reservedUnits: 1,
        dispatched: true, resourceClaimed: true, resultClaimStatus: 'passed', cleanupClaimSettled: true,
        latestCleanupSequence: 1, latestRuntimeEventSequence: 2, latestRuntimeEventKind: 'receipt',
        blockerReasons: ['runtime_receipt_unverified']
      })), aggregateClaimPresent: true, reviewClaimPresent: true, pairReviewRecorded: true, state: 'runtime_unverified' }
  })

  return { objectiveId, storageState: 'present', legacyUnits: 0, preparedUnits: 4, totalUnits: 4, totalSets: 2,
    totalHistory: 2, current: Object.fromEntries(history.map(set => [set.projectId, set])), history, historyTruncated: false,
    blockers: { blocked: true, totalBlockers: 4, entries: history.flatMap(set => set.steps.map(step => ({
      setId: set.setId, projectId: set.projectId, generation: set.generation, runId: step.runId, reasons: step.blockerReasons
    }))), truncated: false }, scalarMetadataOnly: true, accountingVerified: false, sourceBytesVerified: false,
    runtimeReceiptsVerified: false, executionAuthorized: false, acceptanceAuthorized: false, sourceDeliveryAuthorized: false }
}

export function createPreparedNativeModel() {
  const methods: string[] = []
  const creates: Record<string, unknown>[] = []
  const unexpectedMethods: string[] = []
  let ownerGeneration = 1
  let ownerId = PREPARED_NATIVE_OWNER_ID
  let sidecarRevision = 1
  let loseReply = false
  const revisions = new Map(bindings.map(project => [project.id, 1]))
  const receipts = new Map<string, { ownerId: string; payload: string; result: { objective: Record<string, unknown>; snapshot: unknown } }>()

  const currentReferences = (): PreparedNativeReference[] => bindings.map(project => ({ projectId: project.id,
    revision: revisions.get(project.id)!, registrationSha256: hash(`synthetic-registration:${ownerId}:${project.id}:${revisions.get(project.id)}`)
  })).sort((left, right) => left.projectId.localeCompare(right.projectId))

  const outcomes = () => ({ generation: 'synthetic-prepared-outcomes', items: [], unread: 0, total: 0, hasMore: false, nextCursor: null })

  const agents = () => [
    { id: 'executive', name: 'Synthetic Executive', role: 'Executive', capabilities: ['request.accept', 'request.decompose'] },
    { id: 'manager', name: 'Synthetic Manager', role: 'Manager', managerId: 'executive', capabilities: ['request.plan', 'request.integrate'] }
  ].map(agent => ({ ...agent, identityId: `synthetic-${agent.id}`, persistent: true, lifecycle: 'active', team: 'general',
    status: 'idle', responsibilities: [], tools: [], model: null, provider: null,
    summary: 'Synthetic owner-frame identity. No provider calls or work are running.' }))

  const snapshot = () => ({ source: 'runtime', objectives: [...receipts.values()].map(receipt => receipt.result.objective),
    agents: agents(), tasks: [], requests: [], activity: [], knowledge: [], conversations: [], outcomes: outcomes(),
    runtime: { state: 'idle', profile: 'default', capabilities: [], maxWorkers: 1, maxInflight: 1, rosterCount: 2, workingCount: 0,
      scope: 'Synthetic owner-frame records only. No provider calls, validation execution, or source delivery.',
      availableProjects: structuredClone(bindings), setup: { version: 1,
        provider: { status: 'unchecked', blockers: [], inheritedMembers: 2, overriddenMembers: 0 }, backgroundOptIn: false } } })

  const metadata = () => ({ capabilityVersion: 1, state: 'ready', scope: { kind: 'served-profile', profile: 'default' },
    metadata: { organizationId: PREPARED_NATIVE_ORGANIZATION_ID, displayName: 'Synthetic Prepared Studio',
      owner: { ownerId, displayName: 'Synthetic Owner' }, revision: ownerGeneration,
      createdAt: PREPARED_NATIVE_CREATED_AT, updatedAt: PREPARED_NATIVE_CREATED_AT } })

  const descriptor = () => {
    const references = currentReferences()

    return { capabilityVersion: 1, organizationId: PREPARED_NATIVE_ORGANIZATION_ID, ownerId, policyGeneration: ownerGeneration,
      sourceRevision: hash(`synthetic-source:${ownerGeneration}`), sidecarRevision,
      projects: bindings.map(project => {
        const reference = references.find(item => item.projectId === project.id)!

        return { ...project, enabled: true, rootRevoked: false, registrationRevision: reference.revision, registrationHash: reference.registrationSha256 }
      }), registrationRefs: references, blockers: [], scope: { kind: 'served-profile', profile: 'default' },
      platform: { supported: true, system: 'linux', architecture: 'x86_64', backend: 'linux-docker-pinned', runtimeReadiness: 'not_probed' } }
  }

  function create(params: Record<string, unknown>): PreparedNativeReply {
    creates.push(structuredClone(params))
    const allowed = ['title', 'projectIds', 'executiveId', 'managerId', 'description', 'priority', 'acceptanceCriteria', 'deliveryMode', 'requiredChecks', 'preparedValidations', 'idempotencyKey']
    const key = params.idempotencyKey

    if (Object.keys(params).some(field => !allowed.includes(field)) || typeof key !== 'string' || !key.length || key.length > 128 ||
      typeof params.title !== 'string' || !params.title.trim() || params.title !== params.title.trim() ||
      params.executiveId !== 'executive' || params.managerId !== 'manager') { return fail(PREPARED_NATIVE_ERRORS.invalid) }

    const payload = canonical(params)
    const previous = receipts.get(key)

    if (previous) {
      if (previous.ownerId !== ownerId) { return fail(PREPARED_NATIVE_ERRORS.identity) }

      if (previous.payload !== payload) { return fail(PREPARED_NATIVE_ERRORS.idempotencyConflict) }

      return { result: structuredClone(previous.result) }
    }

    if (params.deliveryMode !== 'managed_artifact' || !Array.isArray(params.requiredChecks) ||
      canonical([...params.requiredChecks].sort()) !== canonical(['project_tests', 'project_typecheck'])) { return fail(PREPARED_NATIVE_ERRORS.intent) }

    const references = params.preparedValidations
    const projectIds = params.projectIds
    const expected = currentReferences()

    if (!Array.isArray(projectIds) || canonical([...projectIds].sort()) !== canonical(expected.map(reference => reference.projectId)) ||
      !Array.isArray(references) || references.some(reference => !record(reference) || typeof reference.projectId !== 'string') ||
      canonical([...references].sort((left, right) => left.projectId.localeCompare(right.projectId))) !== canonical(expected)) {
      return fail(PREPARED_NATIVE_ERRORS.staleRegistration)
    }

    const id = `synthetic-prepared-objective-${receipts.size + 1}`

    const objective = { id, title: params.title, description: typeof params.description === 'string' ? params.description : 'Synthetic prepared-validation owner-frame proof.',
      source: 'runtime', status: 'active', createdAt: PREPARED_NATIVE_CREATED_AT, ownerId: 'executive', executiveId: 'executive', managerId: 'manager',
      priority: params.priority ?? 'P3', priorityRevision: 0, deliveryMode: 'managed_artifact', requiredChecks: structuredClone(params.requiredChecks),
      projects: structuredClone(bindings), preparedValidations: structuredClone(references), projectExecution: null,
      preparedValidation: scalarStatus(id, expected), phase: 'Synthetic recorded claims, unverified', progress: 0,
      dispatchControl: { paused: true, revision: 1, runningCount: 0 } }

    const result = { objective, snapshot: {} as unknown }
    receipts.set(key, { ownerId, payload, result })
    result.snapshot = structuredClone(snapshot())

    if (loseReply) {
      loseReply = false

      return fail(PREPARED_NATIVE_ERRORS.lostCreateReply)
    }

    return { result: structuredClone(result) }
  }

  function teamSetup(params: Record<string, unknown>): PreparedNativeReply {
    if (Object.keys(params).length !== 1 || !Object.hasOwn(params, 'expectedOrganizationId')) {
      return fail(PREPARED_NATIVE_ERRORS.invalid, -32602)
    }

    if (params.expectedOrganizationId !== PREPARED_NATIVE_ORGANIZATION_ID) { return fail(PREPARED_NATIVE_ERRORS.identity) }

    // Home reads availability even with a roster. This fixture represents an
    // already configured team; the read neither generates nor starts one.
    return { result: { capabilityVersion: 1, state: 'configured', organizationId: PREPARED_NATIVE_ORGANIZATION_ID,
      sourceRevision: hash(`synthetic-team:${ownerId}`), presets: [],
      supportAgents: agents().filter(agent => agent.id === 'manager').map(({ id, name, role }) => ({ id, name, role })),
      readiness: { status: 'unchecked', blockers: [], modelRouting: 'unchanged', backgroundEnabled: false },
      scope: { kind: 'served-profile', profile: 'default' } } }
  }

  function ownerPolicy(params: Record<string, unknown>): PreparedNativeReply {
    const expectedId = params.expectedOrganizationId

    if (Object.keys(params).length !== 1 || typeof expectedId !== 'string' ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(expectedId)) {
      return fail(PREPARED_NATIVE_ERRORS.invalid, -32602)
    }

    if (expectedId !== PREPARED_NATIVE_ORGANIZATION_ID) { return fail(PREPARED_NATIVE_ERRORS.identity, 5074) }

    // The real detail view reads configured policy after create. These finite
    // synthetic settings neither configure a provider nor permit policy writes.
    return { result: { capabilityVersion: 1, state: 'ready', organizationId: PREPARED_NATIVE_ORGANIZATION_ID,
      ownerId, policyGeneration: ownerGeneration, sourceRevision: hash(`synthetic-source:${ownerGeneration}`),
      managedFields: [], capabilities: { modelRouting: true, budgetPolicy: true, budgetObservation: true },
      configuration: { model_routes: [], model_assignments: [], model_costs: [],
        max_model_calls: 20, max_total_tokens: 100000, max_cost_usd: null, objective_timeout_seconds: 3600,
        max_context_tokens: 32768, max_output_tokens: 8000, completion_reserve_model_calls: 2,
        completion_reserve_tokens: 8000, completion_reserve_cost_usd: null, adaptive_pacing_enabled: false,
        pacing_conserve_below_percent: 25, pacing_critical_below_percent: 10,
        pacing_conserve_interval_seconds: 30, pacing_critical_interval_seconds: 120,
        pacing_conserve_max_inflight: 2, pacing_critical_max_inflight: 1 },
      agents: agents().map(({ id, name, role }) => ({ id, name, role })).sort((left, right) => left.id.localeCompare(right.id)),
      observations: [...receipts.values()].map(({ result }) => ({ objectiveId: result.objective.id, revision: 0, observation: null })),
      scope: { kind: 'served-profile', profile: 'default' } } }
  }

  const handlers: Record<string, (params: Record<string, unknown>) => PreparedNativeReply> = {
    'organization.descriptor': () => ({ result: metadata() }),
    'organization.snapshot': () => ({ result: snapshot() }),
    'organization.teamSetup': teamSetup,
    'organization.ownerPolicy': ownerPolicy,
    'organization.preparedValidationSetup': params => params.expectedOrganizationId === PREPARED_NATIVE_ORGANIZATION_ID
      ? { result: descriptor() } : fail(PREPARED_NATIVE_ERRORS.identity),
    'organization.create': create,
    'organization.outcomes': () => ({ result: outcomes() })
  }

  return { methods, creates, unexpectedMethods,
    get admittedCount() { return receipts.size },
    currentReferences,
    replaceRegistration(projectId: string) {
      const revision = revisions.get(projectId)

      if (revision === undefined) { throw new Error(`Unknown synthetic project: ${projectId}`) }
      revisions.set(projectId, revision + 1); sidecarRevision++
    },
    replaceOwner() {
      ownerGeneration++
      ownerId = `22222222-2222-4222-8222-${ownerGeneration.toString(16).padStart(12, '0')}`

      for (const [projectId, revision] of revisions) { revisions.set(projectId, revision + 1) }
      sidecarRevision++

      return ownerId
    },
    loseNextCreateReply() { loseReply = true },
    reply(method: string, params: Record<string, unknown> = {}): PreparedNativeReply | null {
      if (!method.startsWith('organization.')) { return null }
      methods.push(method)
      const handler = handlers[method]

      if (!handler) {
        unexpectedMethods.push(method)

        return fail(PREPARED_NATIVE_ERRORS.unexpected, -32601)
      }

      return structuredClone(handler(structuredClone(params)))
    }
  }
}
