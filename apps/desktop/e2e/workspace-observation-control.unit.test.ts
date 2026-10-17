import { expect, it, vi } from 'vitest'

import { createGalleryObservationControl } from './workspace-observation-control'

it('holds only requested replies, releases each once, and discards abandoned observations', () => {
  const control = createGalleryObservationControl()
  const immediate = vi.fn()
  const first = vi.fn()
  const second = vi.fn()
  const abandoned = vi.fn()
  const closed = vi.fn()
  control.respond(immediate)
  expect(immediate).toHaveBeenCalledOnce()
  control.pause()
  control.respond(first)
  control.respond(second)
  expect(control.pending).toBe(2)
  expect(first).not.toHaveBeenCalled()
  expect(second).not.toHaveBeenCalled()
  control.release()
  control.release()
  expect(control.pending).toBe(0)
  expect(first).toHaveBeenCalledOnce()
  expect(second).toHaveBeenCalledOnce()
  let connected = true
  control.pause()
  control.respond(closed, () => connected)
  expect(control.pending).toBe(1)
  connected = false
  expect(control.pending).toBe(0)
  control.release()
  expect(closed).not.toHaveBeenCalled()
  control.pause()
  control.respond(abandoned)
  control.discard()
  control.release()
  expect(control.pending).toBe(0)
  expect(abandoned).not.toHaveBeenCalled()
})
