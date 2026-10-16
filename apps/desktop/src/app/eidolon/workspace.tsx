import './eidolon.css'

import { lazy, Suspense, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { Link, useLocation, useMatch, useNavigationType } from 'react-router'

import { Button } from '@/components/ui/button'
import { Loader } from '@/components/ui/loader'
import { SearchField } from '@/components/ui/search-field'
import { useI18n } from '@/i18n/context'

import { WorkspacePageHeading } from '../workspace-page-heading'

import { Activity } from './activity'
import { Home } from './home'
import { Inspector } from './inspector'
import { Messages } from './messages'
import { responsibleTeams } from './objective-context'
import { MetadataSummary } from './objective-metadata'
import { ObjectiveOverview } from './objective-overview'
import { Organization } from './organization'
import { RuntimeArtifact } from './runtime-artifact'
import { RuntimeStatus } from './runtime-detail'
import { RuntimeHistoryBrowser, RuntimeHistoryObjectiveDetail } from './runtime-history'
import { RuntimeOutcomeInbox } from './runtime-outcomes'
import { RuntimeRequests } from './runtime-requests'
import { RuntimeSetup } from './runtime-setup'
import { TeamOnboardingEntry } from './team-onboarding'
import { type Objective, objectiveStatusLabels, type OrganizationAdapter, type OrganizationSnapshot, type RuntimeOrganizationAdapter } from './types'
import { WorkGraph } from './work-graph'
import { WorkOverview } from './work-overview'
import { createWorkspaceScrollMemory, objectiveHref, objectiveReturnTarget } from './workspace-navigation'

const labels = { ...objectiveStatusLabels, pending: 'Pending', approved: 'Approved', rejected: 'Rejected', recorded: 'Recorded' }

export function Status({ status }: { status: keyof typeof labels }) {
  const { t } = useI18n()

  const translated = status === 'active' || status === 'waiting'
    ? t.organizationSurfaces.objectives[status]
    : status in t.organizationHome.statuses
    ? t.organizationHome.statuses[status as keyof typeof t.organizationHome.statuses]
    : t.organizationSurfaces.objectives[status as 'pending' | 'approved' | 'rejected' | 'recorded']

  return <span className={`eid-status eid-status-${status}`}><span aria-hidden="true">●</span> {translated}</span>
}

function ObjectiveList({ objectives, snapshot, onClearFilters, page, setPage }: { objectives: Objective[]; snapshot: OrganizationSnapshot; onClearFilters?: () => void; page: number; setPage: (page: number) => void }) {
  const { t, locale } = useI18n()
  const list = t.organizationSurfaces.objectives
  const location = useLocation()
  const number = new Intl.NumberFormat(locale)
  const percent = new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 2 })
  const lastPage = Math.max(0, Math.ceil(objectives.length / 25) - 1)
  const currentPage = Math.min(page, lastPage)

  return objectives.length ? <><div className="eid-list">{objectives.slice(currentPage * 25, (currentPage + 1) * 25).map(objective => <Link className="eid-row" key={objective.id} to={objectiveHref(objective.id, location.pathname + location.search, snapshot.connection?.ownerScope ?? snapshot.connection?.scope)}>
    <span className="eid-objective-summary"><strong dir="auto">{objective.title}</strong><small dir="auto">{objective.description}</small><small>{objective.progress === undefined ? t.organizationHome.progressUnknown : `${percent.format(objective.progress / 100)}${objective.source === 'runtime' ? '' : ` · ${list.localEstimate}`}`} · <bdi>{objective.milestone || list.noMilestone}</bdi></small><small>{list.responsibleTeam}: <bdi>{responsibleTeams(snapshot, objective, list.unknownTeam)}</bdi></small></span><Status status={objective.status} />
  </Link>)}</div>{objectives.length > 25 && <nav aria-label={list.objectivePages} className="eid-toolbar"><button className="eid-button" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>{list.previousPage}</button><span role="status">{list.pageRange(number.format(currentPage * 25 + 1), number.format(Math.min((currentPage + 1) * 25, objectives.length)), number.format(objectives.length))}</span><button className="eid-button" disabled={currentPage === lastPage} onClick={() => setPage(currentPage + 1)}>{list.nextPage}</button></nav>}</> : onClearFilters ? <div className="eid-empty"><h2>{list.noMatches}</h2><p>{list.filterHint}</p><Button onClick={onClearFilters} variant="secondary">{list.clearFilters}</Button></div> : <div className="eid-empty"><h2>{list.noObjectives}</h2><p>{list.noObjectivesNote}</p><Link className="eid-button" to="/home#eid-home-intake">{t.organizationHome.newObjective}</Link></div>
}


function StaticObjectiveDetail({ objective, snapshot }: { objective: Objective; snapshot: OrganizationSnapshot }) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const [inspecting, setInspecting] = useState(false)

  return <><Link className="eid-back" to="/objectives">← {copy.objectives}</Link><header className="eid-page-header"><div><p className="eid-eyebrow">{copy.legacyDemo}</p><h1 dir="auto">{objective.title}</h1><p dir="auto">{objective.description}</p></div><Status status={objective.status} /></header><p>{copy.legacyNote}</p><button className="eid-button" onClick={() => setInspecting(true)}>{t.organizationHome.detail.inspectObjective}</button>{inspecting && <Inspector kind="objective" onClose={() => setInspecting(false)} title={objective.title}><MetadataSummary objective={objective} /><p>{copy.legacyNote}</p></Inspector>}<MetadataSummary objective={objective} /><ObjectiveOverview objective={objective} snapshot={snapshot} />{objective.result && <><h2>{copy.result}</h2><p dir="auto">{objective.result}</p></>}<WorkGraph objectiveId={objective.id} snapshot={snapshot} /><Activity objectiveId={objective.id} snapshot={snapshot} /><Knowledge objectiveId={objective.id} snapshot={snapshot} /></>
}

import { MemoryWeb } from './memory-web'

export function Knowledge({ snapshot, objectiveId, adapter }: { snapshot: OrganizationSnapshot; objectiveId?: string; adapter?: RuntimeOrganizationAdapter }) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const items = snapshot.knowledge.filter(item => (!objectiveId || item.objectiveId === objectiveId) && `${item.title} ${item.body}`.toLowerCase().includes(search.toLowerCase()))
  const selectedItem = items.find(item => item.id === selected)

  return <><p>{copy.evidenceSource}</p><p className="eid-note">{copy.evidenceNote}</p>{snapshot.knowledge.length > 0 && <SearchField aria-label={copy.evidence} onChange={setSearch} placeholder={copy.evidence} value={search} />}{items.length ? <div className="eid-list">{items.map(item => <button className="eid-row" key={item.id} onClick={() => setSelected(item.id)}><span>{item.title}<small>{item.kind} · {item.objectiveId || copy.evidenceSource}</small></span></button>)}</div> : <p>{copy.evidenceEmpty}</p>}
    {selectedItem && (adapter ? <RuntimeArtifact adapter={adapter} evidenceId={selectedItem.id} key={selectedItem.id} onClose={() => setSelected(null)} snapshot={snapshot} title={selectedItem.title} /> : <Inspector kind="artifact" onClose={() => setSelected(null)} title={selectedItem.title}><p>{selectedItem.body}</p><p>{copy.legacyNote}</p>{selectedItem.objectiveId && <Link to={`/objectives/${selectedItem.objectiveId}`}>{copy.openObjective}</Link>}</Inspector>)}
  </>
}

const RuntimeOrganizationWorkspace = lazy(() => import('./runtime-workspace'))

export function OrganizationWorkspace({ adapter }: { adapter?: OrganizationAdapter }) {
  const { t } = useI18n()

  // Explicit examples/tests never load the gateway boundary or dispatch work.
  return adapter ? <OrganizationWorkspaceView adapter={adapter} /> : <Suspense fallback={<Loader label={t.organizationHome.connecting} />}><RuntimeOrganizationWorkspace /></Suspense>
}

/** Route changes own scroll; snapshot refreshes never do. */
export function useWorkspaceScroll(location: ReturnType<typeof useLocation>, owner: string | undefined) {
  const element = useRef<HTMLElement>(null)
  const navigationType = useNavigationType()
  const memory = useRef(createWorkspaceScrollMemory())
  const previous = useRef<{ pathname: string; search: string; owner: string | undefined } | null>(null)

  useLayoutEffect(() => {
    const root = element.current

    if (!root) { return }
    const route = location.pathname + location.search
    const prior = previous.current
    memory.current.reset(owner)

    const returning = prior && prior.owner === owner && prior.pathname.startsWith('/objectives/') &&
      objectiveReturnTarget(new URLSearchParams(prior.search), owner) === route &&
      (navigationType === 'POP' || location.state?.workspaceReturnOwner === owner)

    const position = returning ? memory.current.read(owner, route) : undefined

    const panels = () => ['.eid-home-panel-body', '.eid-messages-layout > .eid-responsive-detail-list', '.eid-messages-layout > .eid-responsive-detail-content', '.eid-message-context'].flatMap(selector =>
      Array.from(root.querySelectorAll<HTMLElement>(selector)).map(panel => ({ panel, key: `${selector}:${panel.getAttribute('aria-label') ?? ''}` }))
    )

    if (prior?.pathname !== location.pathname || prior?.owner !== owner) {
      root.scrollTop = 0
      root.scrollLeft = 0
      panels().forEach(({ panel, key }) => {
        const saved = position?.panels.find(item => item.key === key)
        panel.scrollTop = saved?.top ?? 0
        panel.scrollLeft = saved?.left ?? 0
      })
    }

    let restorationPending = !!position
    let restoreObserver: MutationObserver | undefined
    let restoreResizeObserver: ResizeObserver | undefined
    let restoreDeadline: ReturnType<typeof setTimeout> | undefined

    const cancelRestore = () => {
      restorationPending = false
      restoreObserver?.disconnect()
      restoreObserver = undefined
      restoreResizeObserver?.disconnect()
      restoreResizeObserver = undefined
      clearTimeout(restoreDeadline)
      restoreDeadline = undefined
    }

    const restore = (allowShorter = false) => {
      if (!restorationPending || !position || !allowShorter && root.scrollHeight + 1 < position.contentHeight) { return }

      const link = position.focusHref ? Array.from(root.querySelectorAll<HTMLAnchorElement>('a[href]'))
        .find(item => item.getAttribute('href') === position.focusHref) : undefined

      if (position.focusHref && !link || position.panels.some(saved => !panels().some(item => item.key === saved.key))) { return }
      root.scrollTop = position.top
      root.scrollLeft = position.left
      panels().forEach(({ panel, key }) => {
        const saved = position.panels.find(item => item.key === key)
        panel.scrollTop = saved?.top ?? 0
        panel.scrollLeft = saved?.left ?? 0
      })
      link?.focus({ preventScroll: true })
      cancelRestore()
    }

    if (position) {
      // Wait for an asynchronously loaded origin once; user input cancels it.
      restoreObserver = new MutationObserver(() => restore())
      restoreObserver.observe(root, { childList: true, characterData: true, subtree: true })

      if (typeof ResizeObserver !== 'undefined') {
        restoreResizeObserver = new ResizeObserver(() => restore())
        restoreResizeObserver.observe(root.firstElementChild ?? root)
      }

      // A changed or permanently shorter list must not retain a pending focus
      // transfer indefinitely. This fallback is cancelled by any new user intent.
      restoreDeadline = setTimeout(() => { restore(true); cancelRestore() }, 1500)
      restore()
    }

    previous.current = { pathname: location.pathname, search: location.search, owner }
    let focusHref = position?.focusHref ?? null

    const save = () => memory.current.save(owner, route, {
      top: root.scrollTop,
      left: root.scrollLeft,
      contentHeight: root.scrollHeight,
      panels: panels().map(({ panel, key }) => ({ key, top: panel.scrollTop, left: panel.scrollLeft })),
      focusHref
    })

    const captureLink = (event: MouseEvent) => {
      const link = event.target instanceof Element ? event.target.closest('a[href]') : null

      if (link && root.contains(link)) { focusHref = link.getAttribute('href'); save() }
    }

    const document = root.ownerDocument

    for (const event of ['pointerdown', 'keydown', 'wheel', 'touchstart']) { document.addEventListener(event, cancelRestore, true) }
    document.addEventListener('focusin', cancelRestore, true)
    root.addEventListener('scroll', save, true)
    root.addEventListener('click', captureLink, true)

    return () => {
      cancelRestore()

      for (const event of ['pointerdown', 'keydown', 'wheel', 'touchstart']) { document.removeEventListener(event, cancelRestore, true) }
      document.removeEventListener('focusin', cancelRestore, true)
      root.removeEventListener('scroll', save, true)
      root.removeEventListener('click', captureLink, true)
    }
  }, [location.key, location.pathname, location.search, location.state, navigationType, owner])

  return element
}

export function OrganizationWorkspaceView({ adapter }: { adapter: OrganizationAdapter }) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const snapshot = useSyncExternalStore(adapter.subscribe, adapter.getSnapshot)
  const location = useLocation()
  const { pathname } = location
  const owner = snapshot.connection?.ownerScope ?? snapshot.connection?.scope
  const scrollRoot = useWorkspaceScroll(location, owner)
  const objectiveRoute = useMatch('/objectives/:objectiveId')
  const [filterState, setFilterState] = useState({ owner, value: 'all' })
  const [queryState, setQueryState] = useState({ owner, value: '' })
  const [pageState, setPageState] = useState({ owner, value: 0 })
  const page = pageState.owner === owner ? pageState.value : 0
  const setPage = (value: number) => setPageState({ owner, value })
  const filter = filterState.owner === owner ? filterState.value : 'all'
  const query = queryState.owner === owner ? queryState.value : ''
  const setFilter = (value: string) => setFilterState({ owner, value })
  const setQuery = (value: string) => setQueryState({ owner, value })
  const objective = snapshot.objectives.find(item => pathname === `/objectives/${item.id}`)
  const list = t.organizationSurfaces.objectives
  const titles: Record<string, string> = { '/objectives': t.organizationHome.objectives, '/activity': t.organizationHome.activity, '/requests': copy.needsYou, '/knowledge': list.knowledge }

  return <main aria-label={list.workspace} className="eidolon eid-workspace" ref={scrollRoot}>
    <div className="eid-page">
      {adapter.mode === 'prototype' ? <div className="eid-demo-bar"><span>{copy.legacyDemo} · {copy.legacyNote}</span></div> : <RuntimeStatus adapter={adapter} snapshot={snapshot} warningsOnly />}
      {pathname === '/home' && <Home adapter={adapter} snapshot={snapshot} starterEntry={adapter.mode === 'runtime' && !snapshot.objectives.length && !snapshot.tasks.length && !snapshot.requests?.length ? <TeamOnboardingEntry adapter={adapter} key={snapshot.connection?.ownerScope ?? snapshot.connection?.scope} /> : undefined} />}
      {pathname === '/messages' && <Messages adapter={adapter.mode === 'runtime' ? adapter : undefined} key={snapshot.connection?.ownerScope ?? snapshot.connection?.scope} snapshot={snapshot} />}
      {pathname === '/work' && <WorkOverview snapshot={snapshot} />}
      {titles[pathname] && <WorkspacePageHeading title={titles[pathname]}><p className="eid-eyebrow">Eidolon · {t.organizationSurfaces.organization} · {adapter.mode === 'runtime' ? list.runtime : list.localPrototype}</p></WorkspacePageHeading>}
      {pathname === '/objectives' && <>{adapter.mode === 'runtime' && snapshot.outcomes && adapter.getOutcomes && <RuntimeOutcomeInbox adapter={adapter} key={snapshot.connection?.ownerScope ?? snapshot.connection?.scope} snapshot={snapshot} />}{snapshot.runtime?.historyLimited && !snapshot.runtime.history && <p className="eid-note">{list.historyNote}</p>}<div className="eid-toolbar"><label className="eid-filter">{list.searchObjectives}<input onChange={event => setQuery(event.target.value)} placeholder={list.findObjective} value={query} /></label><label className="eid-filter">{list.status}<select onChange={event => setFilter(event.target.value)} value={filter}><option value="all">{list.allStatuses}</option>{Object.keys(objectiveStatusLabels).map(key => <option key={key} value={key}>{key === 'active' || key === 'waiting' ? list[key] : t.organizationHome.statuses[key as keyof typeof objectiveStatusLabels]}</option>)}</select></label><Link className="eid-primary" to="/home#eid-home-intake">{t.organizationHome.newObjective}</Link></div><ObjectiveList objectives={snapshot.objectives.filter(item => (filter === 'all' || item.status === filter) && item.title.toLowerCase().includes(query.toLowerCase()))} onClearFilters={filter !== 'all' || query ? () => { setFilter('all'); setQuery('') } : undefined} page={page} setPage={setPage} snapshot={snapshot} />{adapter.mode === 'runtime' && <RuntimeHistoryBrowser adapter={adapter} key={snapshot.connection?.ownerScope ?? snapshot.connection?.scope} snapshot={snapshot} />}</>}
      {pathname.startsWith('/objectives/') && (adapter.mode === 'runtime' ? <RuntimeHistoryObjectiveDetail adapter={adapter} key={`${snapshot.connection?.ownerScope ?? snapshot.connection?.scope}:${pathname}`} objectiveId={objectiveRoute?.params.objectiveId ?? ''} snapshot={snapshot} /> : objective ? <StaticObjectiveDetail key={objective.id} objective={objective} snapshot={snapshot} /> : <div className="eid-empty"><h1>{list.notFound}</h1><p>{list.notFoundNote}</p><Link to="/objectives">{list.backToObjectives}</Link></div>)}
      {pathname === '/requests' && adapter.mode === 'runtime' && <RuntimeRequests adapter={adapter} key={snapshot.connection?.ownerScope ?? snapshot.connection?.scope} snapshot={snapshot} />}
      {pathname === '/activity' && <Activity key={snapshot.connection?.scope} snapshot={snapshot} />}
      {pathname === '/knowledge' && (adapter.mode === 'runtime' ? <Knowledge adapter={adapter} key={snapshot.connection?.scope} snapshot={snapshot} /> : <MemoryWeb items={snapshot.knowledge} />)}
      {pathname === '/organization' && <Organization adapter={adapter.mode === 'runtime' ? adapter : undefined} key={snapshot.connection?.ownerScope ?? snapshot.connection?.scope} snapshot={snapshot} />}
      {adapter.mode === 'runtime' && pathname === '/organization' && <RuntimeSetup snapshot={snapshot} />}
    </div>
  </main>
}
