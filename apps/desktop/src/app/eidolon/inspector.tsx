import { type ReactNode, useContext, useEffect, useRef } from 'react'

import { type WorkspaceFocusRestore, WorkspaceFocusRestoreContext } from './workspace-navigation'

const inspectorStack: { token: symbol; element: HTMLElement; origin: InspectorOrigin | null }[] = []

function available(element: HTMLElement) {
  return element.isConnected && !element.closest('[hidden], [inert]')
}

/** Capture only at the explicit opening action, before preview insertion can anchor scroll. */
export interface InspectorOrigin {
  readonly opener: HTMLElement
  readonly positions: readonly { readonly element: HTMLElement; readonly top: number; readonly left: number }[]
}

export function captureInspectorOrigin(opener: Element | null, options?: { replaceInspector: boolean }): InspectorOrigin | null {
  if (options?.replaceInspector) {
    const current = inspectorStack.findLast(entry => available(entry.element))

    if (opener && current?.element.parentElement?.contains(opener)) {
      // The replacement removes its immediate opener. Carry the current
      // Inspector’s durable origin and original offsets through that handoff.
      return current.origin && available(current.origin.opener) ? current.origin : null
    }
  }

  if (!(opener instanceof HTMLElement) || !available(opener) || opener === opener.ownerDocument.body || opener === opener.ownerDocument.documentElement) { return null }
  const positions: { element: HTMLElement; top: number; left: number }[] = []

  for (let element = opener.parentElement; element; element = element.parentElement) {
    positions.push({ element, top: element.scrollTop, left: element.scrollLeft })
  }

  return { opener, positions }
}

export type InspectorKind = 'agent' | 'objective' | 'task' | 'artifact' | 'terminal' | 'process' | 'event' | 'request'

/** Shared non-modal detail surface. Native resource surfaces can supply their
 * own contents without conflating runtime resources with prototype entities. */
export function Inspector({
  kind,
  title,
  onClose,
  restorationScope,
  focusReturnTarget,
  origin,
  children
}: {
  kind: InspectorKind
  title: string
  onClose: () => void
  /** Let validated origin restoration retain an already focused detail link. */
  restorationScope?: WorkspaceFocusRestore
  focusReturnTarget?: () => HTMLElement | null
  origin?: InspectorOrigin | null
  children: ReactNode
}) {
  const pendingRestoration = useContext(WorkspaceFocusRestoreContext)
  const initialRestoration = useRef({ pendingRestoration, restorationScope, origin })
  const returnTarget = useRef(focusReturnTarget)
  returnTarget.current = focusReturnTarget
  const identity = useRef(Symbol())
  const closeRef = useRef<HTMLButtonElement>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  useEffect(() => {
    const { pendingRestoration, restorationScope, origin } = initialRestoration.current
    const token = identity.current
    const element = closeRef.current!
    const inspector = element.parentElement!
    const captured = available(element) ? origin ?? captureInspectorOrigin(document.activeElement) : null
    inspectorStack.push({ token, element, origin: captured })
    const opener = captured?.opener ?? null
    const positions = captured?.positions ?? []

    const focused = document.activeElement

    const restoredChild = restorationScope && focused instanceof HTMLElement && available(focused) &&
      element.parentElement?.contains(focused)

    const pending = pendingRestoration?.current?.origin
    const restoring = restorationScope && pending?.owner === restorationScope.owner && pending.route === restorationScope.route

    if (available(element) && !restoredChild && !restoring) { element.focus() }

    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && inspectorStack.findLast(entry => available(entry.element))?.token === token) {
        event.preventDefault()
        onCloseRef.current()
      }
    }

    document.addEventListener('keydown', onKey)

    return () => {
      document.removeEventListener('keydown', onKey)
      const index = inspectorStack.findIndex(entry => entry.token === token)

      if (index >= 0) {
        inspectorStack.splice(index, 1)
      }

      const focus = document.activeElement

      // A restored inspector has no surviving original opener. Resolve only its
      // exact current row; never move focus away from a newer outside control.
      if ((returnTarget.current || origin) && focus !== document.body && !inspector.contains(focus)) { return }

      const target = returnTarget.current ? returnTarget.current() :
        opener && available(opener) && !inspector.contains(opener) ? opener : null

      if (target && available(target)) {
        // Focusing a tall row can recenter it. Return to the exact owner context
        // that opened these inline details, including nested scroll containers.
        target.focus({ preventScroll: true })
        positions.forEach(({ element, top, left }) => {
          if (element.isConnected && element.contains(target)) {
            element.scrollTop = top
            element.scrollLeft = left
          }
        })
      }
    }
  }, [])
  const label = `${kind.charAt(0).toUpperCase()}${kind.slice(1)} details`

  return (
    <aside aria-label={label} className="eid-inspector">
      <button aria-label={`Close ${kind} details`} onClick={onClose} ref={closeRef}>
        ×
      </button>
      <p className="eid-eyebrow">{kind}</p>
      <h2>{title}</h2>
      {children}
    </aside>
  )
}
