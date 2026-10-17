import { registryBackendScopeKey } from '@aethermesh/shared'
import { atom } from 'nanostores'

import { registerOrganizationApiGuard } from '@/api/client'
import type { HermesConnection, HermesWindowState } from '@/global'

import { currentSourceSwitchIntent, isCurrentSourceSwitchIntent } from './source-switch-intent'
import { isAuxiliaryWindow, isPeerInstanceWindow, windowProfileOverride } from './windows'

export interface OrganizationSelectionIdentity {
  connectionId: string
  profile: string
  organizationId: string
  ownerId: string
  isolationVersion: 1 | null
}

export interface OrganizationSelectionRecord {
  version: 1
  phase: 'pending' | 'resumable'
  operationId: string
  identity: OrganizationSelectionIdentity | null
}

type Storage = Pick<globalThis.Storage, 'getItem' | 'setItem' | 'removeItem'>
type SelectionRead = { kind: 'none' | 'invalid' | 'storage' } | { kind: 'record'; record: OrganizationSelectionRecord }
export const ORGANIZATION_SELECTION_KEY = 'eidolon.organization.explicitSelection.v1'
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
const key = (value: unknown): value is string => typeof value === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,199}$/.test(value)

export function validOrganizationSelectionIdentity(value: unknown): value is OrganizationSelectionIdentity {
  const item = value as OrganizationSelectionIdentity

  return Boolean(item && Object.keys(item).sort().join(',') === 'connectionId,isolationVersion,organizationId,ownerId,profile' &&
    key(item.connectionId) && key(item.profile) && uuid(item.organizationId) && uuid(item.ownerId) &&
    item.organizationId !== item.ownerId && (item.isolationVersion === null || item.isolationVersion === 1))
}

/** A pending record is never startup authority, even when its target was proved. */
export function createOrganizationSelectionStorage(storage: Storage) {
  const read = (): SelectionRead => {
    let raw: string | null

    try { raw = storage.getItem(ORGANIZATION_SELECTION_KEY) } catch { return { kind: 'storage' } }

    if (raw === null) { return { kind: 'none' } }

    try {
      if (raw.length > 4096) { return { kind: 'invalid' } }
      const item = JSON.parse(raw) as OrganizationSelectionRecord

      if (!item || Object.keys(item).sort().join(',') !== 'identity,operationId,phase,version' ||
          item.version !== 1 || !uuid(item.operationId) || !['pending', 'resumable'].includes(item.phase) ||
          (item.identity === null ? item.phase !== 'pending' : !validOrganizationSelectionIdentity(item.identity))) {
        return { kind: 'invalid' }
      }

      return { kind: 'record', record: item }
    } catch { return { kind: 'invalid' } }
  }
  const write = (record: OrganizationSelectionRecord) => {
    const encoded = JSON.stringify(record)
    storage.setItem(ORGANIZATION_SELECTION_KEY, encoded)

    if (storage.getItem(ORGANIZATION_SELECTION_KEY) !== encoded) { throw new Error('Organization selection storage changed') }

    return record
  }
  const owns = (pending: OrganizationSelectionRecord) => {
    const value = read()

    return value.kind === 'record' && value.record.phase === 'pending' && JSON.stringify(value.record) === JSON.stringify(pending)
  }

  return {
    read,
    owns,
    stage(identity: OrganizationSelectionIdentity | null, operationId: string = crypto.randomUUID()) {
      if ((identity !== null && !validOrganizationSelectionIdentity(identity)) || !uuid(operationId)) {
        throw new Error('Invalid organization selection identity')
      }

      return write({ version: 1, phase: 'pending', operationId, identity })
    },
    promote(pending: OrganizationSelectionRecord) {
      if (!owns(pending) || !pending.identity) { throw new Error('Organization selection superseded') }

      return write({ ...pending, phase: 'resumable' })
    },
    clear(pending?: OrganizationSelectionRecord) {
      if (pending && !owns(pending)) { throw new Error('Organization selection superseded') }
      storage.removeItem(ORGANIZATION_SELECTION_KEY)

      if (storage.getItem(ORGANIZATION_SELECTION_KEY) !== null) { throw new Error('Organization selection could not be cleared') }
    }
  }
}

const selectionStorage = createOrganizationSelectionStorage({
  getItem: key => localStorage.getItem(key),
  setItem: (key, value) => localStorage.setItem(key, value),
  removeItem: key => localStorage.removeItem(key)
})
let apiAuthority: { identity: OrganizationSelectionIdentity; operationId: string; current: () => boolean; readConnection: () => HermesConnection | null } | null = null

export function authorizeOrganizationApi(pending: OrganizationSelectionRecord | null, current: () => boolean, readConnection: () => HermesConnection | null = () => null) {
  if (pending?.identity) { apiAuthority = { identity: pending.identity, operationId: pending.operationId, current, readConnection } }
}

registerOrganizationApiGuard((request, ambientProfile) => {
  if (!organizationSelectionAppliesToWindow()) { return null }
  const saved = selectionStorage.read()

  if (saved.kind === 'none') { return null }
  const authority = apiAuthority
  const intent = currentSourceSwitchIntent()
  const connection = authority?.readConnection()
  const profile = request.profile === undefined ? ambientProfile : request.profile
  const current = () => {
    const latest = selectionStorage.read()

    return authority?.readConnection() === connection && isCurrentSourceSwitchIntent(intent) && apiAuthority === authority && Boolean(authority?.current()) && latest.kind === 'record' &&
      latest.record.operationId === authority?.operationId && latest.record.identity !== null &&
      latest.record.identity.connectionId === authority?.identity.connectionId && latest.record.identity.profile === authority?.identity.profile &&
      latest.record.identity.organizationId === authority?.identity.organizationId && latest.record.identity.ownerId === authority?.identity.ownerId &&
      latest.record.identity.isolationVersion === authority?.identity.isolationVersion
  }

  if (!authority || !current() || request.connectionId !== authority.identity.connectionId || profile !== authority.identity.profile) {
    throw new Error('Organization API requires a verified selected owner')
  }

  return { request: { ...request, profile }, current }
})

// Only the native window-state handler creates these presentation aliases. They
// never authorize a structurally equal replacement or change activation proof.
let presentationOrigins = new WeakMap<HermesConnection, HermesConnection>()
export function organizationWindowPresentation(current: HermesConnection, payload: HermesWindowState): HermesConnection {
  const next = { ...current }
  const fields = ['darwinMajor', 'isFullscreen', 'isMinimized', 'isVisible', 'nativeOverlayWidth', 'windowButtonPosition'] as const

  for (const field of fields) {
    if (field in payload) { Object.assign(next, { [field]: payload[field] }) }
  }
  presentationOrigins.set(next, presentationOrigins.get(current) ?? current)

  return next
}

export const matchesOrganizationPresentation = (current: HermesConnection | null, proved: HermesConnection) =>
  Boolean(current && (presentationOrigins.get(current) ?? current) === (presentationOrigins.get(proved) ?? proved))

let protectedIdentities: OrganizationSelectionIdentity[] = []
let recoveryRecord: string | null = null
let recoveryActions: { retry: () => Promise<void>; startup: () => Promise<void> } | null = null
export const $organizationSelectionRecovery = atom<{
  reason: 'pending' | 'unavailable' | 'storage' | 'invalid'
  profile: string | null
  canRetry: boolean
} | null>(null)

export const organizationSelectionAppliesToWindow = () =>
  typeof window !== 'undefined' && !isAuxiliaryWindow() && !isPeerInstanceWindow(window.location?.search ?? '') && !windowProfileOverride()

export function readOrganizationBootSelection(): SelectionRead {
  if (!organizationSelectionAppliesToWindow()) { return { kind: 'none' } }
  const value = selectionStorage.read()

  if (value.kind === 'record' && value.record.identity) {
    const identity = value.record.identity
    protectedIdentities = [...protectedIdentities.filter(item => item.connectionId !== identity.connectionId || item.profile !== identity.profile), identity]
  }

  return value
}

export function isOrganizationRouteProtected(connectionId: string | null, profile: string): boolean {
  return organizationSelectionAppliesToWindow() && protectedIdentities.some(item => item.connectionId === connectionId && item.profile === profile)
}

export function stageOrganizationSelection(identity: OrganizationSelectionIdentity) {
  if (!organizationSelectionAppliesToWindow()) { return null }
  let pending: OrganizationSelectionRecord

  try { pending = selectionStorage.stage(identity) } catch (error) {
    readOrganizationBootSelection()
    showOrganizationSelectionRecovery('storage', identity)
    throw error
  }
  protectedIdentities = [...protectedIdentities.filter(item => item.connectionId !== identity.connectionId || item.profile !== identity.profile), identity]

  return pending
}

export function promoteOrganizationSelection(pending: OrganizationSelectionRecord | null) {
  if (!pending) { return }
  selectionStorage.promote(pending)
  protectedIdentities = pending.identity ? [pending.identity] : []
  $organizationSelectionRecovery.set(null)
}

export const ownsPendingOrganizationSelection = (pending: OrganizationSelectionRecord | null) =>
  pending === null || selectionStorage.owns(pending)

/** Disarm an old resumable owner before an explicit legacy route can publish. */
export function stageLegacyOrganizationDeparture(connectionId: string | null, profile: string) {
  if (!organizationSelectionAppliesToWindow()) { return null }
  const value = selectionStorage.read()

  if (value.kind === 'none') { return null }
  if (value.kind !== 'record') {
    showOrganizationSelectionRecovery(value.kind === 'storage' ? 'storage' : 'invalid')
    throw new Error('Saved organization selection is unavailable')
  }
  if (value.record.phase === 'resumable' && value.record.identity?.connectionId === connectionId && value.record.identity.profile === profile) { return null }

  try { return selectionStorage.stage(null) } catch (error) {
    showOrganizationSelectionRecovery('storage')
    throw error
  }
}

export function finishLegacyOrganizationDeparture(pending: OrganizationSelectionRecord | null) {
  if (!pending) { return }
  try { selectionStorage.clear(pending) } catch (error) {
    showOrganizationSelectionRecovery('storage')
    throw error
  }
  protectedIdentities = []
  $organizationSelectionRecovery.set(null)
}

export function showOrganizationSelectionRecovery(reason: NonNullable<ReturnType<typeof $organizationSelectionRecovery.get>>['reason'], identity?: OrganizationSelectionIdentity | null) {
  const saved = selectionStorage.read()
  recoveryRecord = saved.kind === 'record' ? JSON.stringify(saved.record) : null
  const target = saved.kind === 'record' ? saved.record.identity : null
  const matches = identity && target && Object.keys(identity).every(key => identity[key as keyof OrganizationSelectionIdentity] === target[key as keyof OrganizationSelectionIdentity])
  $organizationSelectionRecovery.set({ reason, profile: target?.profile ?? identity?.profile ?? null, canRetry: Boolean(matches) })
}

export function refuseOrganizationAutomaticChange(connectionId: string | null, profile: string): boolean {
  if (!isOrganizationRouteProtected(connectionId, profile)) { return false }
  const saved = selectionStorage.read()
  showOrganizationSelectionRecovery('unavailable', saved.kind === 'record' ? saved.record.identity : null)

  return true
}

export function refuseOrganizationAutomaticScopeChange(scope: string): boolean {
  const identity = protectedIdentities.find(item => registryBackendScopeKey(item.connectionId, item.profile) === scope)

  return identity ? refuseOrganizationAutomaticChange(identity.connectionId, identity.profile) : false
}

export function installOrganizationSelectionRecovery(retry: () => Promise<void>) {
  const actions = {
    retry,
    startup: async () => {
      selectionStorage.clear()
      protectedIdentities = []
      window.location.reload()
    }
  }
  recoveryActions = actions

  return () => { if (recoveryActions === actions) { recoveryActions = null } }
}

export async function retryOrganizationSelection() {
  if (!recoveryActions || !$organizationSelectionRecovery.get()?.canRetry) { throw new Error('Organization recovery is unavailable') }
  const saved = selectionStorage.read()
  if (saved.kind !== 'record' || JSON.stringify(saved.record) !== recoveryRecord) {
    showOrganizationSelectionRecovery('pending', saved.kind === 'record' ? saved.record.identity : null)
    throw new Error('Saved organization selection changed; review it before retrying')
  }
  await recoveryActions.retry()
}

export async function openStartupWorkspace() {
  if (!recoveryActions) { throw new Error('Organization recovery is unavailable') }
  await recoveryActions.startup()
}

/** @internal */
export function _resetOrganizationSelectionForTests() {
  protectedIdentities = []
  presentationOrigins = new WeakMap()
  apiAuthority = null
  recoveryRecord = null
  recoveryActions = null
  $organizationSelectionRecovery.set(null)
}
