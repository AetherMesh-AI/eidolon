import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setApiRequestConnection, setApiRequestProfile } from '@/api/client'
import type { HermesConnection } from '@/global'
import { TRANSLATIONS } from '@/i18n/catalog'
import { $pendingConnectionId } from '@/store/connections'
import { $localOwnerProofRevision } from '@/store/gateway'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection } from '@/store/session'
import { $sourceSwitchIntent } from '@/store/source-switch-intent'
import type { PaginatedSessions, SessionInfo, SessionMessagesResponse } from '@/types/eidolon'

import { ArtifactsView } from './index'

const reads = vi.hoisted(() => ({ list: vi.fn(), messages: vi.fn() }))
vi.mock('@/eidolon', async () => ({
  ...(await vi.importActual('@/eidolon')),
  listAllProfileSessions: (...args: unknown[]) => reads.list(...args),
  getAllSessionMessages: (...args: unknown[]) => reads.messages(...args)
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void

  const promise = new Promise<T>((done, fail) => {
    resolve = done
    reject = fail
  })

  return { promise, resolve, reject }
}

const connection = (id = 'source-a', authenticationIdentity = 'auth-a'): HermesConnection => ({
  connectionId: id,
  authenticationIdentity,
  profile: 'default',
  baseUrl: 'https://fixture.invalid',
  mode: 'remote',
  isFullscreen: false,
  nativeOverlayWidth: 0,
  windowButtonPosition: null,
  logs: [],
  token: '',
  wsUrl: ''
})

const session = (id = 'session-a', profile = 'default', source = 'source-a') =>
  ({ id, title: id, profile, connection_id: source }) as SessionInfo

const page = (sessions: SessionInfo[] = [], errors?: PaginatedSessions['errors']): PaginatedSessions => ({
  sessions,
  errors,
  limit: 30,
  offset: 0,
  total: sessions.length
})

const messages = (label: string): SessionMessagesResponse => ({
  session_id: 'session-a',
  messages: [{ role: 'assistant', content: `MEDIA:/outputs/${label}.pdf`, timestamp: 1000 }]
})

const mount = (route = '/artifacts') =>
  render(
    <MemoryRouter initialEntries={[route]}>
      <ArtifactsView />
    </MemoryRouter>
  )

const refresh = () => fireEvent.click(screen.getByRole('button', { name: 'Refresh artifacts' }))

beforeEach(() => {
  reads.list.mockReset().mockResolvedValue(page())
  reads.messages.mockReset().mockResolvedValue(messages('report'))
  $pendingConnectionId.set(null)
  $gatewaySwapTarget.set(null)
  $activeGatewayProfile.set('default')
  $sourceSwitchIntent.set(0)
  setApiRequestConnection('source-a')
  setApiRequestProfile('default')
  $connection.set(connection())
})
afterEach(() => {
  cleanup()
  $connection.set(null)
  setApiRequestConnection(null)
  setApiRequestProfile(null)
})

describe('session artifact read states', () => {
  it('keeps initial loading separate from a failed read, and retries into confirmed empty', async () => {
    const pending = deferred<PaginatedSessions>()
    reads.list.mockReturnValueOnce(pending.promise)
    mount()
    expect(screen.getByRole('status', { name: 'Indexing recent session artifacts' })).toBeTruthy()
    expect(screen.queryByText('No artifacts found')).toBeNull()
    fireEvent.keyDown(window, { key: 'r' })
    fireEvent.keyDown(window, { key: 'r' })
    expect(reads.list).toHaveBeenCalledTimes(1)
    await act(async () => {
      pending.reject(new Error('unavailable'))
    })
    expect(screen.getByRole('alert').textContent).toContain('Artifacts failed to load')
    expect(screen.queryByText('No artifacts found')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Retry indexing' }))
    await screen.findByText('No artifacts found')
    expect(reads.list).toHaveBeenCalledTimes(2)
  })

  it('distinguishes filter misses from a successful empty index', async () => {
    reads.list.mockResolvedValue(page([session()]))
    mount('/artifacts?tab=image')
    await screen.findByText('No matching artifacts')
    expect(screen.queryByText('No artifacts found')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /All/ }))
    await screen.findByRole('button', { name: 'report.pdf' })
    fireEvent.change(screen.getByRole('textbox', { name: 'Search artifacts...' }), {
      target: { value: 'not-in-the-index' }
    })
    expect(screen.getByText('No matching artifacts')).toBeTruthy()
  })

  it('keeps unread session and profile failures visible when no artifacts could be indexed', async () => {
    reads.list.mockResolvedValue(page([session()], [{ profile: 'other', error: 'locked' }]))
    reads.messages.mockRejectedValue(new Error('safe-load limit'))
    mount()
    await screen.findByText('Could not read 1 of 1 recent sessions.')
    expect(screen.getByText('Profiles that could not be read: 1.')).toBeTruthy()
    expect(screen.getByText('Sessions exceeding the safe transcript load limit: 1.')).toBeTruthy()
    expect(screen.getByText(/Other sessions are still unavailable/)).toBeTruthy()
    expect(screen.queryByText('No artifacts found')).toBeNull()
    expect(screen.getByRole('button', { name: 'Retry indexing' })).toBeTruthy()
  })

  it('retains same-owner data during refresh and after failure, then clears stale state on recovery', async () => {
    reads.list.mockResolvedValue(page([session()]))
    mount()
    await screen.findByRole('button', { name: 'report.pdf' })
    const pending = deferred<PaginatedSessions>()
    reads.list.mockReturnValueOnce(pending.promise)
    refresh()
    expect(screen.getByRole('button', { name: 'report.pdf' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Refreshing artifacts' }).hasAttribute('disabled')).toBe(true)
    await act(async () => {
      pending.reject(new Error('offline'))
    })
    expect(screen.getByText(/may be out of date/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'report.pdf' })).toBeTruthy()
    reads.messages.mockResolvedValue(messages('recovered'))
    fireEvent.click(screen.getByRole('button', { name: 'Retry indexing' }))
    await screen.findByRole('button', { name: 'recovered.pdf' })
    expect(screen.queryByRole('button', { name: 'report.pdf' })).toBeNull()
    expect(screen.queryByText(/may be out of date/)).toBeNull()
  })

  it('does not claim retained items or a confirmed empty index after an empty-success refresh fails', async () => {
    mount()
    await screen.findByText('No artifacts found')
    reads.list.mockRejectedValueOnce(new Error('unavailable'))
    refresh()
    await screen.findByRole('button', { name: 'Retry indexing' })
    expect(screen.queryByText(/may be out of date/)).toBeNull()
    expect(screen.queryByText('No artifacts found')).toBeNull()
  })

  it('retains only unread portions of a partial refresh, with an explicit stale notice', async () => {
    reads.list.mockResolvedValue(page([session('read'), session('unread')]))
    reads.messages.mockImplementation(async (id: string) => messages(id))
    mount()
    await screen.findByRole('button', { name: 'unread.pdf' })
    reads.messages.mockImplementation(async (id: string) => {
      if (id === 'unread') {
        throw new Error('unread')
      }

      return { session_id: id, messages: [] }
    })
    refresh()
    await screen.findByText('Could not read 1 of 2 recent sessions.')
    expect(screen.getByRole('button', { name: 'unread.pdf' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'read.pdf' })).toBeNull()
    expect(screen.getByText(/may be out of date/)).toBeTruthy()
  })

  it('retains artifacts from a profile whose enumeration failed, but replaces successfully read sessions', async () => {
    reads.list.mockResolvedValueOnce(page([session('one', 'a'), session('two', 'b')]))
    reads.messages.mockImplementation(async (id: string) => messages(id))
    mount()
    await screen.findByRole('button', { name: 'two.pdf' })
    reads.list.mockResolvedValueOnce(page([session('one', 'a')], [{ profile: 'b', error: 'locked' }]))
    reads.messages.mockResolvedValueOnce(messages('updated'))
    refresh()
    await screen.findByRole('button', { name: 'updated.pdf' })
    expect(screen.getByRole('button', { name: 'two.pdf' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'one.pdf' })).toBeNull()
    expect(screen.getByText(/may be out of date/)).toBeTruthy()
  })

  it('does not read while disconnected or switching', async () => {
    $connection.set(null)
    mount()
    expect(screen.getByText(/Connect to a source/)).toBeTruthy()
    expect(reads.list).not.toHaveBeenCalled()
    await act(async () => {
      $pendingConnectionId.set('source-b')
      $connection.set(connection('source-b'))
    })
    expect(reads.list).not.toHaveBeenCalled()
    expect(screen.getByRole('status', { name: 'Waiting for the selected source' })).toBeTruthy()
    await act(async () => {
      setApiRequestConnection('source-b')
      $pendingConnectionId.set(null)
    })
    await screen.findByText('No artifacts found')
    expect(reads.list).toHaveBeenCalledTimes(1)
  })
})

describe('session artifact owner fences', () => {
  it('discards a late prior-source list without starting its transcript reads', async () => {
    const old = deferred<PaginatedSessions>()
    reads.list
      .mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(page([session('new-session', 'default', 'source-b')]))
    mount()
    await act(async () => {
      setApiRequestConnection('source-b')
      $connection.set(connection('source-b'))
    })
    await screen.findByRole('button', { name: 'report.pdf' })
    await act(async () => {
      old.resolve(page([session('old-session')]))
    })
    expect(reads.messages.mock.calls.map(([id]) => id)).toEqual(['new-session'])
    expect(reads.list.mock.calls[0][6]).toEqual({ connectionId: 'source-a', profile: 'default' })
    expect(reads.list.mock.calls[1][6]).toEqual({ connectionId: 'source-b', profile: 'default' })
  })

  it('clears old-profile artifacts while a new profile is pending and ignores a late old failure', async () => {
    reads.list.mockResolvedValueOnce(page([session()]))
    mount()
    await screen.findByRole('button', { name: 'report.pdf' })
    const late = deferred<PaginatedSessions>()
    const next = deferred<PaginatedSessions>()
    reads.list.mockReturnValueOnce(late.promise).mockReturnValueOnce(next.promise)
    refresh()
    await act(async () => {
      setApiRequestProfile('next')
      $activeGatewayProfile.set('next')
    })
    expect(screen.queryByRole('button', { name: 'report.pdf' })).toBeNull()
    await act(async () => {
      late.reject(new Error('old failure'))
    })
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByRole('status', { name: 'Indexing recent session artifacts' })).toBeTruthy()
    await act(async () => {
      next.resolve(page())
    })
    await screen.findByText('No artifacts found')
  })

  it('revokes pagination and remaining session reads synchronously on newer source intent', async () => {
    const late = deferred<SessionMessagesResponse>()
    reads.list.mockResolvedValueOnce(page([session('old-first'), session('old-second')])).mockResolvedValue(page())
    reads.messages.mockReturnValueOnce(late.promise)
    mount()
    await waitFor(() => expect(reads.messages).toHaveBeenCalledTimes(1))
    const options = reads.messages.mock.calls[0][2]
    expect(options.isCurrent()).toBe(true)
    await act(async () => {
      $sourceSwitchIntent.set(1)
      $pendingConnectionId.set('source-b')
      expect(options.isCurrent()).toBe(false)
      late.resolve(messages('obsolete'))
    })
    expect(reads.messages).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('obsolete.pdf')).toBeNull()
  })

  it('treats a changed authentication identity as a different owner even on the same source', async () => {
    const late = deferred<SessionMessagesResponse>()
    reads.list.mockResolvedValue(page([session()]))
    reads.messages.mockReturnValueOnce(late.promise).mockResolvedValueOnce(messages('new-owner'))
    mount()
    await waitFor(() => expect(reads.messages).toHaveBeenCalledTimes(1))
    await act(async () => {
      $connection.set(connection('source-a', 'auth-b'))
    })
    await screen.findByRole('button', { name: 'new-owner.pdf' })
    await act(async () => {
      late.resolve(messages('old-owner'))
    })
    expect(screen.queryByRole('button', { name: 'old-owner.pdf' })).toBeNull()
  })

  it('invalidates same-route data and pending reads when local owner proof changes', async () => {
    reads.list.mockResolvedValueOnce(page([session()]))
    mount()
    await screen.findByRole('button', { name: 'report.pdf' })
    const old = deferred<PaginatedSessions>()
    const current = deferred<PaginatedSessions>()
    reads.list.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise)
    refresh()
    await act(async () => {
      $localOwnerProofRevision.set($localOwnerProofRevision.get() + 1)
    })
    expect(screen.queryByRole('button', { name: 'report.pdf' })).toBeNull()
    await act(async () => {
      old.resolve(page([session('old-owner')]))
    })
    expect(reads.messages).toHaveBeenCalledTimes(1)
    await act(async () => {
      current.resolve(page())
    })
    await screen.findByText('No artifacts found')
  })

  it('revokes an in-flight transcript after unmount', async () => {
    const late = deferred<SessionMessagesResponse>()
    reads.list.mockResolvedValue(page([session('one'), session('two')]))
    reads.messages.mockReturnValueOnce(late.promise)
    const view = mount()
    await waitFor(() => expect(reads.messages).toHaveBeenCalledTimes(1))
    view.unmount()
    expect(reads.messages.mock.calls[0][2].isCurrent()).toBe(false)
    await act(async () => {
      late.resolve(messages('late'))
    })
    expect(reads.messages).toHaveBeenCalledTimes(1)
  })
})


it('localizes every artifact recovery state in all six supported locales', () => {
  for (const [locale, copy] of Object.entries(TRANSLATIONS)) {
    if (locale === 'en') { continue }

    for (const key of ['waitingForSource', 'noMatchesTitle', 'noMatchesDesc', 'failedLoadDesc', 'partialLoad', 'staleResults', 'incompleteEmpty', 'connectionUnavailable', 'retry', 'noArtifactsDesc'] as const) {
      expect(copy.artifacts[key], `${locale}.${key}`).not.toBe(TRANSLATIONS.en.artifacts[key])
    }

    expect(copy.artifacts.partialLoadDesc(1, 2)).not.toBe(TRANSLATIONS.en.artifacts.partialLoadDesc(1, 2))
    expect(copy.artifacts.profileLoadFailed(1)).not.toBe(TRANSLATIONS.en.artifacts.profileLoadFailed(1))
    expect(copy.artifacts.safeLoadLimit(1)).not.toBe(TRANSLATIONS.en.artifacts.safeLoadLimit(1))
  }
})
