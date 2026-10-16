/** Real renderer/RPC/single-send localhost inference and durable intake receipts. */
import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { buildAppEnv, type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { verifyKeyboardFocus } from './focus-visibility'
import { expect, test } from './test'

let fixture: MockBackendFixture | undefined
const root = path.resolve(import.meta.dirname, '../../..')
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
test.setTimeout(180_000)
test.afterEach(async () => { await fixture?.cleanup(); fixture = undefined })

for (const scenario of ['question', 'admit', 'stale'] as const) {
  // eslint-disable-next-line no-empty-pattern -- fixture lifecycle is owned by this spec
  test(`plain-language intake ${scenario} retains one charged call across reload`, async ({}, testInfo) => {
    let run!: (script: string) => string
    fixture = await setupMockBackend({
      isolateModelCatalog: true,
      modelContextLength: 128000,
      mockServer: { requestIntakeReply: raw => {
        const prompt = JSON.parse(raw)
        const organization = prompt.organization

        return JSON.stringify({
          snapshotId: scenario === 'stale' ? '0'.repeat(64) : organization.snapshotId,
          routeId: 'manager', projectIds: [], deliveryMode: 'managed_artifact', priority: 'P3',
          rationale: 'Routine synthetic status report.', evidenceIds: ['owner-request'],
          acceptanceCriteria: ['Deliver the requested status report.'], assumptions: [],
          duplicateCandidateIds: [], dependencyCandidateIds: [],
          question: scenario === 'question' ? { text: 'Which reporting period should this cover?',
            neededFor: 'The reporting period is absent from the request and supplied context.',
            checkedEvidenceIds: ['owner-request'] } : null
        })
      } },
      beforeLaunch: async sandbox => {
        run = script => execFileSync(python, ['-c', script], { cwd: root, env: buildAppEnv(sandbox), encoding: 'utf8' })
        run(`
import os
from pathlib import Path
from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_metadata import save_metadata
p=Path(os.environ['HERMES_HOME'])/'organization'/'state.db'
s=OrganizationStore(p)
save_metadata(p,display_name='Intake Studio',owner_display_name='Owner',expected_organization_id=None,expected_revision=None,idempotency_key='native-intake-metadata')
# Synthetic fixture fence: create every admitted objective with the ordinary
# paused dispatch state in the SAME transaction, before the scheduler can claim.
# This is not a production default or a stub of admission/claim behavior.
with s._write() as c:
 c.execute('CREATE TRIGGER native_intake_pause AFTER INSERT ON objectives BEGIN INSERT INTO objective_dispatch VALUES (NEW.id,1,1); END')
`)
      }
    })
    await waitForAppReady(fixture)
    const { page, mock } = fixture
    await page.getByRole('complementary', { name: 'Eidolon navigation', exact: true }).getByRole('link', { name: 'Home', exact: true }).click()
    // The fresh-team continuation owns the hero CTA until setup is complete.
    // Open the retained intake through the always-discoverable manual entry,
    // then explicitly choose its assisted disclosure without submitting again.
    await page.getByRole('button', { name: 'New objective', exact: true }).click()
    await page.locator('#eid-assisted-intake > summary').filter({ hasText: /^Describe a request$/ }).click()
    const intake = page.getByRole('region', { name: 'Describe a request', exact: true })
    await expect(intake).toBeVisible()

    if (scenario === 'admit') {
      for (const percent of [100, 200]) {
        await page.evaluate(value => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(value), percent)
        await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(percent / 100)

        const request = intake.getByLabel('Your request', { exact: true })
        const submit = intake.getByRole('button', { name: 'Submit request', exact: true })

        // This advanced disclosure follows manual entry. Prove actual keyboard
        // reachability instead of assuming its native toggle preserves scroll.
        for (let step = 0; step < 12 && !await request.evaluate(element => element === document.activeElement); step++) {
          await page.keyboard.press(percent === 100 ? 'Tab' : 'Shift+Tab')
        }

        await expect(request).toBeFocused()

        await expect(request).toBeInViewport({ ratio: 1 })
        const fieldFocus = await verifyKeyboardFocus(page, request, `Assisted intake field ${percent}%`)
        await testInfo.attach(`intake-field-focus-${percent}`, {
          contentType: 'application/json', body: JSON.stringify(fieldFocus)
        })
        const fieldWindow = await fixture.app.browserWindow(page)

        const fieldPng = await fieldWindow.evaluate(async window =>
          (await window.webContents.capturePage()).toPNG().toString('base64'))

        await testInfo.attach(`intake-field-native-${percent}`, {
          contentType: 'image/png', body: Buffer.from(fieldPng, 'base64')
        })
        await request.fill('Prepare a status report')
        await expect(submit).toBeEnabled()

        for (let step = 0; step < 12 && !await submit.evaluate(element => element === document.activeElement); step++) {
          await page.keyboard.press('Tab')
        }

        await expect(submit).toBeFocused()
        await expect(submit).toBeInViewport({ ratio: 1 })
        const focus = await verifyKeyboardFocus(page, submit, `Assisted intake Submit ${percent}%`)
        await testInfo.attach(`intake-submit-focus-${percent}`, {
          contentType: 'application/json', body: JSON.stringify(focus)
        })
        await expect(request).toHaveValue('Prepare a status report')
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)

        // A clipped inner scroller can leave the document width unchanged.
        const geometry = await page.locator('.eid-home-hero, #eid-assisted-intake').evaluateAll(regions =>
          regions.map(region => {
            // Decorative SVG scenery intentionally uses cropped slice geometry;
            // the surrounding region and all semantic content must still fit.
            const elements = [region, ...region.querySelectorAll('*')].filter(element =>
              element.getClientRects().length > 0 && element.tagName !== 'OPTION' &&
              !element.closest('svg[aria-hidden="true"]'))

            return elements.map(element => {
              const rect = element.getBoundingClientRect()
              let left = 0
              let right = innerWidth

              for (let parent = element.parentElement; parent; parent = parent.parentElement) {
                if (getComputedStyle(parent).overflowX !== 'visible') {
                  const bounds = parent.getBoundingClientRect()
                  left = Math.max(left, bounds.left + parent.clientLeft)
                  right = Math.min(right, bounds.left + parent.clientLeft + parent.clientWidth)
                }
              }

              return { tag: element.tagName, text: element.textContent?.slice(0, 80),
                left: rect.left, right: rect.right, availableLeft: left, availableRight: right,
                clientWidth: element.clientWidth, scrollWidth: element.scrollWidth,
                fits: rect.left >= left - 1 && rect.right <= right + 1 &&
                  (element.clientWidth === 0 || element.scrollWidth <= element.clientWidth + 1) }
            })
          }))

        await testInfo.attach(`intake-region-geometry-${percent}`, {
          contentType: 'application/json', body: JSON.stringify(geometry)
        })
        // Retain the old capture, but use native pixels to review Electron zoom:
        // Playwright's page screenshot can crop to CSS-sized capture bounds.
        const nativeWindow = await fixture.app.browserWindow(page)

        const nativePng = await nativeWindow.evaluate(async window =>
          (await window.webContents.capturePage()).toPNG().toString('base64'))

        await testInfo.attach(`intake-entry-native-${percent}`, {
          contentType: 'image/png', body: Buffer.from(nativePng, 'base64')
        })
        expect(geometry.flat().filter(element => !element.fits)).toEqual([])
        await page.screenshot({ path: testInfo.outputPath(`intake-entry-${percent}.png`) })
      }

      await page.evaluate(() => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(100))
      await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(1)
      expect(mock.receivedPrompts).toHaveLength(0)
    }

    await intake.getByLabel('Your request', { exact: true }).fill('Prepare a status report')
    await intake.getByLabel('Owner priority (optional)', { exact: true }).selectOption('P2')
    await intake.getByRole('button', { name: 'Submit request', exact: true }).click()

    const read = () => JSON.parse(run(`
import os,json,sqlite3
from pathlib import Path
p=Path(os.environ['HERMES_HOME'])/'organization'/'state.db'
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as c:
 c.row_factory=sqlite3.Row
 print(json.dumps({'intakes':[dict(r) for r in c.execute('SELECT * FROM organization_request_intakes')],
 'objectives':[dict(r) for r in c.execute('SELECT o.id,o.priority,a.manager_id,a.executive_id,d.paused FROM objectives o JOIN objective_assignments a ON a.objective_id=o.id JOIN objective_dispatch d ON d.objective_id=o.id')],
 'attempts':c.execute('SELECT coalesce(sum(attempts),0) FROM requests').fetchone()[0]}))
`)) as { intakes: Record<string, unknown>[]; objectives: Record<string, unknown>[]; attempts: number }

    const expected = scenario === 'question' ? 'needs_answer' : scenario === 'admit' ? 'admitted' : 'failed'
    await expect.poll(() => read().intakes[0]?.status, { timeout: 60_000 }).toBe(expected)
    const retained = read()
    expect(retained.intakes).toHaveLength(1)
    expect(retained.intakes[0]).toMatchObject({ input_tokens: 10, output_tokens: 20 })
    expect(Number(retained.intakes[0].input_limit)).toBeGreaterThan(0)
    expect(Number(retained.intakes[0].output_limit)).toBeGreaterThan(0)
    expect(retained.attempts).toBe(0)
    expect(mock.receivedPrompts).toHaveLength(1)

    if (scenario === 'admit') {
      expect(retained.objectives).toHaveLength(1)
      expect(retained.objectives[0]).toMatchObject({ manager_id: 'manager', executive_id: 'executive', priority: 4, paused: 1 })
    } else {
      expect(retained.objectives).toHaveLength(0)
    }

    if (scenario === 'question') {
      await expect(intake.getByText('Which reporting period should this cover?', { exact: true })).toBeVisible()
    }

    await page.reload()
    await waitForAppReady(fixture)
    await page.getByRole('complementary', { name: 'Eidolon navigation', exact: true }).getByRole('link', { name: 'Home', exact: true }).click()
    // The fresh-team continuation owns the hero CTA until setup is complete.
    // Open the retained intake through the always-discoverable manual entry,
    // then explicitly choose its assisted disclosure without submitting again.
    await page.getByRole('button', { name: 'New objective', exact: true }).click()
    await page.locator('#eid-assisted-intake > summary').filter({ hasText: /^Describe a request$/ }).click()
    await intake.getByText('Recent requests', { exact: true }).click()
    await intake.getByRole('button', { name: 'Refresh recent requests', exact: true }).click()
    await intake.getByRole('button', { name: /Prepare a status report/ }).click()
    await expect(intake.getByLabel('Your request', { exact: true })).toHaveValue('Prepare a status report')

    if (scenario === 'question') {
      await expect(intake.getByText('Which reporting period should this cover?', { exact: true })).toBeVisible()
    }

    expect(read()).toEqual(retained)
    expect(mock.receivedPrompts).toHaveLength(1)
    await intake.screenshot({ path: testInfo.outputPath(`intake-${scenario}.png`) })
  })
}
