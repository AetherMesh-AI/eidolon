import { afterEach, describe, expect, it, vi } from 'vitest'

import { galleryPaintReadiness } from './workspace-paint-readiness'

afterEach(() => vi.unstubAllGlobals())

function documentState(overlays: number, animations: object[]) {
  const querySelectorAll = vi.fn(() => Array.from({ length: overlays }))
  vi.stubGlobal('document', { querySelectorAll, getAnimations: () => animations })
  return querySelectorAll
}
const animation = (endTime: number, playState = 'running', pending = false) => ({
  effect: { getComputedTiming: () => ({ endTime }) }, playState, pending
})

describe('native gallery paint readiness', () => {
  it('waits for an exiting startup overlay even when no animation is running', () => {
    const query = documentState(1, [])
    expect(galleryPaintReadiness()).toEqual({ connectingOverlays: 1, finiteAnimations: 0 })
    expect(query).toHaveBeenCalledWith('[data-glass-opaque][class*="--z-connecting"]')
  })
  it('counts running and pending finite transitions but ignores infinite decoration and finished animations', () => {
    documentState(0, [animation(520), animation(360, 'idle', true), animation(Infinity), animation(500, 'finished'), { effect: null, playState: 'idle' }])
    expect(galleryPaintReadiness()).toEqual({ connectingOverlays: 0, finiteAnimations: 2 })
  })
  it('becomes ready only when the overlay is gone and finite transitions have finished', () => {
    documentState(0, [animation(520, 'finished'), animation(Infinity)])
    expect(galleryPaintReadiness()).toEqual({ connectingOverlays: 0, finiteAnimations: 0 })
  })
})
