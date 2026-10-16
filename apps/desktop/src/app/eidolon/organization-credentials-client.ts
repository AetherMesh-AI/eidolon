import {
  credentialMetadata,
  validateCredentialCatalog,
  validateCredentialReceipt,
  validCredentialCreateScope,
  validCredentialGrantPermissions,
  validCredentialIntent,
  validCredentialMetadata
} from './organization-credentials-contract'
import type {
  CredentialAction,
  CredentialError,
  CredentialIntent,
  CredentialMetadata,
  CredentialScope,
  CredentialView
} from './organization-credentials-types'

const EMPTY: CredentialView = { catalog: null, intent: null, receipt: null, cancelledOperationId: null, busy: false, error: 'unavailable' }
const eligible = (scope: CredentialScope) => scope.eligible && Boolean(scope.organizationId && scope.ownerId)

function rejection(error: unknown): CredentialError | null {
  const { code, data } = (error && typeof error === 'object' ? error : {}) as { code?: number; data?: { reason?: string } }

  if (code === -32601) {
    return 'unsupported'
  }

  if (code === 4001) {
    return 'unavailable'
  }

  if (code === -32602) {
    return 'invalid'
  }

  if (code === 5076 && ['identity_changed', 'revision_conflict', 'idempotency_conflict', 'name_conflict'].includes(data?.reason ?? '')) {
    return 'conflict'
  }

  if (code === 5076 && ['invalid_metadata', 'invalid_name', 'invalid_description', 'invalid_kind', 'invalid_expiry',
    'invalid_action', 'invalid_parameters', 'confirmation_required', 'scope_denied', 'permission_denied',
    'classification_change_denied', 'entry_revoked', 'entry_missing', 'revoke_first', 'operation_cancelled'].includes(data?.reason ?? '')) {
    return 'invalid'
  }

  return null
}

function safeIntent(value: CredentialIntent): CredentialIntent {
  return { expectedOrganizationId: value.expectedOrganizationId, expectedOwnerId: value.expectedOwnerId,
    action: value.action, entryId: value.entryId, expectedRevision: value.expectedRevision,
    operationId: value.operationId, metadata: value.metadata ? credentialMetadata(value.metadata) : null,
    ...(value.action === 'configure_grant' ? { actorId: value.actorId, permissions: [...value.permissions!] } : {}) }
}

/** Captured socket only. Submitted values and secure handles never enter a view, cache, or storage. */
export function createOrganizationCredentialsClient(
  source: () => CredentialScope,
  newKey: () => string,
  storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
) {
  let lastScope = ''
  let epoch = 0
  const entries = new Map<string, { key: string; view: CredentialView; sequence: number }>()
  const listeners = new Set<() => void>()

  const readScope = () => {
    const scope = source()
    const identity = JSON.stringify([scope.key, scope.ownerKey, scope.eligible, scope.profile, scope.organizationId, scope.ownerId])

    if (identity !== lastScope) {
      lastScope = identity
      epoch++
    }

    return { ...scope, ownerKey: JSON.stringify([scope.ownerKey, scope.organizationId, scope.ownerId]), key: `${scope.key}:${epoch}` }
  }

  const storageKey = (scope: CredentialScope) => `eidolon.organization-credentials.intent.v1:${scope.ownerKey}`

  const current = (scope: CredentialScope) => {
    const now = readScope()

    return eligible(now) && now.key === scope.key && now.ownerKey === scope.ownerKey
  }

  const publish = (scope: CredentialScope, view: CredentialView, sequence: number) => {
    entries.set(scope.ownerKey, { key: scope.key, view, sequence })
    listeners.forEach(listener => listener())
  }

  const active = (scope: CredentialScope, sequence: number) => current(scope) && entries.get(scope.ownerKey)?.sequence === sequence
  const viewFor = (scope: CredentialScope) => entries.get(scope.ownerKey)?.key === scope.key ? entries.get(scope.ownerKey)!.view : EMPTY

  const retained = (scope: CredentialScope) => {
    const raw = storage.getItem(storageKey(scope))

    if (!raw) {
      return null
    }

    const value: unknown = JSON.parse(raw)

    if (!validCredentialIntent(value, scope)) {
      throw new Error('Invalid retained credential operation')
    }

    return safeIntent(value)
  }

  const clear = (scope: CredentialScope, intent: CredentialIntent) => {
    // A late response from an earlier owner/socket epoch must not erase a newer operation.
    const stored = retained(scope)

    if (stored && JSON.stringify(stored) === JSON.stringify(safeIntent(intent))) {
      storage.removeItem(storageKey(scope))
    }
  }


  const execute = async (scope: CredentialScope, previous: CredentialView, intent: CredentialIntent, value = '') => {
    const sequence = (entries.get(scope.ownerKey)?.sequence ?? 0) + 1

      try {
        storage.setItem(storageKey(scope), JSON.stringify(intent))
      } catch {
        value = ''
        publish(scope, { ...previous, error: 'storage' }, sequence)

        return false
      }

      publish(scope, { ...previous, intent, receipt: null, cancelledOperationId: null, busy: true, error: null }, sequence)
      let inputHandle: string | null = null
      let mutationStarted = false

      try {
        if (intent.action === 'replace_value') {
          // Release the local value immediately after handing it to the authenticated request.
          const pendingInput = scope.request<{ inputHandle: string; expiresIn: number }>('organization.credentials.secureInput', {
            expectedOrganizationId: scope.organizationId, expectedOwnerId: scope.ownerId, entryId: intent.entryId,
            expectedRevision: intent.expectedRevision, operationId: intent.operationId, value
          })

          value = ''
          const input = await pendingInput

          if (!input || typeof input.inputHandle !== 'string' || !input.inputHandle || input.expiresIn !== 90) {
            throw new Error('Invalid secure input receipt')
          }

          inputHandle = input.inputHandle
        }

        if (!active(scope, sequence)) {
          clear(scope, intent)

          return false
        }

        mutationStarted = true

        const pendingMutation = intent.action === 'configure_grant'
          ? scope.request('organization.credentials.configureGrant', {
            expectedOrganizationId: intent.expectedOrganizationId, expectedOwnerId: intent.expectedOwnerId,
            actorId: intent.actorId, scope: intent.entryId, permissions: intent.permissions, bindings: [],
            expectedRevision: intent.expectedRevision, operationId: intent.operationId, confirmed: true
          })
          : scope.request('organization.credentials.mutate', { ...intent, inputHandle, confirmed: true })

        inputHandle = null
        const receipt = validateCredentialReceipt(await pendingMutation, intent, previous.catalog?.entries.find(entry => entry.id === intent.entryId && entry.revision === intent.expectedRevision)?.version)
        clear(scope, intent)

        if (active(scope, sequence)) {
          publish(scope, { ...viewFor(scope), intent: null, receipt, busy: false, error: null }, sequence)
          await client.read()
        }

        return true
      } catch (error) {
        const known = mutationStarted ? rejection(error) : 'secureInput'

        if (known) {
          try {
            clear(scope, intent)
          } catch {
            // An unresolved retained identity must continue to block a duplicate operation.
            if (active(scope, sequence)) {
              publish(scope, { ...viewFor(scope), busy: false, error: 'storage' }, sequence)
            }

            return false
          }
        }

        if (active(scope, sequence)) {
          publish(scope, { ...viewFor(scope), intent: known ? null : intent, busy: false, error: known ?? 'uncertain' }, sequence)
        }

        return false
      } finally {
        value = ''
        inputHandle = null
      }
  }

  const client = {
    subscribe(listener: () => void) {
      listeners.add(listener)

      return () => { listeners.delete(listener) }
    },
    getSnapshot(): CredentialView {
      const scope = readScope()

      return eligible(scope) ? viewFor(scope) : EMPTY
    },
    async read() {
      const scope = readScope()
      const previous = viewFor(scope)

      if (!eligible(scope) || previous.busy) {
        return
      }

      const sequence = (entries.get(scope.ownerKey)?.sequence ?? 0) + 1
      let intent: CredentialIntent | null

      try {
        intent = previous.intent ?? retained(scope)
      } catch {
        publish(scope, { ...EMPTY, error: 'storage' }, sequence)

        return
      }

      publish(scope, { ...previous, intent, busy: true, error: null }, sequence)

      try {
        const raw = await scope.request('organization.credentials.catalog', {
          expectedOrganizationId: scope.organizationId, expectedOwnerId: scope.ownerId
        })

        const catalog = validateCredentialCatalog(raw, scope)

        if (active(scope, sequence)) {
          publish(scope, { ...viewFor(scope), catalog, busy: false, error: intent ? 'uncertain' : null }, sequence)
        }
      } catch (error) {
        if (active(scope, sequence)) {
          publish(scope, { ...viewFor(scope), catalog: null, busy: false, error: rejection(error) ?? 'unavailable' }, sequence)
        }
      }
    },
    async mutate(action: Exclude<CredentialAction, 'configure_grant'>, entryId: string | null, expectedRevision: number | null,
      metadata: CredentialMetadata | null = null, value = ''): Promise<boolean> {
      const scope = readScope()
      const previous = viewFor(scope)
      const catalog = previous.catalog
      const entry = catalog?.entries.find(item => item.id === entryId)
      const allowed = action === 'create' ? catalog?.canCreate : entry?.permissions.includes(action)

      if (!eligible(scope) || !catalog || previous.busy || previous.intent || !allowed ||
        (action !== 'create' && (!entry || entry.revision !== expectedRevision)) ||
        (action === 'remove' && entry?.status !== 'revoked') ||
        (['create', 'edit_metadata'].includes(action) && !validCredentialMetadata(metadata)) ||
        (action === 'replace_value' && (!catalog.secureInputAvailable || catalog.backend.state !== 'available' || !value))) {
        value = ''

        return false
      }

      const intent: CredentialIntent = { expectedOrganizationId: scope.organizationId!, expectedOwnerId: scope.ownerId!,
        action, entryId, expectedRevision, operationId: newKey(), metadata: metadata ? credentialMetadata(metadata) : null }

      return execute(scope, previous, intent, value)
    },
    async configureGrant(actorId: string, entryId: string, expectedRevision: number, permissions: string[]): Promise<boolean> {
      const scope = readScope()
      const previous = viewFor(scope)
      const catalog = previous.catalog
      const grant = catalog?.grants.find(item => item.actor === actorId && item.scope === entryId)

      if (!eligible(scope) || !catalog || previous.busy || previous.intent || !catalog.agents.some(agent => agent.identityId === actorId) ||
        (!catalog.entries.some(entry => entry.id === entryId) && !validCredentialCreateScope(entryId)) ||
        (grant?.revision ?? 0) !== expectedRevision || !validCredentialGrantPermissions(entryId, permissions)) {
        return false
      }

      const intent: CredentialIntent = { expectedOrganizationId: scope.organizationId!, expectedOwnerId: scope.ownerId!,
        action: 'configure_grant', entryId, expectedRevision, operationId: newKey(), metadata: null, actorId, permissions: [...permissions].sort() }

      return execute(scope, previous, intent)
    },
    async cancelPending(operationId: string): Promise<boolean> {
      const scope = readScope()
      const previous = viewFor(scope)
      const intent = previous.intent

      if (!eligible(scope) || previous.busy || !intent || intent.operationId !== operationId || !validCredentialIntent(intent, scope)) {
        return false
      }

      const sequence = (entries.get(scope.ownerKey)?.sequence ?? 0) + 1
      publish(scope, { ...previous, busy: true, error: null }, sequence)

      try {
        const response = await scope.request<{ operationId: unknown; state: unknown; receipt: unknown }>('organization.credentials.cancelOperation', {
          expectedOrganizationId: intent.expectedOrganizationId, expectedOwnerId: intent.expectedOwnerId,
          operationId: intent.operationId, confirmed: true
        })

        if (response?.operationId !== intent.operationId || (response.state !== 'committed' && response.state !== 'cancelled') ||
          (response.state === 'cancelled' && response.receipt !== null)) {
          throw new Error('Invalid credential cancellation receipt')
        }

        const receipt = response.state === 'committed'
          ? validateCredentialReceipt(response.receipt, intent, previous.catalog?.entries.find(entry => entry.id === intent.entryId && entry.revision === intent.expectedRevision)?.version)
          : null

        clear(scope, intent)

        if (active(scope, sequence)) {
          publish(scope, { ...viewFor(scope), receipt, cancelledOperationId: receipt ? null : intent.operationId, intent: null, busy: false, error: null }, sequence)
          await client.read()
        }

        return true
      } catch {
        if (active(scope, sequence)) {
          publish(scope, { ...viewFor(scope), busy: false, error: 'uncertain' }, sequence)
        }

        return false
      }
    },
    async reconcile(): Promise<boolean> {
      const scope = readScope()
      const previous = viewFor(scope)
      const intent = previous.intent

      if (!eligible(scope) || previous.busy || !intent || !validCredentialIntent(intent, scope)) {
        return false
      }

      const sequence = (entries.get(scope.ownerKey)?.sequence ?? 0) + 1
      publish(scope, { ...previous, busy: true, error: null }, sequence)

      try {
        const response = await scope.request<{ receipt: unknown }>('organization.credentials.receipt', {
          expectedOrganizationId: intent.expectedOrganizationId, expectedOwnerId: intent.expectedOwnerId,
          operationId: intent.operationId
        })

        const raw = response?.receipt

        if (raw === null) {
          if (active(scope, sequence)) {
            publish(scope, { ...viewFor(scope), busy: false, error: 'uncertain' }, sequence)
          }

          return false
        }

        const receipt = validateCredentialReceipt(raw, intent, previous.catalog?.entries.find(entry => entry.id === intent.entryId && entry.revision === intent.expectedRevision)?.version)
        clear(scope, intent)

        if (active(scope, sequence)) {
          publish(scope, { ...viewFor(scope), receipt, intent: null, busy: false, error: null }, sequence)
          await client.read()
        }

        return true
      } catch {
        if (active(scope, sequence)) {
          publish(scope, { ...viewFor(scope), busy: false, error: 'uncertain' }, sequence)
        }

        return false
      }
    }
  }

  return client
}

export type OrganizationCredentialsClient = ReturnType<typeof createOrganizationCredentialsClient>
