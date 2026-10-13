import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { organizationOwnerKey } from '@/app/eidolon/organization-owner'
import { $intakeDrafts, acknowledgeIntake, setIntakeDraft } from '@/app/eidolon/runtime-intake-drafts'
import { $ownerChatDrafts, $ownerChatRenewals, acknowledgeOwnerChatIntent, acknowledgeOwnerChatRenewal } from '@/app/eidolon/runtime-owner-chat-drafts'
import { $gatewaySwitching, beginGatewaySwitch, endGatewaySwitch, registerGatewaySwitchLifecycle } from '@/store/gateway-switch'
import { $activeSessionId, $sessions, setActiveSessionId, setSessions } from '@/store/session'

import type { ClientSessionState } from '../../types'

import { useSessionActions } from './use-session-actions'

const owner = (connection: string) => organizationOwnerKey(connection, 'default', { mode: 'local' })
const intakeKey = (connection: string) => JSON.stringify(['intake', owner(connection)])
// Copied ledgers can retain the same member UUID. The route still owns its draft.
const chatKey = (connection: string) => JSON.stringify([owner(connection), 'same-member-identity'])
const fields = (connection: string) => ({ goal: `${connection} objective`, criteria: 'Reviewed evidence', metadata: {} })
const intent = (connection: string) => ({ threadId: 'copied-thread', identityId: 'same-member-identity', text: `${connection} message`, replyToMessageId: null, idempotencyKey: 'same-key-in-separate-ledgers' })

function seed() {
  for (const connection of ['a', 'b']) {
    setIntakeDraft(intakeKey(connection), { ...fields(connection), submitted: fields(connection), idempotencyKey: 'same-key-in-separate-ledgers', busy: false, error: 'Admission uncertain' })
    $ownerChatDrafts.set({ ...$ownerChatDrafts.get(), [chatKey(connection)]: { text: intent(connection).text, intent: intent(connection) } })
    $ownerChatRenewals.set({ ...$ownerChatRenewals.get(), [chatKey(connection)]: { threadId: 'copied-thread', identityId: 'same-member-identity', idempotencyKey: 'same-renewal-key', expectedBudgetVersion: 2, expectedPolicyGeneration: 3, additionalCalls: 1 } })
  }
}

function fixture() {
  const ref = <T,>(current: T) => ({ current })
  const navigate = vi.fn()
  const requestGateway = vi.fn(async () => { throw new Error('Reset must not call a backend') })
  const resetViewSync = vi.fn()

  const { result } = renderHook(() => useSessionActions({
    activeSessionId: 'old-runtime-session', activeSessionIdRef: ref<string | null>('old-runtime-session'),
    selectedStoredSessionId: null, selectedStoredSessionIdRef: ref<string | null>(null),
    busyRef: ref(false), creatingSessionRef: ref(false),
    ensureSessionState: () => ({}) as ClientSessionState, getRouteToken: () => 'organization-route',
    getRoutedStoredSessionId: () => null, navigate, requestGateway, resetViewSync,
    runtimeIdByStoredSessionIdRef: ref(new Map<string, string>()),
    sessionStateByRuntimeIdRef: ref(new Map<string, ClientSessionState>()),
    syncSessionStateToView: vi.fn(), updateSessionState: () => ({}) as ClientSessionState
  }))

  return { result, navigate, requestGateway, resetViewSync }
}

afterEach(() => {
  cleanup(); $intakeDrafts.set({}); $ownerChatDrafts.set({}); $ownerChatRenewals.set({})
  endGatewaySwitch(); setActiveSessionId(null); setSessions([])
})

it.each([false, true])('retains source-owned intent through the real reset boundary (lifecycle fails: %s)', fail => {
  seed()
  const h = fixture()
  const before = structuredClone({ intake: $intakeDrafts.get(), chat: $ownerChatDrafts.get(), renewal: $ownerChatRenewals.get() })

  const off = registerGatewaySwitchLifecycle({
    beforeConnectionSwitch() {
      h.result.current.startFreshSessionDraft({ preserveRoute: true, workspaceTarget: null })

      if (fail) {throw new Error('Following lifecycle cleanup failed')}
    },
    refreshSessions: vi.fn(async () => undefined)
  })

  try {
    act(() => {
      setActiveSessionId('old-runtime-session'); setSessions([{ id: 'old', title: 'Old session' } as never])

      if (fail) {expect(() => beginGatewaySwitch()).toThrow('Following lifecycle cleanup failed')} else {
        const token = beginGatewaySwitch()

        expect($gatewaySwitching.get()).toBe(true)
        expect($sessions.get()).toEqual([])
        endGatewaySwitch(token)
      }
    })
    expect(h.resetViewSync).toHaveBeenCalledOnce()
    expect($activeSessionId.get()).toBeNull()
    expect($gatewaySwitching.get()).toBe(false)
    expect({ intake: $intakeDrafts.get(), chat: $ownerChatDrafts.get(), renewal: $ownerChatRenewals.get() }).toEqual(before)
    expect(h.navigate).not.toHaveBeenCalled()
    expect(h.requestGateway).not.toHaveBeenCalled()
  } finally {off()}
})

it('keeps copied identities and late acknowledgements isolated after a reset', () => {
  seed()
  const h = fixture()
  const b = structuredClone({ intake: $intakeDrafts.get()[intakeKey('b')], chat: $ownerChatDrafts.get()[chatKey('b')], renewal: $ownerChatRenewals.get()[chatKey('b')] })

  const off = registerGatewaySwitchLifecycle({
    beforeConnectionSwitch: () => h.result.current.startFreshSessionDraft({ preserveRoute: true, workspaceTarget: null }),
    refreshSessions: vi.fn(async () => undefined)
  })

  try {
    act(() => {
      const token = beginGatewaySwitch()

      endGatewaySwitch(token)
      acknowledgeIntake(intakeKey('a'), 'same-key-in-separate-ledgers', fields('a'))
      acknowledgeOwnerChatIntent(chatKey('a'), intent('a'))
      acknowledgeOwnerChatRenewal(chatKey('a'), 'same-renewal-key')
    })
    expect($intakeDrafts.get()[intakeKey('a')]).toBeUndefined()
    expect($ownerChatDrafts.get()[chatKey('a')]).toEqual({ text: '' })
    expect($ownerChatRenewals.get()[chatKey('a')]).toBeUndefined()
    expect({ intake: $intakeDrafts.get()[intakeKey('b')], chat: $ownerChatDrafts.get()[chatKey('b')], renewal: $ownerChatRenewals.get()[chatKey('b')] }).toEqual(b)
    expect(h.requestGateway).not.toHaveBeenCalled()
  } finally {off()}
})
