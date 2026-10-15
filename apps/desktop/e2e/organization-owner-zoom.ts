/** Electron content bounds are device-independent pixels; renderer viewport is
 * CSS pixels after page zoom. devicePixelRatio is recorded, never multiplied
 * into content bounds (doing so would double-count a HiDPI display). */
export function ownerZoomGeometryMatches(geometry: {
  native: { width: number; height: number; factor: number }
  renderer: { width: number; height: number; devicePixelRatio: number }
}, percent: number) {
  const { native, renderer } = geometry
  return [native.width, native.height, native.factor, renderer.width, renderer.height,
    renderer.devicePixelRatio, percent].every(value => Number.isFinite(value) && value > 0)
    && Math.abs(native.factor - percent / 100) < .001
    && Math.abs(renderer.width - native.width / native.factor) <= 1
    && Math.abs(renderer.height - native.height / native.factor) <= 1
}
