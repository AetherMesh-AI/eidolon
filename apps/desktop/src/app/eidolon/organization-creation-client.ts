import type { ConnectionSwitchOutcome } from '@/store/connections'

import {
  createdWorkspace,
  type CreatedWorkspace,
  creationCapability,
  isolatedWorkspaceIdentity,
  restoreCreationIntent,
  validWorkspaceModel,
  validWorkspaceName,
  verifyCreatedWorkspace,
  type WorkspaceCreationCapability,
  type WorkspaceCreationIntent
} from './organization-creation-contract'
import { sourceRuntimeCapability, type SourceRuntimeCapability } from './organization-source-capability'
import type { DirectorySource } from './organization-directory'
import { organizationEndpointIdentity } from './organization-owner'

export interface OrganizationCreationView {
  runtimeCapability?: SourceRuntimeCapability | null
  stage:
    'loading' | 'ready' | 'unavailable' | 'creating' | 'uncertain' | 'checking' | 'created' | 'switching' | 'selected'
  canRetry: boolean
  intent: WorkspaceCreationIntent | null
  result: CreatedWorkspace | null
  error: 'unsupported' | 'unconfigured' | 'unavailable' | 'storage' | 'uncertain' | 'selection' | 'superseded' | null
}
export interface OrganizationCreationDependencies {
  readSource: () => DirectorySource
  canRestoreOrigin?: () => boolean
  subscribeSource?: (changed: () => void) => () => void
  rememberCurrent: () => Promise<unknown>
  storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>
  activate: (
    connectionId: string,
    profile: string,
    verify: (value: unknown) => void
  ) => Promise<ConnectionSwitchOutcome>
  saveModel?: (profile: string, model: string) => Promise<{ ok: boolean }>
  newKey?: () => string
}

/** A creation receipt never changes the active owner. Selection is a separate action. */
export function createOrganizationCreationClient(deps: OrganizationCreationDependencies) {
  const source = deps.readSource()
  let origin = source.current ? { ...source.current, scope: { ...source.current.scope } } : null
  const storageKey = `eidolon.organization-creation.intent.v1:${origin?.scope.ownerKey}`
  const endpoint = origin?.endpoint === null ? null : organizationEndpointIdentity(origin?.endpoint)
  const listeners = new Set<() => void>()
  let view: OrganizationCreationView = { stage: 'loading', canRetry: false, intent: null, result: null, error: null }
  let capability: WorkspaceCreationCapability | null = null
  let busy = false
  let disposed = false
  let selectedScope: string | null = null

  const update = (next: Partial<OrganizationCreationView>) => {
    if (disposed) {
      return
    }

    view = { ...view, ...next, ...(next.stage && next.stage !== 'selected' ? { runtimeCapability: null } : {}) }

    for (const listener of listeners) {
      listener()
    }
  }

  const current = () => {
    const live = deps.readSource()
    const registered = live.connections.find(item => item.id === origin?.connectionId)

    return (
      !disposed &&
      live.ready !== false &&
      Boolean(
        origin?.scope.eligible &&
        registered &&
        live.current?.scope.eligible &&
        live.current.scope.key === origin.scope.key &&
        live.current.scope.ownerKey === origin.scope.ownerKey &&
        live.current.connectionId === origin.connectionId &&
        (registered.endpoint === null ? null : organizationEndpointIdentity(registered.endpoint)) === endpoint
      )
    )
  }

  const request = async (method: string, params: Record<string, unknown>) => {
    if (!current() || !origin) {
      throw new Error('Organization source changed')
    }

    return origin.scope.request<unknown>(method, params)
  }

  const status = async () => {
    const intent = view.intent

    if (!intent) {
      return
    }

    const { displayName: _name, ownerDisplayName: _owner, ...params } = intent
    const answer = await request('organization.workspaceCreationStatus', params)

    if (!current()) {
      return
    }

    const state = answer as { capabilityVersion?: unknown; state?: unknown }

    if (state?.capabilityVersion === 1 && ['unknown', 'pending'].includes(String(state.state))) {
      update({ stage: 'uncertain', error: 'uncertain', canRetry: true })

      return
    }

    update({ stage: 'created', result: createdWorkspace(answer, intent), error: null })
  }

  const submit = async (intent: WorkspaceCreationIntent) => {
    busy = true
    update({ stage: 'creating', intent, error: null, canRetry: false })

    try {
      const answer = await request('organization.createWorkspace', { ...intent })

      if (current()) {
        update({ stage: 'created', result: createdWorkspace(answer, intent), error: null })
      }
    } catch {
      if (current()) {
        update({ stage: 'uncertain', error: 'uncertain' })
      }
    } finally {
      busy = false
    }
  }

  const canSetup = () => {
    const source = deps.readSource()
    const live = source.current

    return (
      !disposed &&
      source.ready !== false &&
      view.stage === 'selected' &&
      Boolean(
        live?.scope.eligible &&
        live.scope.key === selectedScope &&
        live.connectionId === origin?.connectionId &&
        live.scope.profile === view.result?.profile
      )
    )
  }

  const unsubscribe = deps.subscribeSource?.(() => {
    if (view.stage === 'selected' && !canSetup()) {
      update({ stage: 'unavailable', error: 'superseded', canRetry: false })

      return
    }

    if (!current() && view.stage !== 'switching' && view.stage !== 'selected') {
      update({ stage: 'unavailable', error: 'superseded', canRetry: false })
    }
  })

  return {
    getSnapshot: () => view,
    subscribe(listener: () => void) {
      listeners.add(listener)

      return () => {
        listeners.delete(listener)
      }
    },
    isCurrent: current,
    async load() {
      if (busy || view.stage !== 'loading') {
        return
      }

      busy = true

      try {
        if (!current() || !origin) {
          update({ stage: 'unavailable', error: 'unavailable' })

          return
        }

        const answer = await request('organization.workspaceCreation', {})

        if (!current()) {
          return
        }

        if ((answer as { state?: unknown; reason?: unknown })?.state === 'unavailable') {
          update({
            stage: 'unavailable',
            error: (answer as { reason?: unknown }).reason === 'origin_unconfigured' ? 'unconfigured' : 'unsupported'
          })

          return
        }

        capability = creationCapability(answer, origin.scope.profile)
        let raw: string | null

        try {
          raw = deps.storage.getItem(storageKey)
        } catch {
          update({ stage: 'unavailable', error: 'storage' })

          return
        }

        await deps.rememberCurrent()

        if (!current()) {
          return
        }

        if (raw) {
          try {
            update({ intent: restoreCreationIntent(raw, capability), stage: 'checking' })
          } catch {
            update({ stage: 'unavailable', error: 'storage' })

            return
          }

          await status()
        } else {
          update({ stage: 'ready', error: null })
        }
      } catch (error) {
        if (current()) {
          update({
            stage: view.intent ? 'uncertain' : 'unavailable',
            error: view.intent
              ? 'uncertain'
              : (error as { code?: number })?.code === -32601
                ? 'unsupported'
                : 'unavailable'
          })
        }
      } finally {
        busy = false
      }
    },
    async create(displayName: string, ownerDisplayName: string) {
      if (
        busy ||
        view.stage !== 'ready' ||
        !capability ||
        !current() ||
        !validWorkspaceName(displayName) ||
        !validWorkspaceName(ownerDisplayName)
      ) {
        return
      }

      const intent: WorkspaceCreationIntent = {
        capabilityVersion: 1,
        expectedOrganizationId: capability.origin.organizationId,
        expectedOwnerId: capability.origin.ownerId,
        displayName: displayName.trim(),
        ownerDisplayName: ownerDisplayName.trim(),
        idempotencyKey: (deps.newKey ?? (() => crypto.randomUUID()))()
      }

      try {
        deps.storage.setItem(storageKey, JSON.stringify(intent))
      } catch {
        update({ error: 'storage' })

        return
      }

      await submit(intent)
    },
    async check() {
      if (busy || view.stage !== 'uncertain' || !view.intent || !current()) {
        return
      }

      busy = true
      update({ stage: 'checking', error: null, canRetry: false })

      try {
        await status()
      } catch {
        if (current()) {
          update({ stage: 'uncertain', error: 'uncertain' })
        }
      } finally {
        busy = false
      }
    },
    async retry() {
      if (busy || !view.canRetry || view.stage !== 'uncertain' || !view.intent || !current()) {
        return
      }

      await submit(view.intent)
    },
    async select() {
      if (busy || view.stage !== 'created' || !view.result || !origin || !current()) {
        return
      }

      busy = true
      const expected = view.result
      update({ stage: 'switching', error: null })

      try {
        const answer = await deps.activate(origin.connectionId, expected.profile, value => {
          if (disposed) {
            throw new Error('Organization selection cancelled')
          }

          verifyCreatedWorkspace(value, expected)
        })

        const live = deps.readSource().current

        if (disposed) {
          return
        }

        if (answer.status === 'superseded') {
          update({ stage: 'created', error: 'superseded' })

          return
        }

        if (
          answer.status === 'unchanged' &&
          !answer.resetStarted &&
          !answer.published &&
          deps.canRestoreOrigin?.() &&
          live?.scope.eligible &&
          live.connectionId === origin.connectionId &&
          live.scope.ownerKey === origin.scope.ownerKey &&
          live.scope.profile === origin.scope.profile &&
          (live.endpoint === null ? null : organizationEndpointIdentity(live.endpoint)) === endpoint
        ) {
          origin = { ...live, scope: { ...live.scope } }
        }

        if (
          answer.status !== 'committed' ||
          !live?.scope.eligible ||
          live.connectionId !== origin.connectionId ||
          live.scope.profile !== expected.profile
        ) {
          update({ stage: 'created', error: 'selection' })

          return
        }

        const descriptor = await live.scope.request<{ runtimeCapability?: unknown }>('organization.descriptor', {})
        verifyCreatedWorkspace(descriptor, expected)
        const runtimeCapability = sourceRuntimeCapability(descriptor.runtimeCapability)
        const final = deps.readSource().current

        if (!final?.scope.eligible || final.scope.key !== live.scope.key) {
          update({ stage: 'created', error: 'superseded' })

          return
        }

        await deps.rememberCurrent()
        const remembered = deps.readSource().current

        if (disposed || !remembered?.scope.eligible || remembered.scope.key !== live.scope.key) {
          update({ stage: 'created', error: 'superseded' })

          return
        }

        selectedScope = live.scope.key

        try {
          deps.storage.removeItem(storageKey)
        } catch {
          update({ stage: 'selected', error: 'storage', runtimeCapability })

          return
        }

        update({ stage: 'selected', error: null, runtimeCapability })
      } catch {
        update({ stage: 'created', error: 'selection' })
      } finally {
        busy = false
      }
    },
    canSetup,
    async saveModel(model: string): Promise<'saved' | 'failed' | 'superseded'> {
      if (busy || !canSetup() || !validWorkspaceModel(model) || !deps.saveModel) {
        return 'failed'
      }

      busy = true

      try {
        return await saveVerifiedModel(deps, view.result!, canSetup, model.trim())
      } finally {
        busy = false
      }
    },
    dispose() {
      disposed = true
      unsubscribe?.()
      listeners.clear()
    }
  }
}

export type OrganizationCreationClient = ReturnType<typeof createOrganizationCreationClient>

type ModelDependencies = Pick<OrganizationCreationDependencies, 'readSource' | 'subscribeSource' | 'saveModel'>
type ModelSaveResult = 'saved' | 'failed' | 'superseded'

/** Shared by the creation shortcut and the durable current-organization setup door. */
async function saveVerifiedModel(
  deps: ModelDependencies,
  expected: Pick<CreatedWorkspace, 'profile' | 'organizationId' | 'ownerId'>,
  isCurrent: () => boolean,
  model: string
): Promise<ModelSaveResult> {
  const live = deps.readSource().current

  if (!live || !isCurrent() || !deps.saveModel || !validWorkspaceModel(model)) {
    return 'superseded'
  }

  try {
    verifyCreatedWorkspace(await live.scope.request('organization.descriptor', {}), expected)

    if (!isCurrent() || deps.readSource().current?.scope.key !== live.scope.key) {
      return 'superseded'
    }

    const result = await deps.saveModel(expected.profile, model)

    if (!isCurrent()) {
      return 'superseded'
    }

    return result.ok === true ? 'saved' : 'failed'
  } catch {
    return isCurrent() ? 'failed' : 'superseded'
  }
}

export function createOrganizationModelSetupClient(deps: ModelDependencies) {
  const initial = deps.readSource().current
  const origin = initial ? { ...initial, scope: { ...initial.scope } } : null
  let disposed = false
  let expected: Pick<CreatedWorkspace, 'profile' | 'organizationId' | 'ownerId'> | null = null

  let view: { stage: 'loading' | 'ready' | 'unavailable'; busy: boolean; result: ModelSaveResult | null } = {
    stage: 'loading',
    busy: false,
    result: null
  }

  const listeners = new Set<() => void>()

  const update = (next: Partial<typeof view>) => {
    if (disposed) {
      return
    }

    view = { ...view, ...next }

    for (const listener of listeners) {
      listener()
    }
  }

  const current = () => {
    const source = deps.readSource()

    return (
      !disposed &&
      source.ready !== false &&
      Boolean(
        origin?.scope.eligible &&
        source.current?.scope.eligible &&
        source.current.scope.key === origin.scope.key &&
        source.current.scope.ownerKey === origin.scope.ownerKey &&
        source.current.connectionId === origin.connectionId &&
        source.current.scope.profile === origin.scope.profile
      )
    )
  }

  const unsubscribe = deps.subscribeSource?.(() => {
    if (!current()) {
      update({ stage: 'unavailable', result: 'superseded', busy: false })
    }
  })

  return {
    getSnapshot: () => view,
    subscribe(listener: () => void) {
      listeners.add(listener)

      return () => {
        listeners.delete(listener)
      }
    },
    isCurrent: current,
    async load() {
      if (view.busy || view.stage !== 'loading') {
        return
      }

      update({ busy: true })

      try {
        if (!current() || !origin) {
          throw new Error('Organization unavailable')
        }

        const descriptor = await origin.scope.request('organization.descriptor', {})

        if (!current()) {
          return
        }

        expected = isolatedWorkspaceIdentity(descriptor, origin.scope.profile)
        update({ stage: 'ready' })
      } catch {
        update({ stage: 'unavailable' })
      } finally {
        update({ busy: false })
      }
    },
    async save(model: string) {
      if (view.busy || view.stage !== 'ready' || !current() || !expected || !validWorkspaceModel(model)) {
        return
      }

      update({ busy: true, result: null })
      const result = await saveVerifiedModel(deps, expected, current, model.trim())
      update({ busy: false, result, ...(result === 'superseded' ? { stage: 'unavailable' as const } : {}) })
    },
    dispose() {
      disposed = true
      unsubscribe?.()
      listeners.clear()
    }
  }
}

export type OrganizationModelSetupClient = ReturnType<typeof createOrganizationModelSetupClient>
