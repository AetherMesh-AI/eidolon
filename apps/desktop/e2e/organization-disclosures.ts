import type { Page } from '@playwright/test'

import { expect } from './test'

/** Open the user-facing native disclosure without bypassing its click behavior. */
export async function openOrganizationDisclosure(
  page: Page,
  label: 'Configured capabilities' | 'Context' | 'Acceptance criteria' | 'Verification and priority'
) {
  const summary = page.locator('summary').filter({ hasText: new RegExp(`^${label}(?:$|\\s)`) })
  const details = summary.locator('..')

  if ((await details.getAttribute('open')) === null) {
    await summary.click()
  }

  await expect(details).toHaveAttribute('open', '')
}
