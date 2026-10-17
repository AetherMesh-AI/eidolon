import { registryBackendScopeKey } from '@aethermesh/shared'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as SelectionModule from './organization-selection'
import type { OrganizationSelectionIdentity, OrganizationSelectionRecord } from './organization-selection'

vi.mock('./notifications', () => ({ notifyError: vi.fn() }))

const operationA = 'a1111111-1111-4111-8111-111111111111'
const operationB = 'b2222222-2222-4222-8222-222222222222'
const ownerA: OrganizationSelectionIdentity = {
  connectionId: 'local',
  profile: 'research',
  organizationId: '11111111-1111-4111-8111-111111111111',
  ownerId: '22222222-2222-4222-8222-222222222222',
  isolationVersion: 1
}
const ownerB: OrganizationSelectionIdentity = {
  ...ownerA,
  connectionId: 'remote',
  profile: 'delivery',
  organizationId: '33333333-3333-4333-8333-333333333333',
  ownerId: '44444444-4444-4444-8444-444444444444'
}

function memoryStorage() {
  const values = new Map<string, string>()

  return {
    values,
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => { values.set(key, value) }),
    removeItem: vi.fn((key: string) => { values.delete(key) })
  }
}

function record(identity: OrganizationSelectionIdentity | null = ownerA): OrganizationSelectionRecord {
  return { version: 1, phase: 'pending', operationId: operationA, identity }
}

let selection: typeof SelectionModule
let storage: ReturnType<typeof memoryStorage>
let reload: ReturnType<typeof vi.fn>

beforeEach(async () => {
  vi.resetModules()
  storage = memoryStorage()
  reload = vi.fn()
  vi.stubGlobal('localStorage', storage)
  vi.stubGlobal('window', { localStorage: storage, location: { search: '', reload } })
  selection = await import('./organization-selection')
})

afterEach(() => {
  selection._resetOrganizationSelectionForTests()
  vi.unstubAllGlobals()
})

describe('organization selection storage authority', () => {
  it('persists only the bounded identity schema and never treats pending as resumable', () => {
    const saved = selection.createOrganizationSelectionStorage(storage)
    expect(saved.read()).toEqual({ kind: 'none' })

    for (const isolationVersion of [1, null] as const) {
      const identity = { ...ownerA, isolationVersion }
      const pending = saved.stage(identity, operationA)
      expect(saved.read()).toEqual({ kind: 'record', record: pending })
      expect(pending.phase).toBe('pending')
      expect(JSON.parse(storage.values.get(selection.ORGANIZATION_SELECTION_KEY)!)).toEqual({
        version: 1, phase: 'pending', operationId: operationA, identity
      })
      expect([...storage.values.keys()]).toEqual([selection.ORGANIZATION_SELECTION_KEY])
      const resumed = saved.promote(pending)
      expect(resumed).toEqual({ ...pending, phase: 'resumable' })
      expect(saved.read()).toEqual({ kind: 'record', record: resumed })
    }

    const departure = saved.stage(null, operationB)
    expect(saved.read()).toEqual({ kind: 'record', record: departure })
    expect(() => saved.promote(departure)).toThrow()
    expect(saved.read()).toEqual({ kind: 'record', record: departure })
  })

  it('rejects malformed, oversized, incomplete and credential-bearing records without rewriting them', () => {
    const saved = selection.createOrganizationSelectionStorage(storage)
    const invalidIdentities: unknown[] = [
      {}, [], { ...ownerA, ownerId: ownerA.organizationId },
      { ...ownerA, ownerId: 'not-an-owner-id' },
      { ...ownerA, organizationId: 'not-an-organization-id' },
      { ...ownerA, isolationVersion: 2 },
      { ...ownerA, connectionId: '' },
      { ...ownerA, connectionId: 'x'.repeat(201) },
      { ...ownerA, profile: 'x'.repeat(201) },
      { ...ownerA, connectionId: 'https://user:secret@host.invalid' },
      { ...ownerA, profile: 'profile?token=secret' },
      { ...ownerA, baseUrl: 'https://private.invalid' },
      { ...ownerA, token: 'private-credential' },
      { connectionId: ownerA.connectionId, profile: ownerA.profile, ownerId: ownerA.ownerId }
    ]

    for (const identity of invalidIdentities) {
      expect(selection.validOrganizationSelectionIdentity(identity), JSON.stringify(identity)).toBe(false)
      expect(() => saved.stage(identity as OrganizationSelectionIdentity, operationA)).toThrow()
    }
    expect(() => saved.stage(ownerA, 'foreign-operation')).toThrow()
    expect(storage.setItem).not.toHaveBeenCalled()

    const invalidRecords = [
      'not-json', 'null', '[]', 'true', '7', '{}', JSON.stringify(record()).padEnd(4097, ' '),
      ...invalidIdentities.map(identity => JSON.stringify({ ...record(), identity })),
      JSON.stringify({ ...record(), phase: 'resumable', identity: null }),
      JSON.stringify({ ...record(), version: 2 }),
      JSON.stringify({ ...record(), phase: 'ready' }),
      JSON.stringify({ ...record(), operationId: 'foreign-operation' }),
      JSON.stringify({ ...record(), token: 'private-credential' }),
      JSON.stringify({ ...record(), baseUrl: 'https://private.invalid' }),
      JSON.stringify({ version: 1, phase: 'pending', operationId: operationA })
    ]

    for (const raw of invalidRecords) {
      storage.values.set(selection.ORGANIZATION_SELECTION_KEY, raw)
      expect(saved.read(), raw).toEqual({ kind: 'invalid' })
      expect(storage.values.get(selection.ORGANIZATION_SELECTION_KEY)).toBe(raw)
    }
    expect(storage.setItem).not.toHaveBeenCalled()
    expect(storage.removeItem).not.toHaveBeenCalled()
    storage.values.set(selection.ORGANIZATION_SELECTION_KEY, JSON.stringify(record()).padEnd(4096, ' '))
    expect(saved.read()).toEqual({ kind: 'record', record: record() })
  })

  it('fails closed on read, stage and promotion storage failures', () => {
    const saved = selection.createOrganizationSelectionStorage(storage)
    storage.getItem.mockImplementationOnce(() => { throw new Error('read unavailable') })
    expect(saved.read()).toEqual({ kind: 'storage' })

    storage.setItem.mockImplementationOnce(() => { throw new Error('quota exceeded') })
    expect(() => saved.stage(ownerA, operationA)).toThrow('quota exceeded')
    expect(saved.read()).toEqual({ kind: 'none' })
    storage.setItem.mockImplementationOnce(() => {})
    expect(() => saved.stage(ownerA, operationA)).toThrow()
    expect(saved.read()).toEqual({ kind: 'none' })

    const pending = saved.stage(ownerA, operationA)
    storage.getItem.mockImplementationOnce(() => { throw new Error('read unavailable') })
    expect(() => saved.promote(pending)).toThrow()
    expect(saved.read()).toEqual({ kind: 'record', record: pending })

    storage.setItem.mockImplementationOnce(() => { throw new Error('quota exceeded') })
    expect(() => saved.promote(pending)).toThrow('quota exceeded')
    expect(saved.read()).toEqual({ kind: 'record', record: pending })
    expect(saved.owns(pending)).toBe(true)
    expect(saved.promote(pending).phase).toBe('resumable')
  })

  it('requires its exact pending record before promotion or clearing even when a newer operation picks the same owner', () => {
    const saved = selection.createOrganizationSelectionStorage(storage)
    const pendingA = saved.stage(ownerA, operationA)
    const pendingB = saved.stage(ownerA, operationB)
    expect(saved.owns(pendingA)).toBe(false)
    expect(() => saved.promote(pendingA)).toThrow()
    expect(() => saved.clear(pendingA)).toThrow()
    expect(saved.read()).toEqual({ kind: 'record', record: pendingB })

    const reorderedIdentity = {
      isolationVersion: ownerA.isolationVersion,
      ownerId: ownerA.ownerId,
      organizationId: ownerA.organizationId,
      profile: ownerA.profile,
      connectionId: ownerA.connectionId
    }
    for (const changed of [
      { ...pendingB, identity: ownerB },
      { ...pendingB, identity: reorderedIdentity },
      { identity: pendingB.identity, operationId: pendingB.operationId, phase: pendingB.phase, version: pendingB.version },
      { ...pendingB, identity: { ...ownerA, isolationVersion: null } }
    ]) {
      storage.values.set(selection.ORGANIZATION_SELECTION_KEY, JSON.stringify(changed))
      expect(saved.owns(pendingB)).toBe(false)
      expect(() => saved.promote(pendingB)).toThrow()
      expect(() => saved.clear(pendingB)).toThrow()
      expect(saved.read()).toEqual({ kind: 'record', record: changed })
    }

    storage.values.set(selection.ORGANIZATION_SELECTION_KEY, JSON.stringify(pendingB))
    saved.promote(pendingB)
    expect(saved.owns(pendingB)).toBe(false)
    expect(() => saved.promote(pendingB)).toThrow()
    expect(() => saved.clear(pendingB)).toThrow()
  })
})

describe('organization selection window and recovery lifecycle', () => {
  it.each(['?peer=1', '?profile=pinned', '?win=secondary', '?win=hud', '?win=browser'])(
    'does not read, stage or protect a main-window selection in %s', search => {
      window.location.search = search
      storage.values.set(selection.ORGANIZATION_SELECTION_KEY, JSON.stringify({ ...record(), phase: 'resumable' }))
      expect(selection.organizationSelectionAppliesToWindow()).toBe(false)
      expect(selection.readOrganizationBootSelection()).toEqual({ kind: 'none' })
      expect(selection.stageOrganizationSelection(ownerB)).toBeNull()
      expect(selection.stageLegacyOrganizationDeparture('legacy', 'default')).toBeNull()
      expect(selection.isOrganizationRouteProtected(ownerA.connectionId, ownerA.profile)).toBe(false)
      expect(selection.refuseOrganizationAutomaticChange(ownerA.connectionId, ownerA.profile)).toBe(false)
      expect(storage.getItem).not.toHaveBeenCalled()
      expect(storage.setItem).not.toHaveBeenCalled()
      expect(storage.removeItem).not.toHaveBeenCalled()
    }
  )

  it('keeps the active owner protected while a pending target is read, then releases it only on promotion', () => {
    expect(selection.organizationSelectionAppliesToWindow()).toBe(true)
    const active = selection.stageOrganizationSelection(ownerA)
    selection.promoteOrganizationSelection(active)
    const pending = selection.stageOrganizationSelection(ownerB)
    expect(selection.readOrganizationBootSelection()).toEqual({ kind: 'record', record: pending })
    expect(pending?.phase).toBe('pending')
    expect(selection.isOrganizationRouteProtected(ownerA.connectionId, ownerA.profile)).toBe(true)
    expect(selection.isOrganizationRouteProtected(ownerB.connectionId, ownerB.profile)).toBe(true)
    expect(selection.isOrganizationRouteProtected(ownerA.connectionId, ownerB.profile)).toBe(false)
    expect(selection.refuseOrganizationAutomaticScopeChange(registryBackendScopeKey(ownerA.connectionId, ownerA.profile))).toBe(true)
    expect(selection.$organizationSelectionRecovery.get()).toEqual({ reason: 'unavailable', profile: ownerB.profile, canRetry: true })

    selection.promoteOrganizationSelection(pending)
    expect(selection.isOrganizationRouteProtected(ownerA.connectionId, ownerA.profile)).toBe(false)
    expect(selection.isOrganizationRouteProtected(ownerB.connectionId, ownerB.profile)).toBe(true)
    expect(selection.$organizationSelectionRecovery.get()).toBeNull()
  })

  it('retries only the displayed pending owner and requires a new click if the saved target changes', async () => {
    selection.promoteOrganizationSelection(selection.stageOrganizationSelection(ownerA))
    selection.stageOrganizationSelection(ownerB)
    expect(selection.refuseOrganizationAutomaticChange(ownerA.connectionId, ownerA.profile)).toBe(true)
    expect(selection.isOrganizationRouteProtected(ownerA.connectionId, ownerA.profile)).toBe(true)
    expect(selection.$organizationSelectionRecovery.get()).toEqual({ reason: 'unavailable', profile: ownerB.profile, canRetry: true })

    const targets: Array<OrganizationSelectionIdentity | null> = []
    const retry = vi.fn(async () => {
      const saved = selection.readOrganizationBootSelection()
      if (saved.kind === 'record') { targets.push(saved.record.identity) }
    })
    const uninstall = selection.installOrganizationSelectionRecovery(retry)
    await selection.retryOrganizationSelection()
    expect(targets).toEqual([ownerB])

    const ownerC = { ...ownerB, connectionId: 'third-connection', profile: 'third-owner' }
    const saved = selection.createOrganizationSelectionStorage(storage)
    saved.stage(ownerC, operationB)
    await expect(selection.retryOrganizationSelection()).rejects.toThrow('review it before retrying')
    expect(targets).toEqual([ownerB])
    expect(selection.$organizationSelectionRecovery.get()).toEqual({ reason: 'pending', profile: ownerC.profile, canRetry: true })
    await selection.retryOrganizationSelection()
    expect(targets).toEqual([ownerB, ownerC])

    storage.values.set(selection.ORGANIZATION_SELECTION_KEY, '{invalid')
    await expect(selection.retryOrganizationSelection()).rejects.toThrow()
    expect(selection.$organizationSelectionRecovery.get()?.canRetry).toBe(false)
    expect(retry).toHaveBeenCalledTimes(2)
    uninstall()
  })

  it('retains active protection when staging fails and leaves a failed final write pending for explicit retry', async () => {
    selection.promoteOrganizationSelection(selection.stageOrganizationSelection(ownerA))
    storage.setItem.mockImplementationOnce(() => { throw new Error('stage unavailable') })
    expect(() => selection.stageOrganizationSelection(ownerB)).toThrow('stage unavailable')
    expect(selection.isOrganizationRouteProtected(ownerA.connectionId, ownerA.profile)).toBe(true)
    expect(selection.isOrganizationRouteProtected(ownerB.connectionId, ownerB.profile)).toBe(false)

    const pending = selection.stageOrganizationSelection(ownerB)
    selection.showOrganizationSelectionRecovery('pending', ownerB)
    storage.setItem.mockImplementationOnce(() => { throw new Error('final write unavailable') })
    expect(() => selection.promoteOrganizationSelection(pending)).toThrow('final write unavailable')
    expect(selection.readOrganizationBootSelection()).toEqual({ kind: 'record', record: pending })
    expect(selection.isOrganizationRouteProtected(ownerA.connectionId, ownerA.profile)).toBe(true)
    expect(selection.$organizationSelectionRecovery.get()?.canRetry).toBe(true)

    const retry = vi.fn(async () => { selection.promoteOrganizationSelection(pending) })
    const uninstall = selection.installOrganizationSelectionRecovery(retry)
    await selection.retryOrganizationSelection()
    expect(retry).toHaveBeenCalledOnce()
    expect(selection.readOrganizationBootSelection()).toEqual({ kind: 'record', record: { ...pending, phase: 'resumable' } })
    expect(selection.$organizationSelectionRecovery.get()).toBeNull()
    uninstall()
  })

  it('disarms a legacy departure before publication and preserves pending plus recovery when clearing fails', () => {
    selection.promoteOrganizationSelection(selection.stageOrganizationSelection(ownerA))
    expect(selection.stageLegacyOrganizationDeparture(ownerA.connectionId, ownerA.profile)).toBeNull()
    const pending = selection.stageLegacyOrganizationDeparture('legacy', 'default')
    expect(pending).toMatchObject({ phase: 'pending', identity: null })
    expect(selection.readOrganizationBootSelection()).toEqual({ kind: 'record', record: pending })
    expect(selection.isOrganizationRouteProtected(ownerA.connectionId, ownerA.profile)).toBe(true)

    storage.removeItem.mockImplementationOnce(() => { throw new Error('clear unavailable') })
    expect(() => selection.finishLegacyOrganizationDeparture(pending)).toThrow('clear unavailable')
    expect(selection.readOrganizationBootSelection()).toEqual({ kind: 'record', record: pending })
    expect(selection.$organizationSelectionRecovery.get()).toEqual({ reason: 'storage', profile: null, canRetry: false })
    expect(selection.isOrganizationRouteProtected(ownerA.connectionId, ownerA.profile)).toBe(true)

    storage.removeItem.mockImplementationOnce(() => {})
    expect(() => selection.finishLegacyOrganizationDeparture(pending)).toThrow()
    expect(selection.readOrganizationBootSelection()).toEqual({ kind: 'record', record: pending })
    selection.finishLegacyOrganizationDeparture(pending)
    expect(selection.readOrganizationBootSelection()).toEqual({ kind: 'none' })
    expect(selection.isOrganizationRouteProtected(ownerA.connectionId, ownerA.profile)).toBe(false)
    expect(selection.$organizationSelectionRecovery.get()).toBeNull()
  })

  it('refuses legacy departure if storage is unreadable or malformed and cannot clear a newer operation', () => {
    storage.getItem.mockImplementationOnce(() => { throw new Error('unavailable') })
    expect(() => selection.stageLegacyOrganizationDeparture('legacy', 'default')).toThrow()
    storage.values.set(selection.ORGANIZATION_SELECTION_KEY, '{invalid')
    expect(() => selection.stageLegacyOrganizationDeparture('legacy', 'default')).toThrow()
    expect(storage.setItem).not.toHaveBeenCalled()

    selection.promoteOrganizationSelection(selection.stageOrganizationSelection(ownerA))
    const departure = selection.stageLegacyOrganizationDeparture('legacy', 'default')
    const newer = selection.stageOrganizationSelection(ownerB)
    expect(() => selection.finishLegacyOrganizationDeparture(departure)).toThrow()
    expect(selection.readOrganizationBootSelection()).toEqual({ kind: 'record', record: newer })
    expect(storage.removeItem).not.toHaveBeenCalled()
  })

  it('keeps cancellation pending, respects the latest recovery handler and reloads only after explicit successful clearing', async () => {
    const pending = selection.stageOrganizationSelection(ownerA)
    selection.showOrganizationSelectionRecovery('pending', ownerA)
    const cancelled = vi.fn(async () => { throw new DOMException('Selection cancelled', 'AbortError') })
    const removeOld = selection.installOrganizationSelectionRecovery(cancelled)
    await expect(selection.retryOrganizationSelection()).rejects.toThrow('Selection cancelled')
    expect(selection.readOrganizationBootSelection()).toEqual({ kind: 'record', record: pending })
    expect(selection.$organizationSelectionRecovery.get()).toEqual({ reason: 'pending', profile: ownerA.profile, canRetry: true })

    const retry = vi.fn(async () => {})
    const removeCurrent = selection.installOrganizationSelectionRecovery(retry)
    removeOld()
    await selection.retryOrganizationSelection()
    expect(retry).toHaveBeenCalledOnce()
    storage.removeItem.mockImplementationOnce(() => { throw new Error('clear unavailable') })
    await expect(selection.openStartupWorkspace()).rejects.toThrow('clear unavailable')
    expect(reload).not.toHaveBeenCalled()
    expect(selection.isOrganizationRouteProtected(ownerA.connectionId, ownerA.profile)).toBe(true)
    expect(selection.readOrganizationBootSelection()).toEqual({ kind: 'record', record: pending })

    await selection.openStartupWorkspace()
    expect(reload).toHaveBeenCalledOnce()
    expect(selection.readOrganizationBootSelection()).toEqual({ kind: 'none' })
    expect(selection.isOrganizationRouteProtected(ownerA.connectionId, ownerA.profile)).toBe(false)
    removeCurrent()
    await expect(selection.retryOrganizationSelection()).rejects.toThrow()
    await expect(selection.openStartupWorkspace()).rejects.toThrow()
  })
})
