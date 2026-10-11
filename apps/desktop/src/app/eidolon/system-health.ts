import type { RuntimeReadinessResult } from '@/lib/runtime-readiness'

import type { OrganizationSnapshot } from './types'

export type SystemHealthState = 'disconnected' | 'unknown' | 'stale' | 'disabled' | 'degraded' | 'attention' | 'healthy'

/** A running gateway process alone is never proof of an operational organization. */
export function systemHealth(
  gateway: string,
  snapshot: OrganizationSnapshot | undefined,
  readiness: RuntimeReadinessResult | null,
  fresh: boolean,
  attention: number,
  signals: { staleWork?: boolean; failedAgents?: number } = {}
): SystemHealthState {
  if (!['open', 'connecting'].includes(gateway) || snapshot?.connection?.state === 'disconnected') {
    return 'disconnected'
  }

  if ((signals.failedAgents ?? 0) > 0) {
    return 'degraded'
  }

  if (snapshot?.connection?.state === 'error') {
    return 'degraded'
  }

  if (gateway !== 'open' || !snapshot?.runtime || !snapshot.connection) {
    return 'unknown'
  }

  if (signals.staleWork || snapshot.connection.state !== 'ready' || (!fresh && readiness !== null)) {
    return 'stale'
  }

  if (
    ['error', 'failed', 'degraded'].includes(snapshot.runtime.state) ||
    (fresh && readiness && (!readiness.ready || readiness.checksDisagree))
  ) {
    return 'degraded'
  }

  if (snapshot.runtime.state === 'disabled' || snapshot.runtime.setup?.backgroundOptIn === false) {
    return 'disabled'
  }

  if (!fresh || !readiness) {
    return 'unknown'
  }

  if (readiness.source !== 'runtime_check' || snapshot.runtime.setup?.backgroundOptIn !== true) {
    return 'unknown'
  }

  if (!['ready', 'running', 'idle'].includes(snapshot.runtime.state)) {
    return 'unknown'
  }

  if (attention > 0) {
    return 'attention'
  }

  return 'healthy'
}
