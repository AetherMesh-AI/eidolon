import {
  policyEqual,
  validateOwnerPolicy,
  validatePolicyPreview,
  validatePolicyReceipt,
  validPolicyConfiguration,
  validPolicyIntent,
  validPolicyObservation
} from './organization-owner-policy-contract'
import type {
  OwnerPolicyAction,
  OwnerPolicyError,
  OwnerPolicyIntent,
  OwnerPolicyRequest,
  OwnerPolicyScope,
  OwnerPolicyView
} from './organization-owner-policy-types'

const EMPTY: OwnerPolicyView = {
  descriptor: null,
  preview: null,
  intent: null,
  receipt: null,
  busy: false,
  error: 'unavailable'
}

// An eligible owner with no read yet is loading, not an unavailable connection.
const UNREAD: OwnerPolicyView = { ...EMPTY, busy: true, error: null }

const methods = {
  modelRouting: ['organization.previewModelRouting', 'organization.configureModelRouting'],
  budgetPolicy: ['organization.previewBudgetPolicy', 'organization.configureBudgetPolicy'],
  budgetObservation: ['organization.previewBudgetObservation', 'organization.recordBudgetObservation']
} as const

function rejection(error: unknown): OwnerPolicyError | null {
  const { code, data } = (error && typeof error === 'object' ? error : {}) as {
    code?: number
    data?: { reason?: string }
  }

  if (code === -32601) {
    return 'unsupported'
  }

  if (code === -32602 || code === 4001 || data?.reason === 'managed_configuration') {
    return 'invalid'
  }

  if (code === 5074) {
    if (data?.reason === 'source_policy_conflict') {
      return 'sourceConflict'
    }

    if (
      [
        'identity_conflict',
        'generation_conflict',
        'source_conflict',
        'preview_conflict',
        'idempotency_conflict'
      ].includes(data?.reason ?? '')
    ) {
      return 'conflict'
    }

    if (data?.reason === 'unsupported') {
      return 'unsupported'
    }

    if (data?.reason === 'unconfigured') {
      return 'unconfigured'
    }

    if (['locked', 'unreadable', 'corrupt'].includes(data?.reason ?? '')) {
      return 'unavailable'
    }
  }

  return null
}

/** One captured authenticated socket per operation. No reconnect, route fallback or implicit initialization. */
export function createOwnerPolicyClient(
  source: () => OwnerPolicyScope,
  newKey: () => string = () => crypto.randomUUID(),
  storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
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

    return { ...scope, key: `${scope.key}:${epoch}` }
  }

  const entries = new Map<string, { key: string; view: OwnerPolicyView; sequence: number }>()
  const listeners = new Set<() => void>()
  const pendingPreviews = new Map<string, number>()
  const storageKey = (scope: OwnerPolicyScope) => `eidolon.organization-owner-policy.intent.v1:${scope.ownerKey}`

  const publish = (scope: OwnerPolicyScope, view: OwnerPolicyView, sequence: number) => {
    entries.set(scope.ownerKey, { key: scope.key, view, sequence })
    listeners.forEach(listener => listener())
  }

  const current = (scope: OwnerPolicyScope, sequence: number) => {
    const now = readScope()

    return (
      now.eligible &&
      now.key === scope.key &&
      now.ownerKey === scope.ownerKey &&
      entries.get(scope.ownerKey)?.sequence === sequence
    )
  }

  const eligible = (scope: OwnerPolicyScope) => scope.eligible && Boolean(scope.organizationId && scope.ownerId)

  const api = {
    subscribe(listener: () => void) {
      listeners.add(listener)

      return () => {
        listeners.delete(listener)
      }
    },
    getSnapshot(): OwnerPolicyView {
      const scope = readScope()
      const entry = entries.get(scope.ownerKey)

      if (!eligible(scope)) {
        return EMPTY
      }

      return entry?.key === scope.key ? entry.view : UNREAD
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
        if (!intent) {
          const saved = storage?.getItem(storageKey(scope))

          if (saved) {
            const value: unknown = JSON.parse(saved)

            if (!validPolicyIntent(value)) {
              throw new Error('Invalid retained intent')
            }

            intent = value
          }
        }
      } catch {
        publish(scope, { ...EMPTY, error: 'storage' }, sequence)

        return
      }

      const previous = { ...EMPTY, receipt: entry?.view.receipt ?? null, intent }
      publish(scope, { ...previous, busy: true, error: intent ? 'uncertain' : null }, sequence)

      try {
        const descriptor = validateOwnerPolicy(
          await scope.request('organization.ownerPolicy', { expectedOrganizationId: scope.organizationId }),
          scope
        )

        if (!current(scope, sequence)) {
          return
        }

        const accepted = previous.receipt?.receipt

        if (
          descriptor.state === 'ready' &&
          accepted?.organizationId === descriptor.organizationId &&
          accepted.ownerId === descriptor.ownerId &&
          accepted.policyGeneration > descriptor.policyGeneration
        ) {
          throw new Error('Policy generation regressed behind a verified receipt')
        }

        publish(
          scope,
          {
            ...previous,
            descriptor,
            error: intent ? 'uncertain' : descriptor.state === 'ready' ? null : descriptor.state
          },
          sequence
        )
      } catch (error) {
        if (current(scope, sequence)) {
          publish(scope, { ...previous, error: intent ? 'uncertain' : (rejection(error) ?? 'invalid') }, sequence)
        }
      }
    },
    discardPreview() {
      const scope = readScope()
      const entry = entries.get(scope.ownerKey)

      if (
        entry?.key === scope.key &&
        !entry.view.intent &&
        (entry.view.preview || pendingPreviews.get(scope.ownerKey) === entry.sequence)
      ) {
        // A discarded or edited candidate cannot reappear when an older preview returns.
        publish(scope, { ...entry.view, preview: null, busy: false }, entry.sequence + 1)
      }
    },
    async preview(action: OwnerPolicyAction, candidate: Pick<OwnerPolicyRequest, 'configuration' | 'observation'>) {
      const scope = readScope()
      const entry = entries.get(scope.ownerKey)

      if (!eligible(scope) || entry?.key !== scope.key || entry.view.busy || entry.view.intent) {
        return
      }

      const view = entry.view
      const data = view.descriptor

      if (!data || data.state !== 'ready' || !data.capabilities[action] || view.error) {
        return
      }

      const valid =
        action === 'budgetObservation'
          ? validPolicyObservation(candidate.observation) && candidate.configuration === undefined
          : candidate.observation === undefined && validPolicyConfiguration(action, candidate.configuration)

      const modifiedManaged =
        candidate.configuration &&
        Object.entries(candidate.configuration).some(
          ([key, value]) =>
            data.managedFields.includes(key) &&
            !policyEqual(value, data.configuration[key as keyof typeof data.configuration])
        )

      const sequence = entry.sequence + 1

      if (!valid || modifiedManaged) {
        publish(scope, { ...view, preview: null, error: 'invalid' }, sequence)

        return
      }

      const request: OwnerPolicyRequest = {
        expectedOrganizationId: data.organizationId,
        expectedGeneration: data.policyGeneration,
        sourceRevision: data.sourceRevision,
        ...structuredClone(candidate)
      }

      pendingPreviews.set(scope.ownerKey, sequence)
      publish(scope, { ...view, busy: true, preview: null, receipt: null, error: null }, sequence)

      try {
        const value = validatePolicyPreview(
          await scope.request(methods[action][0], { ...request }),
          scope,
          request,
          action,
          data
        )

        if (!current(scope, sequence)) {
          return
        }

        const canonicalRequest = {
          ...request,
          ...(action === 'budgetObservation'
            ? { observation: value.observation }
            : { configuration: value.configuration })
        }

        publish(
          scope,
          { ...view, busy: false, receipt: null, error: null, preview: { action, value, request: canonicalRequest } },
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

      if (!data || data.state !== 'ready') {
        return
      }

      const oldIntent = view.intent

      if (
        oldIntent &&
        (oldIntent.expectedOrganizationId !== data.organizationId || oldIntent.ownerId !== data.ownerId)
      ) {
        return
      }

      if (!oldIntent && (!view.preview || view.error || !data.capabilities[view.preview.action])) {
        return
      }

      const intent: OwnerPolicyIntent = oldIntent ?? {
        ...structuredClone(view.preview!.request),
        action: view.preview!.action,
        ownerId: data.ownerId,
        previewVersion: view.preview!.value.previewVersion,
        confirmed: true,
        idempotencyKey: newKey()
      }

      const sequence = entry.sequence + 1

      try {
        storage?.setItem(storageKey(scope), JSON.stringify(intent))
      } catch {
        publish(scope, { ...view, error: 'storage' }, sequence)

        return
      }

      publish(scope, { ...view, busy: true, intent, error: null }, sequence)

      try {
        const { action, ownerId: _ownerId, ...params } = intent
        const receipt = validatePolicyReceipt(await scope.request(methods[action][1], params), scope, intent)

        if (!current(scope, sequence)) {
          return
        }

        storage?.removeItem(storageKey(scope))
        publish(scope, { ...EMPTY, receipt, error: null }, sequence)
        // The receipt is historical. A separate read supplies the next generation.
        await api.read()

        return receipt
      } catch (error) {
        if (!current(scope, sequence)) {
          return
        }

        const rejected = oldIntent ? null : rejection(error)

        if (rejected) {
          try {
            storage?.removeItem(storageKey(scope))
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

export type OwnerPolicyClient = ReturnType<typeof createOwnerPolicyClient>
