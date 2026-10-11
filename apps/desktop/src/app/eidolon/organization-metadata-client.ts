/** Display metadata never routes through the execution adapter or reconnect helper. */
export interface OrganizationMetadata {
  organizationId: string
  displayName: string
  owner: { ownerId: string; displayName: string }
  revision: number
  createdAt: string
  updatedAt: string
}
export interface MetadataDescriptor {
  capabilityVersion: 1
  state: 'ready' | 'unconfigured' | 'unsupported'
  metadata: OrganizationMetadata | null
  reason?: string
  scope: { kind: 'served-profile'; profile: string }
}
export interface MetadataScope {
  key: string
  ownerKey: string
  profile: string
  eligible: boolean
  request: <T>(method: string, params: Record<string, unknown>) => Promise<T>
}
export interface MetadataSaveIntent {
  displayName: string
  ownerDisplayName: string
  expectedOrganizationId: string | null
  expectedRevision: number | null
  idempotencyKey: string
}
export interface MetadataView {
  descriptor: MetadataDescriptor | null
  busy: boolean
  error: 'unavailable' | 'unsupported' | 'invalid' | 'conflict' | 'uncertain' | 'storage' | null
  intent: MetadataSaveIntent | null
}
const EMPTY: MetadataView = { descriptor: null, busy: false, error: 'unavailable', intent: null }
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

const validName = (value: unknown): value is string =>
  typeof value === 'string' &&
  value.trim() === value &&
  value.length > 0 &&
  [...value].length <= 160 &&
  !/\p{C}/u.test(value)

const validTime = (value: unknown): value is string =>
  typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value))

function descriptor(value: unknown, profile: string): MetadataDescriptor {
  const item = value as MetadataDescriptor

  if (
    !item ||
    item.capabilityVersion !== 1 ||
    item.scope?.kind !== 'served-profile' ||
    item.scope.profile !== profile ||
    !['ready', 'unconfigured', 'unsupported'].includes(item.state)
  ) {
    throw new Error('Invalid descriptor')
  }

  const record = item.metadata

  if (item.state === 'ready') {
    if (
      !record ||
      !UUID.test(record.organizationId) ||
      !validName(record.displayName) ||
      !UUID.test(record.owner?.ownerId) ||
      record.owner.ownerId === record.organizationId ||
      !validName(record.owner.displayName) ||
      !Number.isSafeInteger(record.revision) ||
      record.revision < 1 ||
      !validTime(record.createdAt) ||
      !validTime(record.updatedAt)
    ) {
      throw new Error('Invalid metadata')
    }
  } else if (record !== null) {
    throw new Error('Invalid absent metadata')
  }

  return item
}

function rejection(error: unknown): MetadataView['error'] {
  const { code, data } = (error && typeof error === 'object' ? error : {}) as {
    code?: number
    data?: { reason?: string }
  }

  if (code === -32601) {
    return 'unsupported'
  }

  if (code === -32602 || code === 4001) {
    return 'invalid'
  }

  if (
    code === 5072 &&
    ['identity_conflict', 'revision_conflict', 'initialization_conflict', 'idempotency_conflict'].includes(
      data?.reason ?? ''
    )
  ) {
    return 'conflict'
  }

  if (code === 5072 && data?.reason === 'unsupported_schema') {
    return 'unsupported'
  }

  return null
}

/** subscribe never reads. Every operation uses one captured open socket. */
export function createOrganizationMetadataClient(
  source: () => MetadataScope,
  newKey: () => string = () => crypto.randomUUID(),
  intentStorage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
) {
  let lastScope = ''
  let generation = 0

  const readScope = () => {
    const scope = source()
    const identity = JSON.stringify([scope.key, scope.ownerKey, scope.eligible])

    if (identity !== lastScope) {
      lastScope = identity
      generation++
    }

    return { ...scope, key: `${scope.key}:${generation}` }
  }

  const entries = new Map<string, { key: string; view: MetadataView; sequence: number }>()
  const listeners = new Set<() => void>()
  const storageKey = (scope: MetadataScope) => `eidolon.organization-metadata.intent.v1:${scope.ownerKey}`

  const restoredIntent = (scope: MetadataScope): MetadataSaveIntent | null => {
    const raw = intentStorage?.getItem(storageKey(scope))

    if (!raw) {
      return null
    }

    const value = JSON.parse(raw) as MetadataSaveIntent

    if (
      !value ||
      !validName(value.displayName) ||
      !validName(value.ownerDisplayName) ||
      typeof value.idempotencyKey !== 'string' ||
      !value.idempotencyKey ||
      value.idempotencyKey.length > 200 ||
      !(
        (value.expectedOrganizationId === null && value.expectedRevision === null) ||
        (typeof value.expectedOrganizationId === 'string' &&
          UUID.test(value.expectedOrganizationId) &&
          Number.isSafeInteger(value.expectedRevision) &&
          value.expectedRevision! > 0)
      )
    ) {
      throw new Error('Invalid retained Save intent')
    }

    return {
      displayName: value.displayName,
      ownerDisplayName: value.ownerDisplayName,
      expectedOrganizationId: value.expectedOrganizationId,
      expectedRevision: value.expectedRevision,
      idempotencyKey: value.idempotencyKey
    }
  }

  const publish = (scope: MetadataScope, view: MetadataView, sequence: number) => {
    entries.set(scope.ownerKey, { key: scope.key, view, sequence })
    listeners.forEach(listener => listener())
  }

  const current = (scope: MetadataScope) => {
    const now = readScope()

    return now.eligible && now.key === scope.key && now.ownerKey === scope.ownerKey
  }

  const viewFor = (scope: MetadataScope) => entries.get(scope.ownerKey)?.view ?? EMPTY

  const retainNewest = (view: MetadataView, next: MetadataDescriptor) => {
    const previous = view.descriptor

    return previous?.metadata &&
      next.metadata &&
      previous.metadata.organizationId === next.metadata.organizationId &&
      previous.metadata.revision > next.metadata.revision
      ? previous
      : next
  }

  return {
    subscribe(listener: () => void) {
      listeners.add(listener)

      return () => {
        listeners.delete(listener)
      }
    },
    getSnapshot() {
      const scope = readScope()
      const entry = entries.get(scope.ownerKey)

      return scope.eligible && entry?.key === scope.key ? entry.view : EMPTY
    },
    async read() {
      const scope = readScope()

      if (!scope.eligible) {
        return
      }

      const entry = entries.get(scope.ownerKey)

      if (entry?.key === scope.key && entry.view.busy) {
        return
      }

      const sequence = (entry?.sequence ?? 0) + 1
      let previous = viewFor(scope)

      if (!previous.intent) {
        try {
          previous = { ...previous, intent: restoredIntent(scope) }
        } catch {
          publish(scope, { ...EMPTY, error: 'storage' }, sequence)

          return
        }
      }

      publish(
        scope,
        {
          ...previous,
          descriptor: entry?.key === scope.key ? previous.descriptor : null,
          busy: true,
          error: previous.intent ? 'uncertain' : null
        },
        sequence
      )

      try {
        const next = descriptor(await scope.request<unknown>('organization.descriptor', {}), scope.profile)

        if (!current(scope) || entries.get(scope.ownerKey)?.sequence !== sequence) {
          return
        }

        publish(
          scope,
          {
            ...viewFor(scope),
            descriptor: next,
            busy: false,
            error: previous.intent ? 'uncertain' : next.state === 'unsupported' ? 'unsupported' : null
          },
          sequence
        )
      } catch (error) {
        if (current(scope) && entries.get(scope.ownerKey)?.sequence === sequence) {
          publish(
            scope,
            {
              ...viewFor(scope),
              descriptor: null,
              busy: false,
              error: previous.intent ? 'uncertain' : (rejection(error) ?? 'storage')
            },
            sequence
          )
        }
      }
    },
    async save(displayName: string, ownerDisplayName: string, onVerifiedSave?: (receipt: MetadataDescriptor) => void) {
      const scope = readScope()
      const entry = entries.get(scope.ownerKey)

      if (!scope.eligible || !entry || entry.key !== scope.key || entry.view.busy) {
        return
      }

      const view = entry.view
      const data = view.descriptor

      if (!data || data.state === 'unsupported') {
        return
      }

      const oldIntent = view.intent

      // Never initialize a replaced/missing ledger while reconciling an old intent.
      if (
        oldIntent &&
        (!data.metadata ||
          (oldIntent.expectedOrganizationId !== null &&
            oldIntent.expectedOrganizationId !== data.metadata.organizationId))
      ) {
        return
      }

      if (!oldIntent && (view.error || !displayName.trim() || !ownerDisplayName.trim())) {
        return
      }

      const intent = oldIntent ?? {
        displayName: displayName.trim(),
        ownerDisplayName: ownerDisplayName.trim(),
        expectedOrganizationId: data.metadata?.organizationId ?? null,
        expectedRevision: data.metadata?.revision ?? null,
        idempotencyKey: newKey()
      }

      const sequence = entry.sequence + 1

      // Persist before sending: reloading this window must not mint a different request.
      try {
        intentStorage?.setItem(storageKey(scope), JSON.stringify(intent))
      } catch {
        publish(scope, { ...view, error: 'storage' }, sequence)

        return
      }

      publish(scope, { ...view, busy: true, intent, error: null }, sequence)

      try {
        const result = descriptor(
          await scope.request<unknown>('organization.saveMetadata', { ...intent }),
          scope.profile
        )

        const receipt = result.metadata

        if (
          result.state !== 'ready' ||
          !receipt ||
          receipt.displayName !== intent.displayName ||
          receipt.owner.displayName !== intent.ownerDisplayName ||
          receipt.revision !== (intent.expectedRevision ?? 0) + 1 ||
          (intent.expectedOrganizationId !== null &&
            (receipt.organizationId !== intent.expectedOrganizationId ||
              receipt.owner.ownerId !== data.metadata?.owner.ownerId ||
              receipt.createdAt !== data.metadata?.createdAt))
        ) {
          throw new Error('Invalid Save receipt')
        }

        if (!current(scope) || entries.get(scope.ownerKey)?.sequence !== sequence) {
          return
        }

        intentStorage?.removeItem(storageKey(scope))
        // Capture the verified identity before subscribers can unmount an initializing editor.
        onVerifiedSave?.(result)
        publish(
          scope,
          { descriptor: retainNewest(viewFor(scope), result), busy: false, intent: null, error: null },
          sequence
        )

        return true
      } catch (error) {
        if (!current(scope) || entries.get(scope.ownerKey)?.sequence !== sequence) {
          return
        }

        // A rejection of this retry says nothing about whether the original committed.
        // Only a matching receipt can reconcile an already-uncertain request.
        if (oldIntent) {
          publish(scope, { ...viewFor(scope), descriptor: null, busy: false, intent, error: 'uncertain' }, sequence)

          return
        }

        const rejected = rejection(error)

        if (rejected) {
          try {
            intentStorage?.removeItem(storageKey(scope))
          } catch {
            publish(scope, { ...viewFor(scope), busy: false, intent, error: 'uncertain' }, sequence)

            return
          }
        }

        publish(
          scope,
          { ...viewFor(scope), busy: false, intent: rejected ? null : intent, error: rejected ?? 'uncertain' },
          sequence
        )
      }
    }
  }
}
