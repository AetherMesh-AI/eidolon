import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { PaginatedSessions, SessionInfo, SessionMessagesResponse } from '@/types/eidolon'

import { setApiRequestConnection, setApiRequestProfile } from './client'
import { getAllSessionMessages, listAllProfileSessions } from './sessions'

function deferred<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(done => {
    resolve = done
  })

  return { promise, resolve }
}

const api = vi.fn()
beforeEach(() => {
  api.mockReset()
  vi.stubGlobal('hermesDesktop', { api })
  setApiRequestConnection('source-a')
  setApiRequestProfile('profile-a')
})
afterEach(() => {
  vi.unstubAllGlobals()
  setApiRequestConnection(null)
  setApiRequestProfile(null)
})

describe('artifact indexing API read ownership', () => {
  it('pins list routing and stamps a delayed result with its captured source', async () => {
    const pending = deferred<PaginatedSessions>()
    api.mockReturnValueOnce(pending.promise)

    const read = listAllProfileSessions(
      30,
      1,
      'exclude',
      'recent',
      'all',
      {},
      { connectionId: 'source-a', profile: 'profile-a' }
    )

    setApiRequestConnection('source-b')
    setApiRequestProfile('profile-b')
    pending.resolve({
      sessions: [{ id: 'session-a', profile: 'profile-a' } as SessionInfo],
      limit: 30,
      offset: 0,
      total: 1
    })
    const result = await read
    expect(api).toHaveBeenCalledTimes(1)
    expect(api).toHaveBeenCalledWith(expect.objectContaining({ connectionId: 'source-a', profile: 'profile-a' }))
    expect(result.sessions[0].connection_id).toBe('source-a')
  })

  it('preserves an explicit row owner and an explicit local request pin', async () => {
    api.mockResolvedValueOnce({ sessions: [{ id: 'session-a', connection_id: 'row-owner' }], total: 1 })

    const result = await listAllProfileSessions(
      30,
      1,
      'exclude',
      'recent',
      'all',
      {},
      { connectionId: 'local', profile: 'default' }
    )

    expect(api).toHaveBeenCalledWith(expect.objectContaining({ connectionId: 'local', profile: 'default' }))
    expect(result.sessions[0].connection_id).toBe('row-owner')
  })

  it('does not start another page after the indexing owner is revoked', async () => {
    let current = true
    const pending = deferred<SessionMessagesResponse>()
    api.mockReturnValueOnce(pending.promise)

    const read = getAllSessionMessages(
      'session-a',
      { connectionId: 'source-a', profile: 'profile-a' },
      { isCurrent: () => current }
    )

    const rejected = expect(read).rejects.toThrow('superseded')
    current = false
    setApiRequestConnection('source-b')
    setApiRequestProfile('profile-b')
    pending.resolve({
      session_id: 'session-a',
      messages: [{ role: 'assistant', content: 'old page' }],
      pagination: { limit: 1, offset: 0, order: 'oldest', returned: 1 }
    })
    await rejected
    expect(api).toHaveBeenCalledTimes(1)
    expect(api).toHaveBeenCalledWith(expect.objectContaining({ connectionId: 'source-a', profile: 'profile-a' }))
  })

  it('rejects an already revoked scan before sending its first request', async () => {
    await expect(
      getAllSessionMessages('session-a', { connectionId: 'source-a' }, { isCurrent: () => false })
    ).rejects.toThrow('superseded')
    expect(api).not.toHaveBeenCalled()
  })

  it('keeps every allowed page on the explicit session owner despite ambient changes', async () => {
    api
      .mockImplementationOnce(async () => {
        setApiRequestConnection('source-b')
        setApiRequestProfile('profile-b')

        return {
          session_id: 'session-a',
          messages: [{ role: 'assistant', content: 'first' }],
          pagination: { limit: 1, offset: 0, order: 'oldest', returned: 1 }
        }
      })
      .mockResolvedValueOnce({ session_id: 'session-a', messages: [] })

    const result = await getAllSessionMessages(
      'session-a',
      { connectionId: 'source-a', profile: 'profile-a' },
      { isCurrent: () => true }
    )

    expect(result.messages).toHaveLength(1)
    expect(api).toHaveBeenCalledTimes(2)

    for (const [request] of api.mock.calls) {
      expect(request).toMatchObject({ connectionId: 'source-a', profile: 'profile-a' })
    }
  })
})
