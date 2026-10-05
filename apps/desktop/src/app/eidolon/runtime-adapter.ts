import { translateNow } from '@/i18n/runtime'

import { validEditProposal } from './runtime-proposal-validation'
import type { Objective, OrganizationArtifact, OrganizationExecutionAudit, OrganizationSnapshot, OrganizationToolEvidence, RuntimeOrganizationAdapter } from './types'

export interface OrganizationScope {
  /** Exact socket + registry connection + profile. Never use the profile alone. */
  key: string
  ownerKey?: string
  connected: boolean
  switching?: boolean
}
export interface OrganizationGateway {
  request<T>(method: string, params: Record<string, unknown>, timeoutMs: number, signal: AbortSignal): Promise<T>
  getScope(): OrganizationScope
  subscribeScope(listener: () => void): () => void
}

const emptySnapshot = (scope: OrganizationScope): OrganizationSnapshot => ({
  source: 'runtime', objectives: [], agents: [], tasks: [], activity: [], knowledge: [], requests: [],
  connection: { scope: scope.key, ownerScope: scope.ownerKey, state: scope.connected || scope.switching ? 'connecting' : 'disconnected' }
})

export function organizationErrorMessage(reason: unknown) {
  const text = reason instanceof Error ? reason.message : 'Could not reach the organization runtime.'

  return text.replace(/https?:\/\/[^\s"'<>]+/g, value => {
    try {const url = new URL(value);

 return `${url.origin}${url.pathname}`} catch {return '[connection URL]'}
  }).replace(/(bearer\s+)\S+/gi, '$1[redacted]').replace(/((?:token|password|api[_-]?key|secret)=)[^\s&]+/gi, '$1[redacted]')
}

function validateSnapshot(value: OrganizationSnapshot): OrganizationSnapshot {
  if (!value || value.source !== 'runtime' || !['objectives', 'agents', 'tasks', 'activity', 'knowledge', 'requests'].every(field => Array.isArray(value[field as keyof OrganizationSnapshot])) || !value.runtime) {
    throw new Error('This backend did not return a supported organization snapshot. Update the runtime and reconnect.')
  }

  return value
}

/** Server-owned truth. Reads are single-flight and bounded; losing the last
 * subscriber stops polling. Every response and write is bound to its socket
 * scope, and an obsolete scope aborts the transport before it can retry there. */
export function createRuntimeAdapter(gateway: OrganizationGateway): RuntimeOrganizationAdapter {
  let scope = gateway.getScope()
  let snapshot = emptySnapshot(scope)
  let epoch = 0
  let sequence = 0
  let lastApplied = 0
  let writeVersion = 0
  let pendingWrites = 0
  let failures = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  let offScope: (() => void) | undefined
  let reading: Promise<void> | undefined
  const controllers = new Set<AbortController>()
  const listeners = new Set<() => void>()
  const mutations = new Map<string, Promise<unknown>>()
  const createKeys = new Map<string, string>()
  const retryKeys = new Map<string, string>()
  const resolutionKeys = new Map<string, string>()
  const responseKeys = new Map<string, string>()
  const configurationKeys = new Map<string, string>()

  const publish = (next: OrganizationSnapshot) => {
    // A quiet snapshot poll must not repaint the whole organization graph.
    if (JSON.stringify(snapshot) === JSON.stringify(next)) {return}
    snapshot = next
    listeners.forEach(listener => listener())
  }

  const clearTimer = () => {
    if (timer !== undefined) {clearTimeout(timer); timer = undefined}
  }

  const current = (token: number) => token === epoch && gateway.getScope().key === scope.key

  const accept = (value: OrganizationSnapshot, order: number) => {
    if (order < lastApplied) {return}
    const next = validateSnapshot(value)
    lastApplied = order
    failures = 0
    const unchanged = JSON.stringify({ ...snapshot, connection: undefined }) === JSON.stringify({ ...next, connection: undefined })
    publish({ ...next, connection: { scope: scope.key, ownerScope: scope.ownerKey, state: 'ready', lastUpdatedAt: unchanged ? snapshot.connection?.lastUpdatedAt : new Date().toISOString() } })
  }

  const schedule = (delay?: number) => {
    clearTimer()

    if (!listeners.size || !scope.connected || failures >= 3 || pendingWrites) {return}
    const active = snapshot.requests?.some(request => request.status === 'queued' || request.status === 'running')
    timer = setTimeout(() => { timer = undefined; void refresh() }, delay ?? (failures ? 2500 * 2 ** failures : active ? 2500 : 15000))
  }

  const resetScope = () => {
    const next = gateway.getScope()

    if (next.key === scope.key && next.connected === scope.connected && next.switching === scope.switching) {return false}
    const changed = next.key !== scope.key
    const ownerChanged = scope.ownerKey && next.ownerKey ? scope.ownerKey !== next.ownerKey : changed
    epoch++
    clearTimer()
    controllers.forEach(controller => controller.abort())
    controllers.clear()
    reading = undefined
    pendingWrites = 0
    mutations.clear()

    scope = next
    failures = 0
    publish(ownerChanged ? emptySnapshot(scope) : { ...snapshot, connection: { ...snapshot.connection, scope: scope.key, ownerScope: scope.ownerKey, state: scope.connected || scope.switching ? 'connecting' : 'disconnected', error: undefined } })

    return true
  }

  const watchScope = () => {
    if (!offScope) {
      offScope = gateway.subscribeScope(() => {
        if (resetScope() && listeners.size) {void Promise.resolve().then(() => {if (listeners.size) {void refresh()}})}
      })
    }
  }

  const releaseScope = () => {
    if (!listeners.size && !controllers.size) {offScope?.(); offScope = undefined}
  }

  async function refresh(): Promise<void> {
    resetScope()

    if (!scope.connected || pendingWrites) {return}

    if (reading) {return reading}
    clearTimer()
    const token = epoch
    const version = writeVersion
    const order = ++sequence
    const controller = new AbortController()
    controllers.add(controller)
    watchScope()

    const read = (async () => {
      try {
        const result = await Promise.resolve().then(() => gateway.request<OrganizationSnapshot>('organization.snapshot', {}, 15000, controller.signal))

        if (current(token) && version === writeVersion && !controller.signal.aborted) {accept(result, order)}
      } catch (reason) {
        if (current(token) && version === writeVersion && order >= lastApplied && !controller.signal.aborted) {
          failures++
          publish({ ...snapshot, connection: { ...snapshot.connection, scope: scope.key, ownerScope: scope.ownerKey, state: 'error', error: organizationErrorMessage(reason) } })
        }
      } finally {
        controllers.delete(controller)
        releaseScope()

        if (current(token)) {reading = undefined; schedule()}
      }
    })()

    reading = read

    return read
  }

  function mutate<T>(key: string, method: string, params: Record<string, unknown>, resultSnapshot: (value: T) => OrganizationSnapshot): Promise<T> {
    resetScope()
    const existing = mutations.get(key)

    if (existing) {return existing as Promise<T>}

    if (!scope.connected) {return Promise.reject(new Error('Connect to an organization runtime before submitting work.'))}
    clearTimer()
    const token = epoch
    const order = ++sequence
    writeVersion++
    pendingWrites++
    const controller = new AbortController()
    controllers.add(controller)
    watchScope()

    const result = (async () => {
      try {
        const value = await Promise.resolve().then(() => {
          if (!current(token) || controller.signal.aborted) {throw new Error('The connection or profile changed before the request was sent.')}

          return gateway.request<T>(method, params, 20000, controller.signal)
        })

        if (!current(token) || controller.signal.aborted) {throw new Error('The connection or profile changed. Reopen the original profile to check this request.')}
        accept(resultSnapshot(value), order)

        return value
      } catch (reason) {throw new Error(organizationErrorMessage(reason))} finally {
        controllers.delete(controller)
        releaseScope()

        if (current(token)) {
          pendingWrites--
          mutations.delete(key)

          if (!pendingWrites) {schedule(0)}
        }
      }
    })()

    mutations.set(key, result)

    return result
  }

  return {
    mode: 'runtime',
    async getEvidence(id) {
      resetScope()
      const token = epoch
      const controller = new AbortController()

      if (!scope.connected) {throw new Error('Reconnect the organization runtime to read the full artifact.')}
      controllers.add(controller)
      watchScope()

      try {
        const result = await gateway.request<OrganizationArtifact>('organization.evidence', { id }, 15000, controller.signal)

        if (!current(token) || controller.signal.aborted) {throw new Error('The connection or profile changed. Reopen the artifact in its original profile.')}

        if (!result || result.id !== id || typeof result.content !== 'string' || typeof result.sha256 !== 'string') {throw new Error('The runtime returned an invalid evidence record.')}

        if (result.editProposal != null && !validEditProposal(result.editProposal)) {throw new Error(translateNow('organizationRuntime.edits.invalid'))}

        return result
      } catch (reason) {throw new Error(organizationErrorMessage(reason))} finally {controllers.delete(controller); releaseScope()}
    },
    async getToolReceipts(requestId) {
      resetScope()
      const token = epoch
      const controller = new AbortController()

      if (!scope.connected) {throw new Error(translateNow('organizationRuntime.auditReconnect'))}
      controllers.add(controller)
      watchScope()

      try {
        const result = await gateway.request<OrganizationToolEvidence[]>('organization.toolReceipts', { id: requestId }, 15000, controller.signal)

        if (!current(token) || controller.signal.aborted) {throw new Error(translateNow('organizationRuntime.auditScopeChanged'))}

        if (!Array.isArray(result) || result.some(receipt => !receipt || typeof receipt.id !== 'string' || typeof receipt.toolCallId !== 'string' || typeof receipt.toolName !== 'string' || !receipt.arguments || typeof receipt.arguments !== 'object' || typeof receipt.createdAt !== 'string' || receipt.requestId !== requestId || (receipt.result !== undefined && typeof receipt.result !== 'string'))) {throw new Error(translateNow('organizationRuntime.auditInvalid'))}

        return result
      } catch (reason) {throw new Error(organizationErrorMessage(reason))} finally {controllers.delete(controller); releaseScope()}
    },
    async getExecutionAudit(requestId) {
      resetScope()
      const token = epoch
      const controller = new AbortController()

      if (!scope.connected) {throw new Error(translateNow('organizationRuntime.auditReconnect'))}
      controllers.add(controller)
      watchScope()

      try {
        const result = await gateway.request<OrganizationExecutionAudit>('organization.executionAudit', { id: requestId }, 15000, controller.signal)

        if (!current(token) || controller.signal.aborted) {throw new Error(translateNow('organizationRuntime.auditScopeChanged'))}
        const validReport = (row: OrganizationExecutionAudit['contexts'][number]) => row && typeof row.attemptToken === 'string' && typeof row.createdAt === 'string' && row.report && typeof row.report === 'object' && !Array.isArray(row.report)

        if (!result || result.requestId !== requestId || !Array.isArray(result.contexts) || !Array.isArray(result.evidencePasses) || !Array.isArray(result.modelCalls) || !result.contexts.every(validReport) || !result.evidencePasses.every(validReport) || result.modelCalls.some(call => !call || call.request_id !== requestId || typeof call.id !== 'string' || typeof call.provider !== 'string' || typeof call.model !== 'string' || typeof call.createdAt !== 'string' || !Number.isFinite(call.input_limit) || !Number.isFinite(call.output_limit) || (call.reserved_cost_usd !== null && typeof call.reserved_cost_usd !== 'string'))) {throw new Error(translateNow('organizationWork.executionAuditInvalid'))}

        return result
      } catch (reason) {throw new Error(organizationErrorMessage(reason))} finally {controllers.delete(controller); releaseScope()}
    },
    getSnapshot: () => snapshot,
    subscribe(listener) {
      listeners.add(listener)

      if (listeners.size === 1) {
        watchScope()
        void Promise.resolve().then(() => { if (listeners.size) {void refresh()} })
      }

      return () => {
        listeners.delete(listener)

        if (!listeners.size) {
          offScope?.()
          offScope = undefined
          clearTimer()
          epoch++
          controllers.forEach(controller => controller.abort())
          controllers.clear()
          reading = undefined
          pendingWrites = 0
          mutations.clear()
        }
      }
    },
    refresh: () => { failures = 0;

 return refresh() },
    createObjective(input, metadata = {}, idempotencyKey) {
      resetScope()
      const title = input.trim()

      if (!title) {return Promise.reject(new Error('Describe an objective before submitting.'))}
      // Only fields the runtime implements cross this boundary. In particular,
      // prototype owner/status/progress edits never authorize runtime work.
      const params = { title, executiveId: metadata.executiveId, managerId: metadata.managerId, description: metadata.description?.trim() || undefined, priority: metadata.priority, acceptanceCriteria: metadata.acceptanceCriteria, deliveryMode: metadata.deliveryMode, requiredChecks: metadata.requiredChecks }
      const intent = JSON.stringify([scope.ownerKey ?? scope.key, params])
      const key = idempotencyKey ?? createKeys.get(intent) ?? crypto.randomUUID()
      createKeys.set(intent, key)

      return mutate<{ objective: Objective; snapshot: OrganizationSnapshot }>(`create:${key}`, 'organization.create', { ...params, idempotencyKey: key }, result => result.snapshot).then(result => {
        if (createKeys.get(intent) === key) {createKeys.delete(intent)}

        return result.objective
      })
    },
    resolveRequest(input) {
      resetScope()
      const params = { id: input.id, action: input.action, text: input.text?.trim() || undefined, evidenceIds: input.evidenceIds, requiredChecks: input.requiredChecks, acceptanceCriteria: input.acceptanceCriteria }
      const intent = JSON.stringify([scope.ownerKey ?? scope.key, params])
      const idempotencyKey = input.idempotencyKey ?? resolutionKeys.get(intent) ?? crypto.randomUUID()
      resolutionKeys.set(intent, idempotencyKey)

      return mutate<OrganizationSnapshot>(`resolve:${input.id}:${idempotencyKey}`, 'organization.resolve', { ...params, idempotencyKey }, value => value).then(() => {
        if (resolutionKeys.get(intent) === idempotencyKey) {resolutionKeys.delete(intent)}
      })
    },
    respondRequest(input) {
      resetScope()
      const params = { id: input.id, text: input.text.trim(), decision: input.decision }
      const intent = JSON.stringify([scope.ownerKey ?? scope.key, params])
      const idempotencyKey = input.idempotencyKey ?? responseKeys.get(intent) ?? crypto.randomUUID()
      responseKeys.set(intent, idempotencyKey)

      return mutate<OrganizationSnapshot>(`respond:${input.id}:${idempotencyKey}`, 'organization.respond', { ...params, idempotencyKey }, value => value).then(() => {
        if (responseKeys.get(intent) === idempotencyKey) {responseKeys.delete(intent)}
      })
    },
    configureOrganization(input) {
      resetScope()
      const params = { configuration: input.configuration, expectedGeneration: input.expectedGeneration }
      const intent = JSON.stringify([scope.ownerKey ?? scope.key, params])
      const idempotencyKey = input.idempotencyKey ?? configurationKeys.get(intent) ?? crypto.randomUUID()
      configurationKeys.set(intent, idempotencyKey)

      return mutate<OrganizationSnapshot>(`configure:${idempotencyKey}`, 'organization.configure', { ...params, idempotencyKey }, value => value).then(() => {
        if (configurationKeys.get(intent) === idempotencyKey) {configurationKeys.delete(intent)}
      })
    },
    cancelObjective: id => mutate<OrganizationSnapshot>(`cancel:${id}`, 'organization.cancel', { id }, value => value).then(() => undefined),
    retryRequest(id) {
      resetScope()
      const attempt = snapshot.requests?.find(request => request.id === id)?.attempts ?? 0
      const intent = JSON.stringify([scope.ownerKey ?? scope.key, id, attempt])
      const idempotencyKey = retryKeys.get(intent) ?? crypto.randomUUID()
      retryKeys.set(intent, idempotencyKey)

      return mutate<OrganizationSnapshot>(`retry:${id}`, 'organization.retry', { id, idempotencyKey }, value => value).then(() => {
        if (retryKeys.get(intent) === idempotencyKey) {retryKeys.delete(intent)}
      })
    }
  }
}
