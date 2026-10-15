import type {
  IntakePriority,
  RequestIntakeIntent,
  RequestIntakeRecord,
  RequestIntakeScope,
  RequestIntakeView
} from './organization-request-intake-types'

const EMPTY: RequestIntakeView = {
  text: '',
  answer: '',
  record: null,
  intent: null,
  busy: false,
  error: null,
  history: { items: [], busy: false, error: false }
}

const priorities = ['P1', 'P2', 'P3', 'P4', 'P5']

const statuses = [
  'pending',
  'proposed',
  'admitted',
  'linked',
  'needs_answer',
  'answered',
  'needs_review',
  'failed',
  'uncertain'
]

const text = (value: unknown): value is string => typeof value === 'string' && value.length > 0
const nullableText = (value: unknown) => value === null || text(value)
const texts = (value: unknown) => Array.isArray(value) && value.every(text)
const count = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0

/** Rendering an admission requires a scoped, matching receipt, never a transport success alone. */
function receipt(value: unknown, profile: string): RequestIntakeRecord {
  const row = value as RequestIntakeRecord
  const usage = row?.usage

  if (
    !row ||
    row.profile !== profile ||
    !text(row.id) ||
    !text(row.idempotencyKey) ||
    !text(row.request) ||
    !text(row.snapshotId) ||
    !statuses.includes(row.status) ||
    typeof row.dispatched !== 'boolean' ||
    !nullableText(row.parentIntakeId) ||
    !nullableText(row.objectiveId) ||
    !nullableText(row.reason) ||
    !usage ||
    !nullableText(usage.provider) ||
    !nullableText(usage.model) ||
    !count(usage.reservedTokens) ||
    !(usage.inputTokens === null || count(usage.inputTokens)) ||
    !(usage.outputTokens === null || count(usage.outputTokens)) ||
    !(
      usage.reservedCostUsd === null ||
      (typeof usage.reservedCostUsd === 'string' && /^\d+(\.\d+)?$/.test(usage.reservedCostUsd))
    )
  ) {
    throw new Error('Invalid intake receipt')
  }

  const proposal = row.proposal

  if (
    proposal !== null &&
    (!proposal ||
      proposal.snapshotId !== row.snapshotId ||
      !nullableText(proposal.routeId) ||
      !nullableText(proposal.executiveId) ||
      !nullableText(proposal.managerId) ||
      !priorities.includes(proposal.inferredPriority) ||
      !priorities.includes(proposal.effectivePriority) ||
      typeof proposal.ownerOverride !== 'boolean' ||
      !text(proposal.rationale) ||
      !['source_project', 'managed_artifact'].includes(proposal.deliveryMode) ||
      !['proposed', 'needs_answer'].includes(proposal.status) ||
      ![
        proposal.projectIds,
        proposal.evidenceIds,
        proposal.acceptanceCriteria,
        proposal.assumptions,
        proposal.duplicateCandidateIds,
        proposal.dependencyCandidateIds
      ].every(texts) ||
      !(
        proposal.question === null ||
        (proposal.question &&
          text(proposal.question.text) &&
          text(proposal.question.neededFor) &&
          texts(proposal.question.checkedEvidenceIds))
      ))
  ) {
    throw new Error('Invalid intake proposal')
  }

  if (
    (['admitted', 'linked'].includes(row.status) && (!row.objectiveId || !proposal)) ||
    (!['admitted', 'linked'].includes(row.status) && row.objectiveId !== null) ||
    (['proposed', 'needs_answer', 'answered', 'needs_review'].includes(row.status) && !proposal) ||
    (row.status === 'needs_answer' && !proposal?.question)
  ) {
    throw new Error('Inconsistent intake state')
  }

  return row
}

/** Window-local owner drafts survive navigation; no sensitive request text is persisted to browser storage. */
export function createRequestIntakeClient(
  source: () => RequestIntakeScope,
  newKey: () => string = () => crypto.randomUUID()
) {
  const entries = new Map<string, { key: string; view: RequestIntakeView; sequence: number }>()
  const listeners = new Set<() => void>()
  const lists = new Map<string, number>()
  let nextList = 0
  let lastScope = ''
  let epoch = 0

  const scopeNow = () => {
    const scope = source()
    const identity = JSON.stringify([scope.key, scope.ownerKey, scope.profile, scope.eligible])

    if (lastScope !== identity) {
      lastScope = identity
      epoch++
    }

    return { ...scope, key: `${scope.key}:${epoch}`, ownerKey: JSON.stringify([scope.ownerKey, scope.profile]) }
  }

  const entryFor = (scope: RequestIntakeScope) => {
    let entry = entries.get(scope.ownerKey)

    if (entry && entry.key !== scope.key) {
      // A socket swap retires callbacks, but never forgets an uncertain owner action.
      entry = {
        key: scope.key,
        sequence: entry.sequence + 1,
        view: {
          ...entry.view,
          busy: false,
          error: entry.view.intent ? 'uncertainTransport' : entry.view.error,
          history: EMPTY.history
        }
      }
      entries.set(scope.ownerKey, entry)
    }

    return entry
  }

  const publish = (scope: RequestIntakeScope, view: RequestIntakeView, sequence: number, preserveHistory = true) => {
    const history = preserveHistory ? (entries.get(scope.ownerKey)?.view.history ?? EMPTY.history) : view.history

    const items =
      preserveHistory && view.record
        ? [view.record, ...history.items.filter(row => row.id !== view.record!.id)].slice(0, 20)
        : history.items

    entries.set(scope.ownerKey, { key: scope.key, view: { ...view, history: { ...history, items } }, sequence })
    listeners.forEach(listener => listener())
  }

  const current = (scope: RequestIntakeScope, sequence: number) => {
    const now = scopeNow()

    return (
      now.eligible &&
      now.key === scope.key &&
      now.ownerKey === scope.ownerKey &&
      entries.get(scope.ownerKey)?.sequence === sequence
    )
  }

  const mutate = async (
    scope: RequestIntakeScope,
    view: RequestIntakeView,
    intent: RequestIntakeIntent,
    sequence: number
  ) => {
    publish(scope, { ...view, intent, busy: true, error: null }, sequence)

    try {
      // The dedicated authenticated socket owns its profile. Never retarget through a reconnect helper.
      const result = receipt(
        await scope.request(intent.intakeId ? 'organization.intake.answer' : 'organization.intake.submit', {
          ...intent
        }),
        scope.profile
      )

      if (!current(scope, sequence)) {
        return
      }

      if (
        result.idempotencyKey !== intent.idempotencyKey ||
        result.parentIntakeId !== (intent.intakeId ?? null) ||
        (!intent.intakeId && result.request !== intent.text)
      ) {
        throw new Error('Mismatched intake receipt')
      }

      publish(
        scope,
        {
          ...view,
          text: intent.intakeId ? view.text : '',
          record: result,
          intent: null,
          answer: '',
          busy: false,
          error: null
        },
        sequence
      )
    } catch (error) {
      if (!current(scope, sequence)) {
        return
      }

      const code = (error as { code?: number } | null)?.code
      // Only method-not-found proves no action occurred. Other errors may follow a committed intake.
      const unsupported = code === -32601 && !view.intent
      publish(
        scope,
        {
          ...view,
          intent: unsupported ? null : intent,
          busy: false,
          error: unsupported ? 'unsupported' : 'uncertainTransport'
        },
        sequence
      )
    }
  }

  return {
    subscribe(listener: () => void) {
      listeners.add(listener)

      return () => {
        listeners.delete(listener)
      }
    },
    getSnapshot(): RequestIntakeView {
      const scope = scopeNow()

      return scope.eligible ? (entryFor(scope)?.view ?? EMPTY) : EMPTY
    },
    async list() {
      const scope = scopeNow()

      if (!scope.eligible) {
        return
      }

      const entry = entryFor(scope)
      const sequence = ++nextList
      lists.set(scope.ownerKey, sequence)
      const view = entry?.view ?? EMPTY
      publish(scope, { ...view, history: { ...view.history, busy: true, error: false } }, entry?.sequence ?? 0, false)

      try {
        const result = await scope.request<{ profile: string; items: unknown[] }>('organization.intake.list', {})
        const now = scopeNow()

        if (
          !now.eligible ||
          now.key !== scope.key ||
          now.ownerKey !== scope.ownerKey ||
          lists.get(scope.ownerKey) !== sequence
        ) {
          return
        }

        if (!result || result.profile !== scope.profile || !Array.isArray(result.items) || result.items.length > 20) {
          throw new Error('Invalid intake history')
        }

        const items = result.items.map(item => receipt(item, scope.profile))

        if (new Set(items.map(item => item.id)).size !== items.length) {
          throw new Error('Duplicate intake history')
        }

        const latest = entries.get(scope.ownerKey)!
        // History never overwrites an active form, selected receipt, or uncertain action.
        publish(scope, { ...latest.view, history: { items, busy: false, error: false } }, latest.sequence, false)
      } catch {
        const now = scopeNow()
        const latest = entries.get(scope.ownerKey)

        if (
          now.eligible &&
          now.key === scope.key &&
          now.ownerKey === scope.ownerKey &&
          lists.get(scope.ownerKey) === sequence &&
          latest
        ) {
          publish(
            scope,
            { ...latest.view, history: { ...latest.view.history, busy: false, error: true } },
            latest.sequence,
            false
          )
        }
      }
    },
    select(intakeId: string) {
      const scope = scopeNow()
      const entry = entryFor(scope)
      const view = entry?.view
      const selected = view?.history.items.find(row => row.id === intakeId)

      if (!scope.eligible || !view || !selected || view.busy || view.intent || view.answer.trim()) {
        return false
      }

      publish(scope, { ...view, record: selected, answer: '', error: null }, entry!.sequence + 1)

      return true
    },
    edit(fields: { text?: string; answer?: string; ownerPriority?: IntakePriority; forceSeparate?: boolean }) {
      const scope = scopeNow()
      const entry = entryFor(scope)
      const view = entry?.view ?? EMPTY

      if (!scope.eligible || view.busy || view.intent || (view.record && fields.answer === undefined)) {
        return
      }

      publish(scope, { ...view, ...fields, error: null }, (entry?.sequence ?? 0) + 1)
    },
    async submit() {
      const scope = scopeNow()
      const entry = entryFor(scope)
      const view = entry?.view ?? EMPTY

      if (!scope.eligible || view.busy || view.intent || view.record || !view.text.trim()) {
        return
      }

      if (view.text.trim().length > 12000 || (view.ownerPriority && !priorities.includes(view.ownerPriority))) {
        publish(scope, { ...view, error: 'invalid' }, (entry?.sequence ?? 0) + 1)

        return
      }

      await mutate(
        scope,
        view,
        {
          text: view.text.trim(),
          idempotencyKey: newKey(),
          ...(view.ownerPriority ? { ownerPriority: view.ownerPriority } : {}),
          ...(view.forceSeparate ? { forceSeparate: true } : {})
        },
        (entry?.sequence ?? 0) + 1
      )
    },
    async answer() {
      const scope = scopeNow()
      const entry = entryFor(scope)
      const view = entry?.view

      if (
        !scope.eligible ||
        !view ||
        view.busy ||
        view.intent ||
        view.record?.status !== 'needs_answer' ||
        !view.answer.trim()
      ) {
        return
      }

      if (view.answer.trim().length > 12000) {
        publish(scope, { ...view, error: 'invalid' }, entry!.sequence + 1)

        return
      }

      await mutate(
        scope,
        view,
        { text: view.answer.trim(), intakeId: view.record.id, idempotencyKey: newKey() },
        entry!.sequence + 1
      )
    },
    async retry() {
      const scope = scopeNow()
      const entry = entryFor(scope)

      if (!scope.eligible || !entry?.view.intent || entry.view.busy) {
        return
      }

      await mutate(scope, entry.view, entry.view.intent, entry.sequence + 1)
    },
    async read() {
      const scope = scopeNow()
      const entry = entryFor(scope)
      const view = entry?.view
      const previous = view?.record

      if (!scope.eligible || !view || !previous || view.busy || view.intent) {
        return
      }

      const sequence = entry!.sequence + 1
      publish(scope, { ...view, busy: true, error: null }, sequence)

      try {
        const result = receipt(
          await scope.request('organization.intake.read', { intakeId: previous.id }),
          scope.profile
        )

        if (!current(scope, sequence)) {
          return
        }

        if (
          result.id !== previous.id ||
          result.idempotencyKey !== previous.idempotencyKey ||
          result.request !== previous.request ||
          result.parentIntakeId !== previous.parentIntakeId ||
          result.snapshotId !== previous.snapshotId
        ) {
          throw new Error('Mismatched intake read')
        }

        publish(scope, { ...view, record: result, busy: false, error: null }, sequence)
      } catch {
        if (current(scope, sequence)) {
          publish(scope, { ...view, busy: false, error: 'readFailed' }, sequence)
        }
      }
    },
    newRequest() {
      const scope = scopeNow()
      const entry = entryFor(scope)

      if (
        !scope.eligible ||
        !entry ||
        entry.view.busy ||
        entry.view.intent ||
        !entry.view.record ||
        Boolean(entry.view.answer.trim())
      ) {
        return
      }

      publish(scope, { ...EMPTY, text: entry.view.text, ownerPriority: entry.view.ownerPriority }, entry.sequence + 1)
    }
  }
}

export type RequestIntakeClient = ReturnType<typeof createRequestIntakeClient>
