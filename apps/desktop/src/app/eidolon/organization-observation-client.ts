import { validateSnapshot } from './runtime-snapshot-contract'
import type { OrganizationSnapshot } from './types'

export interface ObservationScope {
  /** Exact authenticated socket generation + registry connection + profile. */
  key: string
  profile: string
  /** False for disconnected, switching, or shared sockets serving another profile. */
  eligible: boolean
  /** Captured transport: must never reconnect or route through another socket. */
  request<T>(method: string, params: Record<string, unknown>, timeoutMs: number, signal: AbortSignal): Promise<T>
}
export interface ObservationGateway {
  getScope(): ObservationScope
  /** Emit every transition, including intermediate A → B → A transitions. */
  subscribeScope(listener: () => void): () => void
}
export interface OrganizationObservation {
  capabilityVersion: 1
  state: 'available' | 'unconfigured' | 'unsupported'
  snapshot: OrganizationSnapshot | null
  scope: { kind: 'served-profile'; profile: string }
  reason?: string
}
export interface ObservationView {
  observation: OrganizationObservation | null
  busy: boolean
  error: 'unavailable' | 'unsupported' | 'invalid' | 'storage' | null
}

function validate(value: unknown, profile: string): OrganizationObservation {
  const item = value as OrganizationObservation

  if (!item || item.capabilityVersion !== 1 || item.scope?.kind !== 'served-profile' ||
    item.scope.profile !== profile || !['available', 'unconfigured', 'unsupported'].includes(item.state)) {
    throw new Error('Invalid observation')
  }

  if (item.state === 'available') {
    return { ...item, snapshot: validateSnapshot(item.snapshot as OrganizationSnapshot) }
  }

  if (item.snapshot !== null) {throw new Error('Invalid absent observation')}

  return item
}

/** Explicit reads only. This client never acquires execution, polls, reconnects,
 * or falls back to the legacy starting snapshot RPC. It is not a selector. */
export function createOrganizationObservationClient(gateway: ObservationGateway) {
  let scope = gateway.getScope()
  let epoch = 0
  let disposed = false
  let pending: { controller: AbortController; promise: Promise<void> } | undefined
  let view: ObservationView = { observation: null, busy: false, error: 'unavailable' }
  const listeners = new Set<() => void>()

  const publish = (next: ObservationView) => {view = next; listeners.forEach(listener => listener())}

  const offScope = gateway.subscribeScope(() => {
    epoch++
    pending?.controller.abort()
    pending = undefined
    scope = gateway.getScope()
    publish({ observation: null, busy: false, error: 'unavailable' })
  })

  const read = (): Promise<void> => {
    if (disposed || !scope.eligible) {return Promise.resolve()}

    if (pending) {return pending.promise}

    const captured = { ...scope }
    const generation = epoch
    const controller = new AbortController()
    const current = () => !disposed && generation === epoch

    const promise = Promise.resolve().then(async () => {
      if (!current()) {return}
      publish({ observation: null, busy: true, error: null })

      if (!current()) {return}

      let cancel = () => {}
      let timer: ReturnType<typeof setTimeout> | undefined

      const timeout = new Promise<never>((_, reject) => {
        cancel = () => reject(new Error('Observation cancelled'))
        controller.signal.addEventListener('abort', cancel, { once: true })
        timer = setTimeout(() => {
          reject(new Error('Observation timed out'))
          controller.abort()
        }, 10000)
      })

      try {
        const response = await Promise.race([
          captured.request<unknown>('organization.observe', {}, 10000, controller.signal), timeout
        ])

        if (!current()) {return}

        let observation: OrganizationObservation

        try {observation = validate(response, captured.profile)} catch {
          publish({ observation: null, busy: false, error: 'invalid' })

          return
        }

        publish({ observation, busy: false, error: null })
      } catch (error) {
        if (!current()) {return}
        const code = (error as { code?: number } | null)?.code

        publish({ observation: null, busy: false,
          error: code === -32601 ? 'unsupported' : code === 5073 ? 'storage' : 'unavailable' })
      } finally {
        clearTimeout(timer)
        controller.signal.removeEventListener('abort', cancel)

        if (current()) {pending = undefined}
      }
    })

    pending = { controller, promise }

    return promise
  }

  return {
    read,
    getSnapshot: () => view,
    subscribe(listener: () => void) {listeners.add(listener);

 return () => listeners.delete(listener)},
    dispose() {disposed = true; epoch++; pending?.controller.abort(); pending = undefined; offScope(); listeners.clear()}
  }
}
