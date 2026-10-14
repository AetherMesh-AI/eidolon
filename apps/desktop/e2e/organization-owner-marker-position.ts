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
    .map(element => element.getBoundingClientRect().toJSON())
  controls.push(targetRect)
  const candidates = [
    { left: 4, top: 4 }, { left: innerWidth - 28, top: 4 },
    { left: 4, top: innerHeight - 12 }, { left: innerWidth - 28, top: innerHeight - 12 }
  ]
  return candidates.find(({ left, top }) => left >= 0 && top >= 0 && controls.every(rect =>
    rect.width === 0 || rect.height === 0 || left + 24 < rect.left - 16 || left > rect.right + 16 ||
    top + 8 < rect.top - 16 || top > rect.bottom + 16))
}
