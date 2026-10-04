/**
 * ADOPT-BEFORE-MINT (#92473 part 2).
 *
 * Between the registry miss and our eager `session.title` write, another
 * writer can take the canonical title (peer dm minting server-side, a second
 * machine, cross-connection sync). UNIQUE(title) rejects our write with
 * "already in use". Before this fix that rejection was read as "old gateway"
 * and the compat path prompted into OUR stray lazy session — forking the
 * forever chat. Now the mint re-consults the registry and adopts the winner;
 * the stray zero-message session is abandoned to the gateway's pruner.
 *
 * Ported from tests/canonical-chat-adopt-on-conflict.test.mjs.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { RosterRow } from './types'

const { hostMock, persistMock, requestForBotMock, saveBotMetaMock } = vi.hoisted(() => ({
  hostMock: { openSession: vi.fn(), request: vi.fn() },
  persistMock: vi.fn(),
  requestForBotMock: vi.fn(),
  saveBotMetaMock: vi.fn()
}))

vi.mock('@aethermesh/plugin-sdk', () => ({
  BOT_CHAT_SESSION_HYDRATION_TIMEOUT_MS: 15_000,
  host: hostMock
}))

vi.mock('./routing', () => ({
  backendTargetProfile: (route: { targetProfile?: string } | null, name: string) => route?.targetProfile ?? name,
  botConnectionRoute: () => null,
  botRosterMeta: () => ({}),
  botWorkspaceOwnerKey: (bot: { name?: string } | null) => String(bot?.name || ''),
  requestForBot: requestForBotMock
}))

vi.mock('./data', () => ({
  $botMeta: { get: () => ({}), set: vi.fn() },
  botMetaKey: (bot: { name?: string }) => bot?.name ?? '',
  botOwner: (owner: RosterRow | string) =>
    typeof owner === 'string'
      ? { bot: { name: owner }, key: owner, name: owner, route: null }
      : { bot: owner, key: owner?.name, name: owner?.name, route: null },
  persistBotMetaSnapshot: persistMock,
  saveBotMeta: saveBotMetaMock
}))

vi.mock('./shared', () => ({ getPluginCtx: () => null }))

let events: string[]

function respondWith(handler: (method: string) => unknown) {
  requestForBotMock.mockImplementation(async (_bot: unknown, method: string) => {
    events.push(method)

    return handler(method)
  })
}

async function loadModule() {
  vi.resetModules()

  return import('./canonical-chat')
}

beforeEach(() => {
  vi.clearAllMocks()
  events = []
  hostMock.openSession.mockImplementation(async (id: string) => {
    events.push(`open:${id}`)
  })
})

describe('a title-uniqueness rejection means someone else won the registry', () => {
  it('adopts the racing winner instead of forking into the stray row', async () => {
    let lists = 0

    respondWith(method => {
      if (method === 'session.list') {
        lists += 1

        // First consult: registry miss (this is why we mint at all). Second
        // consult (after the conflict): the racing winner exists.
        return lists === 1
          ? { sessions: [] }
          : { sessions: [{ id: 'winner-1', message_count: 3, resolved_id: 'winner-1', title: 'Bot Chat' }] }
      }

      if (method === 'session.create') {
        return { session_id: 'rt-stray', stored_session_id: 'stray-1' }
      }

      if (method === 'session.title') {
        throw new Error("Title 'Bot Chat' is already in use by session winner-1")
      }

      return {}
    })

    const { createCanonicalChat } = await loadModule()

    expect(await createCanonicalChat('ops')).toBe('winner-1')
    // The winner is opened; the stray is never prompted into or opened.
    expect(events).toContain('open:winner-1')
    expect(events).not.toContain('prompt.submit')
    expect(events).not.toContain('open:stray-1')
  })

  it('adopts the winner but does not navigate once the user has clicked away', async () => {
    // The conflict path is a full extra round-trip past the point every
    // sibling open is staleness-probed at, so it is the likeliest of them all
    // to land late. Identity must still resolve — only the workspace steal is
    // what the probe prevents.
    let lists = 0

    respondWith(method => {
      if (method === 'session.list') {
        lists += 1

        return lists === 1
          ? { sessions: [] }
          : { sessions: [{ id: 'winner-1', message_count: 3, resolved_id: 'winner-1', title: 'Bot Chat' }] }
      }

      if (method === 'session.create') {
        return { session_id: 'rt-stray', stored_session_id: 'stray-1' }
      }

      if (method === 'session.title') {
        throw new Error("Title 'Bot Chat' is already in use by session winner-1")
      }

      return {}
    })

    const { createCanonicalChat } = await loadModule()

    expect(await createCanonicalChat('ops', { openingStillCurrent: () => false })).toBe('winner-1')
    expect(events).not.toContain('open:winner-1')
  })

  it.each(['empty', 'error'] as const)('fails closed when conflict recovery is %s, then allows a later retry', async recovery => {
    let lists = 0

    respondWith(method => {
      if (method === 'session.list') {
        lists += 1

        if (lists === 2 && recovery === 'error') {
          throw new Error('gateway restarting')
        }

        return lists <= 2
          ? { sessions: [] }
          : { sessions: [{ id: 'winner-1', title: 'Bot Chat' }] }
      }

      if (method === 'session.create') {
        return { session_id: 'rt-stray', stored_session_id: 'stray-1' }
      }

      if (method === 'session.title') {
        // The real gateway returns 4022 for ValueError, including title
        // collisions. Use its message as well: 4022 alone is not collision-specific.
        throw { code: 4022, message: "Title 'Bot Chat' is already in use by session winner-1" }
      }

      return {}
    })

    const { createCanonicalChat } = await loadModule()
    const outcome = await createCanonicalChat('ops', { kickoff: true }).then(
      id => ({ id, error: null }),
      error => ({ id: null, error })
    )

    expect(events).not.toContain('open:stray-1')
    expect(events).not.toContain('prompt.submit')
    expect(outcome.id).toBeNull()
    expect(outcome.error?.message).toMatch(/Bot Chat registry/)
    expect(await createCanonicalChat('ops')).toBe('winner-1')
    expect(events.filter(event => event === 'session.create')).toHaveLength(1)
  })

  it('keeps the compat path for a NON-conflict title failure (old gateways)', async () => {
    respondWith(method => {
      if (method === 'session.list') {
        return { sessions: [] }
      }

      if (method === 'session.create') {
        return { session_id: 'rt-1', stored_session_id: 'stored-1' }
      }

      if (method === 'session.title') {
        throw new Error('unknown method')
      }

      return {}
    })

    const { createCanonicalChat } = await loadModule()

    expect(await createCanonicalChat('ops')).toBe('stored-1')
    // Old gateway: eager title unsupported → the kickoff persists the lazy row.
    expect(events).toContain('prompt.submit')
    // Only the initial registry consult — a plain failure must not re-list.
    expect(events.filter(event => event === 'session.list')).toHaveLength(1)
  })
})
