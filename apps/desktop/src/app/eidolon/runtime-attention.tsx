import { useEffect, useRef, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { useI18n } from '@/i18n/context'

import type { OrganizationAttentionItem, OrganizationAttentionPage } from './runtime-attention-types'
import type { OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

interface AttentionProps {
  adapter: RuntimeOrganizationAdapter
  snapshot: OrganizationSnapshot
  onInspect(id: string): void
}

function sameConnection(adapter: RuntimeOrganizationAdapter, snapshot: OrganizationSnapshot) {
  const current = adapter.getSnapshot().connection

  return (
    current?.scope === snapshot.connection?.scope &&
    current?.ownerScope === snapshot.connection?.ownerScope &&
    current?.state === 'ready'
  )
}

interface SeenControlProps extends Pick<AttentionProps, 'adapter' | 'snapshot'> {
  item: OrganizationAttentionItem
  disabled: boolean
  onChanged(): void
}

function SeenControl({ adapter, snapshot, item, disabled, onChanged }: SeenControlProps) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const sending = useRef(false)
  const epoch = useRef(0)

  // eslint-disable-next-line no-restricted-syntax -- fence the lifetime of this exact revision control
  useEffect(() => {
    const invalidated = epoch.current + 1
    sending.current = false
    setPending(false)
    setError('')

    return () => {
      epoch.current = invalidated
    }
  }, [item.revision, snapshot.connection?.scope, snapshot.connection?.state])

  const markSeen = async () => {
    if (sending.current || disabled || item.seen || !adapter.markAttentionSeen || !sameConnection(adapter, snapshot)) {
      return
    }

    const version = epoch.current
    sending.current = true
    setPending(true)
    setError('')

    try {
      await adapter.markAttentionSeen({ id: item.requestId, revision: item.revision })

      if (version === epoch.current && sameConnection(adapter, snapshot)) {
        onChanged()
      }
    } catch (reason) {
      if (version === epoch.current && sameConnection(adapter, snapshot)) {
        setError(reason instanceof Error ? reason.message : copy.attentionWriteError)
      }
    } finally {
      if (version === epoch.current) {
        sending.current = false
        setPending(false)
      }
    }
  }

  return (
    <>
      <Button disabled={disabled || pending || item.seen} onClick={() => void markSeen()} size="sm" variant="secondary">
        {item.seen ? copy.attentionSeen : copy.attentionMarkSeen}
      </Button>
      {pending && <Loader label={copy.attentionSaving} />}
      {error && (
        <div role="alert">
          <ErrorState description={error} title={copy.attentionWriteError}>
            <Button disabled={disabled || pending} onClick={onChanged} size="sm" variant="secondary">
              {copy.refresh}
            </Button>
          </ErrorState>
        </div>
      )}
    </>
  )
}

/** Bounded local projection of the request ledger. Inspection never acknowledges a row. */
export function RuntimeAttentionInbox({ adapter, snapshot, onInspect }: AttentionProps) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const [filter, setFilter] = useState<'all' | 'unread'>('all')
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined])
  const [result, setResult] = useState<{ key: string; revision: string; page: OrganizationAttentionPage } | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const before = cursors[cursors.length - 1]
  const key = JSON.stringify([snapshot.connection?.ownerScope ?? snapshot.connection?.scope, filter, before])

  // Snapshot polls don't reset pagination. Changed blocker content, counts, acknowledgements,
  // or resolutions do invalidate an older page, including pages beyond the snapshot window.
  const revision = JSON.stringify([
    snapshot.attention,
    snapshot.requests
      ?.filter(item => item.status === 'pending_intervention')
      .map(item => [item.id, item.attentionRevision])
  ])

  const page = result?.key === key ? result.page : null
  const fresh = result?.revision === revision
  const ready = snapshot.connection?.state === 'ready'
  const counts = snapshot.attention

  const refresh = () => {
    void adapter.refresh()
    setAttempt(value => value + 1)
  }

  useEffect(() => {
    if (!ready || !adapter.getAttention) {
      return
    }

    let current = true
    setLoading(true)
    setError('')
    void adapter
      .getAttention({ before, unreadOnly: filter === 'unread', limit: 25 })
      .then(value => {
        if (current && sameConnection(adapter, snapshot)) {
          setResult({ key, revision, page: value })
        }
      })
      .catch(reason => {
        if (current && sameConnection(adapter, snapshot)) {
          setError(reason instanceof Error ? reason.message : copy.attentionReadError)
        }
      })
      .finally(() => {
        if (current) {
          setLoading(false)
        }
      })

    return () => {
      current = false
    }
    // Only identity and ledger content invalidate a page; quiet poll timestamps don't.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapter, ready, snapshot.connection?.scope, key, revision, before, filter, attempt, copy.attentionReadError])

  const requests = new Map(snapshot.requests?.map(item => [item.id, item]))
  const items = page?.items.filter(item => requests.get(item.requestId)?.status === 'pending_intervention') ?? []

  const contentChanged =
    !!page &&
    (page.total !== counts?.total ||
      page.unread !== counts?.unread ||
      page.items.some(item => {
        const request = requests.get(item.requestId)

        return request?.status !== 'pending_intervention' || request.attentionRevision !== item.revision
      }))

  return (
    <section aria-label={copy.attentionInbox}>
      <p className="eid-note">{copy.attentionNote}</p>
      <p role="status">
        {copy.needsYou}: {counts?.total ?? 0} · {copy.attentionUnread}: {counts?.unread ?? 0}
      </p>
      <div className="eid-toolbar">
        <SegmentedControl
          onChange={value => {
            setFilter(value)
            setCursors([undefined])
          }}
          options={[
            { id: 'all', label: copy.attentionAll },
            { id: 'unread', label: copy.attentionUnread }
          ]}
          value={filter}
        />
        <Button disabled={!ready || loading} onClick={refresh} size="sm" variant="secondary">
          {copy.refresh}
        </Button>
      </div>
      {!ready && <p role="status">{copy.attentionOffline}</p>}
      {ready && loading && <Loader label={copy.attentionLoading} />}
      {ready && !loading && contentChanged && <p role="status">{copy.attentionChanged}</p>}
      {error && (
        <div role="alert">
          <ErrorState description={error} title={copy.attentionReadError}>
            <Button disabled={!ready || loading} onClick={refresh} size="sm" variant="secondary">
              {copy.attentionRetry}
            </Button>
          </ErrorState>
        </div>
      )}
      {page && (
        <>
          {items.length ? (
            <ol className="eid-list">
              {items.map(item => {
                const request = requests.get(item.requestId)!

                return (
                  <li aria-label={`${copy.inspectRequest}: ${request.type}`} key={item.requestId}>
                    <button
                      aria-label={`${copy.inspectRequest}: ${request.type}`}
                      className="eid-row"
                      onClick={() => onInspect(item.requestId)}
                    >
                      <span>
                        <strong>{request.type}</strong>
                        <small>
                          {snapshot.objectives.find(value => value.id === item.objectiveId)?.title ?? item.objectiveId}
                        </small>
                        {request.requestedOutcome && <small>{request.requestedOutcome}</small>}
                        {request.reason && <small>{request.reason}</small>}
                        <small>
                          {copy.attentionUpdated}:{' '}
                          <time dateTime={item.updatedAt}>{new Date(item.updatedAt).toLocaleString()}</time>
                        </small>
                      </span>
                      <Badge variant={item.seen ? 'muted' : 'warn'}>
                        {item.seen ? copy.attentionSeen : copy.attentionUnread}
                      </Badge>
                    </button>
                    {adapter.markAttentionSeen && (
                      <SeenControl
                        adapter={adapter}
                        disabled={!ready || !fresh || loading || item.revision !== request.attentionRevision}
                        item={item}
                        key={`${item.requestId}:${item.revision}`}
                        onChanged={refresh}
                        snapshot={snapshot}
                      />
                    )}
                  </li>
                )
              })}
            </ol>
          ) : (
            !loading &&
            !contentChanged &&
            !error &&
            ready && (
              <EmptyState
                description={cursors.length > 1 ? copy.attentionPageEmptyNote : copy.attentionEmptyNote}
                title={
                  cursors.length > 1
                    ? copy.attentionPageEmpty
                    : filter === 'unread'
                      ? copy.attentionNoUnread
                      : copy.noNeeds
                }
              />
            )
          )}
        </>
      )}
      {(page || cursors.length > 1) && (
        <nav aria-label={copy.attentionPages} className="eid-toolbar">
          <Button
            disabled={!ready || cursors.length === 1}
            onClick={() => setCursors(value => value.slice(0, -1))}
            size="sm"
            variant="secondary"
          >
            {copy.attentionPrevious}
          </Button>
          <span role="status">
            {copy.attentionPage} {cursors.length}
          </span>
          <Button
            disabled={!ready || loading || !fresh || !page?.nextCursor}
            onClick={() => {
              if (page?.nextCursor) {
                setCursors(value => [...value, page.nextCursor!])
              }
            }}
            size="sm"
            variant="secondary"
          >
            {copy.attentionNext}
          </Button>
        </nav>
      )}
    </section>
  )
}
