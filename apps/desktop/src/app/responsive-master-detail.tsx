import './responsive-master-detail.css'

import { Children, type ReactNode, useLayoutEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

interface ResponsiveMasterDetailProps {
  children: ReactNode
  className?: string
  detailOpen: boolean
  onDetailOpenChange: (open: boolean) => void
  variant?: 'messaging' | 'profiles' | 'import'
}

/** Visibility is presentation state: neither pane is remounted when space changes. */
export function ResponsiveMasterDetail({
  children,
  className,
  detailOpen,
  onDetailOpenChange,
  variant = 'messaging'
}: ResponsiveMasterDetailProps) {
  const { t } = useI18n()
  const rootRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const detailRef = useRef<HTMLDivElement>(null)
  const backRef = useRef<HTMLButtonElement>(null)
  const originRef = useRef<HTMLElement | null>(null)
  const [compact, setCompact] = useState(false)
  const [list, ...detail] = Children.toArray(children)

  useLayoutEffect(() => {
    const root = rootRef.current

    if (!root || typeof ResizeObserver === 'undefined') {
      return
    }

    const observer = new ResizeObserver(entries => {
      const width = entries[0]?.contentRect.width

      if (!width) {
        return
      }

      const narrow = width <= 760

      // A resize must never hide the editor/control the person is using.
      if (narrow && detailRef.current?.contains(document.activeElement)) {
        onDetailOpenChange(true)
      } else if (narrow && listRef.current?.contains(document.activeElement)) {
        onDetailOpenChange(false)
      }

      setCompact(narrow)
    })

    observer.observe(root)

    return () => observer.disconnect()
  }, [onDetailOpenChange])

  useLayoutEffect(() => {
    if (!compact) {
      if (document.activeElement === backRef.current) {
        const control = detailRef.current?.querySelector<HTMLElement>(
          'input:not(:disabled), textarea:not(:disabled), [contenteditable="true"]'
        )

        ;(control ?? detailRef.current)?.focus({ preventScroll: true })
      }

      return
    }

    if (detailOpen && listRef.current?.contains(document.activeElement)) {
      backRef.current?.focus({ preventScroll: true })
    } else if (!detailOpen && detailRef.current?.contains(document.activeElement)) {
      const origin = originRef.current
      const target = origin?.isConnected ? origin : listRef.current
      target?.focus({ preventScroll: true })
    }
  }, [compact, detailOpen])

  return (
    <div className={cn('eid-responsive-detail', className)} data-compact={compact} data-variant={variant} ref={rootRef}>
      <div
        className="eid-responsive-detail-list"
        data-pane="list"
        hidden={compact && detailOpen}
        inert={compact && detailOpen}
        onClickCapture={event => {
          const button = (event.target as HTMLElement).closest<HTMLElement>('button, [role="button"]')

          if (button) {
            originRef.current = button
          }
        }}
        onFocusCapture={event => {
          originRef.current = event.target
        }}
        ref={listRef}
        tabIndex={-1}
      >
        {list}
      </div>
      <div
        className="eid-responsive-detail-content"
        data-pane="detail"
        hidden={compact && !detailOpen}
        inert={compact && !detailOpen}
        ref={detailRef}
        tabIndex={-1}
      >
        <div className="eid-responsive-detail-back" hidden={!compact}>
          <Button onClick={() => onDetailOpenChange(false)} ref={backRef} variant="text">
            {t.common.back}
          </Button>
        </div>
        {detail}
      </div>
    </div>
  )
}
