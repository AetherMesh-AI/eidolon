import type { Page } from '@playwright/test'

import { profileA, profileB } from './organization-dedicated-profile-fixture'
import { expect } from './test'

/** Test selection only; renderer activation still requires its separate build mode. */
export const directoryRecovery = process.env.EIDOLON_DIRECTORY_RECOVERY_ACCEPTANCE === '1'

export function directoryRecoveryDriver(page: Page) {
  const header = page.locator('.eid-workspace-header')
  const trigger = header.getByRole('button', { name: 'Organizations', exact: true })
  const dialog = page.getByRole('dialog', { name: 'Organizations', exact: true })
  const nav = page.getByRole('navigation', { name: 'Primary', exact: true })

  return {
    async rememberCurrent(profile: string) {
      await trigger.click()
      const refresh = dialog.getByRole('button', { name: 'Refresh current organization', exact: true })
      await refresh.click()
      const entry = dialog.getByRole('option').filter({ hasText: profile })
      await expect(entry).toContainText('Not configured')
      await expect(entry).toContainText('Verified now')
      await expect(entry).not.toContainText('Last known')
      await expect(refresh).toBeEnabled()
      await page.keyboard.press('Escape')
      await expect(trigger).toBeFocused()
    },
    async bootstrap() {
      for (const profile of [profileA, profileB, profileA]) {
        await page.evaluate(() => { location.hash = '#/' })
        await page.locator('[data-slot="profile-rail"]').getByRole('button', { name: profile, exact: true }).click()
        await nav.getByRole('link', { name: 'Home', exact: true }).click()
        await expect(header.getByRole('button', { name: new RegExp(profile) })).toBeVisible()
        await this.rememberCurrent(profile)
      }
    },
    async choose(profile: string) {
      await trigger.click()
      await dialog.getByRole('option').filter({ hasText: profile }).click()
      await dialog.getByRole('button', { name: 'Use selected organization', exact: true }).click()
      await expect(dialog.getByRole('region', { name: 'Confirm organization selection', exact: true })).toContainText(profile)
      await dialog.getByRole('button', { name: 'Confirm organization selection', exact: true }).click()
      await expect(dialog.getByRole('status')).toHaveText('Connection and profile selected. No organization is configured.')
      await expect(header.getByRole('button', { name: new RegExp(profile), includeHidden: true })).toBeVisible()
      await dialog.getByRole('button', { name: 'Close', exact: true }).click()
      await expect(dialog).toHaveCount(0)
    }
  }
}
