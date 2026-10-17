import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { createElement, type PropsWithChildren } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useComposerDraft } from '@/app/chat/composer/hooks/use-composer-draft'
import { useHermesConfigRecord } from '@/app/hooks/use-config-record'
import type { HermesApiRequest } from '@/global'
import { clearSessionDraft, mainComposerScope, stashSessionDraft } from '@/store/composer'
import { $composerSuggestionsBySession } from '@/store/composer-suggestions'
import {
  _resetOrganizationSelectionForTests,
  authorizeOrganizationApi,
  createOrganizationSelectionStorage,
  ORGANIZATION_SELECTION_KEY,
  type OrganizationSelectionIdentity
} from '@/store/organization-selection'
import { beginSourceSwitchIntent } from '@/store/source-switch-intent'
import { invalidateMcpSuggestionIndex } from '@/store/suggestion-providers/mcp'
import { invalidateSkillSuggestionIndex } from '@/store/suggestion-providers/skill'
import type { HermesConfigRecord } from '@/types/eidolon'

import { hermesApi, type ProfileScope, setApiRequestConnection, setApiRequestProfile } from './client'
import { getHermesConfigRecord, saveHermesConfigRecord, setEnvVar } from './config'
import * as mcpApi from './mcp'
import { getUsageAnalytics } from './models'
import * as skillsApi from './skills'
import { getActionStatus } from './system'
import { getToolsets, setToolsetEnabled } from './toolsets'

// Keep the hook, restored drafts, suggestion providers, and REST dispatch real.
// Only adapt assistant-ui's composer subscription to this mounted hook harness.
const composerAdapter = vi.hoisted(() => {
  let text = ''
  const listeners = new Set<() => void>()

  const composer = {
    setText: (value: string) => {
      text = value

      for (const listener of listeners) { listener() }
    }
  }

  return {
    aui: { composer: () => composer },
    runtime: {
      getState: () => ({ text }),
      subscribe: (listener: () => void) => {
        listeners.add(listener)

        return () => { listeners.delete(listener) }
      }
    },
    reset: () => { text = ''; listeners.clear() }
  }
})

vi.mock('@assistant-ui/react', () => ({
  useAui: () => composerAdapter.aui,
  useAuiState: (selector: (state: { composer: { text: string } }) => unknown) =>
    selector({ composer: composerAdapter.runtime.getState() }),
  useComposerRuntime: () => composerAdapter.runtime
}))

const ownerA: OrganizationSelectionIdentity = {
  connectionId: 'local',
  profile: 'research',
  organizationId: '11111111-1111-4111-8111-111111111111',
  ownerId: '22222222-2222-4222-8222-222222222222',
  isolationVersion: 1
}
const ownerB: OrganizationSelectionIdentity = {
  connectionId: 'remote',
  profile: 'delivery',
  organizationId: '33333333-3333-4333-8333-333333333333',
  ownerId: '44444444-4444-4444-8444-444444444444',
  isolationVersion: 1
}
const operationA = 'a1111111-1111-4111-8111-111111111111'
const operationB = 'b2222222-2222-4222-8222-222222222222'
const scopeA = { connectionId: ownerA.connectionId, profile: ownerA.profile }
const scopeB = { connectionId: ownerB.connectionId, profile: ownerB.profile }
const foreignScopes: ProfileScope[] = [
  scopeB,
  { connectionId: ownerA.connectionId, profile: ownerB.profile },
  { connectionId: ownerB.connectionId, profile: ownerA.profile },
  ownerB.profile
]
const config: HermesConfigRecord = { model: { default: 'selected-owner-model' } }

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })

  return { promise, resolve }
}

let client: QueryClient
let originalDesktop: PropertyDescriptor | undefined
const api = vi.fn<(request: HermesApiRequest) => Promise<unknown>>()
const saved = createOrganizationSelectionStorage({
  getItem: key => localStorage.getItem(key),
  setItem: (key, value) => localStorage.setItem(key, value),
  removeItem: key => localStorage.removeItem(key)
})
const wrapper = ({ children }: PropsWithChildren) => createElement(QueryClientProvider, { client }, children)

beforeEach(() => {
  _resetOrganizationSelectionForTests()
  localStorage.removeItem(ORGANIZATION_SELECTION_KEY)
  setApiRequestConnection(null)
  setApiRequestProfile(null)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  api.mockReset()
  api.mockImplementation(async request => request.method === 'PUT' ? { ok: true } : config)
  originalDesktop = Object.getOwnPropertyDescriptor(window, 'hermesDesktop')
  Object.defineProperty(window, 'hermesDesktop', { configurable: true, value: { api } })
})

afterEach(() => {
  cleanup()
  client.clear()
  vi.restoreAllMocks()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  _resetOrganizationSelectionForTests()
  localStorage.removeItem(ORGANIZATION_SELECTION_KEY)
  setApiRequestConnection(null)
  setApiRequestProfile(null)
  if (originalDesktop) { Object.defineProperty(window, 'hermesDesktop', originalDesktop) }
  else { Reflect.deleteProperty(window, 'hermesDesktop') }
})

describe('organization selection REST authority', () => {
  it('preserves partial capability-pin payloads without a saved owner and blocks the same pins before verification', async () => {
    setApiRequestConnection(ownerB.connectionId)
    setApiRequestProfile(ownerB.profile)

    const pins: { scope: ProfileScope; expected: { profile?: string } }[] = [
      { scope: {}, expected: {} },
      { scope: { profile: 'pinned-profile' }, expected: { profile: 'pinned-profile' } },
      { scope: { connectionId: null, profile: 'pinned-profile' }, expected: { profile: 'pinned-profile' } }
    ]

    const calls: { run: (scope: ProfileScope) => Promise<unknown>; request: HermesApiRequest }[] = [
      { run: getHermesConfigRecord, request: { path: '/api/config' } },
      { run: scope => saveHermesConfigRecord(config, scope), request: { path: '/api/config', method: 'PUT', body: { config } } },
      { run: scope => setEnvVar('TEST_SETTING', 'pinned-value', scope), request: { path: '/api/env', method: 'PUT', body: { key: 'TEST_SETTING', value: 'pinned-value' } } },
      { run: skillsApi.getSkills, request: { path: '/api/skills' } },
      { run: scope => skillsApi.setSkillEnabled('research-notes', false, scope), request: { path: '/api/skills/toggle', method: 'PUT', body: { name: 'research-notes', enabled: false } } },
      { run: mcpApi.getMcpCatalog, request: { path: '/api/mcp/catalog' } },
      { run: scope => mcpApi.saveMcpServers({}, scope), request: { path: '/api/mcp/servers', method: 'PUT', body: { servers: {} } } },
      { run: getToolsets, request: { path: '/api/tools/toolsets' } },
      { run: scope => setToolsetEnabled('browser tools', false, scope), request: { path: '/api/tools/toolsets/browser%20tools', method: 'PUT', body: { enabled: false } } },
      { run: scope => getUsageAnalytics(7, scope), request: { path: '/api/analytics/usage?days=7' } },
      { run: scope => getActionStatus('setup task', 42, scope), request: { path: '/api/actions/setup%20task/status?lines=42' } }
    ]

    for (const { scope, expected } of pins) {
      api.mockClear()

      for (const { run } of calls) { await run(scope) }

      // A partial object pin never inherited either missing field from the
      // ambient source. Preserve the former preload payload exactly.
      expect(api.mock.calls.map(([request]) => request)).toStrictEqual(
        calls.map(({ request }) => ({ ...expected, ...request }))
      )
    }

    api.mockClear()

    for (const phase of ['pending', 'resumable'] as const) {
      const pending = saved.stage(ownerB, operationB)

      if (phase === 'resumable') { saved.promote(pending) }

      for (const { scope } of pins) {
        for (const { run } of calls) {
          await expect(run(scope)).rejects.toThrow('verified selected owner')
        }
      }

      expect(api).not.toHaveBeenCalled()
    }
  })

  it('preserves legacy routing without a record, but blocks saved and pending owners before the bridge, including mounted config readers', async () => {
    await expect(getHermesConfigRecord()).resolves.toEqual(config)
    await expect(setEnvVar('TEST_SETTING', 'legacy-value')).resolves.toEqual({ ok: true })
    expect(api.mock.calls.map(([request]) => request)).toEqual([
      { path: '/api/config' },
      { path: '/api/env', method: 'PUT', body: { key: 'TEST_SETTING', value: 'legacy-value' } }
    ])

    setApiRequestConnection(ownerA.connectionId)
    setApiRequestProfile(ownerA.profile)
    api.mockClear()
    await getHermesConfigRecord()
    await getHermesConfigRecord(scopeB)
    await setEnvVar('TEST_SETTING', 'foreign-value', scopeB)
    expect(api.mock.calls.map(([request]) => request)).toEqual([
      { ...scopeA, path: '/api/config' },
      { ...scopeB, path: '/api/config' },
      { ...scopeB, path: '/api/env', method: 'PUT', body: { key: 'TEST_SETTING', value: 'foreign-value' } }
    ])

    const legacyHook = renderHook(() => useHermesConfigRecord(), { wrapper })
    await waitFor(() => expect(legacyHook.result.current.data).toEqual(config))
    expect(api).toHaveBeenLastCalledWith({ ...scopeA, path: '/api/config' })
    legacyHook.unmount()
    client.clear()
    api.mockClear()

    for (const phase of ['pending', 'resumable'] as const) {
      const pending = saved.stage(ownerA, operationA)
      if (phase === 'resumable') { saved.promote(pending) }

      for (const scope of [undefined, scopeA, ...foreignScopes]) {
        await expect(getHermesConfigRecord(scope)).rejects.toThrow('verified selected owner')
        await expect(setEnvVar('TEST_SETTING', 'blocked-value', scope)).rejects.toThrow('verified selected owner')
      }
      const hook = renderHook(() => ({
        ambient: useHermesConfigRecord(),
        foreign: useHermesConfigRecord(scopeB)
      }), { wrapper })
      await waitFor(() => {
        expect(hook.result.current.ambient.isError).toBe(true)
        expect(hook.result.current.foreign.isError).toBe(true)
      })
      expect(hook.result.current.ambient.data).toBeUndefined()
      expect(hook.result.current.foreign.data).toBeUndefined()
      expect(api).not.toHaveBeenCalled()
      expect(saved.read()).toEqual({ kind: 'record', record: { ...pending, phase } })
      hook.unmount()
      client.clear()
    }

    localStorage.setItem(ORGANIZATION_SELECTION_KEY, '{malformed')
    for (const scope of [undefined, scopeA, scopeB]) {
      await expect(getHermesConfigRecord(scope)).rejects.toThrow('verified selected owner')
      await expect(setEnvVar('TEST_SETTING', 'invalid-record', scope)).rejects.toThrow('verified selected owner')
    }
    vi.stubGlobal('localStorage', { getItem: () => { throw new Error('storage unavailable') } })
    for (const scope of [undefined, scopeA, scopeB]) {
      await expect(getHermesConfigRecord(scope)).rejects.toThrow('verified selected owner')
      await expect(setEnvVar('TEST_SETTING', 'unreadable-record', scope)).rejects.toThrow('verified selected owner')
    }
    expect(api).not.toHaveBeenCalled()
  })

  it('dispatches only for the exact live owner and rejects closed or replaced generations before dispatch and after late replies', async () => {
    setApiRequestConnection(ownerA.connectionId)
    setApiRequestProfile(ownerA.profile)
    const oldPending = saved.stage(ownerA, operationB)
    const pendingA = saved.stage(ownerA, operationA)
    authorizeOrganizationApi(oldPending, () => true)
    await expect(getHermesConfigRecord(scopeA)).rejects.toThrow('verified selected owner')
    await expect(setEnvVar('TEST_SETTING', 'stale-authority', scopeA)).rejects.toThrow('verified selected owner')
    expect(api).not.toHaveBeenCalled()

    let live = true
    authorizeOrganizationApi(pendingA, () => live)
    for (const scope of foreignScopes) {
      await expect(getHermesConfigRecord(scope)).rejects.toThrow('verified selected owner')
      await expect(setEnvVar('TEST_SETTING', 'foreign-value', scope)).rejects.toThrow('verified selected owner')
    }
    expect(api).not.toHaveBeenCalled()

    await expect(getHermesConfigRecord(scopeA)).resolves.toEqual(config)
    await expect(setEnvVar('TEST_SETTING', 'selected-value', scopeA)).resolves.toEqual({ ok: true })
    // The common boundary also supplies the verified profile to helpers that
    // have not attached a profile themselves.
    await expect(hermesApi({ path: '/api/config' })).resolves.toEqual(config)
    await expect(getHermesConfigRecord({ connectionId: ownerA.connectionId })).resolves.toEqual(config)
    expect(api.mock.calls.map(([request]) => request)).toEqual([
      { ...scopeA, path: '/api/config' },
      { ...scopeA, path: '/api/env', method: 'PUT', body: { key: 'TEST_SETTING', value: 'selected-value' } },
      { ...scopeA, path: '/api/config' },
      { ...scopeA, path: '/api/config' }
    ])
    const authorizedHook = renderHook(() => useHermesConfigRecord(), { wrapper })
    await waitFor(() => expect(authorizedHook.result.current.data).toEqual(config))
    authorizedHook.unmount()
    client.clear()

    const previousIntentReply = deferred<HermesConfigRecord>()
    api.mockImplementationOnce(() => previousIntentReply.promise)
    const previousIntent = expect(getHermesConfigRecord(scopeA)).rejects.toThrow('owner changed')
    beginSourceSwitchIntent()
    // A same-owner no-op intent keeps the verified owner usable, but cannot
    // publish a response that was captured under the preceding intent.
    await expect(getHermesConfigRecord(scopeA)).resolves.toEqual(config)
    previousIntentReply.resolve(config)
    await previousIntent

    api.mockClear()
    live = false
    await expect(getHermesConfigRecord()).rejects.toThrow('verified selected owner')
    await expect(setEnvVar('TEST_SETTING', 'closed-value', scopeA)).rejects.toThrow('verified selected owner')
    expect(api).not.toHaveBeenCalled()

    live = true
    const lateRead = deferred<HermesConfigRecord>()
    const lateWrite = deferred<{ ok: boolean }>()
    api.mockImplementationOnce(() => lateRead.promise).mockImplementationOnce(() => lateWrite.promise)
    const rejectedRead = expect(getHermesConfigRecord()).rejects.toThrow('owner changed')
    const rejectedWrite = expect(setEnvVar('TEST_SETTING', 'late-value')).rejects.toThrow('owner changed')
    expect(api).toHaveBeenCalledTimes(2)
    live = false
    lateRead.resolve(config)
    lateWrite.resolve({ ok: true })
    await Promise.all([rejectedRead, rejectedWrite])

    live = true
    const firstPredicate = () => live
    authorizeOrganizationApi(pendingA, firstPredicate)
    const oldHookReply = deferred<HermesConfigRecord>()
    api.mockClear()
    api.mockImplementationOnce(() => oldHookReply.promise)
    const oldHook = renderHook(() => useHermesConfigRecord(), { wrapper })
    await waitFor(() => expect(api).toHaveBeenCalledOnce())
    expect(oldHook.result.current.data).toBeUndefined()
    expect(oldHook.result.current.isPending).toBe(true)

    const pendingB = saved.stage(ownerB, operationB)
    setApiRequestConnection(ownerB.connectionId)
    setApiRequestProfile(ownerB.profile)
    // Replacing the persisted operation revokes A even before B is authorized.
    await expect(getHermesConfigRecord(scopeA)).rejects.toThrow('verified selected owner')
    await expect(setEnvVar('TEST_SETTING', 'superseded-value', scopeA)).rejects.toThrow('verified selected owner')
    expect(api).toHaveBeenCalledOnce()
    authorizeOrganizationApi(pendingB, () => true)
    await expect(getHermesConfigRecord()).resolves.toEqual(config)
    expect(api).toHaveBeenLastCalledWith({ ...scopeB, path: '/api/config' })

    // Restore even the same operation id and identity: the new authority
    // instance must still be unable to revive A's already-dispatched promise.
    const returnedA = saved.stage(ownerA, operationA)
    setApiRequestConnection(ownerA.connectionId)
    setApiRequestProfile(ownerA.profile)
    const returnedPredicate = () => live
    expect(returnedPredicate).not.toBe(firstPredicate)
    expect(firstPredicate()).toBe(true)
    authorizeOrganizationApi(returnedA, returnedPredicate)
    await expect(getHermesConfigRecord(scopeB)).rejects.toThrow('verified selected owner')
    await expect(setEnvVar('TEST_SETTING', 'superseded-b', scopeB)).rejects.toThrow('verified selected owner')
    expect(api).toHaveBeenCalledTimes(2)
    await act(async () => { oldHookReply.resolve({ model: { default: 'stale-owner-model' } }) })
    await waitFor(() => expect(oldHook.result.current.isError).toBe(true))
    expect(oldHook.result.current.error?.message).toContain('owner changed')
    expect(oldHook.result.current.data).toBeUndefined()
    await act(async () => { await oldHook.result.current.refetch() })
    await waitFor(() => expect(oldHook.result.current.data).toEqual(config))
    expect(api).toHaveBeenLastCalledWith({ ...scopeA, path: '/api/config' })
  })
})

describe('organization selection protects restored Chat capabilities', () => {
  beforeEach(() => {
    composerAdapter.reset()
    invalidateMcpSuggestionIndex()
    invalidateSkillSuggestionIndex()
    setApiRequestConnection(ownerA.connectionId)
    setApiRequestProfile(ownerA.profile)
  })

  afterEach(() => {
    mainComposerScope.clear()
    invalidateMcpSuggestionIndex()
    invalidateSkillSuggestionIndex()

    for (const phase of ['pending', 'resumable']) {
      clearSessionDraft(`organization-${phase}-empty`)
      clearSessionDraft(`organization-${phase}-restored`)
    }
  })

  it('restores and samples a mounted Chat draft without reading skills or MCP data until its saved owner is verified', async () => {
    vi.useFakeTimers()
    const skillsRead = vi.spyOn(skillsApi, 'getSkills')
    const catalogRead = vi.spyOn(mcpApi, 'getMcpCatalog')
    const serversRead = vi.spyOn(mcpApi, 'listMcpServers')
    const draft = 'Use research-notes for this linear request.'
    api.mockImplementation(async request => {
      if (request.path === '/api/skills') {
        return [{ name: 'research-notes', enabled: true, category: 'research', description: 'Research notes' }]
      }

      if (request.path === '/api/mcp/catalog') {
        return { entries: [{ name: 'linear', url: 'https://mcp.linear.app/mcp', suggest: { keywords: ['linear'], hosts: [] } }] }
      }

      if (request.path === '/api/mcp/servers') { return { servers: [] } }
      throw new Error(`Unexpected draft request: ${request.path}`)
    })

    for (const phase of ['pending', 'resumable'] as const) {
      _resetOrganizationSelectionForTests()
      invalidateMcpSuggestionIndex()
      invalidateSkillSuggestionIndex()
      api.mockClear()
      skillsRead.mockClear()
      catalogRead.mockClear()
      serversRead.mockClear()
      const pending = saved.stage(ownerA, operationA)

      if (phase === 'resumable') { saved.promote(pending) }

      const emptySession = `organization-${phase}-empty`
      const restoredSession = `organization-${phase}-restored`
      const queueEditRef = { current: null }
      stashSessionDraft(restoredSession, draft, [])

      const hook = renderHook(({ sessionId }) => useComposerDraft({
        activeQueueSessionKey: sessionId,
        focusKey: null,
        inputDisabled: false,
        queueEditRef,
        sessionId
      }), { initialProps: { sessionId: emptySession } })

      // Restoring a saved session drives the mounted runtime subscription and
      // real debounced providers, rather than invoking their helpers by hand.
      hook.rerender({ sessionId: restoredSession })
      expect(hook.result.current.draftRef.current).toBe(draft)
      await act(async () => { await vi.advanceTimersByTimeAsync(600) })
      expect(skillsRead).toHaveBeenCalledOnce()
      expect(catalogRead).toHaveBeenCalledOnce()
      expect(serversRead).not.toHaveBeenCalled()
      expect(api).not.toHaveBeenCalled()
      expect($composerSuggestionsBySession.get()[restoredSession]).toBeUndefined()

      authorizeOrganizationApi(pending, () => true)
      hook.rerender({ sessionId: emptySession })
      hook.rerender({ sessionId: restoredSession })
      expect(hook.result.current.draftRef.current).toBe(draft)
      await act(async () => { await vi.advanceTimersByTimeAsync(600) })
      expect(skillsRead).toHaveBeenCalledTimes(2)
      expect(catalogRead).toHaveBeenCalledTimes(2)
      expect(serversRead).toHaveBeenCalledOnce()
      expect(api.mock.calls.map(([request]) => request)).toEqual(expect.arrayContaining([
        { ...scopeA, path: '/api/skills' },
        { ...scopeA, path: '/api/mcp/catalog' },
        { ...scopeA, path: '/api/mcp/servers' }
      ]))
      expect(api).toHaveBeenCalledTimes(3)
      expect($composerSuggestionsBySession.get()[restoredSession]?.map(({ id, provider }) => ({ id, provider })))
        .toEqual(expect.arrayContaining([{ id: 'linear', provider: 'mcp' }, { id: 'research-notes', provider: 'skill' }]))
      hook.unmount()
    }
  })

  it('blocks MCP replacement writes before proof and preserves the exact selected-owner payload after proof', async () => {
    const servers = { selected: { command: 'selected-owner-command', args: ['--read-only'] } }

    for (const phase of ['pending', 'resumable'] as const) {
      const pending = saved.stage(ownerA, operationA)

      if (phase === 'resumable') { saved.promote(pending) }

      for (const scope of [undefined, scopeA, ...foreignScopes]) {
        await expect(mcpApi.saveMcpServers(servers, scope)).rejects.toThrow('verified selected owner')
      }

      expect(api).not.toHaveBeenCalled()
    }

    localStorage.setItem(ORGANIZATION_SELECTION_KEY, '{malformed')
    await expect(mcpApi.saveMcpServers(servers)).rejects.toThrow('verified selected owner')
    expect(api).not.toHaveBeenCalled()

    const pending = saved.stage(ownerA, operationA)
    authorizeOrganizationApi(pending, () => true)

    for (const scope of foreignScopes) {
      await expect(mcpApi.saveMcpServers(servers, scope)).rejects.toThrow('verified selected owner')
    }

    expect(api).not.toHaveBeenCalled()
    await expect(mcpApi.saveMcpServers(servers)).resolves.toEqual({ ok: true })
    expect(api).toHaveBeenCalledExactlyOnceWith({ ...scopeA, path: '/api/mcp/servers', method: 'PUT', body: { servers } })
  })
})
