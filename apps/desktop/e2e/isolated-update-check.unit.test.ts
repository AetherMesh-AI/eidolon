import { createRequire } from 'node:module'
import { describe, expect, it, vi } from 'vitest'

const require = createRequire(import.meta.url)
const { installIsolatedUpdateCheck } = require('./isolated-update-check.cjs')

describe('isolated native updater bootstrap', () => {
  it('replaces only the initial read check before any renderer invokes it', async () => {
    const handlers = new Map<string, (...args: any[]) => unknown>()
    const ipc = { handle(channel: string, listener: (...args: any[]) => unknown) { handlers.set(channel, listener) } }
    const originalHandle = ipc.handle
    const state = installIsolatedUpdateCheck(ipc)
    const liveCheck = vi.fn(() => { throw new Error('Live release lookup must not run') })
    const apply = vi.fn(() => 'real apply')
    const other = vi.fn(() => 'other IPC')
    ipc.handle('other', other)
    ipc.handle('hermes:updates:apply', apply)
    ipc.handle('hermes:updates:check', liveCheck)
    expect(ipc.handle).toBe(originalHandle)
    for (const id of [1, 2, 1]) {
      expect(await handlers.get('hermes:updates:check')!({ sender: { id } })).toMatchObject({ supported: true, behind: 0, updateAvailable: false })
    }
    expect(liveCheck).not.toHaveBeenCalled()
    expect(state).toEqual({ registered: true, checks: 3, senders: [1, 2, 1] })
    expect(handlers.get('hermes:updates:apply')).toBe(apply)
    expect(handlers.get('other')).toBe(other)
    // Explicit notification scenarios still control their own check response.
    const updateAvailable = vi.fn(() => ({ updateAvailable: true }))
    ipc.handle('hermes:updates:check', updateAvailable)
    expect(handlers.get('hermes:updates:check')).toBe(updateAvailable)
  })
})
