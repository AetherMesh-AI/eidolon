import { useStore } from '@nanostores/react'
import { type CSSProperties, type PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from 'react'

import { cn } from '@/lib/utils'
import { $paneWidthOverride, setPaneWidthOverride } from '@/store/panes'

const SPLIT_MIN_LEFT_PX = 180
const SPLIT_MIN_RIGHT_PX = 320

/** Existing pane-store seam, shared by wide and responsive master/detail views. */
export function useMasterDetailSplit(resizeId?: string, enabled = true) {
  const gridRef = useRef<HTMLDivElement>(null)
  const stopRef = useRef<(() => void) | null>(null)
  const override = useStore($paneWidthOverride(resizeId ?? ''))
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    if (!enabled) {
      stopRef.current?.()
    }

    return () => stopRef.current?.()
  }, [enabled, resizeId])

  const startSplitDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    const grid = gridRef.current

    if (!enabled || !resizeId || !grid || event.button !== 0) {
      return
    }

    event.preventDefault()
    stopRef.current?.()
    const startX = event.clientX
    const startWidth = (grid.children[0] as HTMLElement).getBoundingClientRect().width
    const max = Math.max(SPLIT_MIN_LEFT_PX, grid.getBoundingClientRect().width - SPLIT_MIN_RIGHT_PX)
    const direction = getComputedStyle(grid).direction === 'rtl' ? -1 : 1
    setDragging(true)

    const onMove = (move: globalThis.PointerEvent) => {
      setPaneWidthOverride(
        resizeId,
        Math.round(Math.min(max, Math.max(SPLIT_MIN_LEFT_PX, startWidth + direction * (move.clientX - startX))))
      )
    }

    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      stopRef.current = null
      setDragging(false)
    }

    stopRef.current = onUp
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp, { once: true })
  }

  return {
    gridRef,
    dragging,
    startSplitDrag,
    splitStyle:
      override !== undefined
        ? ({ '--md-split': `clamp(180px, ${override}px, max(180px, calc(100% - 320px)))` } as CSSProperties)
        : undefined
  }
}

export function MasterDetailSash({
  dragging,
  onPointerDown,
  resizeId,
  className = 'hidden sm:block'
}: {
  dragging: boolean
  onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => void
  resizeId: string
  className?: string
}) {
  return (
    <div
      className={cn(
        'group/vsash absolute inset-y-0 start-0 z-10 w-1 -translate-x-1/2 cursor-col-resize rtl:translate-x-1/2',
        className
      )}
      data-master-detail-sash=""
      onDoubleClick={() => setPaneWidthOverride(resizeId, undefined)}
      onPointerDown={onPointerDown}
    >
      <div
        className={cn(
          'absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-colors',
          dragging ? 'bg-(--ui-stroke-secondary)' : 'group-hover/vsash:bg-(--ui-stroke-secondary)'
        )}
      />
    </div>
  )
}
