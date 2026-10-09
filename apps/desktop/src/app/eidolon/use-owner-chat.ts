import { useEffect, useRef, useState } from 'react'

import { useI18n } from '@/i18n/context'

import { organizationErrorMessage } from './runtime-adapter'
import { OwnerChatRenewalRejected } from './runtime-owner-chat-contract'
import {
  $ownerChatDrafts,
  $ownerChatRenewals,
  acknowledgeOwnerChatIntent,
  acknowledgeOwnerChatRenewal,
  setOwnerChatDraft,
  setOwnerChatRenewal
} from './runtime-owner-chat-drafts'
import type { OwnerChatRenew, OwnerChatSend, OwnerChatThread } from './runtime-owner-chat-types'
import type { OrganizationAgent, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

/** Every continuation checks both the transport scope and its mounted request
 * generation. Closing a pane never cancels backend work or navigates on completion. */
export function useOwnerChat(
  adapter: RuntimeOrganizationAdapter,
  snapshot: OrganizationSnapshot,
  agent: OrganizationAgent,
  draftKey: string
) {
  const { t } = useI18n()
  const copy = t.organizationOwnerChat
  const scope = snapshot.connection?.scope
  const ready = snapshot.connection?.state === 'ready'
  const [view, setView] = useState<{ thread: OwnerChatThread; scope: string | undefined } | null>(null)
  const [busy, setBusy] = useState<'read' | 'send' | 'cancel' | 'renew' | 'history' | null>(null)
  const [historyPage, setHistoryPage] = useState<OwnerChatThread | null>(null)
  const [error, setError] = useState('')
  const [changed, setChanged] = useState(false)
  const [cancelUncertain, setCancelUncertain] = useState(false)
  const sequence = useRef(0)
  const sending = useRef(false)
  const thread = view?.thread
  const verified = Boolean(ready && view && view.scope === scope && !error)

  const current = (token: number) =>
    token === sequence.current &&
    adapter.getSnapshot().connection?.scope === scope &&
    adapter.getSnapshot().connection?.state === 'ready'

  const accept = (value: OwnerChatThread) => {
    const intent = $ownerChatDrafts.get()[draftKey]?.intent

    const renewal = $ownerChatRenewals.get()[draftKey]
    const receipt = value.renewalReceipt

    if (renewal && receipt && value.id === renewal.threadId && value.identityId === renewal.identityId &&
      receipt.idempotencyKey === renewal.idempotencyKey && receipt.additionalCalls === renewal.additionalCalls &&
      receipt.budgetVersion === renewal.expectedBudgetVersion + 1 && receipt.policyGeneration === renewal.expectedPolicyGeneration) {
      acknowledgeOwnerChatRenewal(draftKey, receipt.idempotencyKey)
    }

    setChanged(false)

    if (
      intent &&
      value.id === intent.threadId &&
      value.turns.some(
        turn =>
          turn.idempotencyKey === intent.idempotencyKey &&
          value.messages.some(
            message =>
              message.id === turn.ownerMessageId &&
              message.role === 'owner' &&
              message.text === intent.text &&
              message.replyToMessageId === intent.replyToMessageId
          )
      )
    ) {
      acknowledgeOwnerChatIntent(draftKey, intent)
    } else if (
      intent &&
      value.id === intent.threadId &&
      !value.turns.some(turn => turn.idempotencyKey === intent.idempotencyKey) &&
      (value.latestMessageId !== undefined ? value.latestMessageId : (value.messages.at(-1)?.id ?? null)) !==
        intent.replyToMessageId
    ) {
      // A changed exact reply target makes future admission of this old payload
      // impossible. Preserve its text for an explicit new send, never call it sent.
      setOwnerChatDraft(draftKey, { text: intent.text })
      setChanged(true)
    }

    setView({ thread: value, scope })
    setError('')
    setCancelUncertain(false)
  }

  const refresh = async () => {
    if (!ready || sending.current || !agent.identityId || !adapter.openOwnerChat || !adapter.readOwnerChat) {
      return
    }

    const token = ++sequence.current
    setBusy('read')

    try {
      const value = thread
        ? await adapter.readOwnerChat({ threadId: thread.id, identityId: agent.identityId })
        : await adapter.openOwnerChat({ agentId: agent.id, identityId: agent.identityId })

      if (current(token)) {
        accept(value)
      }
    } catch (reason) {
      if (current(token)) {
        setError(organizationErrorMessage(reason))
      }
    } finally {
      if (current(token)) {
        setBusy(null)
      }
    }
  }

  // eslint-disable-next-line no-restricted-syntax -- invalidate local request locks when transport readiness changes
  useEffect(() => {
    sending.current = false
    setHistoryPage(null)
    setBusy(null)
    void refresh()

    return () => {
      // eslint-disable-next-line react-hooks/exhaustive-deps -- request generation, not a DOM ref
      sequence.current++
      sending.current = false
    }
    // A new transport always rechecks history, but a quiet snapshot does not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapter, scope, ready, agent.identityId, agent.id])

  useEffect(() => {
    if (!ready || busy || error || !thread) {
      return
    }

    const timer = setTimeout(() => void refresh(), thread.activeTurnId ? 2500 : 15000)

    return () => clearTimeout(timer)
    // The timer reads exactly this verified thread and is cancelled on scope changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapter, scope, ready, busy, error, thread])

  const send = async (retry?: OwnerChatSend) => {
    if (
      !ready ||
      !thread ||
      sending.current ||
      !adapter.sendOwnerChat ||
      !agent.identityId ||
      agent.lifecycle !== 'active' ||
      thread.recipient.lifecycle !== 'active'
    ) {
      return
    }

    const draft = $ownerChatDrafts.get()[draftKey]

    if (retry && (retry.threadId !== thread.id || retry.identityId !== agent.identityId)) {
      return
    }

    if (
      !retry &&
      (!verified ||
        !thread.canSend ||
        draft?.intent ||
        !draft?.text.trim() ||
        [...draft.text.trim()].length > thread.limits.maxMessageChars)
    ) {
      return
    }

    const intent: OwnerChatSend = retry ?? {
      threadId: thread.id,
      identityId: agent.identityId,
      text: draft!.text.trim(),
      replyToMessageId:
        thread.latestMessageId !== undefined ? thread.latestMessageId : (thread.messages.at(-1)?.id ?? null),
      idempotencyKey: crypto.randomUUID()
    }

    // Store before touching the transport: repeated clicks and reopened panes reuse it.
    setOwnerChatDraft(draftKey, { text: intent.text, intent })
    sending.current = true
    const token = ++sequence.current
    setBusy('send')
    setError('')

    try {
      const value = await adapter.sendOwnerChat(intent)

      if (current(token)) {
        accept(value)
      }
    } catch (reason) {
      if (current(token)) {
        setError(organizationErrorMessage(reason))
      }
    } finally {
      if (sequence.current === token) {
        sending.current = false
      }

      if (current(token)) {
        setBusy(null)
      }
    }
  }

  const cancel = async () => {
    if (!ready || !thread?.activeTurnId || sending.current || !adapter.cancelOwnerChat) {
      return
    }

    sending.current = true
    const token = ++sequence.current
    setBusy('cancel')
    setError('')

    try {
      const value = await adapter.cancelOwnerChat({
        threadId: thread.id,
        identityId: thread.identityId,
        turnId: thread.activeTurnId
      })

      if (current(token)) {
        accept(value)
      }
    } catch (reason) {
      if (current(token)) {
        setError(organizationErrorMessage(reason))
        setCancelUncertain(true)
      }
    } finally {
      if (sequence.current === token) {
        sending.current = false
      }

      if (current(token)) {
        setBusy(null)
      }
    }
  }

  const loadEarlier = async () => {
    const page = historyPage ?? thread

    if (
      !ready ||
      !page?.history?.hasMore ||
      !page.history.oldestMessageId ||
      sending.current ||
      !adapter.readOwnerChat
    ) {
      return
    }

    sending.current = true
    const token = ++sequence.current
    setBusy('history')

    try {
      const value = await adapter.readOwnerChat({
        threadId: page.id,
        identityId: page.identityId,
        beforeMessageId: page.history.oldestMessageId,
        limit: 50
      })

      if (current(token)) {
        // Keep the latest snapshot and reply target separate from historical pages.
        setHistoryPage(value)
        setError('')
      }
    } catch (reason) {
      if (current(token)) {
        setError(organizationErrorMessage(reason))
      }
    } finally {
      if (sequence.current === token) {
        sending.current = false
      }

      if (current(token)) {
        setBusy(null)
      }
    }
  }

  const renew = async (intent: OwnerChatRenew) => {
    const pending = $ownerChatRenewals.get()[draftKey]

    if (
      !current(sequence.current) ||
      !thread ||
      sending.current ||
      !adapter.renewOwnerChat ||
      agent.lifecycle !== 'active' ||
      thread.recipient.lifecycle !== 'active' ||
      intent.threadId !== thread.id ||
      intent.identityId !== agent.identityId ||
      (pending && JSON.stringify(pending) !== JSON.stringify(intent)) ||
      (!pending &&
        (!verified ||
          !thread.renewal?.canRenew ||
          intent.expectedBudgetVersion !== thread.budget.version ||
          intent.expectedPolicyGeneration !== thread.policyGeneration ||
          intent.additionalCalls > thread.renewal.maxAdditionalCalls))
    ) {
      throw new Error(copy.changed)
    }

    setOwnerChatRenewal(draftKey, intent)
    sending.current = true
    const token = ++sequence.current
    setBusy('renew')
    setError('')

    try {
      const value = await adapter.renewOwnerChat(intent)

      if (current(token)) {
        if (value.renewalReceipt?.idempotencyKey !== intent.idempotencyKey) {
          throw new Error(copy.invalid)
        }

        accept(value)
      }
    } catch (reason) {
      if (current(token)) {
        if (reason instanceof OwnerChatRenewalRejected) {
          acknowledgeOwnerChatRenewal(draftKey, intent.idempotencyKey)
        }

        setError(organizationErrorMessage(reason))
      }

      throw reason
    } finally {
      if (sequence.current === token) {
        sending.current = false
      }

      if (current(token)) {
        setBusy(null)
      }
    }
  }

  return {
    thread,
    historyPage,
    showLatest: () => {
      if (busy === 'history') {
        sequence.current++
        sending.current = false
        setBusy(null)
      }

      setHistoryPage(null)
    },
    loadEarlier,
    renew,
    verified,
    busy,
    error,
    changed,
    cancelUncertain,
    refresh,
    send,
    cancel,
    copy
  }
}
