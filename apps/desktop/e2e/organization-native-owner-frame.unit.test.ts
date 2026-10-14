import { runInNewContext } from 'node:vm'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { captureCausalOwnerFrame } from './organization-native-owner-frame'

const proof = {
  state: { rect: { left: 40, right: 60, top: 40, bottom: 60 }, viewport: { width: 100, height: 100 } },
  keyboard: { offset: 2, width: 2 },
  marker: { id: 'owner-frame-test-nonce', left: 4, top: 4, colors: [[81, 127, 203], [199, 43, 91], [53, 161, 211]] }
}
const outline = [70, 100, 150]
function fixture(options: { firstMarker?: boolean; freshRing?: boolean; noFresh?: boolean; noFrames?: boolean; scale?: number; frames?: number; delayedMutation?: boolean; cleanupError?: boolean; stalledMutation?: boolean; stalledCleanup?: boolean } = {}) {
  const events: string[] = []
  const nodes: Array<{ id: string; attributes: Record<string, string>; style: { cssText: string }; children: unknown[]; remove(): void }> = []
  let callback: (image: Electron.NativeImage, rect: Electron.Rectangle) => void = () => undefined
  let sequence = 0
  const makeImage = (marker: boolean, ring: boolean) => {
    const scale = options.scale ?? 1
    const size = { width: 100 * scale, height: 100 * scale }
    const bitmap = Buffer.alloc(size.width * size.height * 4)
    for (let index = 0; index < bitmap.length; index += 4) {
      bitmap[index] = ring ? outline[2] : 0
      bitmap[index + 1] = ring ? outline[1] : 0
      bitmap[index + 2] = ring ? outline[0] : 0
      bitmap[index + 3] = 255
    }
    if (marker) proof.marker.colors.forEach((color, tile) => {
      for (let x = (4 + tile * 8) * scale; x < (12 + tile * 8) * scale; x++) {
        for (let y = 4 * scale; y < 12 * scale; y++) {
          const offset = (y * size.width + x) * 4
          bitmap[offset] = color[2]; bitmap[offset + 1] = color[1]; bitmap[offset + 2] = color[0]
        }
      }
    })
    const id = ++sequence
    return { getSize: () => size, toBitmap: () => bitmap, toPNG: () => Buffer.from(`frame-${id}`) } as Electron.NativeImage
  }
  const emit = (marker: boolean, ring: boolean) => callback(makeImage(marker, ring), { x: 0, y: 0, width: 100, height: 100 })
  const document = {
    createElement: () => ({ id: '', attributes: {} as Record<string, string>, style: { cssText: '' }, children: [] as unknown[],
      setAttribute(key: string, value: string) { this.attributes[key] = value },
      appendChild(child: unknown) { this.children.push(child) }, remove() { events.push('remove') }
    }),
    documentElement: { appendChild(node: typeof nodes[number]) { nodes.push(node); events.push('mutation') } },
    getElementById: (id: string) => nodes.find(node => node.id === id)
  }
  const rendererContext = { document, Date }
  const native = {
    isFocused: () => true,
    webContents: {
      isFocused: () => true,
      beginFrameSubscription: (_dirty: boolean, cb: typeof callback) => {
        events.push('subscribe'); callback = cb
        if (!options.noFrames) emit(options.firstMarker ?? false, true)
      },
      endFrameSubscription: () => { events.push('unsubscribe') },
      executeJavaScript: async (script: string) => {
        if (options.stalledMutation && script.includes('createElement')) return new Promise(() => undefined)
        if (options.stalledCleanup && !script.includes('createElement')) return new Promise(() => undefined)
        if (options.delayedMutation && script.includes('createElement')) await new Promise(resolve => setTimeout(resolve, 5_001))
        if (options.cleanupError && !script.includes('createElement')) throw new Error('renderer cleanup unavailable')
        const result = runInNewContext(script, rendererContext)
        if (script.includes('createElement') && !options.noFresh) {
          for (let i = 0; i < (options.frames ?? 1); i++) emit(options.frames ? false : true, options.freshRing ?? true)
          emit(true, true)
        }
        return result
      }
    }
  } as unknown as Electron.BrowserWindow
  // Same serialized function used by Playwright, with no module closure.
  const capture = runInNewContext(`(${captureCausalOwnerFrame.toString()})`, { setTimeout, clearTimeout, Date }) as typeof captureCausalOwnerFrame
  return { capture: () => capture(native, proof), events, nodes }
}
afterEach(() => vi.useRealTimers())
describe('causal owner-policy native capture', () => {
  it.each([1, 2])('retains stale first frame and selects first nonce frame at scale %i', async scale => {
    const test = fixture({ scale }); const result = await test.capture()
    expect(result.error).toBeNull()
    expect(result.first?.encoded).toBe(Buffer.from('frame-1').toString('base64'))
    expect(result.first?.markerPresent).toBe(false)
    expect(result.frames.map(frame => frame.markerPresent)).toEqual([false, true])
    expect(result.causal?.sequence).toBe(2)
    expect(result.causal?.samples).toHaveLength(4)
    expect(result.causal?.samples.every(sample => sample.rgb.every(rgb => JSON.stringify(rgb) === JSON.stringify(outline)))).toBe(true)
    expect(test.events).toEqual(['subscribe', 'mutation', 'unsubscribe', 'remove'])
    expect(test.nodes[0].attributes).toEqual({ 'aria-hidden': 'true' })
    expect(test.nodes[0].style.cssText).toContain('pointer-events:none')
  })
  it('does not blindly discard a first callback that already bears the nonce', async () => {
    const result = await fixture({ firstMarker: true }).capture()
    expect(result.causal?.sequence).toBe(1)
    expect(result.frames).toHaveLength(1)
    expect(result.first?.encoded).toBe(result.causal?.encoded)
  })
  it('keeps a missing ring on first fresh frame as a strict four-edge failure', async () => {
    const result = await fixture({ freshRing: false }).capture()
    expect(result.causal?.sequence).toBe(2)
    expect(result.causal?.encoded).toBe(Buffer.from('frame-2').toString('base64'))
    expect(result.frames).toHaveLength(2)
    expect(result.causal?.samples.map(sample => sample.side)).toEqual(['left', 'right', 'top', 'bottom'])
    for (const sample of result.causal!.samples) {
      expect(sample.rgb.some(rgb => rgb.length === 3 && rgb.every((channel, index) => Math.abs(channel - outline[index]) <= 8))).toBe(false)
    }
  })
  it.each([false, true])('times out preserving available first evidence (no frames: %s)', async noFrames => {
    vi.useFakeTimers()
    const test = fixture({ noFresh: true, noFrames }); const pending = test.capture()
    await vi.advanceTimersByTimeAsync(5_000)
    const result = await pending
    expect(result.error).toMatch(/5 seconds/)
    expect(result.causal).toBeNull()
    expect(result.first === null).toBe(noFrames)
    expect(test.events.slice(-2)).toEqual(['unsubscribe', 'remove'])
  })
  it('does not insert a marker when renderer mutation arrives after the deadline', async () => {
    vi.useFakeTimers()
    const test = fixture({ delayedMutation: true, noFresh: true })
    const pending = test.capture()
    await vi.advanceTimersByTimeAsync(5_001)
    const result = await pending
    expect(result.error).toMatch(/5 seconds/)
    expect(result.first?.sequence).toBe(1)
    expect(test.nodes).toHaveLength(0)
    expect(test.events).toEqual(['subscribe', 'unsubscribe'])
  })
  it('returns first evidence within bounded time when renderer injection stalls', async () => {
    vi.useFakeTimers()
    const test = fixture({ stalledMutation: true })
    const pending = test.capture()
    await vi.advanceTimersByTimeAsync(5_000)
    const result = await pending
    expect(result.error).toMatch(/5 seconds/)
    expect(result.first?.sequence).toBe(1)
    expect(result.causal).toBeNull()
    expect(test.nodes).toHaveLength(0)
  })
  it('returns evidence when renderer cleanup stalls', async () => {
    vi.useFakeTimers()
    const pending = fixture({ stalledCleanup: true }).capture()
    await vi.advanceTimersByTimeAsync(1_000)
    const result = await pending
    expect(result.error).toMatch(/cleanup timed out/)
    expect(result.first?.sequence).toBe(1)
    expect(result.causal?.sequence).toBe(2)
  })
  it('retains callback evidence when cleanup reports an error', async () => {
    const result = await fixture({ cleanupError: true }).capture()
    expect(result.error).toMatch(/cleanup failed/)
    expect(result.first?.sequence).toBe(1)
    expect(result.causal?.sequence).toBe(2)
  })
  it('bounds missing-marker callbacks without selecting a later successful frame', async () => {
    const test = fixture({ frames: 120 }); const result = await test.capture()
    expect(result.error).toMatch(/120 frames/)
    expect(result.frames).toHaveLength(120)
    expect(result.causal).toBeNull()
    expect(result.first?.sequence).toBe(1)
    expect(test.events.slice(-2)).toEqual(['unsubscribe', 'remove'])
  })
})
