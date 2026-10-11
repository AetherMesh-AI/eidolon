import type { Page } from '@playwright/test'

import { expect } from './test'

/** Open the user-facing native disclosure without bypassing its click behavior. */
export async function openOrganizationDisclosure(
  page: Page,
  label: 'Configured capabilities' | 'Context' | 'Acceptance criteria' | 'Verification and priority'
) {
  if (label === 'Configured capabilities' && await page.locator('.eid-runtime-disclosure').count() === 0) {
    await page.getByRole('button', { name: /^System health:/ }).click()
  }
  const summary = page.locator('summary').filter({ hasText: new RegExp(`^${label}(?:$|\\s)`) })
  const details = summary.locator('..')

  if ((await details.getAttribute('open')) === null) {
    await summary.click()
  }

  await expect(details).toHaveAttribute('open', '')
}

/** Navigate through the retained operational route in the approved shell. */
export async function openOrganizationRequests(page: Page) {
  const navigation = page.getByRole('complementary', { name: 'Eidolon navigation' })
  const summary = navigation.getByText('Tools and configuration', { exact: true })
  if ((await summary.locator('..').getAttribute('open')) === null) { await summary.click() }
  await navigation.getByRole('link', { name: 'Needs You', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Attention inbox', exact: true })).toBeVisible()
}
