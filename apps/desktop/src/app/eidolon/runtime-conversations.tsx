import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { useI18n } from '@/i18n/context'
import type { OrganizationConversationsCopy } from '@/i18n/organization-conversations'

import type { OrganizationConversation, OrganizationSnapshot } from './types'

interface OrganizationConversationsProps {
  snapshot: OrganizationSnapshot
  agentId?: string
}

function participantLabel(conversation: OrganizationConversation, id: string, copy: OrganizationConversationsCopy) {
  const participant = conversation.participants.find(item => item.id === id)

  return participant ? `${participant.name} (${participant.id}) · ${participant.team || copy.unknownTeam}` : id
}

function ConversationThread({
  conversation,
  snapshot
}: {
  conversation: OrganizationConversation
  snapshot: OrganizationSnapshot
}) {
  const { t, locale } = useI18n()
  const copy = t.organizationConversations
  const messageElements = useRef(new Map<string, HTMLLIElement>())
  const objective = snapshot.objectives.find(item => item.id === conversation.objectiveId)

  const task = snapshot.tasks.find(
    item =>
      item.id === conversation.taskId &&
      item.objectiveId === conversation.objectiveId &&
      (conversation.projectId === null || item.projectId === conversation.projectId)
  )

  const objectivePath = `/objectives/${encodeURIComponent(conversation.objectiveId)}`
  const timestamp = (value: string) => <time dateTime={value}>{new Date(value).toLocaleString(locale)}</time>

  return (
    <article aria-label={conversation.subject} className="space-y-4">
      <h3 className="eid-result-text">{conversation.subject}</h3>
      <dl className="space-y-2">
        <dt>{copy.conversationId}</dt>
        <dd className="eid-result-text">{conversation.id}</dd>
        <dt>{copy.status}</dt>
        <dd>{copy[conversation.status]}</dd>
        {conversation.waitingAgentId && (
          <>
            <dt>{copy.waitingFor}</dt>
            <dd className="eid-result-text">{participantLabel(conversation, conversation.waitingAgentId, copy)}</dd>
          </>
        )}
        <dt>{copy.participants}</dt>
        <dd>
          <ul>
            {conversation.participants.map(item => (
              <li className="eid-result-text" key={item.id}>
                {participantLabel(conversation, item.id, copy)}
              </li>
            ))}
          </ul>
        </dd>
        <dt>{copy.objective}</dt>
        <dd className="eid-result-text">
          {objective ? (
            <Link to={objectivePath}>
              {objective.title} · {objective.id}
            </Link>
          ) : (
            conversation.objectiveId
          )}
        </dd>
        {conversation.taskId && (
          <>
            <dt>{copy.task}</dt>
            <dd className="eid-result-text">
              {task && objective ? (
                <Link to={objectivePath}>
                  {task.title} · {task.id}
                </Link>
              ) : (
                conversation.taskId
              )}
            </dd>
          </>
        )}
        {conversation.projectId && (
          <>
            <dt>{copy.project}</dt>
            <dd className="eid-result-text">{conversation.projectId}</dd>
          </>
        )}
      </dl>
      <ol aria-label={copy.messages} className="space-y-6">
        {conversation.messages.map(message => (
          <li
            className="space-y-2"
            key={message.id}
            ref={element => {
              if (element) {
                messageElements.current.set(message.id, element)
              } else {
                messageElements.current.delete(message.id)
              }
            }}
            tabIndex={-1}
          >
            <dl>
              <dt>{copy.from}</dt>
              <dd className="eid-result-text">{participantLabel(conversation, message.senderId, copy)}</dd>
              <dt>{copy.to}</dt>
              <dd className="eid-result-text">{participantLabel(conversation, message.recipientId, copy)}</dd>
              <dt>{copy.sent}</dt>
              <dd>{timestamp(message.createdAt)}</dd>
              <dt>{copy.messageId}</dt>
              <dd className="eid-result-text">{message.id}</dd>
              {message.replyToId && (
                <>
                  <dt>{copy.replyTo}</dt>
                  <dd className="eid-result-text">
                    <Button
                      onClick={() => {
                        const target = messageElements.current.get(message.replyToId!)
                        target?.scrollIntoView({ block: 'nearest' })
                        target?.focus()
                      }}
                      size="inline"
                      variant="text"
                    >
                      {message.replyToId}
                    </Button>
                  </dd>
                </>
              )}
            </dl>
            <p className="eid-result-text">{message.body}</p>
            <p className="eid-note">
              {message.readAt ? (
                <>
                  {copy.read} · {timestamp(message.readAt)}
                </>
              ) : (
                copy.unread
              )}
            </p>
          </li>
        ))}
      </ol>
    </article>
  )
}

/** Inspection consumes snapshot data only. There is deliberately no delivery,
 * acknowledgement or send operation in this owner-facing surface. */
export function OrganizationConversations({ snapshot, agentId }: OrganizationConversationsProps) {
  const { t } = useI18n()
  const copy = t.organizationConversations
  const scope = snapshot.connection?.scope

  const [selection, setSelection] = useState<{
    id: string
    scope: string | undefined
    agentId: string | undefined
  } | null>(null)

  const [visible, setVisible] = useState(20)

  const conversations = (snapshot.conversations ?? []).filter(
    item => !agentId || item.participants.some(participant => participant.id === agentId)
  )

  const selected =
    selection?.scope === scope && selection?.agentId === agentId
      ? conversations.find(item => item.id === selection?.id)
      : undefined

  useEffect(() => {
    if (!selected) {
      setSelection(null)
    }
  }, [selected])
  useEffect(() => {
    setVisible(20)
  }, [scope, agentId])

  return (
    <section aria-label={copy.heading} className="space-y-4">
      <h2>{copy.heading}</h2>
      <p className="eid-note">{copy.note}</p>
      {selected ? (
        <>
          <Button onClick={() => setSelection(null)} size="inline" variant="text">
            {copy.back}
          </Button>
          <ConversationThread conversation={selected} key={selected.id} snapshot={snapshot} />
        </>
      ) : snapshot.conversations === undefined ? (
        <p>{copy.unavailable}</p>
      ) : !conversations.length ? (
        <EmptyState title={agentId ? copy.agentEmpty : copy.empty} />
      ) : (
        <>
          <ol className="space-y-4">
            {conversations.slice(0, visible).map(item => (
              <li className="space-y-2" key={item.id}>
                <h3 className="eid-result-text">{item.subject}</h3>
                <p className="eid-result-text">
                  {item.participants.map(participant => participantLabel(item, participant.id, copy)).join('; ')}
                </p>
                <p>{copy[item.status]}</p>
                {item.waitingAgentId && (
                  <p className="eid-result-text">
                    {copy.waitingFor}: {participantLabel(item, item.waitingAgentId, copy)}
                  </p>
                )}
                <Button
                  aria-label={`${copy.inspect}: ${item.subject}`}
                  onClick={() => setSelection({ id: item.id, scope, agentId })}
                  size="inline"
                  variant="textStrong"
                >
                  {copy.inspect}
                </Button>
              </li>
            ))}
          </ol>
          {visible < conversations.length && (
            <Button onClick={() => setVisible(count => count + 20)} variant="secondary">
              {copy.more}
            </Button>
          )}
        </>
      )}
    </section>
  )
}
