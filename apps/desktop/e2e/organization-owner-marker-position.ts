/** Renderer-serialized helper: exclude interactive content, not full-screen focus scopes. */
export function ownerMarkerPosition(targetRect: { left: number; top: number; right: number; bottom: number; width: number; height: number }) {
  // Dialog/focus-scope containers can have tabindex=-1 and cover the viewport.
  // Exclude the exact evidence target separately, with its focus-ring margin.
  const selector = 'input,select,button,textarea,a[href],label,[role=checkbox],[role=button],[role=link],[role=switch],[role=combobox],[role=slider],[contenteditable=true],[tabindex]:not([tabindex="-1"])'
  const controls = [...document.querySelectorAll(selector)]
    .filter(element => {
      const style = getComputedStyle(element)
      return style.display !== 'none' && style.visibility !== 'hidden'
    })
    .map(element => {
      const rect = element.getBoundingClientRect()
      // Include the existing safety margin BEFORE clipping: even a clipped
      // border box may have a visible outline inside its scroll container.
      const guarded = { left: rect.left - 16, right: rect.right + 16,
        top: rect.top - 16, bottom: rect.bottom + 16 }
      if (rect.width === 0 || rect.height === 0) return null
      for (let parent = element.parentElement; parent; parent = parent.parentElement) {
        const style = getComputedStyle(parent)
        const bounds = parent.getBoundingClientRect()
        // Border bounds conservatively over-exclude padding/scrollbars and
        // remain in the same transformed viewport coordinates as the control.
        if (/^(hidden|clip|auto|scroll)$/.test(style.overflowX)) {
          guarded.left = Math.max(guarded.left, bounds.left)
          guarded.right = Math.min(guarded.right, bounds.right)
        }
        if (/^(hidden|clip|auto|scroll)$/.test(style.overflowY)) {
          guarded.top = Math.max(guarded.top, bounds.top)
          guarded.bottom = Math.min(guarded.bottom, bounds.bottom)
        }
      }
      return guarded.right > guarded.left && guarded.bottom > guarded.top ? guarded : null
    }).filter((rect): rect is NonNullable<typeof rect> => rect !== null)
  // The evidence target is never clipped away, even for a generic container.
  controls.push({ left: targetRect.left - 16, right: targetRect.right + 16,
    top: targetRect.top - 16, bottom: targetRect.bottom + 16 })
  const candidates = [
    { left: 4, top: 4 }, { left: innerWidth - 28, top: 4 },
    { left: 4, top: innerHeight - 12 }, { left: innerWidth - 28, top: innerHeight - 12 }
  ]
  return candidates.find(({ left, top }) => left >= 0 && top >= 0 && controls.every(rect =>
    left + 24 < rect.left || left > rect.right ||
    top + 8 < rect.top || top > rect.bottom))
}
