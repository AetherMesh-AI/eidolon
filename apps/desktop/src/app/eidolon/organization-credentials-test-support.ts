import { vi } from 'vitest'

import { createOrganizationCredentialsClient } from './organization-credentials-client'
import { credentialMetadata, credentialName } from './organization-credentials-contract'
import type { CredentialCatalog, CredentialEntry, CredentialIntent, CredentialReceipt, CredentialScope } from './organization-credentials-types'

export const identity = { organizationId: '11111111-1111-4111-8111-111111111111', ownerId: '22222222-2222-4222-8222-222222222222' }
export const entryFixture = (): CredentialEntry => ({
  id: '33333333-3333-4333-8333-333333333333', organizationId: identity.organizationId,
  service: 'github', label: 'eidolonReleases', type: 'token', name: 'github_eidolonReleases_token',
  description: 'Synthetic release credential', kind: 'secret', expiresAt: null, revision: 3, version: 1,
  status: 'available', createdAt: 1792108800, updatedAt: 1792108800, updatedBy: identity.ownerId,
  permissions: ['discover', 'edit_metadata', 'replace_value', 'revoke', 'remove'], inFlight: 0
})
export const catalogFixture = (): CredentialCatalog => ({
  version: 1, organizationId: identity.organizationId, backend: { provider: 'synthetic-test', state: 'available' },
  entries: [entryFixture()], agents: [{ id: 'member-1', identityId: 'identity-1', name: 'Synthetic manager' }], grants: [{ actor: 'identity-1', scope: entryFixture().id, revision: 2, permissions: ['discover'], bindings: [] }],
  audit: [], tombstones: [], canCreate: true, environmentInjection: false, providerCredentialCreation: false, secureInputAvailable: true
})

export function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })

  return { promise, resolve, reject }
}

export async function credentialsHarness() {
  const data = catalogFixture()
  const values = new Map<string, string>()
  const receipts = new Map<string, CredentialReceipt>()

  const storage = { getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) }, removeItem: (key: string) => { values.delete(key) } }

  const request = vi.fn(async (method: string, params: Record<string, unknown>): Promise<unknown> => {
    if (method === 'organization.credentials.catalog') {
      return structuredClone(data)
    }

    if (method === 'organization.credentials.receipt') {
      return { receipt: receipts.get(String(params.operationId)) ?? null }
    }

    if (method === 'organization.credentials.cancelOperation') {
      const receipt = receipts.get(String(params.operationId)) ?? null

      return { operationId: params.operationId, state: receipt ? 'committed' : 'cancelled', receipt }
    }

    if (method === 'organization.credentials.secureInput') {
      return { inputHandle: 'synthetic-handle-do-not-retain', expiresIn: 90 }
    }

    if (method === 'organization.credentials.configureGrant') {
      const existing = data.grants.find(grant => grant.actor === params.actorId && grant.scope === params.scope)

      const grant = { actor: String(params.actorId), scope: String(params.scope), revision: Number(params.expectedRevision) + 1,
        permissions: params.permissions as string[], bindings: [] }

      if (existing) {
        Object.assign(existing, grant)
      } else {
        data.grants.push(grant)
      }

      const receipt: CredentialReceipt = { actorId: grant.actor, scope: grant.scope, revision: grant.revision,
        operationId: String(params.operationId), action: 'configure_grant' }

      receipts.set(receipt.operationId, receipt)

      return receipt
    }

    const intent = params as unknown as CredentialIntent
    const previous = data.entries.find(entry => entry.id === intent.entryId)
    const entry = previous ?? { ...entryFixture(), id: '44444444-4444-4444-8444-444444444444', revision: 0, version: 0, status: 'missing' as const }

    if (intent.metadata) {
      Object.assign(entry, credentialMetadata(intent.metadata), { name: credentialName(intent.metadata) })
    }

    entry.revision++

    if (intent.action === 'replace_value') {
      entry.version++
    }

    if (intent.action === 'revoke') {
      entry.status = 'revoked'
    }

    if (intent.action === 'remove') {
      data.entries = data.entries.filter(item => item.id !== entry.id)
    } else if (!previous) {
      data.entries.push(entry)
    }

    const receipt: CredentialReceipt = { entryId: entry.id, operationId: intent.operationId, action: intent.action,
      revision: entry.revision, version: entry.version, ...(['revoke', 'remove'].includes(intent.action) ? {
        inFlight: 0, queuedCancelled: 0, providerRevoked: false as const, storedValueDeleted: false as const
      } : {}) }

    receipts.set(intent.operationId, receipt)

    return receipt
  })

  const scope: CredentialScope = { key: 'socket-a', ownerKey: 'owner-a', profile: 'default', eligible: true, ...identity,
    request: request as CredentialScope['request'] }

  let number = 0
  const newKey = vi.fn(() => `operation-${++number}`)
  const makeClient = () => createOrganizationCredentialsClient(() => scope, newKey, storage)
  const client = makeClient()
  await client.read()

  return { client, scope, data, values, receipts, request, storage, makeClient, newKey }
}
