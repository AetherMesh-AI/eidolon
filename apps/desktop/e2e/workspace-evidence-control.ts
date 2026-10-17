import { createGalleryObservationControl } from './workspace-observation-control'

// Keep the composite native project independent of the renderer type graph.
export interface SyntheticEvidence {
  id: string
  content: string
  summary: string
  sha256: string
  createdAt: string
  objectiveId: string
  taskId: string | null
}

/** Opt-in, read-only evidence transport. Never dispatches a synthetic ID. */
export function createGalleryEvidenceControl(profile: string) {
  const control = createGalleryObservationControl()
  const state = { artifacts: {} as Record<string, SyntheticEvidence>, error: null as string | null }
  const reads: { profile: string; id: string; wrapped: boolean }[] = []
  const rejected: string[] = []

  return {
    control, state, reads, rejected,
    handle(request: { id?: number; method?: string; params?: { id?: string; method?: string; params?: { id?: string } } }, send: (value: object) => void, current: () => boolean) {
      const wrapped = request.method === 'organization.observeRead'
      const method = wrapped ? request.params?.method : request.method

      if (method !== 'organization.evidence') {
        if (wrapped && !['organization.outcomes', 'organization.attention'].includes(method ?? '')) {
          rejected.push(method ?? '')
          send({ jsonrpc: '2.0', id: request.id, error: { code: -32000, message: 'Synthetic contextual proof rejects this observation method.' } })

          return true
        }

        return false
      }

      const id = (wrapped ? request.params?.params?.id : request.params?.id) ?? ''
      reads.push({ profile, id, wrapped })
      control.respond(() => {
        const artifact = state.artifacts[id]

        if (state.error || !artifact || artifact.id !== id) {
          send({ jsonrpc: '2.0', id: request.id, error: { code: -32000, message: state.error ?? `Synthetic evidence unavailable: ${id}` } })
        } else {
          const result = wrapped ? { capabilityVersion: 1, state: 'available', scope: { kind: 'served-profile', profile }, data: artifact } : artifact
          send({ jsonrpc: '2.0', id: request.id, result })
        }
      }, current)

      return true
    }
  }
}
