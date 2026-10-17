import { readFileSync } from 'node:fs'
import path from 'node:path'

/** Native owner UI → production registered gateway → synthetic Responses → explicit Create. */
import type { Locator } from '@playwright/test'

import {
  setupNativeSubscription,
  subscriptionAck,
  subscriptionModel,
  suggestedGoals,
  suggestedJobs
} from './subscription-native-fixture'
import { allowErrorBanners, expect, test } from './test'

let fixture: Awaited<ReturnType<typeof setupNativeSubscription>> | undefined
const jobs = 'Prepare studio drafts'
const goals = 'Deliver reviewed studio work'

test.setTimeout(240_000)
test.afterEach(async () => {
  try {
    if (fixture && test.info().status !== test.info().expectedStatus) {
      await test.info().attach('subscription-failure-diagnostic', {
        contentType: 'application/json',
        body: JSON.stringify(fixture.diagnostic(), null, 2)
      })
    }
  } finally {
    try {
      await fixture?.cleanup()
    } finally {
      fixture = undefined
    }
  }
})

async function completeInference(
  button: Locator,
  method: 'improveTeamInput' | 'generateTeam',
  expected: 'result' | 'unavailable' = 'result'
) {
  const completed = await fixture!.performOperation(method, () => button.click())
  expect(completed).toMatchObject(
    expected === 'result'
      ? { method: `organization.${method}`, phase: 'result' }
      : { method: `organization.${method}`, phase: 'error', code: 5076, reason: 'unavailable' }
  )
}

async function openSetup(legacy: boolean) {
  const { page } = fixture!

  const navigation = page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })

  await navigation.getByRole('link', { name: 'Home', exact: true }).click()
  const entry = page.locator('.eid-team-entry')

  if (legacy) {
    await entry.getByRole('button', { name: 'Generate team · Recommended', exact: true }).click()
  } else {
    await entry.getByRole('button', { name: 'Set up organization', exact: true }).click()
    const identity = page.getByRole('dialog', { name: 'Organization and owner', exact: true })
    await identity.getByLabel('Organization name', { exact: true }).fill('Synthetic Subscription Studio')
    await identity.getByLabel('Your display name', { exact: true }).fill('Synthetic Owner')
    await identity.getByRole('button', { name: 'Save names', exact: true }).click()
    await expect(identity).toHaveCount(0)
  }

  const dialog = page.getByRole('dialog', { name: 'Build your starter team', exact: true })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: 'Check configuration', exact: true }).click()
  await dialog.getByLabel('Work to delegate', { exact: true }).fill(jobs)
  await dialog.getByLabel('What success looks like', { exact: true }).fill(goals)
  await dialog.getByLabel('Team naming style (optional)', { exact: true }).fill('Ash, Ember and Flint')

  return dialog
}

async function activate(dialog: Locator, legacy: boolean) {
  const connection = dialog.getByRole('region', { name: 'ChatGPT plan for setup', exact: true })
  const before = fixture!.snapshot()
  expect(before.generations).toHaveLength(legacy ? 1 : 0)
  fixture!.assertQuiet(0)
  await connection.getByRole('button', { name: 'Set up ChatGPT', exact: true }).click()

  const consent = connection.getByRole('checkbox', {
    name: 'Allow this dedicated connection to be stored locally',
    exact: true
  })

  await expect(consent).not.toBeChecked()
  const begin = connection.getByRole('button', { name: 'Continue with ChatGPT', exact: true })
  await expect(begin).toBeDisabled()
  expect(fixture!.events().filter(row => row.kind === 'consent')).toEqual([])
  await consent.check()
  await begin.click()
  const load = connection.getByRole('button', { name: 'Load account models', exact: true })
  await expect(load).toBeEnabled({ timeout: 30_000 })
  expect(fixture!.hasPrivateStore()).toBe(true)
  expect(fixture!.events().filter(row => row.kind === 'consent')).toHaveLength(1)
  expect(fixture!.events().filter(row => row.kind === 'oauth-exchange')).toHaveLength(1)
  expect(fixture!.events().filter(row => row.kind === 'models')).toEqual([])
  fixture!.assertQuiet(0)
  await load.click()
  await connection.getByLabel('Setup model', { exact: true }).selectOption(subscriptionModel)
  await connection.getByLabel('Improve calls', { exact: true }).fill('4')
  await connection.getByLabel('Generate / Regenerate calls', { exact: true }).fill('3')
  await connection.getByLabel('Total setup calls', { exact: true }).fill('7')
  await connection.getByLabel('Local timeout (seconds)', { exact: true }).fill('120')
  await connection.getByRole('button', { name: 'Review setup policy', exact: true }).click()
  const apply = connection.getByRole('button', { name: 'Apply reviewed policy', exact: true })
  await expect(apply).toBeDisabled()
  expect(fixture!.snapshot().executionPolicy).toEqual(before.executionPolicy)
  expect(fixture!.snapshot().subscriptionPolicy).toEqual([])
  fixture!.assertQuiet(0)
  await connection.getByRole('checkbox', { name: subscriptionAck, exact: true }).check()
  await apply.click()
  await expect(
    connection.getByText('Policy saved. Load account models again before Generate or Improve.', { exact: true })
  ).toBeVisible()
  const after = fixture!.snapshot()
  expect(after.subscriptionPolicy).toHaveLength(1)
  expect(after.executionPolicy.map(row => ({ settings: row.settings, fingerprint: row.fingerprint }))).toEqual(
    before.executionPolicy.map(row => ({ settings: row.settings, fingerprint: row.fingerprint }))
  )
  expect(after.executionPolicy[0].generation).toBe(Number(before.executionPolicy[0].generation) + 1)
  expect(after.model).toEqual(before.model)
  expect(after.generations).toEqual(before.generations)
  fixture!.assertQuiet(0)
  await expect(dialog.getByRole('button', { name: 'Generate team · Recommended', exact: true })).toBeDisabled()
  await load.click()
  await expect(dialog.getByRole('button', { name: 'Improve work description', exact: true })).toBeEnabled()
  expect(fixture!.events().filter(row => row.kind === 'models')).toHaveLength(2)

  return before
}

test.describe('host-native subscription setup', () => {
  test.skip(process.platform !== 'darwin', 'Production protected subscription storage is currently macOS-only')

  for (const legacy of [false, true]) {
    test(`${legacy ? 'legacy-empty' : 'fresh'} profile consents, chooses policy, reviews Improve, generates and explicitly creates`, async () => {
      fixture = await setupNativeSubscription({ legacy })
      const dialog = await openSetup(legacy)
      const before = await activate(dialog, legacy)
      await completeInference(
        dialog.getByRole('button', { name: 'Improve work description', exact: true }),
        'improveTeamInput'
      )
      const suggestion = dialog.getByRole('region', { name: 'Review suggested wording', exact: true })
      await expect(suggestion.getByLabel('Suggested text', { exact: true })).toHaveValue(suggestedJobs)
      await expect(dialog.getByLabel('Work to delegate', { exact: true })).toHaveValue(jobs)
      expect(fixture.snapshot().teams).toEqual([])
      fixture.assertQuiet(1)
      await suggestion.getByLabel('Suggested text', { exact: true }).fill(suggestedJobs + ' Keep scope small.')
      await suggestion.getByRole('button', { name: 'Apply suggestion', exact: true }).click()
      await expect(dialog.getByLabel('Work to delegate', { exact: true })).toHaveValue(
        suggestedJobs + ' Keep scope small.'
      )
      await expect(dialog.getByLabel('Work to delegate', { exact: true })).toBeFocused()
      fixture.assertQuiet(1)
      await completeInference(
        dialog.getByRole('button', { name: 'Improve success description', exact: true }),
        'improveTeamInput'
      )
      await expect(suggestion.getByLabel('Suggested text', { exact: true })).toHaveValue(suggestedGoals)
      await expect(dialog.getByLabel('What success looks like', { exact: true })).toHaveValue(goals)
      await suggestion.getByRole('button', { name: 'Discard suggestion', exact: true }).click()
      await expect(dialog.getByLabel('What success looks like', { exact: true })).toHaveValue(goals)
      fixture.assertQuiet(2)
      await completeInference(
        dialog.getByRole('button', { name: 'Generate team · Recommended', exact: true }),
        'generateTeam'
      )
      await expect(dialog.getByLabel('Name', { exact: true }).first()).toHaveValue('Ash', { timeout: 30_000 })
      fixture.assertQuiet(3)
      expect(fixture.snapshot().teams).toEqual([])
      await dialog.getByLabel('Name', { exact: true }).first().fill('Ash Reviewed')
      await expect(dialog.getByRole('button', { name: 'Create team', exact: true })).toHaveCount(0)
      await dialog.getByRole('button', { name: 'Review changes', exact: true }).click()
      await expect(dialog.getByRole('button', { name: 'Create team', exact: true })).toBeEnabled()
      expect(fixture.snapshot().teams).toEqual([])
      await test
        .info()
        .attach('subscription-reviewed-team', { contentType: 'image/png', body: await dialog.screenshot() })
      await dialog.getByRole('button', { name: 'Create team', exact: true }).click()
      await expect(fixture.page.getByRole('dialog', { name: 'Team saved', exact: true })).toBeVisible()
      const after = fixture.snapshot()
      expect(after.teams).toHaveLength(1)
      expect(after.model).toEqual(before.model)
      expect(after.generations).toHaveLength(legacy ? 4 : 3)
      expect(after.outcomes).toHaveLength(3)
      expect(after.dispatches).toHaveLength(3)
      expect(Object.values(after.counts).every(count => count === 0)).toBe(true)

      if (legacy) {
        expect(after.generations.find(row => row.idempotency_key === 'synthetic-legacy')).toEqual(before.generations[0])
      }

      expect(
        fixture
          .events()
          .filter(row => row.kind === 'responses')
          .map(row => row.operation)
      ).toEqual(['improve_jobs', 'improve_goals', 'generate'])
      expect(
        fixture
          .events()
          .filter(row => row.kind === 'responses')
          .every(row => row.durableDispatch === true)
      ).toBe(true)
      fixture.assertQuiet(3)
      await fixture.page
        .getByRole('dialog', { name: 'Team saved', exact: true })
        .getByRole('button', { name: 'Cancel', exact: true })
        .click()

      const navigation = fixture.page
        .getByRole('complementary', { name: 'Eidolon navigation' })
        .getByRole('navigation', { name: 'Primary', exact: true })

      await navigation.getByRole('link', { name: 'Organization', exact: true }).click()
      await expect(fixture.page.getByText('Ash Reviewed', { exact: true }).first()).toBeVisible()
      await expect(fixture.page.getByText('Ember', { exact: true }).first()).toBeVisible()
      await expect(fixture.page.getByText('Flint', { exact: true }).first()).toBeVisible()
      fixture.assertQuiet(3)
      // The saved team removes setup entry. Lifecycle management remains in
      // Settings and must not turn a configured organization into a fresh one.
      const headPath = path.join(fixture.sandbox.hermesHome, '.setup-subscription-private', 'current.json')
      const beforeHead = JSON.parse(readFileSync(headPath, 'utf8')).body
      expect(beforeHead.phase).toBe('active')
      const eventsBeforeSettings = fixture.events()
      await fixture.page
        .getByRole('complementary', { name: 'Eidolon navigation' })
        .getByRole('link', { name: 'Settings', exact: true })
        .click()
      const settings = fixture.page.locator('.eid-settings-page')
      await settings.getByRole('button', { name: 'Organization', exact: true }).click()
      const management = settings.getByRole('region', { name: 'ChatGPT setup connection', exact: true })
      await expect(management.getByText('Setup connection saved', { exact: true })).toBeVisible()
      await expect(
        management.getByText(
          'Reconnect is unavailable after the team is created or organization work has begun. You can still check, recover or disconnect this setup-only connection here.',
          { exact: true }
        )
      ).toBeVisible()
      await expect(management.getByRole('link', { name: 'Open team setup on Home', exact: true })).toHaveCount(0)
      await expect(management.getByRole('checkbox')).toHaveCount(0)
      await expect(management.getByRole('combobox')).toHaveCount(0)

      for (const name of [
        'Continue with ChatGPT',
        'Load account models',
        'Review setup policy',
        'Apply reviewed policy',
        'Generate team · Recommended',
        'Improve work description'
      ]) {
        await expect(management.getByRole('button', { name, exact: true })).toHaveCount(0)
      }

      expect(fixture.events()).toEqual(eventsBeforeSettings)
      expect(fixture.snapshot()).toEqual(after)
      fixture.assertQuiet(3)
      await management.getByRole('button', { name: 'Disconnect setup connection', exact: true }).click()
      await expect(management.getByText('Setup connection disconnected locally', { exact: true })).toBeVisible()
      await expect(
        management.getByText(
          'Local access is retired. Remote revocation is not confirmed; usage already in progress may continue.',
          { exact: true }
        )
      ).toBeVisible()
      const retiredHead = JSON.parse(readFileSync(headPath, 'utf8')).body
      expect(retiredHead.phase).toBe('disconnected')
      expect(retiredHead.target).toBeNull()
      expect(retiredHead.revision).toBeGreaterThan(beforeHead.revision)
      const afterDisconnect = fixture.snapshot()
      expect(afterDisconnect).toEqual(after)
      expect(fixture.hasPrivateStore()).toBe(true)
      expect(
        fixture.events().filter(row => ['consent', 'oauth-exchange', 'models', 'responses'].includes(row.kind))
      ).toEqual(
        eventsBeforeSettings.filter(row => ['consent', 'oauth-exchange', 'models', 'responses'].includes(row.kind))
      )
      fixture.assertQuiet(3)
      await test
        .info()
        .attach('subscription-settings-disconnected', { contentType: 'image/png', body: await management.screenshot() })
      await test.info().attach('subscription-native-proof', {
        contentType: 'application/json',
        body: JSON.stringify({
          platform: process.platform,
          legacy,
          events: fixture.events(),
          installed: fixture.installed(),
          before,
          after,
          afterDisconnect,
          localRetirement: {
            phase: retiredHead.phase,
            revision: retiredHead.revision,
            hasTarget: retiredHead.target !== null
          }
        })
      })
    })
  }

  test('known unsent inference recovers through one explicit retry without losing debt', async () => {
    allowErrorBanners()
    fixture = await setupNativeSubscription({ legacy: true, recoveryFault: 'sqlite_dispatch_once' })
    const dialog = await openSetup(true)
    const before = await activate(dialog, true)
    const improve = dialog.getByRole('button', { name: 'Improve work description', exact: true })
    await completeInference(improve, 'improveTeamInput', 'unavailable')
    const retry = dialog.getByRole('button', { name: 'Retry unsent request', exact: true })
    await expect(retry).toBeVisible({ timeout: 20_000 })
    await expect(dialog.getByRole('button', { name: 'Check configuration', exact: true })).toBeEnabled({
      timeout: 20_000
    })
    await expect(retry).toBeEnabled()
    await expect(dialog.getByLabel('Work to delegate', { exact: true })).toHaveValue(jobs)
    await expect(dialog.getByLabel('What success looks like', { exact: true })).toHaveValue(goals)
    const faults = fixture.events().filter(row => row.kind === 'recovery-fault')
    expect(faults).toHaveLength(1)
    expect(faults[0]).toMatchObject({ fault: 'sqlite_dispatch_once', triggered: true, restored: true })
    const failed = fixture.snapshot()
    const unsent = failed.generations.filter(row => row.idempotency_key !== 'synthetic-legacy')
    expect(unsent).toHaveLength(1)
    expect(unsent[0]).toMatchObject({ status: 'failed', dispatched: 0, result_json: null })
    expect(failed.dispatches).toEqual([])
    expect(failed.outcomes).toEqual([])
    expect(failed.teams).toEqual([])
    expect(failed.generations.find(row => row.idempotency_key === 'synthetic-legacy')).toEqual(before.generations[0])
    fixture.assertQuiet(0)
    const retained = await fixture.performOperation('improveTeamInput', () => improve.click())
    expect(retained).toMatchObject({
      method: 'organization.improveTeamInput',
      phase: 'error',
      code: 5076,
      reason: 'generation_failed'
    })
    await expect(retry).toBeEnabled({ timeout: 20_000 })
    expect(fixture.snapshot()).toEqual(failed)
    fixture.assertQuiet(0)
    await completeInference(retry, 'improveTeamInput')
    await expect(
      dialog
        .getByRole('region', { name: 'Review suggested wording', exact: true })
        .getByLabel('Suggested text', { exact: true })
    ).toHaveValue(suggestedJobs)
    await expect(retry).toHaveCount(0)
    const after = fixture.snapshot()
    expect(after.generations).toHaveLength(3)
    expect(after.generations.find(row => row.idempotency_key === unsent[0].idempotency_key)).toEqual(unsent[0])
    expect(after.generations.find(row => row.idempotency_key === 'synthetic-legacy')).toEqual(before.generations[0])
    const completed = after.generations.filter(row => row.status === 'completed')
    expect(completed).toHaveLength(1)
    expect(completed[0].idempotency_key).not.toBe(unsent[0].idempotency_key)
    expect(completed[0].dispatched).toBe(1)
    expect(after.dispatches).toHaveLength(1)
    expect(after.outcomes).toHaveLength(1)
    expect(after.model).toEqual(before.model)
    expect(after.executionPolicy).toEqual(failed.executionPolicy)
    expect(after.subscriptionPolicy).toEqual(failed.subscriptionPolicy)
    expect(after.teams).toEqual([])
    expect(Object.values(after.counts).every(count => count === 0)).toBe(true)
    fixture.assertQuiet(1)
    // The now-completed explicit intent is replayed, never sent again.
    await completeInference(improve, 'improveTeamInput')
    expect(fixture.snapshot()).toEqual(after)
    fixture.assertQuiet(1)
    await test.info().attach('subscription-unsent-recovered', {
      contentType: 'image/png',
      body: await dialog.screenshot()
    })
    await test.info().attach('subscription-unsent-recovery-proof', {
      contentType: 'application/json',
      body: JSON.stringify({ before, failed, after, events: fixture.events() })
    })
  })

  test('initial publication refusal recovers locally before explicit new consent', async () => {
    allowErrorBanners()
    fixture = await setupNativeSubscription({ recoveryFault: 'acl_publication_once' })
    const dialog = await openSetup(false)
    const before = fixture.snapshot()
    const connection = dialog.getByRole('region', { name: 'ChatGPT plan for setup', exact: true })
    await connection.getByRole('button', { name: 'Set up ChatGPT', exact: true }).click()

    const consent = connection.getByRole('checkbox', {
      name: 'Allow this dedicated connection to be stored locally',
      exact: true
    })

    await consent.check()
    await connection.getByRole('button', { name: 'Continue with ChatGPT', exact: true }).click()

    const blocked = connection.getByText(
      'Setup cannot proceed with the current policy or uncertain request. Check status; no request will be resent automatically.',
      { exact: true }
    )

    // The explicit action plus setup/status/policy each use a 15s RPC envelope.
    await expect(blocked).toBeVisible({ timeout: 65_000 })
    const faults = fixture.events().filter(row => row.kind === 'recovery-fault')
    expect(faults).toHaveLength(1)
    expect(faults[0]).toMatchObject({
      fault: 'acl_publication_once',
      triggered: true,
      restored: true,
      nativePutCount: 1
    })
    expect(fixture.events().filter(row => row.kind === 'oauth-exchange')).toHaveLength(1)
    expect(fixture.events().filter(row => row.kind === 'models')).toHaveLength(0)
    expect(fixture.snapshot()).toEqual(before)
    fixture.assertQuiet(0)
    await expect(connection.getByRole('button', { name: 'Continue with ChatGPT', exact: true })).toHaveCount(0)
    await expect(consent).toHaveCount(0)
    const recover = connection.getByRole('button', { name: 'Reconcile saved operation', exact: true })
    await expect(recover).toBeEnabled()
    await recover.click()
    // Recover plus its setup/status/policy reads: four 15s envelopes + 5s UI.
    await expect(
      connection.getByText(
        'The saved authorization attempt was retired locally. Remote revocation is unconfirmed. Connect again only when you choose to.',
        { exact: true }
      )
    ).toBeVisible({ timeout: 65_000 })
    await expect(blocked).toHaveCount(0)
    await expect(connection.getByRole('button', { name: 'Check connection', exact: true })).toBeEnabled()
    const checkpoints = fixture.events().filter(row => row.kind === 'recovery-checkpoint')
    expect(checkpoints).toHaveLength(1)
    expect(checkpoints[0]).toMatchObject({
      fault: 'acl_publication_once',
      triggered: true,
      restored: true,
      nativePutCount: 1,
      nativeDeleteCount: 1
    })
    expect(fixture.events().filter(row => row.kind === 'oauth-exchange')).toHaveLength(1)
    expect(fixture.events().filter(row => row.kind === 'models')).toHaveLength(0)
    expect(fixture.snapshot()).toEqual(before)
    fixture.assertQuiet(0)
    await expect(consent).not.toBeChecked()
    const begin = connection.getByRole('button', { name: 'Continue with ChatGPT', exact: true })
    await expect(begin).toBeDisabled()
    await consent.check()
    await begin.click()
    const load = connection.getByRole('button', { name: 'Load account models', exact: true })
    await expect(load).toBeEnabled({ timeout: 65_000 })
    expect(fixture.events().filter(row => row.kind === 'consent')).toHaveLength(2)
    expect(fixture.events().filter(row => row.kind === 'oauth-exchange')).toHaveLength(2)
    await load.click()
    await connection.getByLabel('Setup model', { exact: true }).selectOption(subscriptionModel)
    expect(fixture.events().filter(row => row.kind === 'models')).toHaveLength(1)
    expect(fixture.snapshot()).toEqual(before)
    fixture.assertQuiet(0)
    await test.info().attach('subscription-publication-recovered', {
      contentType: 'image/png',
      body: await connection.screenshot()
    })
    await test.info().attach('subscription-publication-recovery-proof', {
      contentType: 'application/json',
      body: JSON.stringify({ before, after: fixture.snapshot(), events: fixture.events() })
    })
  })

  test('uncertain synthetic inference blocks a new intent without retry or refund', async () => {
    allowErrorBanners()
    fixture = await setupNativeSubscription({ uncertain: true })
    const dialog = await openSetup(false)
    await activate(dialog, false)
    await completeInference(
      dialog.getByRole('button', { name: 'Improve work description', exact: true }),
      'improveTeamInput',
      'unavailable'
    )
    await expect(dialog.getByRole('alert')).toHaveText(
      'Could not confirm setup. Retry the same action to check its outcome.'
    )
    // act() refreshes setup after the error before clearing busy: allow its
    // existing 15s read RPC envelope plus the normal 5s UI assertion budget.
    await expect(dialog.getByRole('button', { name: 'Check configuration', exact: true })).toBeEnabled({
      timeout: 20_000
    })
    await expect(dialog.getByRole('button', { name: 'Generate team · Recommended', exact: true })).toBeDisabled()
    await expect(dialog.getByRole('button', { name: 'Improve work description', exact: true })).toBeDisabled()
    await expect(dialog.getByRole('button', { name: 'Improve success description', exact: true })).toBeDisabled()
    expect(fixture!.snapshot().generations[0]?.status).toBe('uncertain')
    fixture.assertQuiet(1)
    const after = fixture.snapshot()
    expect(after.generations[0].dispatched).toBe(1)
    expect(after.outcomes).toEqual([])
    expect(after.teams).toEqual([])
    await expect(dialog.getByRole('button', { name: 'Generate team · Recommended', exact: true })).toBeDisabled()
    // Changing text cannot turn unknown provider work into a new admitted call.
    await dialog.getByLabel('Work to delegate', { exact: true }).fill('A different synthetic request')
    await expect(dialog.getByRole('button', { name: 'Improve work description', exact: true })).toBeDisabled()
    await expect(dialog.getByRole('button', { name: 'Improve success description', exact: true })).toBeDisabled()
    fixture.assertQuiet(1)
    expect(fixture.snapshot()).toEqual(after)
    await test
      .info()
      .attach('subscription-uncertain-blocked', { contentType: 'image/png', body: await dialog.screenshot() })
    await test.info().attach('subscription-uncertain-proof', {
      contentType: 'application/json',
      body: JSON.stringify({ events: fixture.events(), after })
    })
  })
})
