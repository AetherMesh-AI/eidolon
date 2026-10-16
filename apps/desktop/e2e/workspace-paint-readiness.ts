/** Runs in the renderer; reports readiness without changing animation state. */
export function galleryPaintReadiness() {
  return {
    connectingOverlays: document.querySelectorAll('[data-glass-opaque][class*="--z-connecting"]').length,
    finiteAnimations: document.getAnimations().filter(animation => {
      const endTime = animation.effect?.getComputedTiming().endTime
      return typeof endTime === 'number' && Number.isFinite(endTime) &&
        (animation.pending || animation.playState === 'running')
    }).length
  }
}
