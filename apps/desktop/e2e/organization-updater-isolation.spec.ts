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
    remote = await startOrganizationSwitchGateway(sandbox, mockUrl)
    await page.evaluate(async ({ url, token }) => {
      const bridge = (window as unknown as { hermesDesktop: { connections: { save(input: unknown): Promise<{ ok: boolean }>; setLaunchMode(mode: 'last-used'): Promise<{ ok: boolean; registry: { launchMode: string } }> } } }).hermesDesktop

      if (!(await bridge.connections.save({ kind: 'remote', label: 'Updater fixture B', url, authMode: 'token', token })).ok) {
        throw new Error('Could not register isolated updater gateway')
      }

      // Reload normally restores the registry primary. This scenario explicitly
      // asks the existing preference to restore the source selected through UI.
      const launch = await bridge.connections.setLaunchMode('last-used')

      if (!launch.ok || launch.registry.launchMode !== 'last-used') {
        throw new Error('Could not configure isolated last-used startup')
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
    await expect(switcher).toContainText('Updater fixture B')
    await expect(page.locator('.eid-workspace-header [data-slot="connection-switcher"]')).toHaveAttribute('aria-busy', 'false')
    await expect.poll(() => remote!.updateChecks()).toBeGreaterThan(checksBeforeReload)
    const toast = page.getByRole('status').filter({ has: page.getByText('Update ready', { exact: true }) })
    await expect(toast).toHaveCount(0)

    // Introduce the release only after startup. Session initialization clears
    // notifications and invokes their dismissal callbacks, persisting a snooze.
    // Use the public command while its cached status is current, so it only
    // checks availability; never click an install action.
    const checkThroughMenu = async () => {
      // The connection control stays above notifications; Settings below is
      // deliberately tested for real toast interception later in this flow.
      await switcher.click({ button: 'right' })
      await page.getByRole('menuitem', { name: 'Update Eidolon', exact: true }).click()
    }

    await checkThroughMenu()
    const currentNotice = page.getByRole('dialog').filter({ has: page.getByText('The backend is running the latest version.', { exact: true }) })
    await expect(currentNotice).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(currentNotice).toBeHidden()
    const checksBeforeRelease = remote.updateChecks()
    remote.setUpdateBehind(1)
    await checkThroughMenu()
    await expect.poll(() => remote!.updateChecks()).toBeGreaterThan(checksBeforeRelease)
    await page.keyboard.press('Escape')
    await expect(toast).toBeVisible()
    expect(remote.updateChecks()).toBeGreaterThan(0)

    // The grouped header places Settings under this toast; the connection
    // switcher is above it. Verify real interception without forced clicks.
    const obstructedControl = page.locator('.eid-workspace-header').getByRole('button', { name: 'Settings', exact: true })
    const blocked = await obstructedControl.evaluate(element => {
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
    await obstructedControl.click({ trial: true })
    await switcher.click()
    await expect(page.getByRole('menuitemradio', { name: 'Updater fixture B', exact: true })).toBeVisible()
    await page.keyboard.press('Escape')
    expect(remote.starts()).toBe(0)
    expect(remote.updatePosts()).toBe(0)
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
        backendChecks: remote.updateChecks(), checksBeforeReload, checksBeforeRelease, releaseIntroducedAfterStartup: true, availabilityCheck: 'public Update Eidolon menu command from known-current status', launchMode: 'last-used', restoredSource: 'Updater fixture B', obstructedBeforeDismissal: blocked,
        obstructedControl: 'Settings', dismissal: 'public Dismiss notification button', sameControlReceivesPointerAfterDismissal: true, switcherOpenedAfterDismissal: true,
        schedulerStarts: remote.starts(), backendUpdatePosts: remote.updatePosts(), providerCalls: running.calls.length
      })
    })
  } catch (error) {
    const window = await running.fixture.app.browserWindow(running.fixture.page)
    await test.info().attach('backend-updater-before-cleanup', {
      contentType: 'image/png',
      body: Buffer.from(await window.evaluate(async window => (await window.webContents.capturePage()).toPNG().toString('base64')), 'base64')
    })
    await test.info().attach('backend-updater-failure-state', {
      contentType: 'application/json',
      body: JSON.stringify({
        sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
        sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
        backendChecks: remote?.updateChecks(),
        backendUpdatePosts: remote?.updatePosts(),
        snoozeUntil: await running.fixture.page.evaluate(() => localStorage.getItem('hermes:update-toast-snooze-until'))
      })
    })
    throw error
  } finally {
    await remote?.close()
    await running.fixture.cleanup()
  }
})
