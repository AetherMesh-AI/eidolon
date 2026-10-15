/** Fresh owner UI → authenticated gateway → localhost model → reviewed persistent roster. */
import { setupMockBackend, waitForAppReady, type MockBackendFixture } from './fixtures'
import { expect, test } from './test'

let fixture: MockBackendFixture | undefined
const team = {
  executive: { name: 'Ash', purpose: 'Coordinate the studio', responsibilities: ['Delegate and review outcomes'] },
  members: [
    { id: 'studio-lead', name: 'Ember', role: 'Manager', manager_id: 'executive', team: 'studio',
      capabilities: ['request.plan', 'request.integrate'], purpose: 'Plan studio work', responsibilities: ['Coordinate drafts'] },
    { id: 'studio-writer', name: 'Flint', role: 'Worker', manager_id: 'studio-lead', team: 'studio',
      capabilities: ['work.draft'], purpose: 'Write scoped drafts', responsibilities: ['Produce requested drafts'] }
  ]
}
test.setTimeout(180_000)
test.afterEach(async () => { await fixture?.cleanup(); fixture = undefined })

test('fresh home generates an editable named team and creates it only after explicit review', async () => {
  fixture = await setupMockBackend({ modelContextLength: 128000, mockServer: { starterTeamReply: JSON.stringify(team) } })
  await waitForAppReady(fixture)
  const { page, mock } = fixture
  const navigation = page.getByRole('complementary', { name: 'Eidolon navigation' }).getByRole('navigation', { name: 'Primary', exact: true })
  await navigation.getByRole('link', { name: 'Home', exact: true }).click()
  await page.locator('.eid-team-entry').getByRole('button', { name: 'Set up organization', exact: true }).click()
  const names = page.getByRole('dialog', { name: 'Organization and owner', exact: true })
  await names.getByLabel('Organization name', { exact: true }).fill('Starter Studio')
  await names.getByLabel('Your display name', { exact: true }).fill('Studio Owner')
  await names.getByRole('button', { name: 'Save names', exact: true }).click()
  await expect(names).toHaveCount(0)
  const dialog = page.getByRole('dialog', { name: 'Build your starter team', exact: true })
  await dialog.getByLabel('Jobs to handle', { exact: true }).fill('Writing and planning for a small studio')
  await dialog.getByLabel('Goals and outcomes', { exact: true }).fill('Deliver reviewed drafts')
  await dialog.getByLabel('Naming style', { exact: true }).fill('Use Ash, Ember and Flint')
  const before = mock.receivedPrompts.length
  await dialog.getByRole('button', { name: 'Generate team · Recommended', exact: true }).click()
  await expect(dialog.getByLabel('Name', { exact: true }).first()).toHaveValue('Ash', { timeout: 60_000 })
  expect(mock.receivedPrompts.length).toBe(before + 1)
  await dialog.getByLabel('Name', { exact: true }).first().fill('Ash Studio')
  await expect(dialog.getByRole('button', { name: 'Create team', exact: true })).toHaveCount(0)
  await dialog.getByRole('button', { name: 'Review changes', exact: true }).click()
  await expect(dialog.getByRole('button', { name: 'Create team', exact: true })).toBeEnabled()
  await dialog.screenshot({ path: test.info().outputPath('starter-team-reviewed.png') })
  await dialog.getByRole('button', { name: 'Create team', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Team saved', exact: true })).toBeVisible()
  expect(mock.receivedPrompts.length).toBe(before + 1)
  await page.reload()
  await navigation.getByRole('link', { name: 'Organization', exact: true }).click()
  await expect(page.getByText('Ash Studio', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Ember', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Flint', { exact: true }).first()).toBeVisible()
  expect(mock.receivedPrompts.length).toBe(before + 1)
})
