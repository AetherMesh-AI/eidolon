import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { useStickToBottom } from 'use-stick-to-bottom'

import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { Textarea } from '@/components/ui/textarea'
import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n/context'
import type { OrganizationOwnerChatCopy } from '@/i18n/organization-owner-chat'
import { ArrowUp, MessageCircle } from '@/lib/icons'

import { AgentAvatar } from './avatar'
import { LocalizedTime } from './localized-time'
import { OwnerChatRenewalRejected } from './runtime-owner-chat-contract'
import { $ownerChatDrafts, $ownerChatRenewals, setOwnerChatDraft } from './runtime-owner-chat-drafts'
import type { OwnerChatRenew, OwnerChatThread, OwnerChatTurn } from './runtime-owner-chat-types'
import type { OrganizationAgent, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'
import { useOwnerChat } from './use-owner-chat'

const turnLabel = (turn: OwnerChatTurn, copy: OrganizationOwnerChatCopy) =>
  turn.status === 'uncertain' ? copy.turnUncertain : copy[turn.status]

function OwnerChatTranscript({ thread, sendRevision }: { thread: OwnerChatThread; sendRevision: number }) {
  const { t } = useI18n()
  const copy = t.organizationOwnerChat

  const { scrollRef, contentRef, scrollToBottom, stopScroll } = useStickToBottom({
    initial: 'instant',
    resize: 'instant'
  })

  useEffect(() => {
    void scrollToBottom('instant')
  }, [sendRevision, scrollToBottom])

  const elements = useRef(new Map<string, HTMLLIElement>())
  const turns = new Map(thread.turns.map(turn => [turn.ownerMessageId, turn]))

  // Plain text intentionally bypasses ordinary session directives, tool widgets,
  // and media fetching. A discussion must never turn message content into actions.
  return !thread.messages.length ? (
    <EmptyState title={copy.empty} />
  ) : (
    <div className="eid-owner-chat-scroll" ref={scrollRef}>
      <ol aria-label={copy.messages} className="eid-owner-chat-transcript" ref={contentRef} role="log">
        {thread.messages.map(message => {
          const turn = turns.get(message.id)
          const target = thread.messages.find(item => item.id === message.replyToMessageId)

          return (
            <li
              className={`eid-owner-chat-message eid-owner-chat-${message.role}`}
              key={message.id}
              ref={element => {
                if (element) {
                  elements.current.set(message.id, element)
                } else {
                  elements.current.delete(message.id)
                }
              }}
              tabIndex={-1}
            >
              <div className="eid-owner-chat-message-meta">
                <span>{message.role === 'owner' ? copy.you : thread.recipient.name}</span>
                <LocalizedTime value={message.createdAt} />
              </div>
              {target && (
                <Button
                  aria-label={`${copy.replyTo}: ${target.id}`}
                  onClick={() => {
                    stopScroll()
                    const element = elements.current.get(target.id)
                    element?.scrollIntoView({ block: 'nearest' })
                    element?.focus()
                  }}
                  size="inline"
                  variant="text"
                >
                  {copy.replyTo}: {target.text.slice(0, 80)}
                </Button>
              )}
              {!target && message.replyToMessageId && <p className="eid-note">{copy.retainedAnchor}</p>}
              <p className="eid-owner-chat-bubble">{message.text}</p>
              {turn && (
                <div className="eid-owner-chat-turn" role="status">
                  <span>{turnLabel(turn, copy)}</span>
                  {turn.reason && <p className="eid-result-text">{turn.reason}</p>}
                  {(turn.context?.omittedMessageCount ?? 0) > 0 && (
                    <p className="eid-note">
                      {copy.contextCutoff}{' '}
                      {copy.contextAudit
                        .replace('{count}', String(turn.context?.omittedMessageCount))
                        .replace('{id}', turn.context?.oldestIncludedMessageId ?? '—')}
                    </p>
                  )}
                </div>
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}

function OwnerChatPanel({
  adapter,
  snapshot,
  agent
}: {
  adapter: RuntimeOrganizationAdapter
  snapshot: OrganizationSnapshot
  agent: OrganizationAgent
}) {
  const draftKey = JSON.stringify([snapshot.connection?.ownerScope ?? snapshot.connection?.scope, agent.identityId])
  const [sendRevision, setSendRevision] = useState(0)
  const { t } = useI18n()
  const drafts = useStore($ownerChatDrafts)
  const draft = drafts[draftKey]
  const renewalIntents = useStore($ownerChatRenewals)
  const renewalIntent = renewalIntents[draftKey]
  const [review, setReview] = useState<{ intent: OwnerChatRenew; tokens: number } | null>(null)

  const {
    thread,
    verified,
    busy,
    error,
    changed,
    cancelUncertain,
    refresh,
    send,
    cancel,
    copy,
    historyPage,
    loadEarlier,
    showLatest,
    renew
  } = useOwnerChat(adapter, snapshot, agent, draftKey)

  const ready = snapshot.connection?.state === 'ready'

  const inactive =
    (agent.lifecycle !== undefined && agent.lifecycle !== 'active') ||
    Boolean(thread && thread.recipient.lifecycle !== 'active')

  const exhausted = Boolean(thread && (!thread.budget.remainingCalls || !thread.budget.remainingTokens))
  const disabled = !verified || !thread?.canSend || inactive || !!busy || !!draft?.intent
  const composerDisabled = !thread || !ready || inactive || exhausted

  const submit = () => {
    setSendRevision(value => value + 1)
    void send()
  }

  const pending = snapshot.requests?.filter(item => item.status === 'pending_intervention').length ?? 0
  const text = draft?.text ?? ''
  const length = [...text.trim()].length
  const limit = thread?.limits.maxMessageChars ?? 6000

  const latest =
    thread?.messages.find(message => message.id === thread.latestMessageId) ??
    (thread?.latestMessageId === undefined ? thread?.messages.at(-1) : undefined)

  const page = historyPage ?? thread

  const reviewRenewal = () => {
    if (!thread?.renewal || thread.budget.version === undefined || thread.policyGeneration === undefined) {
      return
    }

    const intent = renewalIntent ?? {
      threadId: thread.id,
      identityId: thread.identityId,
      idempotencyKey: crypto.randomUUID(),
      expectedBudgetVersion: thread.budget.version,
      expectedPolicyGeneration: thread.policyGeneration,
      additionalCalls: thread.renewal.maxAdditionalCalls
    }

    setReview({ intent, tokens: intent.additionalCalls * thread.renewal.tokensPerCall })
  }

  return (
    <div className="eid-owner-chat-panel">
      <p className="eid-note">{copy.note}</p>
      <nav aria-label={copy.needsYou} className="eid-owner-chat-actions">
        <Button asChild size="sm" variant="secondary">
          <Link to="/home">{copy.createObjective}</Link>
        </Button>
        <Button asChild size="sm" variant="secondary">
          <Link to="/requests">
            {copy.needsYou} ({pending})
          </Link>
        </Button>
      </nav>
      {pending > 0 && <p className="eid-note">{copy.pendingNote}</p>}
      {!ready && <p role="status">{copy.offline}</p>}
      {thread && (
        <>
          <header className="eid-owner-chat-identity">
            <AgentAvatar name={thread.recipient.name} />
            <div>
              <strong>{thread.recipient.name}</strong>
              <p>
                {thread.recipient.role}
                {thread.recipient.team ? ` · ${thread.recipient.team}` : ''}
              </p>
            </div>
          </header>
          <p className="eid-note">
            {t.organizationRuntime.provider}: {thread.recipient.provider || copy.profileRoute} ·{' '}
            {t.organizationRuntime.model}: {thread.recipient.model || copy.profileRoute}
          </p>
          <p className="eid-note">{copy.providerUsage}</p>
          <p className="eid-note">{copy.contextWindow}</p>
          {historyPage && <p className="eid-note">{copy.historyPage}</p>}
          <div className="eid-owner-chat-actions">
            {page?.history?.hasMore && (
              <Button disabled={!ready || !!busy} onClick={() => void loadEarlier()} size="sm" variant="secondary">
                {copy.earlier}
              </Button>
            )}
            {historyPage && (
              <Button onClick={showLatest} size="sm" variant="secondary">
                {copy.latest}
              </Button>
            )}
          </div>
          <OwnerChatTranscript sendRevision={sendRevision} thread={page!} />
          <p className="eid-note">
            {copy.calls}: {thread.budget.remainingCalls} / {thread.budget.maxCalls} · {copy.tokens}:{' '}
            {thread.budget.tokensReserved} / {thread.budget.maxTokens}
          </p>
          <p className="eid-note">{copy.budgetNote}</p>
          {renewalIntent && busy !== 'renew' && <p role="status">{copy.renewUncertain}</p>}
          {thread.renewal && adapter.renewOwnerChat && (
            <Button
              disabled={!ready || !!busy || inactive || (!renewalIntent && (!verified || !thread.renewal.canRenew))}
              onClick={reviewRenewal}
              size="sm"
              variant="secondary"
            >
              {renewalIntent ? copy.renewRetry : copy.renew}
            </Button>
          )}
          <ConfirmDialog
            confirmLabel={copy.renewConfirm}
            description={
              review
                ? copy.renewReview
                    .replace('{calls}', String(review.intent.additionalCalls))
                    .replace('{tokens}', String(review.tokens))
                : ''
            }
            onClose={() => setReview(null)}
            onConfirm={async () => {
              if (!review) {
                return
              }

              try {
                await renew(review.intent)
              } catch (reason) {
                if (reason instanceof OwnerChatRenewalRejected) {
                  setReview(null)
                }

                throw reason
              }
            }}
            open={!!review}
            title={copy.renew}
          />
        </>
      )}
      {busy === 'read' && !thread && <Loader label={copy.loading} />}
      {error && (
        <div role="alert">
          <ErrorState description={error} title={copy.error} />
        </div>
      )}
      {draft?.intent && busy !== 'send' && <p role="status">{copy.uncertain}</p>}
      {changed && <p role="status">{copy.changed}</p>}
      {cancelUncertain && <p role="status">{copy.cancelUncertain}</p>}
      {(error || !thread || draft?.intent || cancelUncertain) && (
        <div className="eid-owner-chat-actions">
          <Button disabled={!ready || !!busy} onClick={() => void refresh()} size="sm" variant="secondary">
            {copy.refresh}
          </Button>
          {draft?.intent && (
            <Button
              disabled={!ready || !!busy || inactive || !thread}
              onClick={() => void send(draft.intent)}
              size="sm"
              variant="secondary"
            >
              {copy.retry}
            </Button>
          )}
        </div>
      )}
      {inactive ? (
        <p role="status">{copy.readOnly}</p>
      ) : exhausted ? (
        <p role="status">{copy.exhausted}</p>
      ) : thread && !thread.canSend && !thread.activeTurnId && thread.unavailableReason ? (
        <p role="status">{thread.unavailableReason}</p>
      ) : null}
      <form
        aria-label={copy.message}
        className="eid-owner-chat-composer"
        onSubmit={event => {
          event.preventDefault()

          if (!disabled) {
            submit()
          }
        }}
      >
        <label htmlFor={`owner-message-${agent.identityId}`}>{copy.message}</label>
        {latest && (
          <p className="eid-note">
            {copy.replyingTo}: {latest.text.slice(0, 100)}
          </p>
        )}
        <Textarea
          aria-describedby={`owner-limit-${agent.identityId}`}
          disabled={composerDisabled}
          id={`owner-message-${agent.identityId}`}
          onChange={event => setOwnerChatDraft(draftKey, { text: event.target.value })}
          onKeyDown={event => {
            if (
              event.key === 'Enter' &&
              !event.shiftKey &&
              !event.ctrlKey &&
              !event.metaKey &&
              !event.altKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault()
              event.stopPropagation()

              if (!disabled) {
                submit()
              }
            }
          }}
          placeholder={copy.placeholder}
          readOnly={busy === 'send' || !!draft?.intent}
          rows={3}
          value={text}
        />
        <div className="eid-owner-chat-actions">
          <span className="eid-note" id={`owner-limit-${agent.identityId}`}>
            {copy.limits}: {length} / {limit}
          </span>
          {thread?.activeTurnId ? (
            <Button
              disabled={!ready || busy === 'send' || busy === 'cancel'}
              onClick={() => void cancel()}
              type="button"
              variant="secondary"
            >
              {busy === 'cancel' ? copy.cancelling : copy.cancel}
            </Button>
          ) : (
            <Button disabled={disabled || length === 0 || length > limit} type="submit">
              <ArrowUp />
              {busy === 'send' ? copy.sending : copy.send}
            </Button>
          )}
        </div>
      </form>
    </div>
  )
}

/** Stable member identity, not role, enables the entry point, including Workers.
 * Lifecycle restrictions preserve readable history instead of hiding the action. */
export function OrganizationOwnerChat({
  adapter,
  snapshot,
  agent,
  onOpenChange
}: {
  adapter?: RuntimeOrganizationAdapter
  snapshot: OrganizationSnapshot
  agent: OrganizationAgent
  onOpenChange?(open: boolean): void
}) {
  const { t } = useI18n()
  const copy = t.organizationOwnerChat
  const [open, setOpen] = useState(false)

  const supported = Boolean(
    agent.identityId &&
    snapshot.runtime?.capabilities.includes('owner_chat') &&
    adapter?.openOwnerChat &&
    adapter.readOwnerChat &&
    adapter.sendOwnerChat &&
    adapter.cancelOwnerChat
  )

  if (agent.role === 'Owner' || agent.id.startsWith('control:')) {
    return null
  }

  return (
    <section aria-label={copy.heading} className="eid-owner-chat">
      <div className="eid-owner-chat-actions">
        <Button
          aria-expanded={open}
          disabled={!supported}
          onClick={() => {
            setOpen(!open)
            onOpenChange?.(!open)
          }}
          variant="secondary"
        >
          <MessageCircle />
          {open ? copy.hide : copy.chat}
        </Button>
        <Tip label={copy.callUnavailable}>
          <span aria-label={copy.callUnavailable} tabIndex={0}>
            <Button aria-describedby={`owner-call-${agent.identityId ?? agent.id}`} disabled variant="secondary">
              {copy.call}
            </Button>
          </span>
        </Tip>
        <span className="sr-only" id={`owner-call-${agent.identityId ?? agent.id}`}>
          {copy.callUnavailable}
        </span>
      </div>
      {!supported && <p className="eid-note">{copy.unavailable}</p>}
      {open && supported && adapter && (
        <OwnerChatPanel
          adapter={adapter}
          agent={agent}
          key={`${snapshot.connection?.scope}:${agent.identityId}`}
          snapshot={snapshot}
        />
      )}
    </section>
  )
}
