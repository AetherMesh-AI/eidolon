import { vi } from 'vitest'

import { createCredentialHandoffClient } from './organization-credential-handoff-client'
import type { PreparationReceipt, ReplacementIntent, ReplacementStatus, ReplacementTarget } from './organization-credential-handoff-types'
import { catalogFixture, entryFixture, identity } from './organization-credentials-test-support'
import type { CredentialScope } from './organization-credentials-types'

export const targetFixture = (): ReplacementTarget => ({ actorId: 'identity-1', requestId: 'request-1', generation: 'generation-1',
  entryId: entryFixture().id, expectedRevision: entryFixture().revision, grantRevision: 2 })

export function preparationFixture(intent: ReplacementIntent): PreparationReceipt {
  return { action: 'prepare_replacement', entryId: intent.entryId, expectedRevision: intent.expectedRevision,
    actorId: intent.actorId, requestId: intent.requestId, generation: intent.generation, operationId: intent.operationId,
    workerOperationId: `worker-${intent.operationId}`, handoffId: `handoff-${intent.operationId}`, nextVersion: intent.nextVersion,
    grantRevision: intent.expectedGrantRevision, createdAt: 1792108800, expiresAt: 1792108890 }
}

export function handoffHarness() {
  const data = catalogFixture()
  const values = new Map<string, string>()
  const statuses = new Map<string, ReplacementStatus>()

  const storage = { getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) }, removeItem: (key: string) => { values.delete(key) } }

  const request = vi.fn(async (method: string, params: Record<string, unknown>): Promise<unknown> => {
    if (method.endsWith('.replacementTargets')) { return { targets: [targetFixture()] } }

    if (method.endsWith('.prepareReplacement')) {
      const intent = { ...params, nextVersion: data.entries[0].version + 1 } as unknown as ReplacementIntent
      const receipt = preparationFixture(intent)
      statuses.set(intent.operationId, { operationId: intent.operationId, state: 'pending', receipt })

      return receipt
    }

    const operationId = String(params.operationId)
    const status = statuses.get(operationId) ?? { operationId, state: 'absent' as const, receipt: null }

    if (method.endsWith('.cancelReplacement') && status.state !== 'committed') {
      const result = { ...status, state: 'cancelled' as const }
      statuses.set(operationId, result)

      return result
    }

    return status
  })

  const scope: CredentialScope = { key: 'socket-a', ownerKey: 'owner-a', profile: 'default', eligible: true, ...identity,
    request: request as CredentialScope['request'] }

  let number = 0
  const makeClient = () => createCredentialHandoffClient(() => scope, () => `owner-operation-${++number}`, storage)
  const client = makeClient()
  client.restore()

  return { client, makeClient, data, values, statuses, storage, scope, request }
}
