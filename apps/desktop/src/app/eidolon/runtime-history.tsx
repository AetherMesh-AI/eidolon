import { useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { SearchField } from '@/components/ui/search-field'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { useI18n } from '@/i18n/context'
import { ChevronDown } from '@/lib/icons'

import { LocalizedTime } from './localized-time'
import { RuntimeObjectiveDetail } from './runtime-detail'
import type { HistoryPage, HistoryQuery, HistoryState } from './runtime-history-types'
import type { OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

interface HistoryProps {
  adapter: RuntimeOrganizationAdapter
  snapshot: OrganizationSnapshot
}

function sameConnection(adapter: RuntimeOrganizationAdapter, snapshot: OrganizationSnapshot) {
  const current = adapter.getSnapshot().connection

  return (
    current?.scope === snapshot.connection?.scope &&
    current?.ownerScope === snapshot.connection?.ownerScope &&
    current?.state === 'ready'
  )
}

interface HistoryControlProps extends HistoryProps {
  id: string
  history: HistoryState
  onChanged(): void
}

function HistoryControl({ adapter, snapshot, id, history, onChanged }: HistoryControlProps) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const sending = useRef(false)
  const epoch = useRef(0)
  const permitted = history.archived ? history.canRestore : history.canArchive
  const ready = snapshot.connection?.state === 'ready'

  // Fence a detached control; the adapter retains uncertain mutation identities.
  // eslint-disable-next-line no-restricted-syntax -- request lifetime, not mirrored shared state
  useEffect(() => {
    sending.current = false
    setPending(false)

    return () => {
      epoch.current += 1
    }
  }, [snapshot.connection?.scope, snapshot.connection?.state])

  const change = async () => {
    if (
      sending.current ||
      !ready ||
      !permitted ||
      !adapter.setObjectiveArchived ||
      !sameConnection(adapter, snapshot)
    ) {
      return
    }

    const version = epoch.current
    sending.current = true
    setPending(true)
    setError('')

    try {
      await adapter.setObjectiveArchived({ id, archived: !history.archived, expectedRevision: history.revision })

      if (version === epoch.current && sameConnection(adapter, snapshot)) {
        onChanged()
      }
    } catch (reason) {
      if (version === epoch.current && sameConnection(adapter, snapshot)) {
        setError(reason instanceof Error ? reason.message : copy.historyWriteError)
      }
    } finally {
      if (version === epoch.current) {
        sending.current = false
        setPending(false)
      }
    }
  }

  return (
    <div>
      <Button disabled={!ready || !permitted || pending} onClick={() => void change()} size="sm" variant="secondary">
        {history.archived ? copy.historyRestore : copy.historyArchive}
      </Button>
      {pending && <Loader label={copy.historySaving} />}
      {!permitted && history.blocker && <p className="eid-note">{history.blocker}</p>}
      {error && (
        <div role="alert">
          <ErrorState description={error} title={copy.historyWriteError}>
            <Button disabled={!ready || pending} onClick={onChanged} size="sm" variant="secondary">
              {copy.refresh}
            </Button>
          </ErrorState>
        </div>
      )}
    </div>
  )
}

export function RuntimeHistoryBrowser({ adapter, snapshot }: HistoryProps) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const regionId = useId()
  const [expanded, setExpanded] = useState(false)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Required<Pick<HistoryQuery, 'state' | 'query'>>>({ state: 'current', query: '' })
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined])
  const [result, setResult] = useState<{ key: string; page: HistoryPage } | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const before = cursors[cursors.length - 1]
  const key = JSON.stringify([filter, before])
  const ready = snapshot.connection?.state === 'ready'
  const page = result?.key === key ? result.page : null
  const counts = page?.counts ?? snapshot.runtime?.history

  // History pages stay local: visiting old work must not replace live organization state.
  useEffect(() => {
    if (!expanded || !ready || !adapter.getHistory) {
      return
    }

    let current = true
    setLoading(true)
    setError('')
    void adapter
      .getHistory({ ...filter, before, limit: 25 })
      .then(value => {
        if (current && sameConnection(adapter, snapshot)) {
          setResult({ key, page: value })
        }
      })
      .catch(reason => {
        if (current && sameConnection(adapter, snapshot)) {
          setError(reason instanceof Error ? reason.message : copy.historyReadError)
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
    // Snapshot contents can change during a page read; only connection identity fences it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    adapter,
    expanded,
    ready,
    snapshot.connection?.scope,
    snapshot.connection?.ownerScope,
    filter,
    before,
    key,
    attempt,
    copy.historyReadError
  ])

  if (!adapter.getHistory || !snapshot.runtime?.history) {
    return null
  }

  const refresh = () => setAttempt(value => value + 1)

  const chooseFilter = (state: NonNullable<HistoryQuery['state']>) => {
    setFilter(current => ({ ...current, state }))
    setCursors([undefined])
  }

  return (
    <section aria-label={copy.objectiveHistory} className="mt-6">
      <Button
        aria-controls={regionId}
        aria-expanded={expanded}
        onClick={() => setExpanded(value => !value)}
        variant="textStrong"
      >
        <ChevronDown className={expanded ? 'rotate-180' : undefined} />
        {copy.objectiveHistory}
      </Button>
      <div hidden={!expanded} id={regionId}>
        <p className="eid-note">{copy.historyDescription}</p>
        <p className="eid-note">{copy.historyArchiveNote}</p>
        {counts && (
          <p>
            {copy.historyCurrentCount}: {counts.current} / {counts.currentLimit} · {copy.historyArchiveCount}:{' '}
            {counts.archived} / {counts.archiveLimit}
          </p>
        )}
        <div aria-label={copy.objectiveHistory} className="eid-toolbar" role="group">
          <SegmentedControl
            onChange={chooseFilter}
            options={[
              { id: 'current', label: copy.historyCurrent },
              { id: 'archived', label: copy.historyArchived },
              { id: 'all', label: copy.historyAll }
            ]}
            value={filter.state}
          />
          {((!!counts && counts.current + counts.archived > 0) || !!query) && (
            <form
              className="eid-toolbar"
              onSubmit={event => {
                event.preventDefault()
                setFilter(current => ({ ...current, query }))
                setCursors([undefined])
              }}
            >
              <SearchField
                aria-label={copy.historySearch}
                onChange={setQuery}
                placeholder={copy.historySearch}
                value={query}
              />
              <Button disabled={!ready} size="sm" type="submit" variant="secondary">
                {copy.historySearchAction}
              </Button>
            </form>
          )}
          <Button disabled={!ready || loading} onClick={refresh} size="sm" variant="secondary">
            {copy.refresh}
          </Button>
        </div>
        {!ready && snapshot.connection?.state !== 'connecting' && <p role="status">{copy.historyOffline}</p>}
        {ready && loading && <Loader label={copy.historyLoading} />}
        {error && (
          <div role="alert">
            <ErrorState description={error} title={copy.historyReadError}>
              <Button disabled={!ready || loading} onClick={refresh} size="sm" variant="secondary">
                {copy.historyRetry}
              </Button>
            </ErrorState>
          </div>
        )}
        {page && (
          <>
            {page.items.length ? (
              <ul className="eid-list">
                {page.items.map(item => (
                  <li aria-label={item.title} className="eid-row" key={item.id}>
                    <div>
                      <Link to={`/objectives/${encodeURIComponent(item.id)}`}>{item.title}</Link>
                      <p>
                        {item.status === 'open' ? copy.historyOpen : copy[item.status]} ·{' '}
                        {item.history.archived ? copy.historyArchived : copy.historyCurrent}
                      </p>
                      <LocalizedTime value={item.createdAt} />
                    </div>
                    {adapter.setObjectiveArchived && (
                      <HistoryControl
                        adapter={adapter}
                        history={item.history}
                        id={item.id}
                        onChanged={refresh}
                        snapshot={snapshot}
                      />
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              !loading && !error && <EmptyState description={copy.historyEmptyHint} title={copy.historyEmpty} />
            )}
            <nav aria-label={copy.objectiveHistory} className="eid-toolbar">
              <Button
                disabled={!ready || loading || cursors.length === 1}
                onClick={() => setCursors(current => current.slice(0, -1))}
                size="sm"
                variant="secondary"
              >
                {copy.historyPrevious}
              </Button>
              <span role="status">
                {copy.historyPage} {cursors.length}
              </span>
              <Button
                disabled={!ready || loading || !page.nextCursor}
                onClick={() => {
                  if (page.nextCursor) {
                    setCursors(current => [...current, page.nextCursor!])
                  }
                }}
                size="sm"
                variant="secondary"
              >
                {copy.historyNext}
              </Button>
            </nav>
          </>
        )}
      </div>
    </section>
  )
}

interface HistoryDetailProps extends HistoryProps {
  objectiveId: string
}

export function RuntimeHistoryObjectiveDetail({ adapter, snapshot, objectiveId }: HistoryDetailProps) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const [retained, setRetained] = useState<OrganizationSnapshot | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [loading, setLoading] = useState(false)
  const visible = snapshot.objectives.find(item => item.id === objectiveId)
  const hasVisible = !!visible
  const ready = snapshot.connection?.state === 'ready'
  const supported = !!snapshot.runtime?.history && !!adapter.getHistoryObjective
  const revision = visible?.history?.revision

  // Keep the mounted detail (and uncertain owner intent) while an omitted row
  // is resolved through the exact-history endpoint. Never republish it as active.
  useEffect(() => {
    if (visible) {
      setRetained(previous => {
        const prior = previous?.objectives.find(item => item.id === objectiveId)

        return (prior?.history?.revision ?? -1) > (visible.history?.revision ?? -1) ? previous : snapshot
      })
    }
  }, [snapshot, visible, objectiveId])

  useEffect(() => {
    if (!ready || !supported || !adapter.getHistoryObjective || (hasVisible && !attempt)) {
      return
    }

    let current = true
    setLoading(true)
    setError('')
    void adapter
      .getHistoryObjective(objectiveId)
      .then(value => {
        if (current && sameConnection(adapter, snapshot)) {
          if (value.objectives.some(item => item.id === objectiveId)) {
            setRetained(value)
          } else {
            setError(copy.historyUnavailable)
          }
        }
      })
      .catch(reason => {
        if (current && sameConnection(adapter, snapshot)) {
          setError(reason instanceof Error ? reason.message : copy.historyDetailError)
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
    // Exact detail is cached by the keyed owner/objective route, never the shared snapshot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    adapter,
    objectiveId,
    ready,
    supported,
    hasVisible,
    revision,
    snapshot.connection?.scope,
    snapshot.connection?.ownerScope,
    attempt,
    copy.historyDetailError,
    copy.historyUnavailable
  ])

  // Always use the live connection for controls, including retained data during disconnects.
  const retainedObjective = retained?.objectives.find(item => item.id === objectiveId)

  const useRetained =
    retained && (!visible || (retainedObjective?.history?.revision ?? -1) > (visible.history?.revision ?? -1))

  const detail = useRetained
    ? { ...retained, connection: snapshot.connection, runtime: snapshot.runtime }
    : visible
      ? snapshot
      : null

  const objective = detail?.objectives.find(item => item.id === objectiveId)
  const refresh = () => setAttempt(value => value + 1)

  return (
    <>
      {!objective && (
        <Link className="eid-back" to="/objectives">
          ← {copy.objectives}
        </Link>
      )}
      {!ready && snapshot.connection?.state !== 'connecting' && <p role="status">{copy.historyOffline}</p>}
      {ready && loading && <Loader label={copy.historyDetailLoading} />}
      {error && (
        <div role="alert">
          <ErrorState description={error} title={copy.historyDetailError}>
            <Button disabled={!ready || loading} onClick={refresh} size="sm" variant="secondary">
              {copy.historyRetry}
            </Button>
          </ErrorState>
        </div>
      )}
      {!objective && !error && ready && !supported && <EmptyState title={copy.historyUnavailable} />}
      {objective && detail && (
        <RuntimeObjectiveDetail
          adapter={adapter}
          historyControls={
            supported &&
            objective.history && (
              <section aria-label={copy.objectiveHistory}>
                <p>{objective.history.archived ? copy.historyArchived : copy.historyCurrent}</p>
                <p className="eid-note">{copy.historyArchiveNote}</p>
                {adapter.setObjectiveArchived && (
                  <HistoryControl
                    adapter={adapter}
                    history={objective.history}
                    id={objective.id}
                    onChanged={refresh}
                    snapshot={snapshot}
                  />
                )}
                <Button disabled={!ready || loading} onClick={refresh} size="sm" variant="text">
                  {copy.refresh}
                </Button>
              </section>
            )
          }
          objective={objective}
          snapshot={detail}
        />
      )}
    </>
  )
}
