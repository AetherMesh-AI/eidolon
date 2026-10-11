/** Actual native Electron → RPC → scheduler → SQLite → OS runner → source Git branch. */
import type { Locator, Page } from '@playwright/test'

import { openOrganizationDisclosure, openOrganizationRequests } from './organization-disclosures'
import {
  correctedApp,
  projectCriterion,
  projectTests,
  projectTitle,
  setupProjectFixture
} from './organization-project-fixture'
import { expect, test } from './test'

let running: Awaited<ReturnType<typeof setupProjectFixture>> | undefined
const checks = [
  'Project tests (requires external verification when unavailable)',
  'Managed workspace validation',
  'Source project integration receipt'
]
test.setTimeout(240_000)

function primary(page: Page) {
  return page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })
}
function header(page: Page) {
  return page
    .locator('.eid-page-header')
    .filter({ has: page.getByRole('heading', { name: projectTitle, exact: true }) })
}
function fact(region: Locator, label: string) {
  return region
    .locator('dt')
    .filter({ hasText: new RegExp(`^${label}$`) })
    .locator('+ dd')
}
async function openObjective(page: Page) {
  await primary(page).getByRole('link', { name: 'Objectives', exact: true }).click()
  await page
    .getByRole('link')
    .filter({ has: page.getByText(projectTitle, { exact: true }) })
    .first()
    .click()
  await expect(header(page)).toBeVisible()
}
async function createObjective(page: Page) {
  await primary(page).getByRole('link', { name: 'Home', exact: true }).click()
  await page.getByRole('button', { name: 'New objective', exact: true }).click()
  await openOrganizationDisclosure(page, 'Configured capabilities')
  const capabilities = page.getByRole('region', { name: 'Configured capabilities', exact: true })
  await expect(capabilities).toContainText('Project test grant')
  await expect(capabilities).toContainText('Source branch integration grant')
  await expect(capabilities).toContainText('macOS and Windows execution is unavailable')
  await expect(capabilities).toContainText(
    'No third-party dependencies, shell, network, subprocesses, threads or file creation.'
  )
  await capabilities.getByText('Explicit project recipes (1)', { exact: true }).click()
  await expect(capabilities).toContainText('root0/app.py')
  await expect(capabilities).toContainText('root0/test_app.py')
  await expect(capabilities).toContainText('python_unittest')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: 'System health details', exact: true })).toHaveCount(0)
  await page.getByRole('textbox', { name: 'Objective', exact: true }).fill(projectTitle)
  await openOrganizationDisclosure(page, 'Context')
  await page
    .getByRole('textbox', { name: 'Submitted context (optional)', exact: true })
    .fill(
      'Inspect root0/app.py and root0/test_app.py. Fix subtraction to addition without weakening the tests. Independently review the edit, apply managed bytes, run the configured isolated unittest recipe, independently review its exact evidence, and create the reviewed local source branch.'
    )
  await openOrganizationDisclosure(page, 'Acceptance criteria')
  await page.getByRole('textbox', { name: 'Acceptance criteria', exact: true }).fill(projectCriterion)
  await page.getByRole('combobox', { name: 'Delivery scope', exact: true }).selectOption('source_project')
  await openOrganizationDisclosure(page, 'Verification and priority')
  for (const label of checks) {
    await page.getByRole('checkbox', { name: label, exact: true }).check()
  }
  await page.getByRole('button', { name: 'Create objective', exact: true }).click()
  await expect(header(page)).toBeVisible()
}
async function readExactArtifact(page: Page, button: string) {
  await page.getByRole('button', { name: button, exact: true }).click()
  const inspector = page.getByRole('complementary', { name: 'Artifact details', exact: true })
  await expect(inspector.locator('p.eid-result-text').first()).not.toBeEmpty()
  const exact = await inspector.locator('p.eid-result-text').first().innerText()
  await inspector.getByRole('button', { name: 'Close artifact details', exact: true }).click()
  return exact
}

// eslint-disable-next-line no-empty-pattern -- lifecycle is this spec's real Electron fixture
test.afterEach(async ({}, testInfo) => {
  if (!running) {
    return
  }
  try {
    await testInfo.attach('native-project-provider-and-test-receipts', {
      body: JSON.stringify(
        {
          platform: process.platform,
          sandboxRequired: process.env.EIDOLON_REQUIRE_PROJECT_SANDBOX,
          stages: running.stages,
          errors: running.providerErrors,
          exactRunsReviewed: running.runEvidence
        },
        null,
        2
      ),
      contentType: 'application/json'
    })
    if (!running.fixture.page.isClosed()) {
      const ui = await running.fixture.page.locator('body').ariaSnapshot()
      await testInfo.attach('native-project-ui', { body: ui, contentType: 'text/plain' })
      if (testInfo.status !== testInfo.expectedStatus) {
        console.error(`Native project failure (${testInfo.title}):\n${ui}`)
      }
      await running.fixture.page.screenshot({ path: testInfo.outputPath('native-project-final-state.png') })
    }
  } finally {
    await running.fixture.cleanup()
    running = undefined
  }
})

// eslint-disable-next-line no-empty-pattern -- actual Electron lifecycle belongs to the scenario
test('retains exact native test, independent review and branch receipts, or an honest unsupported outcome', async ({}, testInfo) => {
  running = await setupProjectFixture()
  let { page } = running.fixture
  await createObjective(page)
  const pending = () =>
    page.getByRole('button', { name: /^Inspect request:/ }).filter({ hasText: 'Pending intervention' })
  let state = 'working'
  await expect
    .poll(
      async () => {
        state = running!.providerErrors.length
          ? 'provider_error'
          : (await header(page).innerText()).includes('Completed')
            ? 'completed'
            : (await pending().count())
              ? 'blocked'
              : 'working'
        return state
      },
      { timeout: 90_000 }
    )
    .not.toBe('working')
  expect(running.providerErrors).toEqual([])
  const execution = page.getByRole('region', { name: 'Latest project test execution', exact: true })
  await expect(execution).toContainText('it does not establish full project correctness')
  if (process.platform === 'linux' && process.env.EIDOLON_REQUIRE_PROJECT_SANDBOX === '1') {
    expect(state, 'Native Linux verification requires real functional bubblewrap, not a skip or unsupported pass').toBe(
      'completed'
    )
  }
  let retainedExactTest = ''
  let retainedExactSource = ''
  if (state === 'completed') {
    expect(process.platform).toBe('linux')
    await expect(fact(execution, 'Reported test count')).toHaveText('3')
    await expect(fact(execution, 'Exit code')).toHaveText('0')
    await expect(fact(execution, 'OS isolation')).toContainText('Established')
    const review = execution.getByRole('region', { name: 'Independent test review', exact: true })
    await expect(review).toContainText('Approved')
    await expect(fact(review, 'Reviewer ID')).not.toHaveText('editor')
    const branch = execution.getByRole('region', { name: 'Reviewed source branch', exact: true })
    await expect(branch).toContainText('Integrated into a new Git branch')
    await expect(branch).toContainText('This does not merge, push, publish or deploy.')
    const ref = await fact(branch, 'Branch ref').innerText()
    const commit = await fact(branch, 'Integration commit').innerText()
    expect(running.git('rev-parse', ref)).toBe(commit)
    expect(running.git('show', `${commit}:app.py`)).toBe(correctedApp.trim())
    expect(running.git('show', `${commit}:test_app.py`)).toBe(projectTests.trim())
    expect(await fact(branch, 'Source base commit').innerText()).toBe(running.sourceHead)
    const exactTest = await readExactArtifact(page, 'Read exact test evidence')
    retainedExactTest = exactTest
    const proof = JSON.parse(exactTest) as { execution: unknown; snapshot: Array<{ path: string; content: string }> }
    expect(proof.execution).toEqual(running.runEvidence[0].execution)
    expect(Object.fromEntries(proof.snapshot.map(file => [file.path, file.content]))).toEqual({
      'root0/app.py': correctedApp,
      'root0/test_app.py': projectTests
    })
    const exactSource = await readExactArtifact(page, 'Read exact source integration evidence')
    retainedExactSource = exactSource
    expect(JSON.parse(exactSource)).toMatchObject({
      status: 'integrated',
      ref,
      commit,
      sourceBaseCommit: running.sourceHead,
      workingTreeWritesPerformed: false,
      indexWritesPerformed: false,
      remotePushPerformed: false
    })
    await testInfo.attach('exact-retained-project-test-evidence', { body: exactTest, contentType: 'application/json' })
    await testInfo.attach('exact-retained-source-branch-evidence', {
      body: exactSource,
      contentType: 'application/json'
    })
    const testReview = running.stages.find(stage => stage.kind === 'request.test_review')!
    expect(testReview.agent).not.toBe('editor')
    expect(running.stages.find(stage => stage.kind === 'request.accept')!.agent).not.toBe(
      running.stages.find(stage => stage.kind === 'request.integrate')!.agent
    )
  } else {
    await expect(header(page)).not.toContainText('Completed')
    expect(running.runEvidence).toEqual([])
    expect(
      running.stages.some(stage => ['request.test_review', 'request.integrate', 'request.accept'].includes(stage.kind))
    ).toBe(false)
    if (process.platform === 'win32') {
      await expect(execution).toContainText('No project test execution receipt recorded.')
      await expect(pending().first()).toContainText('POSIX no-follow directory-descriptor support')
    } else {
      await expect(execution).toContainText('Unavailable on this runtime')
      await expect(fact(execution, 'OS isolation')).toContainText('Not established')
      await expect(execution).toContainText('No automatic source integration receipt recorded.')
      const exactUnsupported = await readExactArtifact(page, 'Read exact test evidence')
      expect(JSON.parse(exactUnsupported).execution).toMatchObject({
        status: 'unsupported',
        testCount: 0,
        isolation: { established: false },
        sourceWritesPerformed: false
      })
      await testInfo.attach('native-unsupported-execution-proof', {
        body: exactUnsupported,
        contentType: 'application/json'
      })
    }
    expect(running.git('for-each-ref', '--format=%(refname)', 'refs/heads/')).toBe('refs/heads/main')
  }
  running.assertSourceUntouched()
  const beforeRestart = await execution.innerText()
  const modelCalls = running.stages.length
  await page.screenshot({ path: testInfo.outputPath('01-native-project-receipt.png') })
  await page.reload()
  await expect(execution).toHaveText(beforeRestart, { useInnerText: true, timeout: 60_000 })
  await running.restart()
  page = running.fixture.page
  await openObjective(page)
  await expect(page.getByRole('region', { name: 'Latest project test execution', exact: true })).toHaveText(
    beforeRestart,
    { useInnerText: true }
  )
  expect(running.stages).toHaveLength(modelCalls)
  running.assertSourceUntouched()
  await page.screenshot({ path: testInfo.outputPath('02-native-receipt-after-process-restart.png') })
  if (state === 'completed') {
    await page.getByRole('button', { name: 'Archive objective', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Restore to current history', exact: true })).toBeVisible()
    await primary(page).getByRole('link', { name: 'Objectives', exact: true }).click()
    const outcome = page
      .getByRole('region', { name: 'Outcomes inbox', exact: true })
      .getByRole('listitem', { name: projectTitle, exact: true })
    await expect(outcome).toContainText('Accepted')
    await expect(outcome).toContainText('Archived history')
    await expect(outcome.getByRole('button', { name: 'Mark seen', exact: true })).toBeVisible()
    await outcome.getByRole('link', { name: projectTitle, exact: true }).click()
    expect(await readExactArtifact(page, 'Read exact test evidence')).toBe(retainedExactTest)
    expect(await readExactArtifact(page, 'Read exact source integration evidence')).toBe(retainedExactSource)
    await expect(page.getByRole('region', { name: 'Latest project test execution', exact: true })).toHaveText(
      beforeRestart,
      { useInnerText: true }
    )
    expect(running.stages).toHaveLength(modelCalls)
    running.assertSourceUntouched()
    await page.screenshot({ path: testInfo.outputPath('03-archived-inbox-exact-project-evidence.png') })
  } else {
    const blockedRequestName = await pending().first().getAttribute('aria-label')
    expect(blockedRequestName).toBeTruthy()
    await primary(page).getByRole('link', { name: 'Objectives', exact: true }).click()
    await expect(
      page.getByRole('region', { name: 'Outcomes inbox', exact: true }).getByText('No outcomes yet', { exact: true })
    ).toBeVisible()
    await expect(page.getByRole('region', { name: 'Outcomes inbox', exact: true }).getByRole('listitem')).toHaveCount(0)
    await openOrganizationRequests(page)
    // Follow the same request identity into attention; this surface renders
    // the blocker reason rather than the queue's 'Pending intervention' label.
    await expect(
      page
        .getByRole('region', { name: 'Attention inbox', exact: true })
        .getByRole('button', { name: blockedRequestName!, exact: true })
    ).toContainText(projectTitle)
  }
})

// eslint-disable-next-line no-empty-pattern -- actual Electron lifecycle belongs to the scenario
test('keeps the model budget blocked across process restart and cancellation without running project code', async ({}, testInfo) => {
  running = await setupProjectFixture({ budgetLimited: true })
  let { page } = running.fixture
  await createObjective(page)
  const reason = 'Objective model-call budget exhausted. Automatic execution has stopped.'
  // The executive has delegated, but the shared model budget stops manager
  // planning before any task or configured worker activation is admitted.
  const blocked = () => page.getByRole('button', { name: 'Inspect request: request.plan', exact: true })
  await expect(blocked()).toContainText(reason, {
    timeout: 90_000
  })
  const assertStopped = async (cancelled = false) => {
    await expect(blocked()).toContainText(cancelled ? 'Cancelled' : reason)
    await expect(page.getByRole('button', { name: 'Inspect request: work.inspect', exact: true })).toHaveCount(0)
    expect(running!.stages.map(stage => stage.kind)).toEqual(['request.decompose'])
    expect(running!.providerErrors).toEqual([])
    await expect(page.getByRole('region', { name: 'Latest project test execution', exact: true })).toContainText(
      'No project test execution receipt recorded.'
    )
    await expect(header(page)).not.toContainText('Completed')
    running!.assertSourceUntouched()
    expect(running!.git('for-each-ref', '--format=%(refname)', 'refs/heads/')).toBe('refs/heads/main')
  }
  await assertStopped()
  await page.screenshot({ path: testInfo.outputPath('01-project-budget-block.png') })
  await running.restart()
  page = running.fixture.page
  await openObjective(page)
  await expect(blocked()).toContainText(reason)
  await assertStopped()
  await page.getByRole('button', { name: 'Cancel objective', exact: true }).click()
  await expect(header(page)).toContainText('Cancelled')
  await page.reload()
  await expect(header(page)).toContainText('Cancelled', { timeout: 60_000 })
  await assertStopped(true)
  await page.screenshot({ path: testInfo.outputPath('02-cancelled-project-budget-history.png') })
})

// eslint-disable-next-line no-empty-pattern -- actual Electron lifecycle belongs to the scenario
test('cancels an in-flight real provider response before any project execution or integration', async ({}, testInfo) => {
  running = await setupProjectFixture({ holdPlan: true })
  let { page } = running.fixture
  await createObjective(page)
  await expect.poll(() => running!.stages.map(stage => stage.kind)).toEqual(['request.decompose'])
  await page.getByRole('button', { name: 'Cancel objective', exact: true }).click()
  await expect(header(page)).toContainText('Cancelled')
  running.releasePlan()
  await running.restart()
  page = running.fixture.page
  await openObjective(page)
  await expect(header(page)).toContainText('Cancelled')
  await expect(page.getByRole('region', { name: 'Latest project test execution', exact: true })).toContainText(
    'No project test execution receipt recorded.'
  )
  expect(running.stages.map(stage => stage.kind)).toEqual(['request.decompose'])
  expect(running.providerErrors).toEqual([])
  expect(running.runEvidence).toEqual([])
  running.assertSourceUntouched()
  expect(running.git('for-each-ref', '--format=%(refname)', 'refs/heads/')).toBe('refs/heads/main')
  await page.screenshot({ path: testInfo.outputPath('cancelled-in-flight-project.png') })
})
