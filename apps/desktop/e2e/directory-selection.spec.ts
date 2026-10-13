/** Only the explicit organization-directory-acceptance renderer build enables this door. */
import { execFileSync } from 'node:child_process'
import { join } from 'node:path'

import { profileA, profileB, setupDedicatedProfiles } from './organization-dedicated-profile-fixture'
import { expect, test } from './test'

test('confirms actual directory selections, cancels without switching and exposes changed destination identity', async () => {
  test.setTimeout(240_000)
  const dedicated = await setupDedicatedProfiles()
  const { app, page, mock } = dedicated.fixture
  const captures: string[] = []
  const header = page.locator('.eid-workspace-header')
  const trigger = header.getByRole('button', { name: /^(?:Unnamed organization|Directory A|Directory B) · Organizations$/ })
  const dialog = page.getByRole('dialog', { name: 'Organizations', exact: true })

  const capture = async (name: string) => {
    // DOM assertions can precede Electron's painted frame. Use the same paint
    // barrier as the visual suite before capturing the native content surface.
    await page.evaluate(async () => {
      await document.fonts.ready
      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
    })
    const window = await app.browserWindow(page)
    await test.info().attach(name, {
      contentType: 'image/png',
      body: Buffer.from(
        await window.evaluate(async window => (await window.webContents.capturePage()).toPNG().toString('base64')),
        'base64'
      )
    })
    captures.push(name)
  }

  const expectProfile = async (profile: string) => {
    // Modal dialogs hide the background from the accessibility tree. This is
    // a read-only visual assertion; never interact with the hidden background.
    await expect(header.getByRole('button', { name: new RegExp(profile), includeHidden: true })).toBeVisible()
  }

  // Bootstrap visited records through the existing profile rail. Every selection
  // under test below uses the actual directory and explicit confirmation.
  const visit = async (profile: string) => {
    await page.evaluate(() => {
      location.hash = '#/'
    })
    await page.locator('[data-slot="profile-rail"]').getByRole('button', { name: profile, exact: true }).click()
    await page
      .getByRole('navigation', { name: 'Primary', exact: true })
      .getByRole('link', { name: 'Home', exact: true })
      .click()
    await expectProfile(profile)
    await expect(trigger).toBeEnabled()
  }

  const refresh = async (profile: string, expectedName: string) => {
    await trigger.click()
    const refreshButton = dialog.getByRole('button', { name: 'Refresh current organization', exact: true })
    await refreshButton.click()
    const entry = dialog.getByRole('option').filter({ hasText: profile })
    // The profile label may already identify a stale unconfigured record.
    // Wait for the saved metadata and completed fresh read before leaving it.
    await expect(entry).toContainText(expectedName)
    await expect(entry).toContainText('Verified now')
    await expect(entry).not.toContainText('Last known')
    await expect(refreshButton).toBeEnabled()
    await page.keyboard.press('Escape')
    await expect(trigger).toBeFocused()
    await expect(trigger).toHaveAccessibleName(`${expectedName === 'Not configured' ? 'Unnamed organization' : expectedName} · Organizations`)
  }

  const initialize = async (name: string, profile: string) => {
    await page.getByRole('button', { name: 'Edit organization and owner names', exact: true }).click()
    const editor = page.getByRole('dialog', { name: 'Organization and owner', exact: true })
    await editor.getByLabel('Organization name', { exact: true }).fill(name)
    await editor.getByLabel('Your display name', { exact: true }).fill('Fixture Owner')
    await editor.getByRole('button', { name: 'Save names', exact: true }).click()
    await expect(editor).toHaveCount(0)
    await refresh(profile, name)
  }

  const prepare = async (profile: string) => {
    await trigger.click()
    await dialog.getByRole('option').filter({ hasText: profile }).click()
    await dialog.getByRole('button', { name: 'Use selected organization', exact: true }).click()
    await expect(dialog.getByRole('button', { name: 'Confirm organization selection', exact: true })).toBeVisible()
  }

  try {
    await dedicated.seedQueuedWork()
    await visit(profileA)
    await initialize('Directory A', profileA)
    await visit(profileB)
    await refresh(profileB, 'Not configured')
    const unconfiguredB = dedicated.ledger(1)
    await visit(profileA)
    await prepare(profileB)
    await expect(dialog.getByRole('region', { name: 'Confirm organization selection', exact: true })).toContainText(
      'Not configured'
    )
    await dialog.getByRole('button', { name: 'Confirm organization selection', exact: true }).click()
    await expect(dialog.getByRole('status')).toHaveText(
      'Connection and profile selected. No organization is configured.'
    )
    await expectProfile(profileB)
    await capture('directory-selected-unconfigured')
    await dialog.getByRole('button', { name: 'Close', exact: true }).click()
    expect(dedicated.ledger(1)).toBe(unconfiguredB)
    await initialize('Directory B', profileB)
    await visit(profileA)
    const beforeA = dedicated.ledger(0)
    const beforeB = dedicated.ledger(1)

    await prepare(profileB)
    await expectProfile(profileA)
    await capture('directory-confirmation-before-selection')
    await page.keyboard.press('Escape')
    await expect(dialog.getByRole('button', { name: 'Use selected organization', exact: true })).toBeFocused()
    await expectProfile(profileA)
    await page.keyboard.press('Escape')
    await expect(trigger).toBeFocused()
    expect(dedicated.ledger(0)).toBe(beforeA)
    expect(dedicated.ledger(1)).toBe(beforeB)

    for (const profile of [profileB, profileA]) {
      await prepare(profile)
      await dialog.getByRole('button', { name: 'Confirm organization selection', exact: true }).click()
      await expect(dialog.getByRole('status')).toHaveText('Selected organization verified.')
      await expectProfile(profile)
      await capture(`directory-selected-${profile === profileA ? 'a' : 'b'}`)
      await dialog.getByRole('button', { name: 'Close', exact: true }).click()
    }

    expect(dedicated.ledger(0)).toBe(beforeA)
    expect(dedicated.ledger(1)).toBe(beforeB)
    // Replace only the synthetic destination identity after caching its original ID.
    execFileSync('python3', [
      '-c',
      `import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as c:
 c.execute("UPDATE organization_metadata SET organization_id=?", ('33333333-3333-4333-8333-333333333333',))`,
      join(dedicated.homes[1], 'organization', 'state.db')
    ])
    const replacedB = dedicated.ledger(1)
    await prepare(profileB)
    await dialog.getByRole('button', { name: 'Confirm organization selection', exact: true }).click()
    await expect(dialog.getByRole('status')).toHaveText(
      'The selected organization could not be verified. The connection may have changed. Check the current organization before continuing.'
    )
    await expectProfile(profileB)
    await capture('directory-destination-identity-unverified')
    expect(dedicated.ledger(1)).toBe(replacedB)
    expect(dedicated.starts(0)).toBe(0)
    expect(dedicated.starts(1)).toBe(0)
    expect(mock.receivedPrompts).toHaveLength(0)
    await test.info().attach('directory-selection-evidence', {
      contentType: 'application/json',
      body: JSON.stringify({
        sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
        sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
        buildMode: 'organization-directory-acceptance',
        productionActivationEnabled: false,
        switchingDoor: 'actual Organizations directory with explicit confirmation',
        topology: 'two dedicated local profiles on This device',
        routes: dedicated.routes,
        cancelledWithoutSwitch: true,
        cancelFocusRestored: true,
        bothDirectionsVerified: true,
        unconfiguredOutcome: 'selected-unconfigured',
        unconfiguredSelectionPreservedLedger: true,
        destinationIdentityMismatch: 'committed-unverified',
        ledgersUnchangedBySelection: true,
        schedulerStartsA: dedicated.starts(0),
        schedulerStartsB: dedicated.starts(1),
        providerCalls: mock.receivedPrompts.length,
        captures
      })
    })
  } catch (error) {
    await capture('directory-failure-before-cleanup')
    throw error
  } finally {
    await dedicated.fixture.cleanup()
  }
})
