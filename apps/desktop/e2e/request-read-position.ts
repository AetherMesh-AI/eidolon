export interface RequestReadPosition {
  x: number
  y: number
  workspace: { x: number; y: number }
  devicePixelRatio: number
}

/** Header reflow moves the scrollport. Native anchoring rounds content movement
 * to physical scroll pixels, so retain the local position within one pixel. */
export function requestReadPositionPreserved(before: RequestReadPosition, after: RequestReadPosition) {
  const values = [before.x, before.y, before.workspace.x, before.workspace.y, before.devicePixelRatio,
    after.x, after.y, after.workspace.x, after.workspace.y, after.devicePixelRatio]

  return values.every(Number.isFinite) && before.devicePixelRatio > 0 && before.devicePixelRatio === after.devicePixelRatio &&
    Math.abs((after.x - after.workspace.x) - (before.x - before.workspace.x)) * after.devicePixelRatio < 1 &&
    Math.abs((after.y - after.workspace.y) - (before.y - before.workspace.y)) * after.devicePixelRatio < 1
}
