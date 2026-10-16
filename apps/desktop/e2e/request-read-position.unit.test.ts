import { expect, it } from 'vitest'

import { type RequestReadPosition, requestReadPositionPreserved } from './request-read-position'

const position = (y: number, workspaceY = 95, devicePixelRatio = 1): RequestReadPosition => ({
  x: 38, y, workspace: { x: 0, y: workspaceY }, devicePixelRatio
})

it('accounts for the measured truthful header reflow and native anchoring quantization', () => {
  // Header +24px; status content +173.96875px; native scrollTop +173px.
  expect(requestReadPositionPreserved(position(332.953125), position(357.921875, 119))).toBe(true)
  // Loader removal -46.140625px; native scrollTop -47px, unchanged header.
  expect(requestReadPositionPreserved(position(333.765625), position(334.625))).toBe(true)
})

it('rejects a full physical pixel of local movement at native and zoomed scales', () => {
  expect(requestReadPositionPreserved(position(333), position(334))).toBe(false)
  expect(requestReadPositionPreserved(position(0, 0, 1.25), position(.8, 0, 1.25))).toBe(false)
  expect(requestReadPositionPreserved(position(0, 0, 1.25), position(.79, 0, 1.25))).toBe(true)
  expect(requestReadPositionPreserved(position(333, 95, 2), position(333.5, 95, 2))).toBe(false)
  expect(requestReadPositionPreserved(position(333, 95, 2), position(333.49, 95, 2))).toBe(true)
})

it('rejects horizontal drift, zoom changes and absolute positioning that loses local context', () => {
  expect(requestReadPositionPreserved(position(333), { ...position(333), x: 39 })).toBe(false)
  expect(requestReadPositionPreserved(position(333), position(333, 95, 2))).toBe(false)
  expect(requestReadPositionPreserved(position(333), position(333, 119))).toBe(false)
  expect(requestReadPositionPreserved(position(333), position(357, 119))).toBe(true)
})

it('rejects nonfinite geometry and invalid pixel scales', () => {
  for (const value of [NaN, Infinity, -Infinity]) {
    expect(requestReadPositionPreserved(position(value), position(333))).toBe(false)
    expect(requestReadPositionPreserved(position(333), position(333, value))).toBe(false)
    expect(requestReadPositionPreserved(position(333), { ...position(333), x: value })).toBe(false)
    expect(requestReadPositionPreserved(position(333, 95, value), position(333, 95, value))).toBe(false)
  }

  expect(requestReadPositionPreserved(position(333, 95, 0), position(333, 95, 0))).toBe(false)
  expect(requestReadPositionPreserved(position(333, 95, -1), position(333, 95, -1))).toBe(false)
})
