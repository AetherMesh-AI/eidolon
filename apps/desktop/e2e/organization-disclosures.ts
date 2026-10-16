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

/** Open the Work context before following its Objectives link. */
export async function openOrganizationObjectives(page: Page) {
  const navigation = page.getByRole('navigation', { name: 'Primary', exact: true })
  const objectives = navigation.getByRole('link', { name: 'Objectives', exact: true })
  if (await objectives.count() === 0) {
    await navigation.getByRole('link', { name: 'Work Overview', exact: true }).click()
  }
  await expect(objectives).toBeVisible()
  await objectives.click()
  await expect(page.getByRole('heading', { name: 'Objectives', exact: true })).toBeVisible()
}

/** Navigate through the primary owner-attention route. */
export async function openOrganizationRequests(page: Page) {
  const navigation = page.getByRole('complementary', { name: 'Eidolon navigation' })
  await navigation.getByRole('link', { name: 'Needs You', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Attention inbox', exact: true })).toBeVisible()
}
