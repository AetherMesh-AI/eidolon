/** Real owner form → served-profile gateway → atomic ledger receipt. */
import { execFileSync } from 'node:child_process'
import path from 'node:path'

import type { Page } from '@playwright/test'

import { buildAppEnv, setupMockBackend, waitForAppReady } from './fixtures'
import { completionTitle, originalScope, setupCompletionFixture } from './organization-completion-fixture'
import { openOrganizationDisclosure } from './organization-disclosures'
import { metadataCopy, policyCopy } from './owner-policy-copy'
import { captureOwnerPolicy } from './owner-policy-evidence'
import { expect, test } from './test'
import { galleryLocales } from './workspace-gallery-fixture'

const primary = (page: Page) => page.getByRole('navigation', { name: 'Primary', exact: true })
interface Rpc { id?: string | number; method?: string; params?: Record<string, unknown>; result?: Record<string, unknown>; error?: unknown }

test.setTimeout(360_000)
// eslint-disable-next-line no-empty-pattern -- this spec owns the actual Electron lifecycle
test('owner previews, reconciles a lost acknowledgement, assigns a model and records objective allowance', async ({}, testInfo) => {
  const running = await setupCompletionFixture(false, { completionReserve: true, adaptivePacing: true, modelRouting: true })
  const { page, app } = running.fixture
  const root = path.resolve(import.meta.dirname, '../../..')
  const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')

  const ledger = (all: boolean) => JSON.parse(execFileSync(python, ['-c', `
import os,json,sqlite3
from pathlib import Path
with sqlite3.connect((Path(os.environ['HERMES_HOME'])/'organization'/'state.db').as_uri()+'?mode=ro',uri=True) as c:
 c.row_factory=sqlite3.Row
 c.execute('BEGIN')
 schema=[dict(r) for r in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
 tables=[r['name'] for r in schema if r['type']=='table' and not r['name'].startswith('sqlite_')]
 chosen=tables if ${all ? 'True' : 'False'} else ['objective_budgets','organization_model_calls','objective_completion_reserves','organization_model_call_classes']
 print(json.dumps({'schema':schema if ${all ? 'True' : 'False'} else None,'rows':{t:[dict(r) for r in c.execute('SELECT * FROM "'+t+'" ORDER BY rowid')] for t in chosen}}))
`], { cwd: root, env: buildAppEnv(running.fixture.sandbox), encoding: 'utf8' })) as unknown

  const requests: Rpc[] = []
  const responses: Array<{ method: string; value: Rpc }> = []
  let dropBudgetAck = false
  let droppedBudgetAck = false

  try {
    await page.routeWebSocket(/.*/, socket => {
      const server = socket.connectToServer()
      const methods = new Map<string | number, string>()
      let droppedId: string | number | undefined
      socket.onMessage(message => {
        const request = JSON.parse(message.toString()) as Rpc

        if (request.id !== undefined && request.method) {methods.set(request.id, request.method)}

        if (request.method?.startsWith('organization.')) {requests.push(request)}

        if (request.method === 'organization.configureBudgetPolicy' && dropBudgetAck) {
          dropBudgetAck = false
          droppedId = request.id
        }

        server.send(message)
      })
      server.onMessage(message => {
        const response = JSON.parse(message.toString()) as Rpc
        const method = response.id !== undefined ? methods.get(response.id) : undefined

        if (method?.startsWith('organization.')) {responses.push({ method, value: response })}

        if (response.id !== undefined && response.id === droppedId) {
          expect(response.error).toBeUndefined()
          droppedBudgetAck = true

          return
        }

        socket.send(message)
      })
    })
    await page.reload()
    await expect(primary(page)).toBeVisible({ timeout: 60_000 })
    await page.getByRole('button', { name: 'Edit organization and owner names', exact: true }).click()
    const names = page.getByRole('dialog', { name: 'Organization and owner', exact: true })
    await names.getByLabel('Organization name', { exact: true }).fill('Owner policy acceptance')
    await names.getByLabel('Your display name', { exact: true }).fill('Fixture Owner')
    await names.getByRole('button', { name: 'Save names', exact: true }).click()
    await expect(names).toHaveCount(0)
    // Visiting the existing Organization runtime is the explicit setup action;
    // policy reads/previews themselves must never initialize the ledger.
    await primary(page).getByRole('link', { name: 'Organization', exact: true }).click()
    await expect(page.locator('.eid-organization .eid-agent').first()).toBeVisible()
    await page.evaluate(() => {location.hash = '#/settings?tab=organization'})
    await expect(page.getByRole('heading', { name: 'Models and allowance', exact: true })).toBeVisible()
    const editBudget = page.getByRole('button', { name: 'Edit prices and allowance', exact: true })
    await expect(editBudget).toBeEnabled()
    const beforePreview = ledger(true)
    await editBudget.click()
    let budget = page.getByRole('dialog', { name: 'Edit prices and allowance', exact: true })
    const calls = budget.getByLabel('Model calls per objective', { exact: true })
    await expect(calls).toHaveValue('40')
    await calls.fill('30')
    await budget.getByRole('button', { name: 'Preview changes', exact: true }).click()
    const review = budget.getByRole('region', { name: 'Review changes', exact: true })
    await expect(review).toContainText('30')
    expect(requests.filter(row => row.method === 'organization.configureBudgetPolicy')).toHaveLength(0)
    expect(running.stages).toHaveLength(0)
    await captureOwnerPolicy(page, app, [
      { name: 'model-call-limit', locator: calls },
      { name: 'preview-change', locator: review.locator('dd').first() },
      { name: 'preview-scope', locator: review.locator('p').first() }
    ], 'budget-preview', testInfo, responses.filter(row => row.method === 'organization.previewBudgetPolicy'))
    expect(ledger(true)).toEqual(beforePreview)
    dropBudgetAck = true
    await budget.getByRole('button', { name: 'Apply changes', exact: true }).click()
    await expect.poll(() => droppedBudgetAck).toBe(true)
    const original = requests.filter(row => row.method === 'organization.configureBudgetPolicy')[0].params!
    // Reload after server acceptance, before any acknowledgement reaches UI.
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Models and allowance', exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Reconcile saved change', exact: true }).click()
    budget = page.getByRole('dialog', { name: 'Edit prices and allowance', exact: true })
    await budget.getByRole('button', { name: 'Reconcile saved change', exact: true }).click()
    await expect(budget).toContainText('Change verified and saved.')
    const writes = requests.filter(row => row.method === 'organization.configureBudgetPolicy')
    expect(writes).toHaveLength(2)
    expect(writes[1].params).toEqual(original)
    const receipts = responses.filter(row => row.method === 'organization.configureBudgetPolicy').map(row => row.value.result?.receipt)
    expect(receipts).toHaveLength(2)
    expect(receipts[1]).toEqual(receipts[0])
    await expect(budget.getByLabel('Model calls per objective', { exact: true })).toHaveValue('30')
    await budget.getByRole('button', { name: 'Close', exact: true }).click()
    expect(running.stages).toHaveLength(0)

    await page.evaluate(() => {location.hash = '#/organization'})
    await page.getByRole('button', { name: 'Inspect Executive', exact: true }).click()
    await page.getByRole('button', { name: 'Edit model assignment', exact: true }).click()
    const executive = page.getByRole('dialog', { name: 'Edit model assignment', exact: true })
    await expect(executive.getByLabel('Assignment mode', { exact: true })).toHaveValue('fixed')
    await expect(executive.getByLabel('Assignment mode', { exact: true }).locator('option[value="auto"]')).toHaveCount(0)
    await executive.getByRole('button', { name: 'Close', exact: true }).click()
    await page.keyboard.press('Escape')
    const descriptor = responses.filter(row => row.method === 'organization.ownerPolicy').at(-1)?.value.result
    const agents = descriptor?.agents as Array<{ id: string; name: string }>
    const worker = agents.find(agent => agent.id === 'worker-1')!
    expect(worker).toBeDefined()
    await page.getByRole('button', { name: `Inspect ${worker.name}`, exact: true }).click()
    await page.getByRole('button', { name: 'Edit model assignment', exact: true }).click()
    const assignment = page.getByRole('dialog', { name: 'Edit model assignment', exact: true })
    // Remove a declared-ineligible route through the catalogue, preserving the
    // eligible economy/quality choices used by the real loopback dispatch.
    await assignment.getByRole('checkbox', { name: /ineligible/ }).uncheck()
    await assignment.getByRole('button', { name: 'Preview changes', exact: true }).click()
    await expect(assignment.getByRole('region', { name: 'Review changes', exact: true })).toBeVisible()
    await captureOwnerPolicy(page, app, [
      { name: 'assignment-mode', locator: assignment.getByLabel('Assignment mode', { exact: true }) },
      { name: 'assignment-evidence', locator: assignment.getByLabel('Minimum declared evidence level', { exact: true }) }
    ], 'assignment-preview', testInfo, responses.filter(row => row.method === 'organization.previewModelRouting'))
    await assignment.getByRole('button', { name: 'Apply changes', exact: true }).click()
    await expect(assignment).toContainText('Change verified and saved.')
    await assignment.getByRole('button', { name: 'Close', exact: true }).click()
    await page.keyboard.press('Escape')

    await primary(page).getByRole('link', { name: 'Home', exact: true }).click()
    await page.getByRole('button', { name: 'New objective', exact: true }).click()
    await page.getByRole('textbox', { name: 'Objective', exact: true }).fill(completionTitle)
    await openOrganizationDisclosure(page, 'Context')
    await page.getByRole('textbox', { name: 'Submitted context (optional)', exact: true }).fill(originalScope)
    await openOrganizationDisclosure(page, 'Acceptance criteria')
    await page.getByRole('textbox', { name: 'Acceptance criteria', exact: true }).fill('The recommendation includes successful project-test verification.')
    await page.getByRole('combobox', { name: 'Delivery scope', exact: true }).selectOption('managed_artifact')
    await openOrganizationDisclosure(page, 'Verification and priority')
    await page.getByRole('checkbox', { name: 'Project tests (requires external verification when unavailable)', exact: true }).check()
    await page.getByRole('button', { name: 'Create objective', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Inspect request: request.project_test', exact: true })).toContainText('Pending intervention', { timeout: 90_000 })
    expect(running.stages.map(stage => stage.kind)).toEqual(['request.decompose', 'request.plan', 'work.draft', 'request.review'])
    expect(running.stages.some(stage => stage.agentId === 'worker-1')).toBe(true)
    expect(running.stages.filter(stage => stage.agentId === 'worker-1').every(stage => stage.model === 'mock-economy')).toBe(true)
    const capturedBeforeObservation = ledger(false)
    const stagesBeforeObservation = structuredClone(running.stages)
    await page.getByRole('button', { name: 'Edit reported allowance', exact: true }).click()
    const observation = page.getByRole('dialog', { name: 'Edit reported allowance', exact: true })
    await observation.getByLabel('Reported remaining percent', { exact: true }).fill('45')
    await observation.getByLabel('Observed at (ISO 8601 with timezone)', { exact: true }).fill(new Date(Date.now() - 1000).toISOString())
    await observation.getByLabel('Expires at (ISO 8601 with timezone)', { exact: true }).fill(new Date(Date.now() + 3600_000).toISOString())
    await observation.getByRole('button', { name: 'Preview changes', exact: true }).click()
    await expect(observation.getByRole('region', { name: 'Review changes', exact: true })).toContainText('45')
    await captureOwnerPolicy(page, app, [
      { name: 'reported-percent', locator: observation.getByLabel('Reported remaining percent', { exact: true }) },
      { name: 'reported-expiry', locator: observation.getByLabel('Expires at (ISO 8601 with timezone)', { exact: true }) }
    ], 'observation-preview', testInfo, responses.filter(row => row.method === 'organization.previewBudgetObservation'))
    await observation.getByRole('button', { name: 'Apply changes', exact: true }).click()
    await expect(observation).toContainText('Change verified and saved.')
    expect(ledger(false)).toEqual(capturedBeforeObservation)
    expect(running.stages).toEqual(stagesBeforeObservation)
    expect(running.providerErrors).toEqual([])
    await testInfo.attach('owner-policy-rpc-receipts', { contentType: 'application/json', body: JSON.stringify({ requests, responses, stages: running.stages }, null, 2) })
  } finally {
    await running.fixture.cleanup()
  }
})

for (const language of galleryLocales) {
  // eslint-disable-next-line no-empty-pattern -- actual native fixture owned here
  test(`owner policy fields retain native labels, focus and previews in ${language.locale}`, async ({}, testInfo) => {
    const fixture = await setupMockBackend({ extraDisplayConfig: `  language: ${language.locale}`, extraConfig: 'organization:\n  max_model_calls: 40\n' })
    const { page, app } = fixture
    const copy = policyCopy[language.locale]
    const namesCopy = metadataCopy[language.locale]

    try {
      await waitForAppReady(fixture)
      await expect(page.locator('html')).toHaveAttribute('lang', language.locale)
      await expect(page.locator('html')).toHaveAttribute('dir', language.locale === 'ar' ? 'rtl' : 'ltr')
      await page.getByRole('button', { name: namesCopy.edit, exact: true }).click()
      const names = page.getByRole('dialog', { name: namesCopy.title, exact: true })
      await names.getByLabel(namesCopy.organizationName, { exact: true }).fill('Owner policy gallery')
      await names.getByLabel(namesCopy.ownerName, { exact: true }).fill('Fixture Owner')
      await names.getByRole('button', { name: namesCopy.save, exact: true }).click()
      await expect(names).toHaveCount(0)
      await page.evaluate(() => {location.hash = '#/organization'})
      await expect(page.locator('.eid-organization .eid-agent').first()).toBeVisible()

      for (const mode of ['dark', 'light'] as const) {
        await page.evaluate(() => {location.hash = '#/settings?tab=config:appearance'})
        const theme = mode === 'dark' ? { label: 'Eidolon', id: 'eidolon' } : { label: 'AetherMesh', id: 'nous' }
        await page.getByRole('button').filter({ has: page.getByText(theme.label, { exact: true }) }).click()
        await page.getByRole('button', { name: language[mode], exact: true }).click()
        await expect(page.locator('html')).toHaveAttribute('data-hermes-theme', theme.id)
        await expect.poll(() => page.locator('html').evaluate(element => element.classList.contains('dark'))).toBe(mode === 'dark')
        await page.evaluate(() => {location.hash = '#/settings?tab=organization'})
        await page.getByRole('button', { name: copy.editBudget, exact: true }).click()
        const budget = page.getByRole('dialog', { name: copy.editBudget, exact: true })
        await budget.getByLabel(copy.max_model_calls, { exact: true }).fill('30')
        await budget.getByRole('button', { name: copy.preview, exact: true }).click()
        const review = budget.getByRole('region', { name: copy.review, exact: true })
        await expect(review).toContainText('30')
        await captureOwnerPolicy(page, app, [
          { name: 'calls', locator: budget.getByLabel(copy.max_model_calls, { exact: true }) },
          { name: 'pacing', locator: budget.getByLabel(copy.adaptive_pacing_enabled, { exact: true }) },
          { name: 'preview', locator: review.locator('dd').first() },
          { name: 'apply', locator: budget.getByRole('button', { name: copy.apply, exact: true }) }
        ], `budget-${language.locale}-${mode}`, testInfo, { action: 'preview-only', locale: language.locale, mode })
        await budget.getByRole('button', { name: copy.close, exact: true }).click()
        await page.getByRole('button', { name: copy.editRoutes, exact: true }).click()
        const routes = page.getByRole('dialog', { name: copy.editRoutes, exact: true })

        if (await routes.getByLabel(copy.provider, { exact: true }).count() === 0) {
          await routes.getByRole('button', { name: copy.addRoute, exact: true }).click()
        }

        const requestType = routes.getByRole('checkbox', { name: 'work.analyze', exact: true })
        await requestType.setChecked(false)
        await expect(requestType).not.toBeChecked()

        await captureOwnerPolicy(page, app, [
          { name: 'route-provider', locator: routes.getByLabel(copy.provider, { exact: true }) },
          { name: 'route-model', locator: routes.getByLabel(copy.model, { exact: true }) },
          { name: 'route-evidence', locator: routes.getByLabel(copy.evidence, { exact: true }) },
          { name: 'request-type-unchecked', locator: requestType }
        ], `routes-${language.locale}-${mode}`, testInfo, { action: 'unsaved-draft', locale: language.locale, mode })
        await requestType.setChecked(true)
        await expect(requestType).toBeChecked()
        await captureOwnerPolicy(page, app, [{ name: 'request-type-checked', locator: requestType }],
          `routes-checked-${language.locale}-${mode}`, testInfo, { action: 'unsaved-draft', locale: language.locale, mode })
        await routes.getByRole('button', { name: copy.close, exact: true }).click()
      }

      expect(fixture.mock.receivedPrompts).toHaveLength(0)
    } finally {
      await fixture.cleanup()
    }
  })
}
