import { type ReactNode, useEffect, useRef } from 'react'

export type InspectorKind = 'agent' | 'objective' | 'task' | 'artifact' | 'terminal' | 'process' | 'event'

/** Shared non-modal detail surface. Native resource surfaces can supply their
 * own contents without conflating runtime resources with prototype entities. */
export function Inspector({ kind, title, onClose, children }: {
  kind: InspectorKind; title: string; onClose: () => void; children: ReactNode
}) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    closeRef.current?.focus()

    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onCloseRef.current() }
    }

    document.addEventListener('keydown', onKey)

    return () => { document.removeEventListener('keydown', onKey);

 if (opener?.isConnected) {opener.focus()} }
  }, [])
  const label = `${kind.charAt(0).toUpperCase()}${kind.slice(1)} details`

  return <aside aria-label={label} className="eid-inspector">
    <button aria-label={`Close ${kind} details`} onClick={onClose} ref={closeRef}>×</button>
    <p className="eid-eyebrow">{kind}</p><h2>{title}</h2>{children}
  </aside>
}
