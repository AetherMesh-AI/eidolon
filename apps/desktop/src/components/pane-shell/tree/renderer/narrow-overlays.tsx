/**
 * Narrow-viewport edge overlays — the tree's take on the app's hover-reveal
 * collapse. Collapsible panes leave the grid below the sidebar-collapse
 * breakpoint; an edge strip (hover) or PANE_TOGGLE_REVEAL_EVENT (⌘B / ⌘G /
 * titlebar toggles route here on narrow) slides the pane OVER the layout
 * instead of squeezing it. Event reveals pin; hover reveals follow the mouse.
 */

import { useStore } from '@nanostores/react'
import { useEffect, useLayoutEffect, useMemo, useRef } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { PaneTab, PaneTabLabel, PaneTabStrip } from '@/components/ui/pane-tab'
import { ContribBoundary, ContribRender } from '@/contrib/react/boundary'
import { useContributions } from '@/contrib/react/use-contributions'
import type { Contribution } from '@/contrib/types'
import { useI18n } from '@/i18n'
import { ESCAPE_PRIORITY, isTopEscapeLayer, pushEscapeLayer } from '@/lib/escape-layers'
import { cn } from '@/lib/utils'

import { PANE_TOGGLE_REVEAL_EVENT } from '../..'
import { allPaneIds, findGroupOfPane } from '../model'
import { $narrowOverlayReveal, narrowOverlayId, setNarrowOverlayReveal as setReveal } from '../narrow-overlay-state'
import { $hiddenTreePanes, $layoutTree, $narrowViewport } from '../store'

import { paneChrome } from './track-model'

export function NarrowOverlays() {
  const narrow = useStore($narrowViewport)
  const tree = useStore($layoutTree)
  const panes = useContributions('panes')
  const hiddenPanes = useStore($hiddenTreePanes)
  const reveal = useStore($narrowOverlayReveal)
  const { t } = useI18n()
  const overlay = useRef<HTMLDivElement>(null)
  const returnFocus = useRef<HTMLElement | null>(null)
  const focusOnReveal = useRef(false)

  const close = () => {
    if (overlay.current?.contains(document.activeElement) && returnFocus.current?.isConnected) {
      returnFocus.current.focus()
    }

    returnFocus.current = null
    focusOnReveal.current = false
    setReveal(null)
  }

  const closeRef = useRef(close)
  closeRef.current = close

  useLayoutEffect(() => {
    if (focusOnReveal.current && reveal) {
      focusOnReveal.current = false

      const target = overlay.current?.querySelector<HTMLElement>('[aria-current="page"]') ?? overlay.current?.querySelector<HTMLElement>('a[href], button:not(:disabled)')

      ;(target ?? overlay.current)?.focus()
    }
  }, [reveal])

  useEffect(() => () => closeRef.current(), [])

  // Own an Escape layer only while something is revealed, so Escape closes the
  // overlay only when it's the top layer (never under a dialog / edit mode).
  const revealActive = reveal !== null
  const escapePriority = returnFocus.current ? ESCAPE_PRIORITY.focusedNarrowOverlay : ESCAPE_PRIORITY.narrowOverlay
  useEffect(() => (revealActive ? pushEscapeLayer(escapePriority) : undefined), [revealActive, escapePriority])

  const inTree = useMemo(() => new Set(tree ? allPaneIds(tree) : []), [tree])

  const collapsibles = useMemo(
    () => panes.filter(p => paneChrome(p).collapsible && ((inTree.has(p.id) && !hiddenPanes.has(p.id)) || (reveal?.independentOfDock && reveal.id === p.id))),
    [panes, inTree, hiddenPanes, reveal]
  )

  const collapsiblesRef = useRef(collapsibles)
  collapsiblesRef.current = collapsibles
  const panesRef = useRef(panes)
  panesRef.current = panes

  useEffect(() => {
    if (reveal && !collapsibles.some(pane => pane.id === reveal.id)) {closeRef.current()}
  }, [collapsibles, reveal])

  // ⌘B / ⌘G's narrow branch dispatches the app's toggle-reveal event with the
  // REAL pane id — accept those via each contribution's revealAliases.
  // eslint-disable-next-line no-restricted-syntax -- The event handler records its invoking DOM focus, not a mirrored reactive value.
  useEffect(() => {
    if (!narrow) {
      closeRef.current()

      return
    }

    const onToggle = (event: Event) => {
      const detail = (event as CustomEvent<{ id?: string; mode?: 'close' | 'open' | 'toggle'; focus?: boolean }>).detail
      const id = detail?.id

      if (!id) {
        return
      }

      const candidates = detail.focus ? panesRef.current.filter(p => paneChrome(p).collapsible) : collapsiblesRef.current
      const match = candidates.find(p => p.id === id || paneChrome(p).revealAliases?.includes(id))

      if (!match) {
        return
      }

      // `open`/`close` are explicit intents (programmatic reveal, titlebar show);
      // `toggle` (default) is the ⌘B/⌘G flip.
      const mode = detail?.mode ?? 'toggle'
      const current = $narrowOverlayReveal.get()

      if (current?.id === match.id && (mode === 'close' || (mode === 'toggle' && current.pinned))) {
        closeRef.current()

        return
      }

      if (detail.focus && mode !== 'close') {
        // A replacement opened inside the rail keeps its surviving outer owner.
        if (!overlay.current?.contains(document.activeElement) || !returnFocus.current?.isConnected) {
          returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
        }

        focusOnReveal.current = true
      } else if (current?.id !== match.id && mode !== 'close') {
        returnFocus.current = null
        focusOnReveal.current = false
      }

      setReveal(current => {
        if (mode === 'open') {
          return { id: match.id, pinned: true, independentOfDock: detail.focus || (current?.id === match.id && current.independentOfDock) }
        }

        if (mode === 'close') {
          return current?.id === match.id ? null : current
        }

        return { id: match.id, pinned: true, independentOfDock: detail.focus || (current?.id === match.id && current.independentOfDock) }
      })
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (!$narrowOverlayReveal.get() || event.key !== 'Escape' || event.defaultPrevented || !isTopEscapeLayer(returnFocus.current ? ESCAPE_PRIORITY.focusedNarrowOverlay : ESCAPE_PRIORITY.narrowOverlay)) {
        return
      }

      event.preventDefault()
      closeRef.current()
    }

    window.addEventListener(PANE_TOGGLE_REVEAL_EVENT, onToggle)
    window.addEventListener('keydown', onKeyDown)

    return () => {
      window.removeEventListener(PANE_TOGGLE_REVEAL_EVENT, onToggle)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [narrow])

  if (!narrow || collapsibles.length === 0) {
    return null
  }

  const sideOf = (c: Contribution) => (paneChrome(c).placement === 'left' ? 'left' : 'right')
  const revealed = reveal ? collapsibles.find(p => p.id === reveal.id) : undefined
  const sides = [...new Set(collapsibles.map(sideOf))]

  // The revealed pane's ZONE-mates that also left the grid (the sessions zone
  // stacks SESSIONS | BOTS): the overlay mirrors the zone's tab strip so a
  // pane docked into a collapsed zone stays reachable on narrow viewports —
  // without this, only the zone's first pane ever surfaces again.
  const zonePanes = (() => {
    if (!revealed || !tree) {
      return [revealed].filter((p): p is Contribution => Boolean(p))
    }

    const zone = findGroupOfPane(tree, revealed.id)
    const mates = zone ? zone.panes.map(id => collapsibles.find(p => p.id === id)) : []
    const shown = mates.filter((p): p is Contribution => Boolean(p))

    return shown.length > 0 ? shown : [revealed]
  })()

  return (
    <>
      {/* Hover-intent strips on each edge that has a collapsed pane. */}
      {sides.map(side => (
        <div
          className={cn('absolute inset-y-0 z-30 w-1.5', side === 'left' ? 'left-0' : 'right-0')}
          key={side}
          onMouseEnter={() => {
            const first = collapsibles.find(p => sideOf(p) === side)

            if (first) {
              setReveal(current => (current?.pinned ? current : { id: first.id, pinned: false }))
            }
          }}
        />
      ))}

      {revealed && (
        <div
          className={cn(
            'absolute inset-y-0 z-40 flex flex-col overflow-hidden bg-(--ui-sidebar-surface-background) shadow-2xl',
            sideOf(revealed) === 'left'
              ? 'left-0 border-r border-(--ui-stroke-secondary)'
              : 'right-0 border-l border-(--ui-stroke-secondary)'
          )}
          // Floats OVER the layout, so under glass its surface must mask the
          // panes beneath it — a see-through overlay reads as text bleeding
          // through text. Contract: `[data-glass-opaque]` in styles.css.
          data-glass-opaque=""
          id={narrowOverlayId(revealed.id)}
          onMouseLeave={() => setReveal(current => (current?.pinned ? current : null))}
          // Match the pane's docked width (sessions ~237px, files its rail
          // width) instead of a fat fixed 20rem — capped for tiny screens.
          ref={overlay}
          style={{ width: `min(${(revealed.data as { width?: string } | undefined)?.width ?? '18rem'}, 85vw)` }}
          tabIndex={-1}
        >
          <div className="flex shrink-0 justify-end p-1">
            <Button aria-label={t.common.close} className="transition-colors focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-2 focus-visible:outline-(--ui-accent)" onClick={close} size="icon" variant="ghost"><Codicon name="close" /></Button>
          </div>
          {/* Zone-mates share the overlay through the zone's own tab strip
              (SESSIONS | BOTS) — a lone pane keeps the stripless form. */}
          {zonePanes.length > 1 && (
            <PaneTabStrip>
              {zonePanes.map(pane => (
                <PaneTab
                  active={pane.id === revealed.id}
                  aria-selected={pane.id === revealed.id}
                  data-narrow-overlay-tab={pane.id}
                  key={pane.id}
                  onPointerDown={event => {
                    if (event.button === 0) {
                      event.preventDefault()
                      setReveal(current => ({ id: pane.id, pinned: current?.pinned ?? false }))
                    }
                  }}
                >
                  <PaneTabLabel>{pane.title ?? pane.id}</PaneTabLabel>
                </PaneTab>
              ))}
            </PaneTabStrip>
          )}
          <ContribBoundary id={revealed.id}>
            {revealed.render && <ContribRender render={revealed.render} />}
          </ContribBoundary>
        </div>
      )}
    </>
  )
}
