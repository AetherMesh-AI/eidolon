import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ getEnvVars: vi.fn(), setEnvVar: vi.fn(), deleteEnvVar: vi.fn(), revealEnvVar: vi.fn() }))
vi.mock('@/eidolon', () => api)
vi.mock('@/store/profile', () => ({ $activeGatewayProfile: atom('alpha'), normalizeProfileKey: (name?: string) => name || 'default' }))
vi.mock('@/store/session', () => ({ $connection: atom({ connectionId: 'synthetic-a' }) }))
vi.mock('@/store/notifications', () => ({ notify: vi.fn(), notifyError: vi.fn() }))

import { $activeGatewayProfile } from '@/store/profile'
import { $connection } from '@/store/session'
import type { EnvVarInfo } from '@/types/eidolon'

import { useEnvCredentials } from './env-credentials'
import { envVar } from './test-utils'

const deferred = <T,>() => {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })

  return { promise, resolve, reject }
}

const catalog: Record<string, EnvVarInfo> = { SYNTHETIC_KEY: envVar('tool') }

beforeEach(() => { vi.clearAllMocks(); $activeGatewayProfile.set('alpha') })
afterEach(cleanup)

it('keeps loading, empty and failures distinct, deduplicates retries, and preserves same-owner edits', async () => {
  const initial = deferred<typeof catalog>()
  api.getEnvVars.mockReturnValueOnce(initial.promise)
  const { result } = renderHook(() => useEnvCredentials())
  expect(result.current.vars).toBeNull()
  expect(result.current.read.loading).toBe(true)
  await act(async () => { initial.reject(new Error('synthetic private detail')) })
  expect(result.current.read.failed).toBe(true)
  expect(result.current.read.loading).toBe(false)
  const retry = deferred<typeof catalog>()
  api.getEnvVars.mockReturnValueOnce(retry.promise)
  act(() => { void result.current.read.refresh(); void result.current.read.refresh() })
  expect(api.getEnvVars).toHaveBeenCalledTimes(2)
  await act(async () => { retry.reject(new Error('another private detail')) })
  expect(result.current.read.failed).toBe(true)
  api.getEnvVars.mockResolvedValueOnce(catalog)
  await act(async () => { await result.current.read.refresh() })
  act(() => { result.current.rowProps.setEdits({ SYNTHETIC_KEY: 'synthetic unsaved text' }) })
  api.getEnvVars.mockRejectedValueOnce(new Error('private refresh failure'))
  await act(async () => { await result.current.read.refresh() })
  expect(result.current.vars).toEqual(catalog)
  expect(result.current.read.failed).toBe(true)
  expect(result.current.rowProps.edits).toEqual({ SYNTHETIC_KEY: 'synthetic unsaved text' })
  api.getEnvVars.mockResolvedValueOnce({})
  await act(async () => { await result.current.read.refresh() })
  expect(result.current.vars).toEqual({})
  expect(result.current.read.failed).toBe(false)
  expect(result.current.rowProps.edits).toEqual({ SYNTHETIC_KEY: 'synthetic unsaved text' })
  expect(api.setEnvVar).not.toHaveBeenCalled()
  expect(api.deleteEnvVar).not.toHaveBeenCalled()
  expect(api.revealEnvVar).not.toHaveBeenCalled()
  expect(api.getEnvVars.mock.calls.every(([profile]) => profile === undefined)).toBe(true)
  api.getEnvVars.mockImplementationOnce(() => { throw new Error('synchronous bridge failure') })
  await act(async () => { await result.current.read.refresh() })
  expect(result.current.read.failed).toBe(true)
  api.getEnvVars.mockResolvedValueOnce(catalog)
  await act(async () => { await result.current.read.refresh() })
  expect(result.current.read.failed).toBe(false)
  expect(result.current.vars).toEqual(catalog)
  const olderRead = deferred<Record<string, never>>()
  api.getEnvVars.mockReturnValueOnce(olderRead.promise)
  act(() => { void result.current.read.refresh() })
  api.setEnvVar.mockResolvedValueOnce({ ok: true })
  await act(async () => { await result.current.saveValue('SYNTHETIC_KEY', 'synthetic new value') })
  await act(async () => { olderRead.resolve({}) })
  expect(result.current.vars?.SYNTHETIC_KEY.is_set).toBe(true)
  expect(result.current.read.loading).toBe(false)
})

it('suppresses old-owner data, edits and late success/error across profile, connection and unmount boundaries', async () => {
  api.getEnvVars.mockResolvedValueOnce(catalog)
  const { result, unmount } = renderHook(() => useEnvCredentials())
  await waitFor(() => expect(result.current.vars).toEqual(catalog))
  act(() => { result.current.rowProps.setEdits({ SYNTHETIC_KEY: 'owner A only' }) })
  const old = deferred<typeof catalog>()
  const current = deferred<typeof catalog>()
  api.getEnvVars.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise)
  act(() => { void result.current.read.refresh(); $activeGatewayProfile.set('beta') })
  expect(result.current.vars).toBeNull()
  expect(result.current.rowProps.edits).toEqual({})
  expect(result.current.read.failed).toBe(false)
  await act(async () => { old.resolve(catalog) })
  expect(result.current.vars).toBeNull()
  await act(async () => { current.resolve({}) })
  expect(result.current.vars).toEqual({})
  const oldConnection = deferred<typeof catalog>()
  const newConnection = deferred<typeof catalog>()
  api.getEnvVars.mockReturnValueOnce(oldConnection.promise).mockReturnValueOnce(newConnection.promise)
  act(() => { void result.current.read.refresh(); $connection.set({ ...$connection.get()!, connectionId: 'synthetic-b' }) })
  await act(async () => { oldConnection.reject(new Error('private old-owner failure')) })
  expect(result.current.read.failed).toBe(false)
  expect(result.current.read.loading).toBe(true)
  unmount()
  await act(async () => { newConnection.resolve(catalog) })
  expect(api.setEnvVar).not.toHaveBeenCalled()
})
