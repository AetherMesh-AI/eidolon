/** Test-only compositor provenance. This function is serialized into Electron's main
 * process by Playwright: keep every runtime dependency inside its body. */
export async function captureCausalOwnerFrame(native: Electron.BrowserWindow, proof: {
  state: { rect: { left: number; right: number; top: number; bottom: number }; viewport: { width: number; height: number } }
  keyboard: { offset: number; width: number } | null
  marker: { id: string; left: number; top: number; colors: number[][] }
}) {
  const windowFocused = native.isFocused()
  const contentsFocused = native.webContents.isFocused()
  const frames: Array<{ sequence: number; size: { width: number; height: number }; dirtyRect: Electron.Rectangle; markerPresent: boolean }> = []
  let first: { encoded: string; sequence: number; size: { width: number; height: number }; dirtyRect: Electron.Rectangle; markerPresent: boolean } | null = null
  type Snapshot = { encoded: string; bitmap: Uint8Array; size: { width: number; height: number }; sequence: number; dirtyRect: Electron.Rectangle }
  let causal: Snapshot | null = null
  const deadline = Date.now() + 5_000
  let error: string | null = null
  let timer: ReturnType<typeof setTimeout> | undefined
  let mutationSettled = false
  let finished = false
  let finish: () => void = () => undefined
  const done = new Promise<void>(resolve => { finish = resolve })
  const stop = (reason?: string) => {
    if (finished) return
    finished = true
    error = reason ?? null
    finish()
  }
  try {
    timer = setTimeout(() => stop('Native owner-policy causal marker missing after 5 seconds'), 5_000)
    native.webContents.beginFrameSubscription(false, (image: Electron.NativeImage, dirtyRect: Electron.Rectangle) => {
      if (finished) return
      try {
        const size = image.getSize()
        const bitmap = image.toBitmap()
        const sx = size.width / proof.state.viewport.width
        const sy = size.height / proof.state.viewport.height
        // Three nonce-color tiles, sampled well inside each tile. Never inspect
        // outline pixels to decide which frame is the first causally fresh frame.
        const markerPresent = bitmap.length === size.width * size.height * 4 && proof.marker.colors.every((color, tile) =>
          [-1, 0, 1].every(delta => {
            const x = Math.floor((proof.marker.left + tile * 8 + 4) * sx) + delta
            const y = Math.floor((proof.marker.top + 4) * sy)
            if (x < 0 || y < 0 || x >= size.width || y >= size.height) return false
            const index = (y * size.width + x) * 4
            return [bitmap[index + 2], bitmap[index + 1], bitmap[index]].every((channel, i) => Math.abs(channel - color[i]) <= 2)
          }))
        const frame = { sequence: frames.length + 1, size, dirtyRect, markerPresent }
        frames.push(frame)
        if (!first) first = { ...frame, encoded: image.toPNG().toString('base64') }
        if (markerPresent) {
          // Preserve this image synchronously; subsequent frames cannot replace it.
          causal = { encoded: image.toPNG().toString('base64'), bitmap: Uint8Array.from(bitmap), size, sequence: frame.sequence, dirtyRect }
          stop()
        } else if (frames.length >= 120) {
          stop('Native owner-policy causal marker missing after 120 frames')
        }
      } catch (cause) { stop(String(cause)) }
    })
    // The subscription is installed BEFORE this renderer mutation. The marker
    // follows final keyboard focus and occupies only a disclosed empty corner.
    // invalidate() alone does not establish freshness for an onscreen window.
    const mutation = native.webContents.executeJavaScript(`(() => {
      const marker = ${JSON.stringify(proof.marker)};
      if (globalThis[marker.id]) { delete globalThis[marker.id]; return; }
      if (Date.now() >= ${deadline}) return;
      const node = document.createElement('div');
      node.id = marker.id;
      node.setAttribute('aria-hidden', 'true');
      node.style.cssText = 'all:initial;position:fixed;pointer-events:none;z-index:2147483647;width:24px;height:8px;overflow:hidden;left:' + marker.left + 'px;top:' + marker.top + 'px;';
      marker.colors.forEach((color, index) => {
        const tile = document.createElement('span');
        tile.style.cssText = 'all:initial;position:absolute;pointer-events:none;left:' + (index * 8) + 'px;top:0;width:8px;height:8px;background:rgb(' + color.join(',') + ');';
        node.appendChild(tile);
      });
      document.documentElement.appendChild(node);
    })()`).then(() => { mutationSettled = true }, cause => { mutationSettled = true; stop(String(cause)) })
    await Promise.race([mutation, done])
    await done
  } catch (cause) {
    stop(String(cause))
  } finally {
    clearTimeout(timer)
    try { native.webContents.endFrameSubscription() } catch (cause) { error = error ?? String(cause) }
    let cleanupTimer: ReturnType<typeof setTimeout> | undefined
    try {
      // Bound cleanup too. A cancellation tombstone prevents delayed injection
      // from adding a marker after cleanup, even if the renderer was stalled.
      const cleanup = native.webContents.executeJavaScript(`(() => {
        ${mutationSettled ? '' : `globalThis[${JSON.stringify(proof.marker.id)}] = true;`}
        document.getElementById(${JSON.stringify(proof.marker.id)})?.remove();
      })()`)
      await Promise.race([cleanup, new Promise<never>((_, reject) => {
        cleanupTimer = setTimeout(() => reject(new Error('renderer cleanup timed out after 1 second')), 1_000)
      })])
    } catch (cause) { error = error ?? `Causal marker cleanup failed: ${String(cause)}` }
    finally { clearTimeout(cleanupTimer) }
  }
  // Explicit casts account for assignments in the native callback.
  const presented = causal as Snapshot | null
  const firstFrame = first as { encoded: string; sequence: number; size: { width: number; height: number }; dirtyRect: Electron.Rectangle; markerPresent: boolean } | null
  if (!presented) return { error, first: firstFrame, frames, causal: null, windowFocused, contentsFocused }
  const { size, bitmap } = presented
  const samples: Array<{ side: string; rgb: number[][] }> = []
  if (proof.keyboard) {
    const { rect, viewport } = proof.state
    const { offset, width } = proof.keyboard
    const sx = size.width / viewport.width
    const sy = size.height / viewport.height
    const distance = offset + width / 2
    const points = [
      { side: 'left', x: rect.left - distance, y: (rect.top + rect.bottom) / 2, vertical: true },
      { side: 'right', x: rect.right + distance, y: (rect.top + rect.bottom) / 2, vertical: true },
      { side: 'top', x: (rect.left + rect.right) / 2, y: rect.top - distance, vertical: false },
      { side: 'bottom', x: (rect.left + rect.right) / 2, y: rect.bottom + distance, vertical: false }
    ]
    for (const point of points) {
      const rgb = [-1, 0, 1].map(delta => {
        const x = Math.floor(point.x * sx) + (point.vertical ? 0 : delta)
        const y = Math.floor(point.y * sy) + (point.vertical ? delta : 0)
        if (x < 0 || y < 0 || x >= size.width || y >= size.height) return []
        const index = (y * size.width + x) * 4
        // NativeImage bitmap is BGRA on the little-endian Linux acceptance runner.
        return [bitmap[index + 2], bitmap[index + 1], bitmap[index]]
      })
      samples.push({ side: point.side, rgb })
    }
  }

  return { error, first: firstFrame, frames, windowFocused, contentsFocused,
    causal: { encoded: presented.encoded, sequence: presented.sequence, size,
      bitmapBytes: bitmap.length, dirtyRect: presented.dirtyRect, samples } }
}
