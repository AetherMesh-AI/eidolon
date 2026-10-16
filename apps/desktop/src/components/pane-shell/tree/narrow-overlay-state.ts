import { atom } from 'nanostores'

export interface NarrowOverlayReveal {
  id: string
  pinned: boolean
  /** A deliberate header reveal can bypass dock visibility without persisting it. */
  independentOfDock?: boolean
}

// Ephemeral visibility is shared with the compact header; dock preferences
// remain owned by the layout tree and are never changed by a reveal.
export const $narrowOverlayReveal = atom<NarrowOverlayReveal | null>(null)

export function setNarrowOverlayReveal(next: NarrowOverlayReveal | null | ((current: NarrowOverlayReveal | null) => NarrowOverlayReveal | null)) {
  $narrowOverlayReveal.set(typeof next === 'function' ? next($narrowOverlayReveal.get()) : next)
}

export const narrowOverlayId = (paneId: string) => `narrow-overlay-${paneId}`
