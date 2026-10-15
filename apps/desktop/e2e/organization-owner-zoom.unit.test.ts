import { describe, expect, it } from 'vitest'
import { ownerZoomGeometryMatches } from './organization-owner-zoom'
const native = { width: 1220, height: 800, factor: 2 }
describe('owner zoom geometry readiness', () => {
  it('rejects acknowledged native zoom while renderer layout still lags', () => {
    const frames = [
      { width: 1220, height: 800, devicePixelRatio: 1 },
      { width: 610, height: 800, devicePixelRatio: 2 },
      { width: 610, height: 400, devicePixelRatio: 2 }
    ]
    expect(frames.map(renderer => ownerZoomGeometryMatches({ native, renderer }, 200)))
      .toEqual([false, false, true])
  })
  it.each([1, 2, 3])('does not double-count display scale %i', scale => {
    expect(ownerZoomGeometryMatches({ native,
      renderer: { width: 610, height: 400, devicePixelRatio: 2 * scale } }, 200)).toBe(true)
  })
  it('rejects stale native zoom and invalid bounds', () => {
    const renderer = { width: 610, height: 400, devicePixelRatio: 2 }
    expect(ownerZoomGeometryMatches({ native: { ...native, factor: 1 }, renderer }, 200)).toBe(false)
    expect(ownerZoomGeometryMatches({ native: { ...native, width: 0 }, renderer }, 200)).toBe(false)
    expect(ownerZoomGeometryMatches({ native, renderer: { ...renderer, width: NaN } }, 200)).toBe(false)
  })
  it('allows viewport integer rounding at fractional zoom', () => {
    expect(ownerZoomGeometryMatches({ native: { ...native, factor: 1.5 },
      renderer: { width: 813, height: 533, devicePixelRatio: 1.5 } }, 150)).toBe(true)
  })
})
