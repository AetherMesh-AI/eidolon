import { useEffect, useState } from 'react'
import { Link } from 'react-router'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { useI18n } from '@/i18n/context'

import { RevisionSeenControl, sameConnection } from './runtime-inbox-seen'
import type { OrganizationOutcomePage } from './runtime-outcome-types'
import type { OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

interface OutcomeProps {
  adapter: RuntimeOrganizationAdapter
  snapshot: OrganizationSnapshot
}

/** Durable outcome receipts remain independent of requests awaiting an owner decision. */
export function RuntimeOutcomeInbox({ adapter, snapshot }: OutcomeProps) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const [filter, setFilter] = useState<'all' | 'unread'>('all')
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined])
  const [result, setResult] = useState<{ key: string; revision: string; page: OrganizationOutcomePage } | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const before = cursors[cursors.length - 1]
  const key = JSON.stringify([snapshot.connection?.ownerScope ?? snapshot.connection?.scope, filter, before])

  // Backend generation includes records outside this page and the snapshot window.
  const revision = snapshot.outcomes?.generation ?? ''

  const page = result?.key === key ? result.page : null
  const fresh = result?.revision === revision
  const ready = snapshot.connection?.state === 'ready'
  const counts = snapshot.outcomes

  const refresh = () => {
    void adapter.refresh()
    setAttempt(value => value + 1)
  }

  useEffect(() => {
    if (!ready || !adapter.getOutcomes) {
      return
    }

    let current = true
    setLoading(true)
    setError('')
    void adapter
      .getOutcomes({ before, unreadOnly: filter === 'unread', limit: 25 })
      .then(value => {
        if (current && sameConnection(adapter, snapshot)) {
          setResult({ key, revision: value.generation, page: value })
        }
      })
      .catch(reason => {
        if (current && sameConnection(adapter, snapshot)) {
          setError(reason instanceof Error ? reason.message : copy.outcomeReadError)
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
  }, [adapter, ready, snapshot.connection?.scope, key, revision, before, filter, attempt, copy.outcomeReadError])

  const items = page?.items ?? []
  const contentChanged = !!page && !fresh

  return (
    <section aria-label={copy.outcomeInbox}>
      <h2>{copy.outcomeInbox}</h2>
      <p className="eid-note">{copy.outcomeNote}</p>
      <p role="status">
        {copy.outcomeInbox}: {counts?.total ?? 0} · {copy.attentionUnread}: {counts?.unread ?? 0}
      </p>
      <div className="eid-toolbar">
        <SegmentedControl
          onChange={value => {
            setFilter(value)
            setCursors([undefined])
          }}
          options={[
            { id: 'all', label: copy.outcomeAll },
            { id: 'unread', label: copy.attentionUnread }
          ]}
          value={filter}
        />
        <Button disabled={!ready || loading} onClick={refresh} size="sm" variant="secondary">
          {copy.refresh}
        </Button>
      </div>
      {!ready && <p role="status">{copy.outcomeOffline}</p>}
      {ready && loading && <Loader label={copy.outcomeLoading} />}
      {ready && !loading && contentChanged && <p role="status">{copy.outcomeChanged}</p>}
      {error && (
        <div role="alert">
          <ErrorState description={error} title={copy.outcomeReadError}>
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
              {items.map(item => (
                <li aria-label={item.title} key={item.objectiveId}>
                  <div className="eid-row">
                    <span>
                      <Link to={`/objectives/${encodeURIComponent(item.objectiveId)}`}><strong>{item.title}</strong></Link>
                      <small>{copy[item.status === 'accepted' ? 'outcomeAccepted' : item.status === 'cancelled' ? 'cancelled' : 'outcomeLegacy']}</small>
                      {item.summary && <small>{item.summary}</small>}
                      {item.status === 'cancelled' && <small>{copy.outcomeCancelledNote}</small>}
                      {item.archived && <small>{copy.historyArchived}</small>}
                      <small>{copy.attentionUpdated}: <time dateTime={item.updatedAt}>{new Date(item.updatedAt).toLocaleString()}</time></small>
                    </span>
                    <Badge variant={item.seen ? 'muted' : 'warn'}>{item.seen ? copy.attentionSeen : copy.attentionUnread}</Badge>
                  </div>
                  {adapter.markOutcomeSeen && (
                    <RevisionSeenControl
                      adapter={adapter}
                      disabled={!ready || !fresh || loading}
                      errorTitle={copy.outcomeWriteError}
                      item={{ ...item, id: item.objectiveId }}
                      key={`${item.objectiveId}:${item.revision}`}
                      markSeen={adapter.markOutcomeSeen}
                      onChanged={refresh}
                      snapshot={snapshot}
                    />
                  )}
                </li>
              ))}
            </ol>
          ) : (
            !loading &&
            !contentChanged &&
            !error &&
            ready && (
              <EmptyState
                description={cursors.length > 1 ? copy.attentionPageEmptyNote : copy.outcomeEmptyNote}
                title={
                  cursors.length > 1
                    ? copy.outcomePageEmpty
                    : filter === 'unread'
                      ? copy.outcomeNoUnread
                      : copy.outcomeEmpty
                }
              />
            )
          )}
        </>
      )}
      {(page || cursors.length > 1) && (
        <nav aria-label={copy.outcomePages} className="eid-toolbar">
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
