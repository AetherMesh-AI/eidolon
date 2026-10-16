import { replacementIntent, validatePreparation, validateReplacementStatus, validateReplacementTargets, validateRetainedReplacement } from './organization-credential-handoff-contract'
import type { HandoffError, HandoffScope, HandoffView, ReplacementTarget, RetainedReplacement } from './organization-credential-handoff-types'
import type { CredentialEntry } from './organization-credentials-types'

const EMPTY: HandoffView = { entry: null, targets: null, retained: null, status: null, busy: false, error: 'unavailable' }
const eligible = (scope: HandoffScope) => scope.eligible && Boolean(scope.organizationId && scope.ownerId)
const sameIntent = (left: RetainedReplacement, right: RetainedReplacement) => JSON.stringify(replacementIntent(left.intent)) === JSON.stringify(replacementIntent(right.intent))

function readError(error: unknown): HandoffError {
  const { code, data } = (error && typeof error === 'object' ? error : {}) as { code?: number; data?: { reason?: string } }

  if (code === -32601) { return 'unsupported' }

  if (code === 5076 && ['revision_conflict', 'grant_revision_conflict', 'identity_changed'].includes(data?.reason ?? '')) { return 'conflict' }

  return 'unavailable'
}

/** One immutable owner authorization. Values exist only for the captured request call. */
export function createCredentialHandoffClient(source: () => HandoffScope, newKey: () => string,
  storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>) {
  let lastScope = ''
  let epoch = 0
  const entries = new Map<string, { key: string; sequence: number; view: HandoffView }>()
  const listeners = new Set<() => void>()

  const readScope = () => {
    const scope = source()
    const identity = JSON.stringify([scope.key, scope.ownerKey, scope.eligible, scope.profile, scope.organizationId, scope.ownerId])

    if (identity !== lastScope) { lastScope = identity; epoch++ }

    return { ...scope, key: `${scope.key}:${epoch}`, ownerKey: JSON.stringify([scope.ownerKey, scope.organizationId, scope.ownerId]) }
  }

  const storageKey = (scope: HandoffScope) => `eidolon.credential-handoff.intent.v1:${scope.ownerKey}`
  const viewFor = (scope: HandoffScope) => entries.get(scope.ownerKey)?.key === scope.key ? entries.get(scope.ownerKey)!.view : EMPTY

  const publish = (scope: HandoffScope, view: HandoffView, sequence: number) => {
    entries.set(scope.ownerKey, { key: scope.key, view, sequence })
    listeners.forEach(listener => listener())
  }

  const active = (scope: HandoffScope, sequence: number) => {
    const now = readScope()

    return eligible(now) && now.key === scope.key && now.ownerKey === scope.ownerKey && entries.get(scope.ownerKey)?.sequence === sequence
  }

  const nextSequence = (scope: HandoffScope) => (entries.get(scope.ownerKey)?.sequence ?? 0) + 1

  const retained = (scope: HandoffScope) => {
    const raw = storage.getItem(storageKey(scope))

    return raw ? validateRetainedReplacement(JSON.parse(raw), scope) : null
  }

  const clear = (scope: HandoffScope, expected: RetainedReplacement) => {
    const stored = retained(scope)

    // Compare the complete immutable intent, including the exact reviewed worker generation.
    if (stored && sameIntent(stored, expected)) { storage.removeItem(storageKey(scope)) }
  }

  const keep = (scope: HandoffScope, expected: RetainedReplacement) => {
    const stored = retained(scope)

    if (!stored || !sameIntent(stored, expected)) { throw new Error('Changed retained replacement') }
    storage.setItem(storageKey(scope), JSON.stringify(expected))
  }

  const base = (scope: HandoffScope) => ({ expectedOrganizationId: scope.organizationId, expectedOwnerId: scope.ownerId })

  const recover = async (cancel: boolean, operationId?: string) => {
    const scope = readScope()
    const previous = viewFor(scope)
    const record = previous.retained

    if (!eligible(scope) || previous.busy || !record || (cancel && operationId !== record.intent.operationId)) { return false }

    const sequence = nextSequence(scope)
    publish(scope, { ...previous, busy: true, error: null }, sequence)

    if (!active(scope, sequence)) { return false }

    try {
      const raw = await scope.request(`organization.credentials.${cancel ? 'cancelReplacement' : 'replacementStatus'}`, {
        ...base(scope), operationId: record.intent.operationId, ...(cancel ? { confirmed: true } : {})
      })

      const status = validateReplacementStatus(raw, record, cancel)
      const terminal = status.state === 'committed' || status.state === 'cancelled'
      const updated = status.receipt?.action === 'prepare_replacement' ? { ...record, preparation: status.receipt } : record

      if (terminal) { clear(scope, record) }

      if (active(scope, sequence)) {
        if (!terminal) { keep(scope, updated) }
        publish(scope, { ...viewFor(scope), retained: terminal ? null : updated, status, busy: false,
          error: status.state === 'absent' ? 'uncertain' : null }, sequence)
      }

      return terminal
    } catch (error) {
      if (active(scope, sequence)) {
        publish(scope, { ...viewFor(scope), busy: false, error: readError(error) === 'unsupported' ? 'unsupported' : 'uncertain' }, sequence)
      }

      return false
    }
  }

  const client = {
    subscribe(listener: () => void) { listeners.add(listener);

 return () => { listeners.delete(listener) } },
    getSnapshot(): HandoffView {
      const scope = readScope()

      return eligible(scope) ? viewFor(scope) : EMPTY
    },
    restore() {
      const scope = readScope()

      if (!eligible(scope) || viewFor(scope).busy) { return }

      const sequence = nextSequence(scope)

      try {
        const record = retained(scope)
        publish(scope, { ...EMPTY, retained: record, error: record ? 'uncertain' : null }, sequence)
      } catch {
        publish(scope, { ...EMPTY, error: 'storage' }, sequence)
      }
    },
    async loadTargets(entry: CredentialEntry) {
      const scope = readScope()
      const previous = viewFor(scope)

      if (!eligible(scope) || previous.busy || previous.retained || previous.error === 'storage' || entry.organizationId !== scope.organizationId) { return }

      const sequence = nextSequence(scope)
      publish(scope, { ...previous, entry: { id: entry.id, revision: entry.revision, version: entry.version }, targets: null, busy: true, error: null }, sequence)

      try {
        const raw = await scope.request('organization.credentials.replacementTargets', { ...base(scope), entryId: entry.id, expectedRevision: entry.revision })
        const targets = validateReplacementTargets(raw, entry.id, entry.revision)

        if (active(scope, sequence)) { publish(scope, { ...viewFor(scope), targets, busy: false, error: null }, sequence) }
      } catch (error) {
        if (active(scope, sequence)) { publish(scope, { ...viewFor(scope), targets: null, busy: false, error: readError(error) }, sequence) }
      }
    },
    async prepare(entry: CredentialEntry, target: ReplacementTarget, value: string) {
      const scope = readScope()
      const previous = viewFor(scope)

      if (!eligible(scope) || previous.busy || previous.retained || previous.error === 'storage' || !value ||
        entry.organizationId !== scope.organizationId || previous.entry?.id !== entry.id || previous.entry.revision !== entry.revision ||
        previous.entry.version !== entry.version || !previous.targets?.some(item => JSON.stringify(item) === JSON.stringify(target))) {
        value = ''

        return false
      }

      const intent = replacementIntent({ ...base(scope), entryId: entry.id, expectedRevision: entry.revision, nextVersion: entry.version + 1,
        actorId: target.actorId, requestId: target.requestId, generation: target.generation,
        expectedGrantRevision: target.grantRevision, operationId: newKey() } as RetainedReplacement['intent'])

      const record: RetainedReplacement = { intent, preparation: null }
      const sequence = nextSequence(scope)

      try {
        // A reload or another client may already own the durable slot, even before this view hydrates.
        const existing = retained(scope)

        if (existing) {
          value = ''
          publish(scope, { ...previous, retained: existing, status: null, error: 'uncertain' }, sequence)

          return false
        }

        validateRetainedReplacement(record, scope)
        storage.setItem(storageKey(scope), JSON.stringify(record))
      } catch {
        value = ''
        publish(scope, { ...previous, error: 'storage' }, sequence)

        return false
      }

      publish(scope, { ...previous, retained: record, status: null, busy: true, error: null }, sequence)

      try {
        if (!active(scope, sequence)) { clear(scope, record);

 return false }

        const pending = scope.request('organization.credentials.prepareReplacement', {
          ...base(scope), entryId: intent.entryId, expectedRevision: intent.expectedRevision, actorId: intent.actorId,
          requestId: intent.requestId, generation: intent.generation, expectedGrantRevision: intent.expectedGrantRevision,
          operationId: intent.operationId, value, confirmed: true
        })

        value = ''
        const preparation = validatePreparation(await pending, intent)

        if (active(scope, sequence)) {
          const updated = { intent, preparation }
          keep(scope, updated)
          // Authorization is pending. Only replacementStatus can establish that the worker committed it.
          publish(scope, { ...viewFor(scope), retained: updated, status: { operationId: intent.operationId, state: 'pending', receipt: preparation }, busy: false, error: null }, sequence)
        }

        return true
      } catch {
        if (active(scope, sequence)) { publish(scope, { ...viewFor(scope), busy: false, error: 'uncertain' }, sequence) }

        return false
      } finally { value = '' }
    },
    reconcile: () => recover(false),
    cancel: (operationId: string) => recover(true, operationId)
  }

  return client
}

export type CredentialHandoffClient = ReturnType<typeof createCredentialHandoffClient>
