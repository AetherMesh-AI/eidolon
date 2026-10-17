/** Home keeps resolving its workspace after native focus, as before. */
export function queueHomeKeyboardReveal(intake: HTMLElement): () => void {
  return queueKeyboardReveal(intake)
}

/** One-shot completion of native Tab scrolling, including the control's outline. */
export function queueKeyboardReveal(intake: HTMLElement, scroller?: HTMLElement): () => void {
  const document = intake.ownerDocument
  const window = document.defaultView

  if (!window) {return () => {}}
  let pending = true
  let target: HTMLElement | null = null
  let frame = 0

  const cancel = () => {
    pending = false
    window.cancelAnimationFrame(frame)
    document.removeEventListener('focusin', onFocus, true)

    for (const type of ['keydown', 'pointerdown', 'wheel', 'touchstart', 'input']) {
      document.removeEventListener(type, cancel, true)
    }
  }

  const onFocus = (event: FocusEvent) => {
    if (target || !(event.target instanceof HTMLElement) || !intake.contains(event.target)) {
      cancel()

      return
    }

    target = event.target
  }

  document.addEventListener('focusin', onFocus, true)

  for (const type of ['keydown', 'pointerdown', 'wheel', 'touchstart', 'input']) {
    document.addEventListener(type, cancel, true)
  }

  frame = window.requestAnimationFrame(() => {
    const active = pending && target && intake.isConnected && target.isConnected &&
      document.activeElement === target && intake.contains(target) ? target : null

    cancel()

    if (!active || !active.matches('button, a, input, textarea, select, summary')) {return}
    const workspace = scroller ?? intake.closest<HTMLElement>('.eid-workspace')

    if (!workspace || !workspace.isConnected || !workspace.contains(active)) {return}
    const style = window.getComputedStyle(active)

    const outline = Math.max(0, Number.parseFloat(style.outlineWidth) || 0) +
      Math.max(0, Number.parseFloat(style.outlineOffset) || 0) + 1

    const box = active.getBoundingClientRect()
    const scrollport = workspace.getBoundingClientRect()
    const top = scrollport.top + workspace.clientTop
    const bottom = top + workspace.clientHeight

    // Oversized editors retain native caret navigation rather than oscillating.
    if (box.height + outline * 2 > workspace.clientHeight) {return}

    const delta = box.top - outline < top ? box.top - outline - top :
      box.bottom + outline > bottom ? box.bottom + outline - bottom : 0

    if (delta) {workspace.scrollTop += delta}
  })

  return cancel
}
