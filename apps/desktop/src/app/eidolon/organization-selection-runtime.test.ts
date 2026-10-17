import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { getHermesConfigRecord } from '@/api/config'
import type { DesktopRegistryConnection, HermesConnection } from '@/global'
import { $connectionsRegistry, _resetConnectionsForTests, initializeConnectionsRegistry, selectConnectionWithOutcome, setConnectionsRegistry } from '@/store/connections'
import { $gateway, activeGateway, closeSecondaryGateways, configureGatewayRegistry, disposeSecondariesForConnection, requestGatewayForAgent, setPrimaryGateway } from '@/store/gateway'
import { registerGatewaySwitchLifecycle } from '@/store/gateway-switch'
import { $organizationSelectionRecovery, _resetOrganizationSelectionForTests, createOrganizationSelectionStorage, ORGANIZATION_SELECTION_KEY, readOrganizationBootSelection } from '@/store/organization-selection'
import { $activeGatewayProfile, ensureGatewayProfile } from '@/store/profile'
import { $connection, setConnection } from '@/store/session'
import { beginSourceSwitchIntent } from '@/store/source-switch-intent'

import { deferred } from '../../test/deferred'

import { organizationActivationVerifier } from './organization-creation-runtime'
import { restoreOrganizationSelection, selectRuntimeOrganizationConnection } from './organization-selection-runtime'

const { sockets, state, MockGateway } = vi.hoisted(() => {
const sockets: MockGateway[] = []
const state = { descriptorReply: async (_profile: string): Promise<unknown> => ({}) }
class MockGateway {
  connectionState = 'closed'
  generation = 1
  profile = ''
  listener = (_state: string) => {}
  connect = vi.fn(async (url: string) => { this.profile = url.split('/').at(-1)!; this.emit('open') })
  request = vi.fn(async (method: string) => {
    if (method !== 'organization.descriptor') { return { profile: this.profile, session_id: 'synthetic-session' } }

    return state.descriptorReply(this.profile)
  })
  close = vi.fn(() => this.emit('closed'))
  emit(state: string) { this.connectionState = state; this.listener(state) }
  onState = (listener: (state: string) => void) => { this.listener = listener; return () => { this.listener = () => {} } }
  onEvent = () => () => {}
  constructor() { sockets.push(this) }
}

return { sockets, state, MockGateway }
})
vi.mock('@/eidolon', async importOriginal => ({ ...(await importOriginal<object>()), HermesGateway: MockGateway }))

const uuid = (n: number) => `${String(n).padStart(8, '0')}-0000-4000-8000-000000000000`
const identity = (profile = 'org-one', n = 1) => ({ connectionId: 'local', profile, organizationId: uuid(n), ownerId: uuid(n + 1), isolationVersion: null })
const descriptor = (profile = 'org-one', n = 1) => ({ capabilityVersion: 1, state: 'ready', scope: { kind: 'served-profile', profile }, metadata: { organizationId: uuid(n), owner: { ownerId: uuid(n + 1) } } })
const registry = () => ({ version: 2, primary: 'local', launchMode: 'primary' as const, secureTokenStorage: true,
  connections: [{ id: 'local', kind: 'local' as const, label: 'This device', tokenSet: false, tokenPreview: null }] })
const connection = (profile: string): HermesConnection => ({ connectionId: 'local', profile, mode: 'local', registryScoped: true,
  baseUrl: `http://127.0.0.1:6161/${profile}`, wsUrl: `ws://127.0.0.1:6161/${profile}`, token: '' }) as HermesConnection
const reset = vi.fn(() => true)
let source: InstanceType<typeof MockGateway>
let bridge: NonNullable<typeof window.hermesDesktop>
const storage = () => createOrganizationSelectionStorage(localStorage)
const remember = (profile = 'org-one', n = 1) => { const pending = storage().stage(identity(profile, n)); storage().promote(pending); return pending }

beforeEach(() => {
  _resetOrganizationSelectionForTests()
  closeSecondaryGateways()
  localStorage.clear()
  _resetConnectionsForTests()
  reset.mockClear()
  sockets.length = 0
  source = new MockGateway()
  source.connectionState = 'open'
  const original = connection('default')
  setPrimaryGateway(source as never, 'default', original)
  $gateway.set(source as never)
  $connection.set(original)
  $activeGatewayProfile.set('default')
  setConnectionsRegistry(registry())
  configureGatewayRegistry({ onEvent: vi.fn(), onActiveConnectionChanged: setConnection, onActiveRouteChanged: value => $activeGatewayProfile.set(value) })
  state.descriptorReply = async profile => descriptor(profile)
  bridge = {
    getConnectionFor: vi.fn(async ({ profile }: { profile: string }) => connection(profile)),
    getGatewayWsUrlFor: vi.fn(async ({ profile }: { profile: string }) => connection(profile).wsUrl),
    connections: {
      list: vi.fn(async () => registry()),
      setLastUsed: vi.fn(async () => ({ registry: { ...registry(), lastUsed: 'local' } }))
    },
    api: vi.fn(async (request: { path: string }) => request.path === '/api/profiles' ? { profiles: [] } : { current: $connection.get()?.profile })
  } as never
  window.hermesDesktop = bridge
})
afterEach(() => { _resetOrganizationSelectionForTests(); closeSecondaryGateways(); localStorage.clear(); vi.restoreAllMocks() })

it('persists only a committed verified identity and restores the same-ID profile before any default lookup', async () => {
  const result = await selectRuntimeOrganizationConnection('local', 'org-one', organizationActivationVerifier(() => {}))
  expect(result.status).toBe('committed')
  expect(storage().read()).toMatchObject({ kind: 'record', record: { phase: 'resumable', identity: identity() } })
  expect(localStorage.getItem(ORGANIZATION_SELECTION_KEY)).not.toMatch(/http|token|baseUrl/)
  _resetOrganizationSelectionForTests()
  closeSecondaryGateways()
  setPrimaryGateway(null)
  $gateway.set(null)
  $connection.set(null)
  _resetConnectionsForTests()
  await restoreOrganizationSelection(() => true)
  expect($connection.get()?.profile).toBe('org-one')
  expect($gateway.get()).toBe(sockets.at(-1))
  expect(sockets.at(-1)?.request.mock.calls.map(call => call[0])).toEqual(['organization.descriptor'])
  await initializeConnectionsRegistry()
  expect($connection.get()?.profile).toBe('org-one')
})

it.each(['pending', 'missing', 'removed-profile', 'remote', 'foreign-owner', 'foreign-profile'] as const)('fails closed on %s without default publication', async failure => {
  const pending = remember()
  if (failure === 'pending') { localStorage.setItem(ORGANIZATION_SELECTION_KEY, JSON.stringify(pending)) }
  if (failure === 'missing') { vi.mocked(bridge.connections!.list).mockResolvedValue({ ...registry(), connections: [] }) }
  if (failure === 'removed-profile') { vi.mocked(bridge.getConnectionFor!).mockRejectedValue(new Error('Profile no longer exists')) }
  if (failure === 'remote') { vi.mocked(bridge.connections!.list).mockResolvedValue({ ...registry(), connections: [{ ...registry().connections[0], kind: 'remote' }] }) }
  if (failure === 'foreign-owner') { state.descriptorReply = async profile => descriptor(profile, 7) }
  if (failure === 'foreign-profile') { state.descriptorReply = async () => descriptor('other') }
  const original = $connection.get()
  await expect(restoreOrganizationSelection(() => true)).rejects.toThrow()
  expect($connection.get()).toBe(original)
  expect($organizationSelectionRecovery.get()).not.toBeNull()
  expect(activeGateway()).toBe(source)
  if (['pending', 'missing', 'remote'].includes(failure)) { expect(bridge.getConnectionFor).not.toHaveBeenCalled() }
})

it.each(['intent', 'record'] as const)('does not let delayed registry restore override newer %s', async change => {
  remember()
  const listing = deferred<ReturnType<typeof registry>>()
  vi.mocked(bridge.connections!.list).mockReturnValue(listing.promise)
  const restore = restoreOrganizationSelection(() => true)
  if (change === 'intent') { beginSourceSwitchIntent() }
  else { remember('org-two', 3) }
  listing.resolve(registry())
  await expect(restore).rejects.toThrow('cancelled')
  expect(bridge.getConnectionFor).not.toHaveBeenCalled()
})

it('pre-write failure prevents reset/publication and a failed promotion leaves pending recovery', async () => {
  const original = Storage.prototype.setItem
  const set = vi.spyOn(Storage.prototype, 'setItem')
  set.mockImplementation((key, value) => { if (key === ORGANIZATION_SELECTION_KEY) { throw new Error('full') } original.call(localStorage, key, value) })
  let result = await selectRuntimeOrganizationConnection('local', 'org-one', organizationActivationVerifier(() => {}))
  expect(result).toMatchObject({ status: 'unchanged', resetStarted: false, published: false })
  expect($connection.get()?.profile).toBe('default')
  set.mockRestore()
  const actual = Storage.prototype.setItem
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, key, value) {
    if (key === ORGANIZATION_SELECTION_KEY && JSON.parse(value).phase === 'resumable') { throw new Error('full') }
    actual.call(this, key, value)
  })
  result = await selectRuntimeOrganizationConnection('local', 'org-one', organizationActivationVerifier(() => {}))
  expect(result).toMatchObject({ status: 'committed', published: true, phase: 'bookkeeping' })
  expect(storage().read()).toMatchObject({ kind: 'record', record: { phase: 'pending' } })
  expect($organizationSelectionRecovery.get()?.reason).toBe('storage')
})

it.each(['organization', 'legacy'] as const)('surfaces recovery when a %s pending write succeeds but readback fails', async route => {
  if (route === 'legacy') {
    remember()
    await restoreOrganizationSelection(() => true)
  }
  const originalConnection = $connection.get()
  const originalGateway = $gateway.get()
  const setItem = Storage.prototype.setItem
  const getItem = Storage.prototype.getItem
  let failReadback = false
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, key, value) {
    setItem.call(this, key, value)
    if (key === ORGANIZATION_SELECTION_KEY && JSON.parse(value).phase === 'pending') { failReadback = true }
  })
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, key) {
    if (key === ORGANIZATION_SELECTION_KEY && failReadback) {
      failReadback = false
      throw new Error('Readback unavailable')
    }

    return getItem.call(this, key)
  })
  const result = route === 'organization'
    ? await selectRuntimeOrganizationConnection('local', 'org-one', organizationActivationVerifier(() => {}))
    : await selectConnectionWithOutcome('local', { profile: 'legacy-two' })
  expect(result).toMatchObject({ status: 'unchanged', resetStarted: false, published: false })
  expect($connection.get()).toBe(originalConnection)
  expect($gateway.get()).toBe(originalGateway)
  expect(storage().read()).toMatchObject({ kind: 'record', record: { phase: 'pending' } })
  expect($organizationSelectionRecovery.get()).toMatchObject({ reason: 'storage', canRetry: route === 'organization' })
  await expect(getHermesConfigRecord()).rejects.toThrow('verified selected owner')
})

it('requires fresh proof after disconnect and never redials or falls back after registry replacement', async () => {
  remember()
  await restoreOrganizationSelection(() => true)
  const old = sockets.at(-1)!
  old.emit('closed')
  expect($gateway.get()).toBeNull()
  expect(activeGateway()).toBeNull()
  const calls = vi.mocked(bridge.getConnectionFor!).mock.calls.length
  disposeSecondariesForConnection('local', { redial: true })
  expect(vi.mocked(bridge.getConnectionFor!).mock.calls).toHaveLength(calls)
  expect($connection.get()?.profile).toBe('org-one')
  state.descriptorReply = async profile => descriptor(profile, 9)
  await expect(restoreOrganizationSelection(() => true, true)).rejects.toThrow()
  expect(sockets.at(-1)).not.toBe(old)
  expect($gateway.get()).toBeNull()
})

it('clears the remembered owner only after an explicit legacy departure succeeds', async () => {
  remember()
  await restoreOrganizationSelection(() => true)
  const result = await selectConnectionWithOutcome('local', { profile: 'legacy-two' })
  expect(result.status).toBe('committed')
  expect(storage().read()).toEqual({ kind: 'none' })
  expect($connection.get()?.profile).toBe('legacy-two')
})

it.each([[false, false], [true, false], [false, true], [true, true]])('selects a verified unconfigured profile without recovery (remembered owner: %s, inventory enriched: %s)', async (remembered, enriched) => {
  if (remembered) {
    remember()
    await restoreOrganizationSelection(() => true)
  }
  if (enriched) {
    vi.mocked(bridge.connections!.setLastUsed!).mockResolvedValue({ ok: true, registry: {
      ...registry(), lastUsed: 'local', connections: registry().connections.map(route => ({ ...route, installId: 'display-only-probed-installation' }))
    } })
  }
  state.descriptorReply = async profile => ({ capabilityVersion: 1, state: 'unconfigured', scope: { kind: 'served-profile', profile }, metadata: null })
  const result = await selectRuntimeOrganizationConnection('local', 'empty-profile', organizationActivationVerifier(() => {}))
  expect({ result, profile: $connection.get()?.profile, gatewayMatches: Object.is($gateway.get(), sockets.at(-1)), recovery: $organizationSelectionRecovery.get(), saved: storage().read() }).toMatchObject({
    result: { status: 'committed', published: true }, profile: 'empty-profile', gatewayMatches: true, recovery: null, saved: { kind: 'none' }
  })
})

it('allows explicit retry of a pending owner but never automatic replay', async () => {
  storage().stage(identity())
  readOrganizationBootSelection()
  await expect(restoreOrganizationSelection(() => true)).rejects.toThrow('confirmation')
  expect(bridge.getConnectionFor).not.toHaveBeenCalled()
  await restoreOrganizationSelection(() => true, true)
  expect($connection.get()?.profile).toBe('org-one')
  expect(storage().read()).toMatchObject({ kind: 'record', record: { phase: 'resumable' } })
})

it.each(['installId', 'key-order'] as const)('promotes a verified owner through %s inventory bookkeeping', async change => {
  const route = registry().connections[0]
  const updated = change === 'installId' ? { ...route, installId: 'display-only-probed-installation' } : Object.fromEntries(Object.entries(route).reverse()) as unknown as DesktopRegistryConnection
  vi.mocked(bridge.connections!.setLastUsed!).mockResolvedValue({ ok: true, registry: { ...registry(), connections: [updated] } })
  const result = await selectRuntimeOrganizationConnection('local', 'org-one', organizationActivationVerifier(() => {}))
  expect(result).toMatchObject({ status: 'committed', published: true })
  expect(storage().read()).toMatchObject({ kind: 'record', record: { phase: 'resumable', identity: identity() } })
  expect($organizationSelectionRecovery.get()).toBeNull()
  await expect(getHermesConfigRecord()).resolves.toMatchObject({ current: 'org-one' })
})

it.each([
  ['removed', null], ['id', { id: 'other' }], ['kind', { kind: 'remote' }], ['label', { label: 'Changed' }],
  ['url', { url: 'https://changed.example' }], ['authMode', { authMode: 'oauth' }], ['org', { org: 'changed' }],
  ['host', { host: 'changed.example' }], ['user', { user: 'changed' }], ['port', { port: 22 }],
  ['keyPath', { keyPath: '/synthetic/changed' }], ['remoteHermesPath', { remoteHermesPath: '/synthetic/changed' }],
  ['remoteProfile', { remoteProfile: 'changed' }], ['tokenSet', { tokenSet: true }],
  ['tokenPreview', { tokenPreview: 'synthetic-preview' }], ['headerNames', { headerNames: ['X-Synthetic-Auth'] }],
  ['unknown', { futureAuthRevision: 2 }]
] as const)('refuses promotion and owned REST after %s changes during bookkeeping', async (_field, delta) => {
  const connections = delta ? [{ ...registry().connections[0], ...delta } as DesktopRegistryConnection] : []
  vi.mocked(bridge.connections!.setLastUsed!).mockResolvedValue({ ok: true, registry: { ...registry(), connections } })
  const result = await selectRuntimeOrganizationConnection('local', 'org-one', organizationActivationVerifier(() => {}))
  expect(result).toMatchObject({ status: 'committed', published: true })
  expect(storage().read()).toMatchObject({ kind: 'record', record: { phase: 'pending', identity: identity() } })
  expect($organizationSelectionRecovery.get()).toMatchObject({ reason: 'pending' })
  vi.mocked(bridge.api!).mockClear()
  await expect(getHermesConfigRecord()).rejects.toThrow('verified selected owner')
  expect(bridge.api).not.toHaveBeenCalled()
})

it('still rejects a replaced registry object before reset even when only its display hint changes', async () => {
  const original = $connection.get()
  const proof = deferred<unknown>()
  state.descriptorReply = () => proof.promise
  const selecting = selectRuntimeOrganizationConnection('local', 'org-one', organizationActivationVerifier(() => {}))
  await vi.waitFor(() => expect(sockets.at(-1)?.request).toHaveBeenCalled())
  setConnectionsRegistry({ ...registry(), connections: [{ ...registry().connections[0], installId: 'display-only-probed-installation' }] })
  proof.resolve(descriptor())
  expect(await selecting).toMatchObject({ status: 'unchanged', resetStarted: false, published: false })
  expect($connection.get()).toBe(original)
  expect(storage().read()).toEqual({ kind: 'none' })
})


it('keeps verified same-owner REST usable after a no-op profile intent while revoking old replies', async () => {
  remember()
  await restoreOrganizationSelection(() => true)
  const response = deferred<unknown>()
  vi.mocked(bridge.api).mockReturnValueOnce(response.promise)
  const old = getHermesConfigRecord().catch(error => error)
  await ensureGatewayProfile('org-one')
  await expect(getHermesConfigRecord()).resolves.toMatchObject({ current: 'org-one' })
  response.resolve({ model: { default: 'old-response' } })
  expect(await old).toBeInstanceOf(Error)
})

it('routes a verified default coordinate through its dedicated socket, never the physical primary', async () => {
  remember('default')
  await restoreOrganizationSelection(() => true)
  const selected = sockets.at(-1)!
  expect(selected).not.toBe(source)
  await expect(requestGatewayForAgent('local', 'default', 'config.get')).resolves.toMatchObject({ profile: 'default' })
  expect(selected.request.mock.calls.map(call => call[0])).toEqual(['organization.descriptor', 'config.get'])
  expect(source.request).not.toHaveBeenCalled()
})

it('refuses pinned owned RPCs during deferred proof and rejects a reply after transport revocation', async () => {
  remember()
  const proof = deferred<unknown>()
  state.descriptorReply = () => proof.promise
  const restoring = restoreOrganizationSelection(() => true)
  await vi.waitFor(() => expect(sockets.at(-1)?.request).toHaveBeenCalledWith('organization.descriptor', {}, 15000, expect.any(AbortSignal)))
  const target = sockets.at(-1)!
  await expect(requestGatewayForAgent('local', 'org-one', 'config.get')).rejects.toThrow('verified transport')
  expect(target.request.mock.calls.map(call => call[0])).toEqual(['organization.descriptor'])
  proof.resolve(descriptor())
  await restoring
  const reply = deferred<unknown>()
  target.request.mockReturnValueOnce(reply.promise)
  const read = requestGatewayForAgent('local', 'org-one', 'config.get').catch(error => error)
  await vi.waitFor(() => expect(target.request).toHaveBeenCalledWith('config.get', {}))
  target.emit('closed')
  reply.resolve({ private: 'old-owner-data' })
  expect(await read).toBeInstanceOf(Error)
})

it('rechecks registry publication listeners before starting a cold restore intent', async () => {
  remember()
  const next = registry()
  vi.mocked(bridge.connections!.list).mockResolvedValue(next)
  const off = $connectionsRegistry.listen(value => { if (value === next) { beginSourceSwitchIntent() } })
  await expect(restoreOrganizationSelection(() => true)).rejects.toThrow('cancelled')
  off()
  expect(bridge.getConnectionFor).not.toHaveBeenCalled()
})

it('a source intent superseding the pending write leaves honest recovery without activation', async () => {
  const actual = Storage.prototype.setItem
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, key, value) {
    actual.call(this, key, value)
    if (key === ORGANIZATION_SELECTION_KEY && JSON.parse(value).phase === 'pending') { beginSourceSwitchIntent() }
  })
  const result = await selectRuntimeOrganizationConnection('local', 'org-one', organizationActivationVerifier(() => {}))
  expect(result).toMatchObject({ status: 'superseded', resetStarted: false, published: false })
  expect($connection.get()?.profile).toBe('default')
  expect(storage().read()).toMatchObject({ kind: 'record', record: { phase: 'pending' } })
  await expect(restoreOrganizationSelection(() => true)).rejects.toThrow('confirmation')
})

it('revokes API authority if any persisted identity field changes within the same operation', async () => {
  remember()
  await restoreOrganizationSelection(() => true)
  const saved = storage().read()
  if (saved.kind !== 'record' || !saved.record.identity) { throw new Error('Expected restored owner') }
  localStorage.setItem(ORGANIZATION_SELECTION_KEY, JSON.stringify({ ...saved.record, identity: { ...saved.record.identity, isolationVersion: 1 } }))
  await expect(getHermesConfigRecord()).rejects.toThrow('verified selected owner')
})

it.each(['after-proof', 'during-reset'] as const)('revokes a disposed selection validator %s before destination publication', async timing => {
  let disposed = false
  let proofCalls = 0
  const originalConnection = $connection.get()
  const originalGateway = $gateway.get()
  const beforeReset = vi.fn(() => { if (timing === 'during-reset') { disposed = true } })
  const off = registerGatewaySwitchLifecycle({ beforeConnectionSwitch: beforeReset, refreshSessions: async () => {} })
  const verify = organizationActivationVerifier(() => {
    if (disposed) { throw new Error('Organization selection cancelled') }
    proofCalls++
    if (timing === 'after-proof' && proofCalls === 1) { queueMicrotask(() => { disposed = true }) }
  })

  try {
    const result = await selectRuntimeOrganizationConnection('local', 'org-one', verify)
    expect(result).toMatchObject({ resetStarted: timing === 'during-reset', published: false })
    expect(result.status).not.toBe('committed')
    expect($connection.get()).toBe(originalConnection)
    expect($gateway.get()).toBe(originalGateway)
    expect(storage().read()).toMatchObject({ kind: 'record', record: { phase: 'pending' } })
    expect($organizationSelectionRecovery.get()).not.toBeNull()
    expect(beforeReset).toHaveBeenCalledTimes(timing === 'during-reset' ? 1 : 0)
  } finally { off() }
})
