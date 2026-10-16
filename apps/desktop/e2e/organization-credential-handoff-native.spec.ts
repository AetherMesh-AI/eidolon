/** Native owner input → real live Worker → exact broker replacement, synthetic only. */
import { setupHandoffNativeFixture } from './organization-credential-handoff-native-fixture'
import { expect, test } from './test'

let running: Awaited<ReturnType<typeof setupHandoffNativeFixture>> | undefined
test.setTimeout(240_000)
test.afterEach(async () => { await running?.fixture.cleanup(); running = undefined })

// eslint-disable-next-line no-empty-pattern -- owns the isolated backend and model lifecycle
test('authorizes one live Worker replacement without exposing a value or replaying a native write', async ({}, info) => {
  running = await setupHandoffNativeFixture()
  const { page } = running.fixture
  await running.start()
  await page.evaluate(() => { location.hash = '/settings?tab=organization' })
  const panel = page.getByRole('region', { name: 'Agent value replacement', exact: true })
  await panel.getByRole('button', { name: 'Prepare agent replacement', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Prepare agent replacement', exact: true })
  await dialog.getByRole('combobox', { name: 'Credential entry', exact: true }).click()
  await page.getByRole('option', { name: /synthetic_workerHandoff_token/ }).click()
  await dialog.getByRole('combobox', { name: 'Running worker', exact: true }).click()
  await page.getByRole('option', { name: new RegExp(running.seed.requestId) }).click()
  await dialog.getByLabel('New value', { exact: true }).fill('SYNTHETIC_NATIVE_HANDOFF_d9')
  const submit = dialog.getByRole('button', { name: 'Authorize replacement', exact: true })
  await expect(submit).toBeDisabled()
  await dialog.getByRole('checkbox', { name: 'I reviewed this entry, worker, request, generation, and grant revision.', exact: true }).check()
  await submit.click()
  await expect(dialog).toHaveCount(0)
  await expect(panel).toContainText('Authorization recorded. Replacement has not been confirmed.')
  const prepared = running.ledger()
  expect(prepared.entries[0].version).toBe(0)
  expect(prepared.receipts.filter(receipt => receipt.action === 'prepare_replacement')).toHaveLength(1)
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain('SYNTHETIC_')
  await expect(panel).not.toContainText('SYNTHETIC_')

  running.model.mock.releaseHeldStream()
  await expect.poll(() => running!.ledger().request.status, { timeout: 30_000 }).toBe('completed')
  await panel.getByRole('button', { name: 'Check replacement status', exact: true }).click()
  await expect(panel).toContainText('Agent replacement committed.')
  const final = running.ledger()
  expect(final.entries[0].version).toBe(1)
  expect(final.request.execution_count).toBe(1)
  expect(final.modelCalls).toBe(5)
  expect(final.receipts.filter(receipt => receipt.action === 'replace_value')).toHaveLength(1)
  expect(running.model.toolResults[1]).toEqual({ success: false, reason: 'handoff_unavailable' })
  expect(running.model.toolResults[2].success).toBe(true)
  expect(running.model.toolResults[3]).toEqual(running.model.toolResults[2])
  expect(running.model.requests.join('')).not.toContain('SYNTHETIC_NATIVE_HANDOFF_d9')
  expect(running.model.requests.join('')).not.toContain('inputHandle')
  expect(running.databaseText()).not.toContain('SYNTHETIC_NATIVE_HANDOFF_d9')
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain('SYNTHETIC_')
  await page.screenshot({ path: info.outputPath('credential-agent-handoff.png') })
  await panel.getByRole('button', { name: 'Prepare agent replacement', exact: true }).click()
  const retired = page.getByRole('dialog', { name: 'Prepare agent replacement', exact: true })
  await retired.getByRole('combobox', { name: 'Credential entry', exact: true }).click()
  await page.getByRole('option', { name: /synthetic_workerHandoff_token/ }).click()
  await expect(retired.getByText('No running worker has the required current replacement grant for this entry.', { exact: true })).toBeVisible()
  await expect(retired.getByRole('combobox', { name: 'Running worker', exact: true })).toHaveCount(0)
  await expect(retired.getByLabel('New value', { exact: true })).toHaveCount(0)
  await expect(retired.getByRole('button', { name: 'Authorize replacement', exact: true })).toBeDisabled()
  await retired.press('Escape')
  await info.attach('credential-agent-handoff-proof', { contentType: 'application/json', body: JSON.stringify({
    syntheticValuesOnly: true, nativeStorageExercised: false, realSchedulerAndExecutor: true,
    request: final.request, modelCalls: final.modelCalls, replacementWrites: 1,
    unknownReferenceRejected: true, repeatedReferenceReturnedOriginalReceipt: true, retiredWorkerExcluded: true,
    entry: final.entries[0], receipt: final.receipts.find(receipt => receipt.action === 'replace_value')
  }) })
})
