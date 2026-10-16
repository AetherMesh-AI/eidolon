import { act, cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { $paneWidthOverride, setPaneWidthOverride } from '@/store/panes'

import { MasterDetail } from './master-detail'
import { ResponsiveMasterDetail } from './responsive-master-detail'
import { observeMasterDetailWidth } from './responsive-master-detail.test-helpers'

const id = 'test-master-detail-seam'

afterEach(() => {
  cleanup()
  setPaneWidthOverride(id, undefined)
  vi.unstubAllGlobals()
})

function measure(grid: HTMLElement) {
  vi.spyOn(grid, 'getBoundingClientRect').mockReturnValue({ width: 900 } as DOMRect)
  vi.spyOn(grid.children[0], 'getBoundingClientRect').mockReturnValue({ width: 300 } as DOMRect)
}

function pointer(target: Element | Window, type: string, x: number) {
  fireEvent(target, new MouseEvent(type, { bubbles: true, button: 0, clientX: x }))
}

it('preserves the legacy seam store, clamps, RTL movement and double-click reset', () => {
  setPaneWidthOverride(id, 280)

  const { container } = render(
    <MasterDetail resizeId={id} split="wide">
      <aside>List</aside>
      <main>Detail</main>
    </MasterDetail>
  )

  const grid = container.querySelector<HTMLElement>('.grid')!
  const sash = container.querySelector('[data-master-detail-sash]')!
  measure(grid)
  expect(grid.style.getPropertyValue('--md-split')).toBe('clamp(180px, 280px, max(180px, calc(100% - 320px)))')
  pointer(sash, 'pointerdown', 200)
  pointer(window, 'pointermove', 350)
  expect($paneWidthOverride(id).get()).toBe(450)
  pointer(window, 'pointermove', 1000)
  expect($paneWidthOverride(id).get()).toBe(580)
  pointer(window, 'pointerup', 1000)
  grid.style.direction = 'rtl'
  pointer(sash, 'pointerdown', 200)
  pointer(window, 'pointermove', 400)
  expect($paneWidthOverride(id).get()).toBe(180)
  pointer(window, 'pointerup', 400)
  fireEvent.doubleClick(sash)
  expect($paneWidthOverride(id).get()).toBeUndefined()
  expect(grid.style.getPropertyValue('--md-split')).toBe('')
})

it('retains the saved wide split and ends active dragging when a container becomes compact', () => {
  const resize = observeMasterDetailWidth()
  const onCompactChange = vi.fn()
  setPaneWidthOverride(id, 280)

  const { container } = render(
    <ResponsiveMasterDetail
      detailOpen={false}
      onCompactChange={onCompactChange}
      onDetailOpenChange={vi.fn()}
      resizeId={id}
      variant="capabilities"
    >
      <aside>List</aside>
      <main>Detail</main>
    </ResponsiveMasterDetail>
  )

  const grid = container.querySelector<HTMLElement>('.eid-responsive-detail')!
  measure(grid)
  resize(900)
  pointer(container.querySelector('[data-master-detail-sash]')!, 'pointerdown', 200)
  pointer(window, 'pointermove', 250)
  expect($paneWidthOverride(id).get()).toBe(350)
  resize(400)
  expect(container.querySelector('[data-master-detail-sash]')).toBeNull()
  pointer(window, 'pointermove', 500)
  expect($paneWidthOverride(id).get()).toBe(350)
  resize(900)
  expect(onCompactChange).toHaveBeenCalledWith(true)
  expect(onCompactChange).toHaveBeenLastCalledWith(false)
  expect(grid.style.getPropertyValue('--md-split')).toBe('clamp(180px, 350px, max(180px, calc(100% - 320px)))')
  expect(container.querySelector('[data-master-detail-sash]')).not.toBeNull()
  act(() => setPaneWidthOverride(id, 1000))
  resize(800)
  expect($paneWidthOverride(id).get()).toBe(1000)
  expect(grid.style.getPropertyValue('--md-split')).toBe('clamp(180px, 1000px, max(180px, calc(100% - 320px)))')
})
