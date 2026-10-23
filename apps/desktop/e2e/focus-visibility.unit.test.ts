import type { Locator } from '@playwright/test'
import { beforeEach, expect, it, vi } from 'vitest'

const { attach } = vi.hoisted(() => ({ attach: vi.fn() }))
vi.mock('./test', () => ({ expect, test: { info: () => ({ attach }) } }))

import { expectKeyboardFocusVisible } from './focus-visibility'

expect.extend({ toBeFocused: () => ({ pass: true, message: () => 'synthetic target is focused' }) })

const clear = {
  focused: true, clipping: [], clippingBounds: [], viewport: { width: 640, height: 360 },
  ring: { top: 260, bottom: 290 }, visible: true, style: 'solid', width: 3, offset: 3, contrast: 8
}
const clipped = {
  ...clear, clipping: ['synthetic scroller'], ring: { top: 285, bottom: 321 },
  clippingBounds: [{ top: 170, bottom: 295, scrollTop: 200, scrollHeight: 500, clientHeight: 125 }]
}

beforeEach(() => attach.mockReset())

it('retains the first failure even when the next frame is unclipped', async () => {
  const evaluate = vi.fn().mockResolvedValueOnce(clipped).mockResolvedValueOnce(true).mockResolvedValueOnce(clear)
  const target = { evaluate, focus: vi.fn(), scrollIntoViewIfNeeded: vi.fn() }
  await expect(expectKeyboardFocusVisible(target as unknown as Locator, '200% save')).rejects.toThrow('complete outline')
  const evidence = JSON.parse(attach.mock.calls[0][1].body)
  expect(evidence).toEqual({ label: '200% save', ...clipped })
  expect(JSON.parse(attach.mock.calls[1][1].body)).toEqual({ label: '200% save', frameObserved: true, afterFrame: clear })
  expect(target.focus).not.toHaveBeenCalled()
  expect(target.scrollIntoViewIfNeeded).not.toHaveBeenCalled()
  expect(evaluate).toHaveBeenCalledTimes(3)
})

it('preserves original failure when later inspection fails', async () => {
  const evaluate = vi.fn().mockResolvedValueOnce(clipped).mockRejectedValueOnce(new Error('synthetic detached target'))
  await expect(expectKeyboardFocusVisible({ evaluate } as unknown as Locator, '200% save')).rejects.toThrow('complete outline')
  expect(JSON.parse(attach.mock.calls[0][1].body)).toEqual({ label: '200% save', ...clipped })
  expect(attach).toHaveBeenCalledTimes(1)
  expect(attach.mock.calls[0][1].body).not.toContain('detached target')
})

it('does not add a frame or attachment to a successful check', async () => {
  const evaluate = vi.fn().mockResolvedValue(clear)
  await expect(expectKeyboardFocusVisible({ evaluate } as unknown as Locator, '200% save')).resolves.toEqual({ label: '200% save', ...clear })
  expect(evaluate).toHaveBeenCalledTimes(1)
  expect(attach).not.toHaveBeenCalled()
})

it('retains the failure without a second measurement when no frame arrives', async () => {
  const evaluate = vi.fn().mockResolvedValueOnce(clipped).mockResolvedValueOnce(false)
  await expect(expectKeyboardFocusVisible({ evaluate } as unknown as Locator, '200% save')).rejects.toThrow('complete outline')
  expect(evaluate).toHaveBeenCalledTimes(2)
  expect(JSON.parse(attach.mock.calls[1][1].body)).toEqual({ label: '200% save', frameObserved: false, afterFrame: null })
})

it('does not mask the original clipping assertion when the optional attachment fails', async () => {
  attach.mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('optional attachment unavailable'))
  const evaluate = vi.fn().mockResolvedValueOnce(clipped).mockResolvedValueOnce(true).mockResolvedValueOnce(clear)
  await expect(expectKeyboardFocusVisible({ evaluate } as unknown as Locator, '200% save')).rejects.toThrow('complete outline')
  expect(JSON.parse(attach.mock.calls[0][1].body)).toEqual({ label: '200% save', ...clipped })
})
