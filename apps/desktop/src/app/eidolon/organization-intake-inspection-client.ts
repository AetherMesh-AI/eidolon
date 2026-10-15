import type { MetadataScope } from './organization-metadata-client'

export interface IntakeInspectionConfiguration {
  enabled: boolean
  workerId: string | null
  projectIds: string[]
  paths: string[]
}
export interface IntakeInspectionDescriptor extends IntakeInspectionConfiguration {
  profile: string
  revision: number
  organizationId: string | null
  workers: { id: string; name: string; team: string }[]
  projects: { id: string; root: string; team: string }[]
}
export interface IntakeInspectionDraft {
  enabled: boolean
  workerId: string
  projectIds: string[]
  pathsText: string
}
export interface IntakeInspectionIntent extends IntakeInspectionConfiguration {
  expectedOrganizationId: string
  expectedRevision: number
  confirmed: true
  idempotencyKey: string
}
interface IntakeInspectionPreview extends IntakeInspectionConfiguration {
  expectedOrganizationId: string
  expectedRevision: number
}
export interface IntakeInspectionView {
  descriptor: IntakeInspectionDescriptor | null
  draft: IntakeInspectionDraft | null
  preview: IntakeInspectionPreview | null
  intent: IntakeInspectionIntent | null
  busy: boolean
  saved: boolean
  error: 'invalid' | 'readFailed' | 'unsupported' | 'uncertain' | null
}

export const inspectionContextFiles = [
  'README.md',
  'README.rst',
  'README.txt',
  'package.json',
  'pyproject.toml',
  'Cargo.toml',
  'go.mod',
  'context.md',
  'architecture.md'
]
const filenames = new Set(inspectionContextFiles.map(name => name.toLowerCase()))
const text = (value: unknown): value is string => typeof value === 'string' && value.length > 0

const texts = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every(text) && new Set(value).size === value.length

const object = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value)

const path = (value: string) => /^root[0-7]\/[^/\\]+$/.test(value) && filenames.has(value.split('/')[1].toLowerCase())

const configuration = (value: IntakeInspectionConfiguration) => ({
  enabled: value.enabled,
  workerId: value.workerId,
  projectIds: value.projectIds,
  paths: value.paths
})

const empty = (): IntakeInspectionView => ({
  descriptor: null,
  draft: null,
  preview: null,
  intent: null,
  busy: false,
  saved: false,
  error: null
})

function descriptor(value: unknown, profile: string): IntakeInspectionDescriptor {
  if (
    !object(value) ||
    value.profile !== profile ||
    typeof value.enabled !== 'boolean' ||
    !Number.isSafeInteger(value.revision) ||
    (value.revision as number) < 0 ||
    !(value.organizationId === null || text(value.organizationId)) ||
    !(value.workerId === null || text(value.workerId)) ||
    !texts(value.projectIds) ||
    !texts(value.paths) ||
    value.paths.length > 4 ||
    !value.paths.every(path) ||
    !Array.isArray(value.workers) ||
    !value.workers.every(worker => object(worker) && text(worker.id) && text(worker.name) && text(worker.team)) ||
    !Array.isArray(value.projects) ||
    !value.projects.every(
      project =>
        object(project) &&
        text(project.id) &&
        text(project.team) &&
        typeof project.root === 'string' &&
        /^root[0-7]$/.test(project.root)
    ) ||
    new Set(value.workers.map(worker => worker.id)).size !== value.workers.length ||
    new Set(value.projects.map(project => project.id)).size !== value.projects.length ||
    (value.enabled
      ? !value.organizationId || !value.workerId || !value.projectIds.length || !value.paths.length
      : value.workerId !== null || value.projectIds.length !== 0 || value.paths.length !== 0)
  ) {
    throw new Error('Invalid inspection configuration')
  }

  return structuredClone(value) as unknown as IntakeInspectionDescriptor
}

function draftFrom(value: IntakeInspectionDescriptor): IntakeInspectionDraft {
  return {
    enabled: value.enabled,
    workerId: value.workerId ?? '',
    projectIds: [...value.projectIds],
    pathsText: value.paths.join('\n')
  }
}

/** No reads or previews delegate access. A confirmed write stays on its captured authenticated socket. */
export function createIntakeInspectionClient(
  source: () => MetadataScope,
  newKey: () => string = () => crypto.randomUUID()
) {
  let identity = ''
  let epoch = 0
  let sequence = 0
  let view = empty()
  const listeners = new Set<() => void>()

  const scopeNow = () => {
    const scope = source()
    const next = JSON.stringify([scope.key, scope.ownerKey, scope.profile, scope.eligible])

    if (next !== identity) {
      identity = next
      epoch++
      sequence++
      // New authority must read and review again. No draft or callback crosses a scope rotation.
      view = empty()
    }

    return { ...scope, epoch }
  }

  const publish = (next: IntakeInspectionView) => {
    view = next
    listeners.forEach(listener => listener())
  }

  const current = (scope: ReturnType<typeof scopeNow>, request: number) => {
    const now = scopeNow()

    return now.eligible && now.epoch === scope.epoch && request === sequence
  }

  const send = async (scope: ReturnType<typeof scopeNow>, intent: IntakeInspectionIntent) => {
    const request = ++sequence
    publish({ ...view, preview: null, intent, busy: true, error: null, saved: false })

    try {
      // Clone the transport input: even a mutating transport cannot change a retained retry.
      const result = await scope.request(
        'organization.intake.inspection.configure',
        structuredClone(intent) as unknown as Record<string, unknown>
      )

      if (!current(scope, request)) {
        return
      }

      const value = descriptor(result, scope.profile)

      if (
        value.organizationId !== intent.expectedOrganizationId ||
        value.revision !== intent.expectedRevision + 1 ||
        JSON.stringify(configuration(value)) !== JSON.stringify(configuration(intent))
      ) {
        throw new Error('Unconfirmed inspection configuration')
      }

      publish({
        descriptor: value,
        draft: draftFrom(value),
        preview: null,
        intent: null,
        busy: false,
        saved: true,
        error: null
      })
    } catch {
      if (current(scope, request)) {
        publish({ ...view, busy: false, error: 'uncertain' })
      }
    }
  }

  return {
    subscribe(listener: () => void) {
      listeners.add(listener)

      return () => {
        listeners.delete(listener)
      }
    },
    getSnapshot() {
      scopeNow()

      return view
    },
    async read() {
      const scope = scopeNow()

      if (!scope.eligible || view.busy || view.intent || view.preview) {
        return
      }

      const request = ++sequence
      publish({ ...empty(), busy: true })

      try {
        const result = await scope.request('organization.intake.inspection.read', {})

        if (!current(scope, request)) {
          return
        }

        const value = descriptor(result, scope.profile)
        publish({ ...empty(), descriptor: value, draft: draftFrom(value) })
      } catch (reason) {
        if (current(scope, request)) {
          publish({ ...empty(), error: (reason as { code?: number })?.code === -32601 ? 'unsupported' : 'readFailed' })
        }
      }
    },
    edit(patch: Partial<IntakeInspectionDraft>) {
      const scope = scopeNow()

      if (!scope.eligible || !view.draft || view.busy || view.intent || view.preview) {
        return
      }

      let draft = { ...view.draft, ...patch, projectIds: [...(patch.projectIds ?? view.draft.projectIds)] }

      if (patch.workerId !== undefined && patch.workerId !== view.draft.workerId) {
        draft = { ...draft, projectIds: [], pathsText: '' }
      }

      publish({ ...view, draft, error: null, saved: false })
    },
    review() {
      const scope = scopeNow()
      const { descriptor: value, draft } = view

      if (!scope.eligible || !value?.organizationId || !draft || view.busy || view.intent || view.preview) {
        return
      }

      const worker = value.workers.find(item => item.id === draft.workerId)
      const projects = draft.projectIds.map(id => value.projects.find(item => item.id === id))

      const paths = draft.pathsText
        .split('\n')
        .map(item => item.trim())
        .filter(Boolean)

      const roots = projects.map(project => project?.root)

      if (
        draft.enabled &&
        (!worker ||
          !projects.length ||
          projects.some(project => !project || project.team !== worker.team) ||
          new Set(draft.projectIds).size !== draft.projectIds.length ||
          !texts(paths) ||
          paths.length < 1 ||
          paths.length > 4 ||
          paths.some(item => !path(item) || !roots.includes(item.split('/')[0])))
      ) {
        publish({ ...view, error: 'invalid' })

        return
      }

      const preview = {
        expectedOrganizationId: value.organizationId,
        expectedRevision: value.revision,
        enabled: draft.enabled,
        workerId: draft.enabled ? draft.workerId : null,
        projectIds: draft.enabled ? [...draft.projectIds] : [],
        paths: draft.enabled ? paths : []
      }

      Object.freeze(preview.paths)
      Object.freeze(preview.projectIds)
      Object.freeze(preview)
      publish({ ...view, preview, error: null, saved: false })
    },
    cancel() {
      scopeNow()

      if (!view.busy && !view.intent) {
        publish({ ...view, preview: null })
      }
    },
    async confirm() {
      const scope = scopeNow()

      if (!scope.eligible || !view.preview || view.busy || view.intent) {
        return
      }

      const intent = { ...view.preview, confirmed: true as const, idempotencyKey: newKey() }
      Object.freeze(intent)
      await send(scope, intent)
    },
    async retry() {
      const scope = scopeNow()

      if (!scope.eligible || !view.intent || view.busy) {
        return
      }

      await send(scope, view.intent)
    }
  }
}
