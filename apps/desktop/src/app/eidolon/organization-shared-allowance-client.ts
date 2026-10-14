import {
  validateSharedAllowance,
  validateSharedAllowancePreview,
  validateSharedAllowanceReceipt,
  validSharedAllowanceCandidate,
  validSharedAllowanceIntent
} from './organization-shared-allowance-contract'
import type {
  SharedAllowanceAction,
  SharedAllowanceError,
  SharedAllowanceIntent,
  SharedAllowanceRequest,
  SharedAllowanceScope,
  SharedAllowanceView
} from './organization-shared-allowance-types'

const EMPTY: SharedAllowanceView = {
  descriptor: null,
  preview: null,
  intent: null,
  receipt: null,
  busy: false,
  error: 'unavailable'
}

const methods = {
  configuration: ['organization.previewSharedAllowance', 'organization.configureSharedAllowance'],
  observation: ['organization.previewSharedAllowanceObservation', 'organization.recordSharedAllowanceObservation']
} as const

function rejection(error: unknown): SharedAllowanceError | null {
  const { code, data } = (error && typeof error === 'object' ? error : {}) as {
    code?: number
    data?: { reason?: string }
  }

  if (code === -32601 || data?.reason === 'unsupported') {
    return 'unsupported'
  }

  if (code === -32602 || code === 4001) {
    return 'invalid'
  }

  if (code !== 5075) {
    return null
  }

  if (data?.reason === 'unconfigured') {
    return 'unconfigured'
  }

  if (['locked', 'unreadable', 'corrupt'].includes(data?.reason ?? '')) {
    return 'unavailable'
  }

  if (data?.reason?.endsWith('_conflict') || data?.reason === 'installation_owner_required') {
    return 'conflict'
  }

  if (data?.reason === 'managed_configuration') {
    return 'invalid'
  }

  return null
}

function definitivelyRejected(error: unknown) {
  const { code, data } = (error && typeof error === 'object' ? error : {}) as {
    code?: number
    data?: { reason?: string }
  }

  return (
    code === -32601 ||
    code === -32602 ||
    code === 4001 ||
    (code === 5075 &&
      [
        'identity_conflict',
        'generation_conflict',
        'source_conflict',
        'preview_conflict',
        'idempotency_conflict',
        'revision_conflict',
        'source_revision_conflict',
        'managed_configuration',
        'installation_owner_required'
      ].includes(data?.reason ?? ''))
  )
}

function immutable<T>(value: T): T {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(immutable)
    Object.freeze(value)
  }

  return value
}

/** Every operation captures one authenticated route. Uncertain writes retain the exact approved payload. */
export function createSharedAllowanceClient(
  source: () => SharedAllowanceScope,
  newKey: () => string,
  storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
) {
  let lastScope = ''
  let epoch = 0

  const readScope = () => {
    const scope = source()
    const identity = JSON.stringify([scope.key, scope.ownerKey, scope.eligible, scope.organizationId, scope.ownerId])

    if (identity !== lastScope) {
      lastScope = identity
      epoch++
    }

    return {
      ...scope,
      ownerKey: JSON.stringify([scope.ownerKey, scope.organizationId, scope.ownerId]),
      key: `${scope.key}:${epoch}`
    }
  }

  const entries = new Map<string, { key: string; view: SharedAllowanceView; sequence: number }>()
  const listeners = new Set<() => void>()
  const storageKey = (scope: SharedAllowanceScope) => `eidolon.shared-allowance.intent.v1:${scope.ownerKey}`
  const eligible = (scope: SharedAllowanceScope) => scope.eligible && Boolean(scope.organizationId && scope.ownerId)

  const publish = (scope: SharedAllowanceScope, view: SharedAllowanceView, sequence: number) => {
    entries.set(scope.ownerKey, { key: scope.key, view, sequence })
    listeners.forEach(listener => listener())
  }

  const current = (scope: SharedAllowanceScope, sequence: number) => {
    const now = readScope()

    return (
      eligible(now) &&
      now.key === scope.key &&
      now.ownerKey === scope.ownerKey &&
      entries.get(scope.ownerKey)?.sequence === sequence
    )
  }

  const api = {
    subscribe(listener: () => void) {
      listeners.add(listener)

      return () => {
        listeners.delete(listener)
      }
    },
    getSnapshot(): SharedAllowanceView {
      const scope = readScope()
      const entry = entries.get(scope.ownerKey)

      return eligible(scope) && entry?.key === scope.key ? entry.view : EMPTY
    },
    async read() {
      const scope = readScope()

      if (!eligible(scope)) {
        return
      }

      const entry = entries.get(scope.ownerKey)

      if (entry?.key === scope.key && entry.view.busy) {
        return
      }

      const sequence = (entry?.sequence ?? 0) + 1
      let intent = entry?.view.intent ?? null

      try {
        const saved = !intent && storage.getItem(storageKey(scope))

        if (saved) {
          const value: unknown = JSON.parse(saved)

          if (!validSharedAllowanceIntent(value)) {
            throw new Error('Invalid retained intent')
          }

          intent = immutable(value)
        }
      } catch {
        publish(scope, { ...EMPTY, error: 'storage' }, sequence)

        return
      }

      const previous = { ...EMPTY, intent, receipt: entry?.view.receipt ?? null }
      publish(scope, { ...previous, busy: true, error: intent ? 'uncertain' : null }, sequence)

      try {
        const descriptor = validateSharedAllowance(
          await scope.request('organization.sharedAllowance', { expectedOrganizationId: scope.organizationId }),
          scope
        )

        if (!current(scope, sequence)) {
          return
        }

        const accepted = previous.receipt?.receipt

        if (
          accepted?.organizationId === descriptor.organizationId &&
          accepted.ownerId === descriptor.ownerId &&
          accepted.generation > descriptor.generation
        ) {
          throw new Error('Allowance generation regressed behind a verified receipt')
        }

        publish(
          scope,
          {
            ...previous,
            descriptor,
            error: intent ? 'uncertain' : descriptor.state === 'unsupported' ? 'unsupported' : null
          },
          sequence
        )
      } catch (error) {
        if (current(scope, sequence)) {
          publish(scope, { ...previous, error: intent ? 'uncertain' : (rejection(error) ?? 'unavailable') }, sequence)
        }
      }
    },
    discardPreview() {
      const scope = readScope()
      const entry = entries.get(scope.ownerKey)

      if (
        entry?.key === scope.key &&
        !entry.view.intent &&
        (entry.view.preview || (entry.view.busy && entry.view.descriptor))
      ) {
        publish(scope, { ...entry.view, preview: null, busy: false }, entry.sequence + 1)
      }
    },
    async preview(
      action: SharedAllowanceAction,
      candidate: Pick<SharedAllowanceRequest, 'configuration' | 'observation'>
    ) {
      const scope = readScope()
      const entry = entries.get(scope.ownerKey)

      if (!eligible(scope) || entry?.key !== scope.key || entry.view.busy || entry.view.intent) {
        return
      }

      const view = entry.view
      const data = view.descriptor

      if (!data || data.authority !== 'installation-owner' || !data.capabilities[action] || view.error) {
        return
      }

      const sequence = entry.sequence + 1

      if (!validSharedAllowanceCandidate(action, candidate, data)) {
        publish(scope, { ...view, preview: null, error: 'invalid' }, sequence)

        return
      }

      const request: SharedAllowanceRequest = {
        expectedOrganizationId: data.organizationId,
        expectedOwnerId: data.ownerId,
        expectedGeneration: data.generation,
        sourceRevision: data.sourceRevision,
        [action]: structuredClone(candidate[action])
      }

      publish(scope, { ...view, busy: true, preview: null, receipt: null, error: null }, sequence)

      try {
        const value = validateSharedAllowancePreview(
          await scope.request(methods[action][0], { ...request }),
          scope,
          action,
          request,
          data
        )

        if (!current(scope, sequence)) {
          return
        }

        const canonical = immutable({ ...request, [action]: value[action] })
        publish(
          scope,
          { ...view, busy: false, receipt: null, error: null, preview: { action, value, request: canonical } },
          sequence
        )

        return true
      } catch (error) {
        if (current(scope, sequence)) {
          publish(
            scope,
            { ...view, busy: false, preview: null, receipt: null, error: rejection(error) ?? 'invalid' },
            sequence
          )
        }
      }
    },
    async apply() {
      const scope = readScope()
      const entry = entries.get(scope.ownerKey)

      if (!eligible(scope) || entry?.key !== scope.key || entry.view.busy) {
        return
      }

      const view = entry.view
      const data = view.descriptor

      if (!data || data.authority !== 'installation-owner') {
        return
      }

      const oldIntent = view.intent
      const action = oldIntent?.action ?? view.preview?.action

      if (!action || !data.capabilities[action]) {
        return
      }

      if (
        oldIntent &&
        (oldIntent.expectedOrganizationId !== data.organizationId || oldIntent.expectedOwnerId !== data.ownerId)
      ) {
        return
      }

      if (!oldIntent && (!view.preview || view.error)) {
        return
      }

      const intent: SharedAllowanceIntent =
        oldIntent ??
        immutable({
          ...structuredClone(view.preview!.request),
          action,
          previewVersion: view.preview!.value.previewVersion,
          idempotencyKey: newKey(),
          confirmed: true as const
        })

      const sequence = entry.sequence + 1

      try {
        storage.setItem(storageKey(scope), JSON.stringify(intent))
      } catch {
        publish(scope, { ...view, error: 'storage' }, sequence)

        return
      }

      publish(scope, { ...view, busy: true, intent, error: null }, sequence)

      try {
        const { action: _action, ...params } = intent

        const receipt = validateSharedAllowanceReceipt(
          await scope.request(methods[action][1], structuredClone(params)),
          scope,
          intent
        )

        if (!current(scope, sequence)) {
          return
        }

        storage.removeItem(storageKey(scope))
        publish(scope, { ...EMPTY, receipt, error: null }, sequence)
        await api.read()

        return receipt
      } catch (error) {
        if (!current(scope, sequence)) {
          return
        }

        // Storage/runtime failures may happen after commit. Only a known
        // pre-commit rejection can release the original idempotent intent.
        const classified = rejection(error)
        const rejected = !oldIntent && definitivelyRejected(error) ? classified : null

        if (rejected) {
          try {
            storage.removeItem(storageKey(scope))
          } catch {
            publish(scope, { ...view, busy: false, intent, error: 'uncertain' }, sequence)

            return
          }
        }

        publish(
          scope,
          { ...view, preview: null, busy: false, intent: rejected ? null : intent, error: rejected ?? 'uncertain' },
          sequence
        )
      }
    }
  }

  return api
}

export type SharedAllowanceClient = ReturnType<typeof createSharedAllowanceClient>
