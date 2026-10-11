import { setupNoProvider, type NoProviderFixture } from './fixtures'
import { expect, test } from './test'

type UpdateEvidence = {
  registered: boolean
  checks: number
  senders: number[]
  windows: { id: number; registeredBeforeCreation: boolean }[]
}

test('isolates startup, reload and new-window update checks in fresh profiles', async () => {
  test.setTimeout(180_000)
  for (let profile = 0; profile < 2; profile += 1) {
    let fixture: NoProviderFixture | undefined
    try {
      fixture = await setupNoProvider()
      const { app, page } = fixture
      const evidence = () => app.evaluate(() => (globalThis as typeof globalThis & { __eidolonUpdateFixture: UpdateEvidence }).__eidolonUpdateFixture)
      await expect.poll(async () => (await evidence()).checks).toBeGreaterThan(0)
      const startup = await evidence()
      expect(startup.registered).toBe(true)
      expect(startup.windows.every(window => window.registeredBeforeCreation)).toBe(true)
      expect(startup.senders).toContain(startup.windows[0].id)
      await page.reload()
      await expect.poll(async () => (await evidence()).checks).toBeGreaterThan(startup.checks)
      const reloaded = await evidence()
      await page.evaluate(() => (window as unknown as { hermesDesktop: { openWindow(): Promise<{ ok: boolean }> } }).hermesDesktop.openWindow())
      await expect.poll(async () => new Set((await evidence()).senders).size).toBeGreaterThan(1)
      const final = await evidence()
      expect(final.checks).toBeGreaterThan(reloaded.checks)
      expect(final.windows.every(window => window.registeredBeforeCreation)).toBe(true)
      await expect(page.getByRole('status').filter({ has: page.getByText('Update ready', { exact: true }) })).toHaveCount(0)
      await test.info().attach(`isolated-updater-profile-${profile}`, { contentType: 'application/json', body: JSON.stringify(final) })
    } finally {
      await fixture?.cleanup()
    }
  }
})
