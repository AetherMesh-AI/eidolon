import { type NoProviderFixture, setupNoProvider, waitForAppReady } from './fixtures'
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

test('a backend release notification obstructs the header until explicitly dismissed', async () => {
  test.setTimeout(180_000)
  const { execFileSync } = await import('node:child_process')
  const { setupOwnerChatFixture } = await import('./organization-owner-chat-fixture')
  const { startOrganizationSwitchGateway } = await import('./organization-switch-fixture')
  const running = await setupOwnerChatFixture()
  let remote: Awaited<ReturnType<typeof startOrganizationSwitchGateway>> | undefined

  try {
    const { app, page, sandbox, mockUrl } = running.fixture
    remote = await startOrganizationSwitchGateway(sandbox, mockUrl, 1)
    await page.evaluate(async ({ url, token }) => {
      const bridge = (window as unknown as { hermesDesktop: { connections: { save(input: unknown): Promise<{ ok: boolean }> } } }).hermesDesktop

      if (!(await bridge.connections.save({ kind: 'remote', label: 'Updater fixture B', url, authMode: 'token', token })).ok) {
        throw new Error('Could not register isolated updater gateway')
      }
    }, { url: remote.url, token: remote.token })
    const switcher = page.locator('.eid-workspace-header [data-slot="connection-switcher"]').getByRole('button')
    await switcher.click()
    await page.getByRole('menuitemradio', { name: 'Updater fixture B', exact: true }).click()
    await expect(switcher).toContainText('Updater fixture B')
    await expect(page.locator('.eid-workspace-header [data-slot="connection-switcher"]')).toHaveAttribute('aria-busy', 'false')
    // A published label does not mean the renderer finished adopting the route.
    // Exercise startup on the now-selected source, rather than racing the
    // old renderer's mode-change check against connection retirement.
    const checksBeforeReload = remote.updateChecks()
    await page.reload()
    await waitForAppReady(running.fixture)
    await expect.poll(() => remote!.updateChecks()).toBeGreaterThan(checksBeforeReload)
    const toast = page.getByRole('status').filter({ has: page.getByText('Update ready', { exact: true }) })
    await expect(toast).toBeVisible()
    expect(remote.updateChecks()).toBeGreaterThan(0)

    // Reproduce the failed acceptance geometry without forced clicks or timeouts.
    const blocked = await switcher.evaluate(element => {
      const rect = element.getBoundingClientRect()
      const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)

      return Boolean(hit?.closest('[role="status"]')?.textContent?.includes('Update ready'))
    })

    expect(blocked).toBe(true)
    const obstructedWindow = await app.browserWindow(page)
    await test.info().attach('backend-update-obstruction', {
      contentType: 'image/png',
      body: Buffer.from(await obstructedWindow.evaluate(async window => (await window.webContents.capturePage()).toPNG().toString('base64')), 'base64')
    })
    await toast.getByRole('button', { name: 'Dismiss notification', exact: true }).click()
    await expect(toast).toBeHidden()
    await switcher.click()
    await expect(page.getByRole('menuitemradio', { name: 'Updater fixture B', exact: true })).toBeVisible()
    await page.keyboard.press('Escape')
    expect(remote.starts()).toBe(0)
    expect(running.calls).toHaveLength(0)
    const window = await app.browserWindow(page)
    await test.info().attach('backend-update-dismissed', {
      contentType: 'image/png',
      body: Buffer.from(await window.evaluate(async window => (await window.webContents.capturePage()).toPNG().toString('base64')), 'base64')
    })
    await test.info().attach('backend-updater-isolation-evidence', {
      contentType: 'application/json',
      body: JSON.stringify({
        sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
        sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
        backendChecks: remote.updateChecks(), checksBeforeReload, obstructedBeforeDismissal: blocked,
        dismissal: 'public Dismiss notification button', switcherOpenedAfterDismissal: true,
        schedulerStarts: remote.starts(), providerCalls: running.calls.length
      })
    })
  } finally {
    await remote?.close()
    await running.fixture.cleanup()
  }
})
