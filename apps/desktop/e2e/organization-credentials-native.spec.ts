/** Real renderer → authenticated local gateway → catalog, with only synthetic secure input. */
import type { Page } from '@playwright/test'

import { setupCredentialsNativeFixture } from './organization-credentials-native-fixture'
import { acknowledgeExpectedErrorBanner, expect, test } from './test'

const copy = {
  title: 'Environment & credentials', create: 'Create entry', service: 'Service', label: 'Label', type: 'Type',
  description: 'Description and purpose', submitValue: 'Submit value', value: 'New value', replaceValue: 'Replace value',
  uncertain: 'The result is unknown. Check the original operation before another change. No value or secure input handle is retained for retry.', reconcile: 'Check original operation', manageGrants: 'Manage agent permissions',
  actor: 'Actor', scope: 'Exact scope', grantSave: 'Save reviewed permissions',
  grantReviewed: 'I reviewed this exact agent, entry, and permission set.', manageCreation: 'Manage creation permission',
  allowCreate: 'Allow metadata creation for this exact scope', revoke: 'Revoke access', remove: 'Remove entry',
  edit: 'Edit metadata', save: 'Save metadata', cancelPending: 'Cancel pending operation', cancelled: 'Pending operation cancelled'
}

let running: Awaited<ReturnType<typeof setupCredentialsNativeFixture>> | undefined
test.setTimeout(240_000)
test.afterEach(async () => { await running?.fixture.cleanup(); running = undefined })

async function open(page: Page) {
  await page.evaluate(() => { location.hash = '/settings?tab=organization' })
  const panel = page.getByRole('region', { name: copy.title, exact: true })
  await expect(panel.getByRole('button', { name: copy.create, exact: true })).toBeEnabled()

  return panel
}

// eslint-disable-next-line no-empty-pattern -- this fixture owns its isolated backend lifecycle
test('persists owner credential metadata and reviewed grants through real gateway mutations and restart', async ({}, info) => {
  running = await setupCredentialsNativeFixture()
  let { page } = running.fixture
  const writes: Array<{ method: string; action?: string; operationId: string }> = []
  let loseReplacementReply = false
  let dropMetadataWrite = false
  await page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer()
    const pending = new Map<number | string, string>()
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as { id: number | string; method: string; params: Record<string, unknown> }

      if (request.method === 'organization.credentials.mutate' || request.method === 'organization.credentials.configureGrant' || request.method === 'organization.credentials.cancelOperation') {
        // Never retain submitted values, handles, or entire wire payloads in diagnostics.
        writes.push({ method: request.method, action: typeof request.params.action === 'string' ? request.params.action : undefined,
          operationId: String(request.params.operationId) })

        if (dropMetadataWrite && request.params.action === 'edit_metadata') {
          dropMetadataWrite = false
          socket.send(JSON.stringify({ jsonrpc: '2.0', id: request.id,
            error: { code: 5076, message: 'Synthetic unsent metadata edit', data: { reason: 'synthetic_unsent_operation' } } }))

          return
        }

        if (loseReplacementReply && request.params.action === 'replace_value') {
          pending.set(request.id, 'lose-replacement-reply')
        }
      }

      server.send(message)
    })
    server.onMessage(message => {
      const reply = JSON.parse(message.toString()) as { id: number | string; result?: unknown }

      if (pending.get(reply.id) === 'lose-replacement-reply' && reply.result) {
        loseReplacementReply = false
        pending.delete(reply.id)
        socket.send(JSON.stringify({ jsonrpc: '2.0', id: reply.id,
          error: { code: 5076, message: 'Synthetic acknowledgement loss', data: { reason: 'synthetic_acknowledgement_loss' } } }))

        return
      }

      socket.send(message)
    })
  })
  await page.reload()
  let panel = await open(page)
  await panel.getByRole('button', { name: copy.create, exact: true }).click()
  let dialog = page.getByRole('dialog')
  await dialog.getByLabel(copy.service, { exact: true }).fill('github')
  await dialog.getByLabel(copy.label, { exact: true }).fill('eidolonReleases')
  await dialog.getByLabel(copy.type, { exact: true }).fill('token')
  await dialog.getByLabel(copy.description, { exact: true }).fill('Synthetic native acceptance credential')
  await dialog.getByRole('button', { name: copy.create, exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(panel.getByText('github_eidolonReleases_token', { exact: true })).toBeVisible()
  expect(running.ledger().receiptCount).toBe(1)

  await panel.getByRole('button', { name: copy.submitValue, exact: true }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByLabel(copy.value, { exact: true }).fill('SYNTHETIC_cancelled')
  await dialog.press('Escape')
  await expect(dialog).toHaveCount(0)
  expect(running.ledger().receiptCount).toBe(1)
  await panel.getByRole('button', { name: copy.submitValue, exact: true }).click()
  await page.getByLabel(copy.value, { exact: true }).fill('SYNTHETIC_first_value')
  await page.getByRole('dialog').getByRole('button', { name: copy.submitValue, exact: true }).click()
  await expect(panel.getByRole('button', { name: copy.replaceValue, exact: true })).toBeEnabled()
  expect(running.ledger().catalog.entries[0].version).toBe(1)

  loseReplacementReply = true
  await panel.getByRole('button', { name: copy.replaceValue, exact: true }).click()
  await page.getByLabel(copy.value, { exact: true }).fill('SYNTHETIC_second_value')
  await page.getByRole('dialog').getByRole('button', { name: copy.replaceValue, exact: true }).click()
  await expect(panel.getByRole('alert')).toContainText(copy.uncertain)
  expect(running.ledger().catalog.entries[0].version).toBe(2)
  await panel.getByRole('button', { name: copy.reconcile, exact: true }).click()
  await expect(panel.getByRole('button', { name: copy.reconcile, exact: true })).toHaveCount(0)
  await acknowledgeExpectedErrorBanner(page, copy.uncertain)
  expect(writes.filter(write => write.action === 'replace_value')).toHaveLength(2)
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain('SYNTHETIC_')
  await expect(panel).not.toContainText('SYNTHETIC_')

  const beforeCancellation = running.ledger()
  dropMetadataWrite = true
  await panel.getByRole('button', { name: copy.edit, exact: true }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByLabel(copy.description, { exact: true }).fill('Synthetic change that must never commit')
  await dialog.getByRole('button', { name: copy.save, exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(panel.getByRole('alert')).toContainText(copy.uncertain)
  expect(running.ledger().catalog.entries).toEqual(beforeCancellation.catalog.entries)
  await panel.getByRole('button', { name: copy.reconcile, exact: true }).click()
  await expect(panel.getByRole('button', { name: copy.create, exact: true })).toBeDisabled()
  await panel.getByRole('button', { name: copy.cancelPending, exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: copy.cancelPending, exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(panel.getByText(copy.cancelled, { exact: false })).toBeVisible()
  await expect(panel.getByRole('button', { name: copy.create, exact: true })).toBeEnabled()
  await acknowledgeExpectedErrorBanner(page, copy.uncertain)
  const cancelled = running.ledger()
  expect(cancelled.catalog.entries).toEqual(beforeCancellation.catalog.entries)
  expect(cancelled.receiptCount).toBe(beforeCancellation.receiptCount)
  expect(cancelled.auditCount).toBe(beforeCancellation.auditCount + 1)
  expect(writes.filter(write => write.method === 'organization.credentials.cancelOperation')).toHaveLength(1)
  expect(writes.filter(write => write.action === 'edit_metadata')).toHaveLength(1)
  expect(writes.find(write => write.method === 'organization.credentials.cancelOperation')!.operationId)
    .toBe(writes.find(write => write.action === 'edit_metadata')!.operationId)

  await panel.getByRole('button', { name: copy.manageGrants, exact: true }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByRole('combobox', { name: copy.actor, exact: true }).click()
  await page.getByRole('option', { name: new RegExp(running.seed.identityId) }).click()
  await dialog.getByRole('combobox', { name: copy.scope, exact: true }).click()
  await page.getByRole('option', { name: /github_eidolonReleases_token/ }).click()
  await dialog.getByRole('checkbox', { name: 'discover', exact: true }).check()
  await dialog.getByRole('checkbox', { name: 'edit_metadata', exact: true }).check()
  await expect(dialog.getByRole('button', { name: copy.grantSave, exact: true })).toBeDisabled()
  await dialog.getByRole('checkbox', { name: copy.grantReviewed, exact: true }).check()
  await dialog.getByRole('button', { name: copy.grantSave, exact: true }).click()
  await expect(dialog).toHaveCount(0)

  await panel.getByRole('button', { name: copy.manageCreation, exact: true }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByRole('combobox', { name: copy.actor, exact: true }).click()
  await page.getByRole('option', { name: new RegExp(running.seed.identityId) }).click()

  for (const [label, value] of [[copy.service, 'discord'], [copy.label, 'statusAlerts'], [copy.type, 'webhookUrl']]) {
    await dialog.getByLabel(label, { exact: true }).fill(value)
  }

  await dialog.getByRole('checkbox', { name: copy.allowCreate, exact: true }).check()
  await dialog.getByRole('checkbox', { name: copy.grantReviewed, exact: true }).check()
  await dialog.getByRole('button', { name: copy.grantSave, exact: true }).click()
  await expect(dialog).toHaveCount(0)
  const beforeRestart = running.ledger()
  const originalBackendPid = running.backendPid()
  expect(beforeRestart.receiptCount).toBe(5)
  expect(beforeRestart.auditCount).toBe(6)
  expect(beforeRestart.catalog.grants.map(grant => grant.permissions).sort()).toEqual([['create'], ['discover', 'edit_metadata']])
  expect(beforeRestart.modelCalls).toBe(0)
  expect(beforeRestart.attempts).toBe(0)
  expect(running.fixture.mock.receivedPrompts).toHaveLength(0)
  await page.screenshot({ path: info.outputPath('credential-owner-catalog.png') })

  await running.restart()
  await expect.poll(() => running!.backendPid()).not.toBe(originalBackendPid)
  page = running.fixture.page
  panel = await open(page)
  const restarted = running.ledger()
  expect(restarted.receiptCount).toBe(beforeRestart.receiptCount)
  expect(restarted.catalog.grants).toEqual(beforeRestart.catalog.grants)
  expect(restarted.catalog.entries.map(({ id, name, revision, version }) => ({ id, name, revision, version })))
    .toEqual(beforeRestart.catalog.entries.map(({ id, name, revision, version }) => ({ id, name, revision, version })))
  // The synthetic backend deliberately has no persisted bytes. This asserts metadata, not native storage persistence.
  await panel.getByRole('button', { name: copy.revoke, exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: copy.revoke, exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await panel.getByRole('button', { name: copy.remove, exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: copy.remove, exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  const final = running.ledger()
  expect(final.catalog.entries).toHaveLength(0)
  expect(final.catalog.tombstones).toHaveLength(1)
  expect(final.receiptCount).toBe(7)
  expect(final.auditCount).toBe(8)
  expect(final.modelCalls).toBe(0)
  expect(final.attempts).toBe(0)
  await info.attach('credential-owner-proof', { contentType: 'application/json', body: JSON.stringify({
    syntheticValuesOnly: true, nativeStorageExercised: false, metadataRestarted: true,
    originalBackendPid, restartedBackendPid: running.backendPid(),
    receiptCount: final.receiptCount, auditCount: final.auditCount, modelCalls: final.modelCalls, attempts: final.attempts, writes
  }) })
})
