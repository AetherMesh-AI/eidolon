/** Native Electron selection → authenticated RPC → durable bindings → full app restart.
 * Loopback inference deliberately parks planning; this proves selection, not execution. */
import type { Page } from '@playwright/test'

import {
  projectSelectionIntervention,
  projectSelectionTitle,
  setupCompletionFixture
} from './organization-completion-fixture'
import { expect, test } from './test'

let running: Awaited<ReturnType<typeof setupCompletionFixture>> | undefined
test.setTimeout(240_000)

function primary(page: Page) {
  return page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })
}

async function assertRetainedProjects(page: Page) {
  const bindings = page.getByRole('region', { name: 'Objective repositories', exact: true })
  await expect(bindings.getByRole('listitem')).toHaveCount(2)
  await expect(bindings.getByRole('listitem').filter({ hasText: 'frontend · root0 · frontend' })).toBeVisible()
  await expect(bindings.getByRole('listitem').filter({ hasText: 'backend · root1 · backend' })).toBeVisible()

  for (const id of ['frontend', 'backend']) {
    const execution = page.getByRole('region', { name: `Latest project test execution: ${id}`, exact: true })
    await expect(execution.getByText('No project test execution receipt recorded.', { exact: true })).toBeVisible()
    await expect(execution.getByRole('button', { name: 'Read exact test evidence', exact: true })).toHaveCount(0)
  }

  await expect(page.getByText('No accepted final result yet.', { exact: true })).toBeVisible()
}

// eslint-disable-next-line no-empty-pattern -- actual Electron lifecycle belongs to this scenario
test.afterEach(async ({}, testInfo) => {
  if (!running) {
    return
  }

  try {
    await testInfo.attach('project-selection-provider-context', {
      body: JSON.stringify({ stages: running.stages, errors: running.providerErrors }, null, 2),
      contentType: 'application/json'
    })

    if (!running.fixture.page.isClosed()) {
      await testInfo.attach('project-selection-native-ui', {
        body: await running.fixture.page.locator('body').ariaSnapshot(),
        contentType: 'text/plain'
      })
      await running.fixture.page.screenshot({ path: testInfo.outputPath('project-selection-final.png') })
    }
  } finally {
    await running.fixture.cleanup()
    running = undefined
  }
})

// eslint-disable-next-line no-empty-pattern -- actual Electron lifecycle belongs to this scenario
test('retains both owner-selected repository IDs across reconnect and full native restart', async ({}, testInfo) => {
  running = await setupCompletionFixture(false, { projectSelection: true })
  let { page } = running.fixture
  await primary(page).getByRole('link', { name: 'Command', exact: true }).click()
  await page.getByRole('textbox', { name: 'Objective', exact: true }).fill(projectSelectionTitle)
  await page.getByRole('combobox', { name: 'Delivery scope', exact: true }).selectOption('source_project')
  await page.getByRole('button', { name: 'Create objective', exact: true }).click()
  await expect(
    page.getByText('Select at least one repository for source-project delivery.', { exact: true })
  ).toBeVisible()
  expect(running.stages).toEqual([])

  for (const id of ['frontend', 'backend']) {
    await page.getByRole('checkbox', { name: new RegExp(`^${id} · root[01] · ${id}$`) }).check()
  }

  await page.getByRole('button', { name: 'Create objective', exact: true }).click()
  await expect(page.getByRole('heading', { name: projectSelectionTitle, exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Inspect request: request.plan', exact: true })).toContainText(
    projectSelectionIntervention,
    { timeout: 60_000 }
  )
  await assertRetainedProjects(page)
  expect(running.providerErrors).toEqual([])
  expect(running.stages.map(stage => stage.projectIds)).toEqual([['backend', 'frontend']])
  await page.screenshot({ path: testInfo.outputPath('project-selection-created.png') })

  await page.reload()
  await expect(page.getByRole('heading', { name: projectSelectionTitle, exact: true })).toBeVisible({ timeout: 60_000 })
  await assertRetainedProjects(page)

  await running.restart()
  page = running.fixture.page
  await primary(page).getByRole('link', { name: 'Objectives', exact: true }).click()
  const objective = page.getByRole('link').filter({ has: page.getByText(projectSelectionTitle, { exact: true }) })
  await expect(objective).toHaveCount(1)
  await objective.click()
  await expect(page.getByRole('heading', { name: projectSelectionTitle, exact: true })).toBeVisible()
  await assertRetainedProjects(page)
  expect(running.providerErrors).toEqual([])
  expect(running.stages.map(stage => stage.projectIds)).toEqual([['backend', 'frontend']])
})
