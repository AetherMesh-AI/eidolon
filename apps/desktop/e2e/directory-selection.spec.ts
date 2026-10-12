/** Only the explicit organization-directory-acceptance renderer build enables this door. */
import { execFileSync } from 'node:child_process'
import { join } from 'node:path'

import { buildAppEnv } from './fixtures'
import { setupOwnerChatFixture } from './organization-owner-chat-fixture'
import { fixtureLedger, startOrganizationSwitchGateway } from './organization-switch-fixture'
import { expect, test } from './test'

test('confirms actual directory selections, cancels without switching and exposes changed destination identity', async () => {
  test.setTimeout(240_000)
  const running = await setupOwnerChatFixture()
  let remote: Awaited<ReturnType<typeof startOrganizationSwitchGateway>> | undefined

  try {
    const { app, page, sandbox, mockUrl } = running.fixture
    remote = await startOrganizationSwitchGateway(sandbox, mockUrl)

    const local = await page.evaluate(
      async ({ url, token }) => {
        const bridge = (
          window as unknown as {
            hermesDesktop: {
              connections: {
                list(): Promise<{ connections: Array<{ kind: string; label: string }> }>
                save(input: unknown): Promise<{ ok: boolean }>
              }
            }
          }
        ).hermesDesktop

        const local = (await bridge.connections.list()).connections.find(item => item.kind === 'local')

        if (
          !local ||
          !(
            await bridge.connections.save({
              kind: 'remote',
              label: 'Directory fixture B',
              url,
              authMode: 'token',
              token
            })
          ).ok
        ) {
          throw new Error('Fixture registration failed')
        }

        return local.label
      },
      { url: remote.url, token: remote.token }
    )

    const switcher = page.locator('.eid-workspace-header [data-slot="connection-switcher"]').getByRole('button')
    const trigger = page.locator('.eid-workspace-header').getByRole('button', { name: 'Organizations', exact: true })
    const dialog = page.getByRole('dialog', { name: 'Organizations', exact: true })
    const captures: string[] = []

    const capture = async (name: string) => {
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

    async function initialize(name: string) {
      await page.getByRole('button', { name: 'Edit organization and owner names', exact: true }).click()
      const editor = page.getByRole('dialog', { name: 'Organization and owner', exact: true })
      await editor.getByLabel('Organization name', { exact: true }).fill(name)
      await editor.getByLabel('Your display name', { exact: true }).fill('Fixture Owner')
      await editor.getByRole('button', { name: 'Save names', exact: true }).click()
      await expect(editor).toHaveCount(0)
      await trigger.click()
      await dialog.getByRole('button', { name: 'Refresh current organization', exact: true }).click()
      await expect(dialog.getByRole('option').filter({ hasText: name })).toBeVisible()
      await page.keyboard.press('Escape')
      await expect(trigger).toBeFocused()
    }

    const prepare = async (name: string) => {
      await trigger.click()
      await dialog.getByRole('option').filter({ hasText: name }).click()
      await dialog.getByRole('button', { name: 'Use selected organization', exact: true }).click()
      await expect(dialog.getByRole('button', { name: 'Confirm organization selection', exact: true })).toBeVisible()
    }

    await initialize('Directory A')
    // Bootstrap the second visited record through the existing switcher only once.
    await switcher.click()
    await page.getByRole('menuitemradio', { name: 'Directory fixture B', exact: true }).click()
    await expect(switcher).toContainText('Directory fixture B')
    await trigger.click()
    await dialog.getByRole('button', { name: 'Refresh current organization', exact: true }).click()
    await expect(dialog.getByRole('option').filter({ hasText: 'Directory fixture B' })).toContainText('Not configured')
    await page.keyboard.press('Escape')
    const unconfiguredB = remote.ledger()
    await switcher.click()
    await page.getByRole('menuitemradio', { name: local, exact: true }).click()
    await expect(switcher).toContainText(local)
    await prepare('Directory fixture B')
    await expect(dialog.getByRole('region', { name: 'Confirm organization selection', exact: true })).toContainText(
      'Not configured'
    )
    await dialog.getByRole('button', { name: 'Confirm organization selection', exact: true }).click()
    await expect(dialog.getByRole('status')).toHaveText(
      'Connection and profile selected. No organization is configured.'
    )
    await expect(switcher).toContainText('Directory fixture B')
    await capture('directory-selected-unconfigured')
    await dialog.getByRole('button', { name: 'Close', exact: true }).click()
    expect(remote.ledger()).toBe(unconfiguredB)
    await initialize('Directory B')
    await switcher.click()
    await page.getByRole('menuitemradio', { name: local, exact: true }).click()
    await expect(switcher).toContainText(local)
    const beforeA = fixtureLedger(sandbox.hermesHome, buildAppEnv(sandbox))
    const beforeB = remote.ledger()

    await prepare('Directory B')
    await expect(switcher).toContainText(local)
    await capture('directory-confirmation-before-selection')
    await page.keyboard.press('Escape')
    await expect(dialog.getByRole('button', { name: 'Use selected organization', exact: true })).toBeFocused()
    await expect(switcher).toContainText(local)
    await page.keyboard.press('Escape')
    await expect(trigger).toBeFocused()
    expect(remote.ledger()).toBe(beforeB)

    for (const [name, label] of [
      ['Directory B', 'Directory fixture B'],
      ['Directory A', local]
    ]) {
      await prepare(name)
      await dialog.getByRole('button', { name: 'Confirm organization selection', exact: true }).click()
      await expect(dialog.getByRole('status')).toHaveText('Selected organization verified.')
      await expect(switcher).toContainText(label)
      await capture(`directory-selected-${name.endsWith('A') ? 'a' : 'b'}`)
      await dialog.getByRole('button', { name: 'Close', exact: true }).click()
    }

    expect(fixtureLedger(sandbox.hermesHome, buildAppEnv(sandbox))).toBe(beforeA)
    expect(remote.ledger()).toBe(beforeB)
    // Replace only the synthetic destination identity after caching its original ID.
    execFileSync('python3', [
      '-c',
      `import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as c:
 c.execute("UPDATE organization_metadata SET organization_id=?", ('33333333-3333-4333-8333-333333333333',))`,
      join(remote.home, 'organization', 'state.db')
    ])
    const replacedB = remote.ledger()
    await prepare('Directory B')
    await dialog.getByRole('button', { name: 'Confirm organization selection', exact: true }).click()
    await expect(dialog.getByRole('status')).toHaveText(
      'The selected organization could not be verified. The connection may have changed. Check the current organization before continuing.'
    )
    await expect(switcher).toContainText('Directory fixture B')
    await capture('directory-destination-identity-unverified')
    expect(remote.ledger()).toBe(replacedB)
    expect(remote.starts()).toBe(0)
    expect(running.calls).toHaveLength(0)
    await test.info().attach('directory-selection-evidence', {
      contentType: 'application/json',
      body: JSON.stringify({
        sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
        sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
        buildMode: 'organization-directory-acceptance',
        productionActivationEnabled: false,
        switchingDoor: 'actual Organizations directory with explicit confirmation',
        cancelledWithoutSwitch: true,
        cancelFocusRestored: true,
        bothDirectionsVerified: true,
        unconfiguredOutcome: 'selected-unconfigured',
        unconfiguredSelectionPreservedLedger: true,
        destinationIdentityMismatch: 'committed-unverified',
        ledgersUnchangedBySelection: true,
        schedulerStartsB: remote.starts(),
        providerCalls: running.calls.length,
        captures
      })
    })
  } finally {
    await remote?.close()
    await running.fixture.cleanup()
  }
})
