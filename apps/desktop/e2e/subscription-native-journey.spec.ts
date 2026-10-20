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
import { cleanupAndReport } from './subscription-native-lifecycle'
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
      const closing = fixture

      if (closing) {
        await cleanupAndReport(closing.cleanup, async () => {
          await test.info().attach('subscription-teardown-diagnostic', {
            contentType: 'application/json',
            body: JSON.stringify(closing.cleanupDiagnostic(), null, 2)
          })
        })
      }
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

async function completeModelLoad(connection: Locator, expected: 'unreviewed' | 'ready' | 'stale-policy' = 'unreviewed') {
  const result = await fixture!.performSetupAction('models', () =>
    connection.getByRole('button', { name: 'Load account models', exact: true }).click()
  )

  const steps = result.steps as { method: string; phase: string; shape: Record<string, unknown> }[]
  expect(steps).toHaveLength(5)
  expect(steps.every(row => row.phase === 'result')).toBe(true)
  expect(steps[1].shape).toMatchObject({ connectionState: 'active', cleanupPending: false })
  expect(steps[3].shape).toMatchObject({ sourceMatches: true, connectionRevisionMatchesRequest: true })
  // The old epoch policy may still be present after migration. Readiness is
  // required only after the owner applies the policy for this exact grant.
  expect(steps[4].shape).toMatchObject({ state: 'available', organizationMatches: true, sourceMatchesAction: true })

  if (expected === 'ready') {
    expect(steps[3].shape).toMatchObject({ catalogEpochMatchesPolicy: true, catalogConnectionMatchesPolicy: true })
    expect(steps[4].shape).toMatchObject({
      readinessStatus: 'unchecked',
      readinessBlockers: [],
      modelAvailability: 'cached'
    })
  } else if (expected === 'stale-policy') {
    expect(steps[3].shape).toMatchObject({ catalogEpochMatchesPolicy: false, catalogConnectionMatchesPolicy: true })
    expect(steps[4].shape).toMatchObject({
      policyVersionThree: true, readinessStatus: 'blocked', readinessBlockers: ['subscription_models_required']
    })
  }
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
  const checkConfiguration = dialog.getByRole('button', { name: 'Check configuration', exact: true })
  await expect(checkConfiguration).toBeEnabled()
  await completeCheckConfiguration(checkConfiguration)
  await dialog.getByLabel('Work to delegate', { exact: true }).fill(jobs)
  await dialog.getByLabel('What success looks like', { exact: true }).fill(goals)
  await dialog.getByLabel('Team naming style (optional)', { exact: true }).fill('Ash, Ember and Flint')

  return dialog
}

// This owns only the explicit Check action. Unknown earlier bootstrap reads
// still refuse before the click; socket identity is not client provenance.
async function completeCheckConfiguration(button: Locator) {
  const result = await fixture!.performSetupAction('checkConfiguration', () => button.click())
  const steps = result.steps as { method: string; phase: string; shape: Record<string, unknown> }[]
  expect(steps).toHaveLength(1)
  expect(steps[0]).toMatchObject({
    method: 'organization.teamSetup',
    phase: 'result',
    shape: {
      result: 'object',
      capabilityVersionOne: true,
      state: 'available',
      organizationMatches: true,
      requestOrganizationMatchesAction: true,
      sourceMatchesAction: true,
      sourceRevisionValid: true,
      organizationIdValid: true
    }
  })
}

async function completePolicyApply(apply: Locator, hostIdentityState: 'current' | 'migration_required') {
  const result = await fixture!.performSetupAction('policy', () => apply.click())
  const steps = result.steps as { method: string; phase: string; shape: Record<string, unknown> }[]
  expect(steps).toHaveLength(4)
  expect(steps.every(row => row.phase === 'result')).toBe(true)
  expect(steps[0].shape).toMatchObject({ policyReceiptMatchesRequest: true })
  expect(steps[1].shape).toMatchObject({
    state: 'available', organizationMatches: true, sourceMatchesAction: true,
    policyVersionThree: true, subscriptionMode: true,
    readinessStatus: 'blocked', readinessBlockers: ['subscription_models_required']
  })
  expect(steps[2].shape).toMatchObject({ connectionState: 'active', hostIdentityState, cleanupPending: false })
  expect(steps[3].shape).toMatchObject({
    state: 'available', organizationMatches: true, sourceMatchesAction: true,
    savedPolicyVersionThree: true, policyGenerationMatchesApplied: true
  })
}

// Each scenario names its exact terminal contract. Completion observes only the
// existing click's replies; the original UI and accounting checks follow it.
async function completeConnectionAction(
  button: Locator,
  scenario:
    | 'inspect-new'
    | 'inspect-legacy'
    | 'inspect-cancelled'
    | 'begin-connected'
    | 'begin-publication-refused'
    | 'migration-held'
    | 'migration-connected'
    | 'cancel-migration'
    | 'recover-publication'
    | 'settings-disconnect'
) {
  const kind = {
    'inspect-new': 'inspect',
    'inspect-legacy': 'inspect',
    'inspect-cancelled': 'inspect',
    'begin-connected': 'begin',
    'begin-publication-refused': 'begin',
    'migration-held': 'migration',
    'migration-connected': 'migration',
    'cancel-migration': 'cancel',
    'recover-publication': 'recover',
    'settings-disconnect': 'settingsDisconnect'
  } as const

  const result = await fixture!.performSetupAction(kind[scenario], () => button.click())
  const steps = result.steps as { method: string; phase: string; shape: Record<string, unknown> }[]
  const count = { inspect: 3, begin: 4, migration: 7, cancel: 4, recover: 4, settingsDisconnect: 6 }
  expect(steps).toHaveLength(count[kind[scenario]])
  expect(steps.every(row => row.phase === 'result' && row.shape.result === 'object')).toBe(true)

  for (const row of steps) {
    expect(row.shape).toMatchObject({ requestOrganizationMatchesAction: true })

    if (row.method === 'organization.teamSetup') {
      expect(row.shape).toMatchObject({
        state: scenario === 'settings-disconnect' ? 'configured' : 'available',
        organizationMatches: true,
        sourceMatchesAction: true
      })
    }

    if (row.method === 'organization.setupSubscriptionStatus') {
      expect(row.shape).toMatchObject({ canCancelValid: true })
    }

    if (row.method === 'organization.setupSubscriptionPolicy') {
      expect(row.shape).toMatchObject({ state: 'available', organizationMatches: true, sourceMatchesAction: true })
    }
  }

  const terminal = steps[scenario === 'settings-disconnect' ? 5 : steps.length - 2].shape
  expect(terminal).toMatchObject({ connectionRevisionValid: true, operationRevisionMatchesConnection: true })

  if (scenario === 'inspect-new') {
    expect(terminal).toMatchObject({
      connectionState: 'absent',
      hostIdentityState: 'new',
      stage: 'idle',
      hasAttempt: false,
      cleanupPending: false,
      canMigrateHostIdentity: false
    })
  } else if (scenario === 'inspect-legacy') {
    expect(terminal).toMatchObject({
      connectionState: 'active',
      hostIdentityState: 'migration_required',
      stage: 'idle',
      hasAttempt: false,
      cleanupPending: false,
      canMigrateHostIdentity: false
    })
  } else if (scenario === 'inspect-cancelled' || scenario === 'cancel-migration') {
    expect(terminal).toMatchObject({
      connectionState: 'disconnected',
      hostIdentityState: 'migration_required',
      stage: 'cancelled',
      hasAttempt: true,
      cleanupPending: false,
      canCancel: false,
      canMigrateHostIdentity: true
    })

    if (scenario === 'cancel-migration') {
      expect(steps[0].shape).toMatchObject({
        stage: 'cancelled',
        cancelAttemptMatchesRequest: true,
        requestSourceMatchesAction: true
      })
    }
  } else if (scenario === 'begin-publication-refused') {
    expect(steps[0].shape).toMatchObject({
      stage: 'publication_uncertain',
      hasAttempt: true,
      requestSourceMatchesAction: true
    })
    expect(terminal).toMatchObject({
      connectionState: 'disconnected',
      hostIdentityState: 'current',
      stage: 'publication_uncertain',
      cleanupPending: true,
      canCancel: false,
      canMigrateHostIdentity: false,
      operationState: 'recovery_required'
    })
  } else if (scenario === 'recover-publication') {
    expect(steps[0].shape).toMatchObject({
      state: 'local_retired',
      remoteRevocation: 'unconfirmed',
      requestSourceMatchesAction: true
    })
    expect(terminal).toMatchObject({
      connectionState: 'disconnected',
      hostIdentityState: 'current',
      stage: 'locally_retired',
      cleanupPending: false,
      canCancel: false,
      canMigrateHostIdentity: false,
      operationState: 'none'
    })
  } else if (scenario === 'settings-disconnect') {
    expect(steps[1].shape).toMatchObject({
      connectionState: 'active',
      hostIdentityState: 'current',
      cleanupPending: false
    })
    expect(steps[2].shape).toMatchObject({
      state: 'prepared',
      preparedTicketValid: true,
      requestRevisionMatchesAction: true,
      requestSourceMatchesAction: true
    })
    expect(steps[3].shape).toMatchObject({
      state: 'admitted',
      executeTicketMatchesPrepared: true,
      requestSourceMatchesAction: true,
      remoteRevocation: 'unconfirmed'
    })
    expect(terminal).toMatchObject({
      connectionState: 'disconnected',
      hostIdentityState: 'current',
      cleanupPending: false,
      operationState: 'admitted'
    })
  } else {
    const migration = scenario === 'migration-held' || scenario === 'migration-connected'
    const begin = steps[migration ? 3 : 0].shape

    expect(begin).toMatchObject({ requestSourceMatchesAction: true })

    if (migration) {
      expect(steps[1].shape).toMatchObject({
        connectionState: 'disconnected',
        hostIdentityState: 'migration_required',
        cleanupPending: false,
        canMigrateHostIdentity: true,
        operationState: 'completed'
      })
      expect(begin).toMatchObject({ requestRevisionMatchesAction: true, requestSourceMatchesAction: true })
      expect(steps[1].shape.canCancel === true).toBe(false)
      expect(steps[1].shape).toMatchObject({ connectionRevisionValid: true, operationRevisionMatchesConnection: true })
      expect(steps[1].shape).toMatchObject(
        scenario === 'migration-held'
          ? { stage: 'idle', hasAttempt: false }
          : { stage: 'cancelled', hasAttempt: true, canCancel: false }
      )
    }

    if (scenario === 'migration-held') {
      expect(begin).toMatchObject({ stage: 'pending', hasAttempt: true })
      expect(terminal).toMatchObject({
        connectionState: 'disconnected',
        hostIdentityState: 'migration_required',
        stage: 'pending',
        hasAttempt: true,
        cleanupPending: false,
        canCancel: true,
        canMigrateHostIdentity: false
      })
    } else {
      expect(begin).toMatchObject({ stage: 'connected', hasAttempt: true })
      expect(terminal).toMatchObject({
        connectionState: 'active',
        hostIdentityState: 'current',
        stage: 'connected',
        cleanupPending: false,
        canCancel: false,
        canMigrateHostIdentity: false
      })
    }
  }
}

async function activate(dialog: Locator, legacy: boolean) {
  const connection = dialog.getByRole('region', { name: 'ChatGPT plan for setup', exact: true })
  const before = fixture!.snapshot()
  expect(before.generations).toHaveLength(legacy ? 1 : 0)
  fixture!.assertQuiet(0)
  await completeConnectionAction(connection.getByRole('button', { name: 'Set up ChatGPT', exact: true }), 'inspect-new')

  const consent = connection.getByRole('checkbox', {
    name: 'Allow this dedicated connection to be stored locally',
    exact: true
  })

  await expect(consent).not.toBeChecked()
  const begin = connection.getByRole('button', { name: 'Continue with ChatGPT', exact: true })
  await expect(begin).toBeDisabled()
  expect(fixture!.events().filter(row => row.kind === 'consent')).toEqual([])
  await consent.check()
  await completeConnectionAction(begin, 'begin-connected')
  const load = connection.getByRole('button', { name: 'Load account models', exact: true })
  await expect(load).toBeEnabled({ timeout: 30_000 })
  expect(fixture!.hasPrivateStore()).toBe(true)
  expect(fixture!.events().filter(row => row.kind === 'consent')).toHaveLength(1)
  expect(fixture!.events().filter(row => row.kind === 'oauth-exchange')).toHaveLength(1)
  expect(fixture!.events().filter(row => row.kind === 'models')).toEqual([])
  fixture!.assertQuiet(0)
  await completeModelLoad(connection)
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
  await completePolicyApply(apply, 'current')
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
  await completeModelLoad(connection, 'ready')
  await expect(dialog.getByRole('button', { name: 'Improve work description', exact: true })).toBeEnabled()
  expect(fixture!.events().filter(row => row.kind === 'models')).toHaveLength(2)

  return before
}

// Used only by the migration scenario; both policy paths await their exact reads.
async function reviewMigrationPolicy(
  connection: Locator,
  hostIdentityState: 'migration_required' | 'current',
  stalePolicy = false
) {
  const before = fixture!.snapshot()
  await completeModelLoad(connection, stalePolicy ? 'stale-policy' : 'unreviewed')

  if (stalePolicy) {
    const dialog = fixture!.page.getByRole('dialog', { name: 'Build your starter team', exact: true })
    await expect(dialog.getByRole('button', { name: 'Generate team · Recommended', exact: true })).toBeDisabled()
    await expect(dialog.getByRole('button', { name: 'Improve work description', exact: true })).toBeDisabled()
    await expect(dialog.getByRole('button', { name: 'Improve success description', exact: true })).toBeDisabled()
  }

  await connection.getByLabel('Setup model', { exact: true }).selectOption(subscriptionModel)
  await connection.getByLabel('Improve calls', { exact: true }).fill('4')
  await connection.getByLabel('Generate / Regenerate calls', { exact: true }).fill('3')
  await connection.getByLabel('Total setup calls', { exact: true }).fill('7')
  await connection.getByLabel('Local timeout (seconds)', { exact: true }).fill('120')
  await connection.getByRole('button', { name: 'Review setup policy', exact: true }).click()
  const review = connection.getByRole('region', { name: 'Review setup policy', exact: true })
  await expect(review.getByText(/^Previous calls retained: 1, including 1 legacy calls\./)).toBeVisible()
  const acknowledgement = connection.getByRole('checkbox', { name: subscriptionAck, exact: true })
  await expect(acknowledgement).not.toBeChecked()
  const apply = connection.getByRole('button', { name: 'Apply reviewed policy', exact: true })
  await expect(apply).toBeDisabled()
  expect(fixture!.snapshot()).toEqual(before)
  fixture!.assertQuiet(0)
  await acknowledgement.check()
  await completePolicyApply(apply, hostIdentityState)
  await expect(
    connection.getByText('Policy saved. Load account models again before Generate or Improve.', { exact: true })
  ).toBeVisible()
  fixture!.assertQuiet(0)
}

test.describe('host-native subscription setup', () => {
  test.skip(process.platform !== 'darwin', 'Production protected subscription storage is currently macOS-only')

  for (const legacy of [false, true]) {
    test(`${legacy ? 'legacy-empty' : 'fresh'} profile consents, chooses policy, reviews Improve, generates and explicitly creates`, async () => {
      fixture = await setupNativeSubscription({
        legacy,
        availabilityProbe: legacy ? 'scheduler_writer_once' : 'ancestor_final_path_once'
      })
      const dialog = await openSetup(legacy)
      const before = await activate(dialog, legacy)
      await completeInference(
        dialog.getByRole('button', { name: 'Improve work description', exact: true }),
        'improveTeamInput'
      )
      const probes = fixture.events().filter(row => row.kind === 'availability-probe')
      expect(probes).toHaveLength(1)
      expect(probes[0]).toMatchObject({
        probe: legacy ? 'scheduler_writer_once' : 'ancestor_final_path_once',
        triggered: true,
        restored: true,
        renewed: !legacy,
        queued: legacy
      })
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
      await completeConnectionAction(management.getByRole('button', { name: 'Disconnect setup connection', exact: true }), 'settings-disconnect')
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

  test('stranded unsent inference reconciles locally before one explicit retry', async () => {
    allowErrorBanners()
    fixture = await setupNativeSubscription({ legacy: true, availabilityProbe: 'scheduler_cleanup_once' })
    const dialog = await openSetup(true)
    const before = await activate(dialog, true)
    const improve = dialog.getByRole('button', { name: 'Improve work description', exact: true })
    await completeInference(improve, 'improveTeamInput', 'unavailable')
    const check = dialog.getByRole('button', { name: 'Check unsent request', exact: true })
    const retry = dialog.getByRole('button', { name: 'Retry unsent request', exact: true })
    await expect(check).toBeEnabled({ timeout: 20_000 })
    await expect(retry).toHaveCount(0)
    // The actual retained cleanup worker uses a 5s accounting deadline. This
    // concrete checkpoint is emitted only after its refusal and holder unwind.
    await expect
      .poll(() => fixture!.events().filter(row => row.kind === 'availability-probe').length, {
        timeout: 10_000
      })
      .toBe(1)
    const probe = fixture.events().find(row => row.kind === 'availability-probe')!
    expect(probe).toMatchObject({
      probe: 'scheduler_cleanup_once',
      triggered: true,
      queued: true,
      cleanupRefused: true,
      restored: true
    })
    const running = fixture.snapshot()
    const unsent = running.generations.filter(row => row.idempotency_key !== 'synthetic-legacy')
    expect(unsent).toHaveLength(1)
    expect(unsent[0]).toMatchObject({ status: 'running', dispatched: 0, result_json: null })
    expect(running.dispatches).toEqual([])
    expect(running.outcomes).toEqual([])
    expect(running.generations.find(row => row.idempotency_key === 'synthetic-legacy')).toEqual(before.generations[0])
    fixture.assertQuiet(0)

    const eventCounts = () =>
      Object.fromEntries(
        ['oauth-exchange', 'jwks', 'models', 'responses'].map(kind => [
          kind,
          fixture!.events().filter(row => row.kind === kind).length
        ])
      )

    const beforeCheck = eventCounts()
    await check.click()
    // One explicit local Check and its passive setup refresh: 2 x 15s + 5s UI.
    await expect(retry).toBeEnabled({ timeout: 35_000 })
    const checked = fixture.snapshot()
    const expected = structuredClone(running)
    expected.generations.find(row => row.idempotency_key === unsent[0].idempotency_key)!.status = 'failed'
    expect(checked).toEqual(expected)
    expect(eventCounts()).toEqual(beforeCheck)
    const checkpoints = fixture.events().filter(row => row.kind === 'reconciliation-checkpoint')
    expect(checkpoints).toHaveLength(1)
    expect(checkpoints[0]).toMatchObject({
      nativePutCount: probe.nativePutCount,
      nativeDeleteCount: probe.nativeDeleteCount
    })
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
    expect(after.generations.find(row => row.idempotency_key === unsent[0].idempotency_key)).toEqual({
      ...unsent[0],
      status: 'failed'
    })
    expect(after.generations.find(row => row.idempotency_key === 'synthetic-legacy')).toEqual(before.generations[0])
    const completed = after.generations.filter(row => row.status === 'completed')
    expect(completed).toHaveLength(1)
    expect(completed[0].idempotency_key).not.toBe(unsent[0].idempotency_key)
    expect(completed[0].dispatched).toBe(1)
    expect(after.dispatches).toHaveLength(1)
    expect(after.outcomes).toHaveLength(1)
    expect(after.model).toEqual(before.model)
    expect(after.executionPolicy).toEqual(checked.executionPolicy)
    expect(after.subscriptionPolicy).toEqual(checked.subscriptionPolicy)
    expect(after.teams).toEqual([])
    expect(Object.values(after.counts).every(count => count === 0)).toBe(true)
    fixture.assertQuiet(1)
    await completeInference(improve, 'improveTeamInput')
    expect(fixture.snapshot()).toEqual(after)
    fixture.assertQuiet(1)
    await test.info().attach('subscription-stranded-unsent-reconciled', {
      contentType: 'image/png',
      body: await dialog.screenshot()
    })
    await test.info().attach('subscription-stranded-unsent-proof', {
      contentType: 'application/json',
      body: JSON.stringify({ before, running, checked, after, events: fixture.events() })
    })
  })

  test('initial publication refusal recovers locally before explicit new consent', async () => {
    allowErrorBanners()
    fixture = await setupNativeSubscription({ recoveryFault: 'acl_publication_once' })
    const dialog = await openSetup(false)
    const before = fixture.snapshot()
    const connection = dialog.getByRole('region', { name: 'ChatGPT plan for setup', exact: true })
    await completeConnectionAction(connection.getByRole('button', { name: 'Set up ChatGPT', exact: true }), 'inspect-new')

    const consent = connection.getByRole('checkbox', {
      name: 'Allow this dedicated connection to be stored locally',
      exact: true
    })

    await consent.check()
    await completeConnectionAction(connection.getByRole('button', { name: 'Continue with ChatGPT', exact: true }), 'begin-publication-refused')

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
    await completeConnectionAction(recover, 'recover-publication')
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
    await completeConnectionAction(begin, 'begin-connected')
    const load = connection.getByRole('button', { name: 'Load account models', exact: true })
    await expect(load).toBeEnabled({ timeout: 65_000 })
    expect(fixture.events().filter(row => row.kind === 'consent')).toHaveLength(2)
    expect(fixture.events().filter(row => row.kind === 'oauth-exchange')).toHaveLength(2)
    await completeModelLoad(connection)
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

  test('second open-window refusal recovers locally before explicit new consent', async () => {
    allowErrorBanners()
    fixture = await setupNativeSubscription({ recoveryFault: 'acl_open_recheck_once' })
    const dialog = await openSetup(false)
    const before = fixture.snapshot()
    const connection = dialog.getByRole('region', { name: 'ChatGPT plan for setup', exact: true })
    await completeConnectionAction(connection.getByRole('button', { name: 'Set up ChatGPT', exact: true }), 'inspect-new')

    const consent = connection.getByRole('checkbox', {
      name: 'Allow this dedicated connection to be stored locally',
      exact: true
    })

    await consent.check()
    await completeConnectionAction(connection.getByRole('button', { name: 'Continue with ChatGPT', exact: true }), 'begin-publication-refused')

    const blocked = connection.getByText(
      'Setup cannot proceed with the current policy or uncertain request. Check status; no request will be resent automatically.',
      { exact: true }
    )

    // The explicit action plus setup/status/policy each use a 15s RPC envelope.
    await expect(blocked).toBeVisible({ timeout: 65_000 })
    const faults = fixture.events().filter(row => row.kind === 'recovery-fault')
    expect(faults).toHaveLength(1)
    expect(faults[0]).toMatchObject({
      fault: 'acl_open_recheck_once',
      triggered: true,
      restored: true,
      nativePutCount: 1
    })
    const boundaries = fixture.diagnostic().boundaries

    const recheckIndex = boundaries.findIndex(
      row =>
        row.event === 'identity_mismatch' &&
        row.identity?.comparison === 'initial_recheck' &&
        row.identity?.ancestorPosition === 'parent' &&
        row.identity?.linksDirection === 'decrease'
    )

    expect(recheckIndex).toBeGreaterThan(0)
    const recheck = boundaries[recheckIndex]

    const initial = boundaries
      .slice(0, recheckIndex)
      .find(
        row =>
          row.event === 'identity_mismatch' &&
          row.lane === recheck.lane &&
          row.identity?.comparison === 'initial_fd' &&
          row.identity?.ancestorPosition === 'parent' &&
          row.identity?.linksDirection === 'increase'
      )

    expect(initial).toBeDefined()

    for (const row of [initial!, recheck]) {
      expect(row.identity?.changedFields).toMatchObject({
        device: false,
        inode: false,
        mode: false,
        owner: false,
        group: false,
        links: true
      })
    }

    expect(
      boundaries
        .slice(recheckIndex + 1)
        .some(
          row =>
            row.lane === recheck.lane &&
            row.stage === 'ancestor_acl' &&
            row.line === 80 &&
            row.reason === 'unsafe_filesystem'
        )
    ).toBe(true)
    expect(fixture.events().filter(row => row.kind === 'oauth-exchange')).toHaveLength(1)
    expect(fixture.events().filter(row => row.kind === 'jwks')).toHaveLength(1)
    expect(fixture.events().filter(row => row.kind === 'models')).toHaveLength(0)
    expect(fixture.snapshot()).toEqual(before)
    fixture.assertQuiet(0)
    await expect(connection.getByRole('button', { name: 'Continue with ChatGPT', exact: true })).toHaveCount(0)
    await expect(consent).toHaveCount(0)
    const recover = connection.getByRole('button', { name: 'Reconcile saved operation', exact: true })
    await expect(recover).toBeEnabled()
    await completeConnectionAction(recover, 'recover-publication')
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
      fault: 'acl_open_recheck_once',
      triggered: true,
      restored: true,
      nativePutCount: 1,
      nativeDeleteCount: 1
    })
    expect(fixture.events().filter(row => row.kind === 'oauth-exchange')).toHaveLength(1)
    expect(fixture.events().filter(row => row.kind === 'jwks')).toHaveLength(1)
    expect(fixture.events().filter(row => row.kind === 'models')).toHaveLength(0)
    expect(fixture.snapshot()).toEqual(before)
    fixture.assertQuiet(0)
    await expect(consent).not.toBeChecked()
    const begin = connection.getByRole('button', { name: 'Continue with ChatGPT', exact: true })
    await expect(begin).toBeDisabled()
    await consent.check()
    await completeConnectionAction(begin, 'begin-connected')
    const load = connection.getByRole('button', { name: 'Load account models', exact: true })
    await expect(load).toBeEnabled({ timeout: 65_000 })
    expect(fixture.events().filter(row => row.kind === 'consent')).toHaveLength(2)
    expect(fixture.events().filter(row => row.kind === 'oauth-exchange')).toHaveLength(2)
    await completeModelLoad(connection)
    await connection.getByLabel('Setup model', { exact: true }).selectOption(subscriptionModel)
    expect(fixture.events().filter(row => row.kind === 'models')).toHaveLength(1)
    expect(fixture.snapshot()).toEqual(before)
    fixture.assertQuiet(0)
    await test.info().attach('subscription-open-recheck-recovered', {
      contentType: 'image/png',
      body: await connection.screenshot()
    })
    await test.info().attach('subscription-open-recheck-recovery-proof', {
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

  test('legacy host identity requires explicit migration, cancel and editor reopen, with retained debt and fresh policy', async () => {
    fixture = await setupNativeSubscription({
      legacy: true,
      hostMigration: 'cancel_once'
    })

    let dialog = await openSetup(true)
    let connection = dialog.getByRole('region', { name: 'ChatGPT plan for setup', exact: true })
    const history = fixture.snapshot()
    expect(history.generations).toHaveLength(1)
    expect(history.generations[0]).toMatchObject({ idempotency_key: 'synthetic-legacy', dispatched: 1 })
    expect(history.subscriptionPolicy).toEqual([])
    fixture.assertQuiet(0)
    await completeConnectionAction(connection.getByRole('button', { name: 'Set up ChatGPT', exact: true }), 'inspect-legacy')
    await expect(connection.getByRole('button', { name: 'Load account models', exact: true })).toBeEnabled()
    await expect(connection.getByRole('button', { name: 'Update identity and reconnect', exact: true })).toHaveCount(0)
    expect(fixture.events().filter(row => row.kind === 'consent')).toEqual([])
    const legacyGrant = fixture.migrationEvidence()
    expect(legacyGrant).toMatchObject({ legacySeeded: true, provenanceCommitted: false, changedEpoch: false })

    // Establish the old policy through the same owner UI. No fabricated policy
    // receipt or automatic provider authorization is used to create this state.
    await reviewMigrationPolicy(connection, 'migration_required')
    const oldPolicy = fixture.snapshot()
    expect(oldPolicy.subscriptionPolicy).toHaveLength(1)
    expect(oldPolicy.generations).toEqual(history.generations)
    await completeModelLoad(connection, 'ready')
    await expect(dialog.getByRole('button', { name: 'Improve work description', exact: true })).toBeEnabled()
    expect(fixture.events().filter(row => row.kind === 'consent')).toEqual([])
    await connection.locator('details > summary').click()

    const disconnect = await fixture.performSetupAction('disconnect', () =>
      connection.getByRole('button', { name: 'Disconnect ChatGPT', exact: true }).click()
    )

    const disconnectSteps = disconnect.steps as { phase: string; shape: Record<string, unknown> }[]
    expect(disconnectSteps).toHaveLength(5)
    expect(disconnectSteps.every(row => row.phase === 'result')).toBe(true)
    expect(disconnectSteps[3].shape).toMatchObject({
      connectionState: 'disconnected',
      hostIdentityState: 'migration_required',
      canMigrateHostIdentity: true,
      cleanupPending: false,
      operationState: 'completed'
    })

    const migrationLabel = 'Allow this host identity change and a new locally stored connection'

    const disclosure =
      'Updating this profile changes the host identity shared with ChatGPT and requires fresh consent in your system browser. Local history, usage and outstanding obligations stay in place; other profiles are unchanged. Prior remote authorization may remain unless separately revoked.'

    let consent = connection.getByRole('checkbox', { name: migrationLabel, exact: true })
    let begin = connection.getByRole('button', { name: 'Update identity and reconnect', exact: true })
    await expect(connection.getByText(disclosure, { exact: true })).toBeVisible()
    await expect(consent).not.toBeChecked()
    await expect(begin).toBeDisabled()
    await expect(connection.getByRole('button', { name: 'Continue with ChatGPT', exact: true })).toHaveCount(0)
    expect(fixture.snapshot()).toEqual(oldPolicy)
    expect(fixture.events().filter(row => row.kind === 'consent')).toEqual([])
    fixture.assertQuiet(0)
    await consent.check()
    expect(fixture.events().filter(row => row.kind === 'consent')).toEqual([])
    await completeConnectionAction(begin, 'migration-held')
    await expect(connection.getByRole('button', { name: 'Cancel sign-in', exact: true })).toBeEnabled()
    const firstConsent = fixture.events().filter(row => row.kind === 'consent')
    expect(firstConsent).toHaveLength(1)
    expect(firstConsent[0]).toMatchObject({
      dynamicRegistration: false,
      hostMatchesInstallationUrn: true,
      promptConsent: true,
      noIdTokenHint: true,
      noLoginHint: true,
      savedClient: true,
      freshNonce: true,
      callbackDeferred: true
    })
    expect(fixture.events().filter(row => row.kind === 'oauth-exchange')).toEqual([])
    expect(fixture.diagnostic().rpc.rpc).toContainEqual(expect.objectContaining({
      method: 'organization.beginSetupSubscriptionHostMigration', phase: 'result'
    }))
    expect(fixture.diagnostic().rpc.rpc.some(row => row.method === 'organization.beginSetupSubscription')).toBe(false)
    await completeConnectionAction(connection.getByRole('button', { name: 'Cancel sign-in', exact: true }), 'cancel-migration')
    await expect(consent).not.toBeChecked()
    await expect(begin).toBeDisabled()
    expect(fixture.snapshot()).toEqual(oldPolicy)
    expect(fixture.migrationEvidence()).toMatchObject({ provenanceCommitted: false, changedEpoch: false })
    fixture.assertQuiet(0)

    // This boundary is an editor/dialog reopen, not an application or backend
    // restart: the synthetic vault is intentionally process-local.
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
    await expect(dialog).toHaveCount(0)
    dialog = await openSetup(true)
    connection = dialog.getByRole('region', { name: 'ChatGPT plan for setup', exact: true })
    await completeConnectionAction(connection.getByRole('button', { name: 'Set up ChatGPT', exact: true }), 'inspect-cancelled')
    consent = connection.getByRole('checkbox', { name: migrationLabel, exact: true })
    begin = connection.getByRole('button', { name: 'Update identity and reconnect', exact: true })
    await expect(connection.getByText(disclosure, { exact: true })).toBeVisible()
    await expect(consent).not.toBeChecked()
    await expect(begin).toBeDisabled()
    expect(fixture.events().filter(row => row.kind === 'consent')).toHaveLength(1)
    expect(fixture.events().filter(row => row.kind === 'oauth-exchange')).toEqual([])
    await consent.check()
    await completeConnectionAction(begin, 'migration-connected')
    await expect(connection.getByRole('button', { name: 'Load account models', exact: true })).toBeEnabled({ timeout: 30_000 })
    const consents = fixture.events().filter(row => row.kind === 'consent')
    expect(consents).toHaveLength(2)
    expect(consents[1]).toMatchObject({
      dynamicRegistration: false,
      hostMatchesInstallationUrn: true,
      promptConsent: true,
      noIdTokenHint: true,
      noLoginHint: true,
      savedClient: true,
      freshNonce: true,
      callbackDeferred: false
    })
    expect(fixture.events().filter(row => row.kind === 'oauth-exchange')).toHaveLength(1)
    const migrated = fixture.migrationEvidence()
    expect(migrated).toMatchObject({
      legacySeeded: true,
      sameInstallation: true,
      sameBinding: true,
      sameOwner: true,
      sameAccount: true,
      sameClient: true,
      changedEpoch: true,
      provenanceCommitted: true,
      priorTicketEvidenceRetained: true,
      priorGrantLocallyRetired: true,
      retainedGenerationRows: true
    })
    expect(fixture.snapshot()).toEqual(oldPolicy)
    await expect(dialog.getByRole('button', { name: 'Generate team · Recommended', exact: true })).toBeDisabled()
    await expect(dialog.getByRole('button', { name: 'Improve work description', exact: true })).toBeDisabled()
    await expect(dialog.getByRole('button', { name: 'Improve success description', exact: true })).toBeDisabled()
    fixture.assertQuiet(0)

    // The previous epoch's policy is not authorization for this grant. The
    // retained legacy call remains charged in the new explicit policy review.
    await reviewMigrationPolicy(connection, 'current', true)
    const after = fixture.snapshot()
    expect(after.subscriptionPolicy).toHaveLength(2)
    expect(after.subscriptionPolicy).toContainEqual(oldPolicy.subscriptionPolicy[0])
    const priorReceipt = JSON.parse(String(oldPolicy.subscriptionPolicy[0].receipt_json))

    const nextReceipt = JSON.parse(String(after.subscriptionPolicy.find(row =>
      row.idempotency_key !== oldPolicy.subscriptionPolicy[0].idempotency_key
    )!.receipt_json))

    expect(nextReceipt.candidate.authorizationEpoch).not.toBe(priorReceipt.candidate.authorizationEpoch)
    expect(nextReceipt.retainedCallCounts).toEqual(priorReceipt.retainedCallCounts)
    expect(nextReceipt.retainedCallCounts).toMatchObject({ total: 1, legacy: 1 })
    expect(after.generations).toEqual(oldPolicy.generations)
    expect(after.attempts).toEqual(oldPolicy.attempts)
    expect(after.dispatches).toEqual(oldPolicy.dispatches)
    expect(after.outcomes).toEqual(oldPolicy.outcomes)
    expect(after.model).toEqual(oldPolicy.model)
    expect(after.executionPolicy.map(row => ({ settings: row.settings, fingerprint: row.fingerprint }))).toEqual(
      oldPolicy.executionPolicy.map(row => ({ settings: row.settings, fingerprint: row.fingerprint }))
    )
    expect(after.executionPolicy[0].generation).toBe(Number(oldPolicy.executionPolicy[0].generation) + 1)
    expect(after.teams).toEqual([])
    expect(Object.values(after.counts).every(count => count === 0)).toBe(true)
    await expect(dialog.getByRole('button', { name: 'Generate team · Recommended', exact: true })).toBeDisabled()
    await completeModelLoad(connection, 'ready')
    await expect(dialog.getByRole('button', { name: 'Improve work description', exact: true })).toBeEnabled()
    fixture.assertQuiet(0)
    await test.info().attach('subscription-host-migration-reviewed', {
      contentType: 'image/png', body: await dialog.screenshot()
    })
    await test.info().attach('subscription-host-migration-proof', {
      contentType: 'application/json',
      body: JSON.stringify({
        legacyGrant, migrated,
        consentChecks: consents.map(row => Object.fromEntries([
          'hostMatchesInstallationUrn', 'promptConsent', 'noIdTokenHint', 'noLoginHint',
          'savedClient', 'freshNonce', 'callbackDeferred'
        ].map(field => [field, row[field] === true]))),
        history, oldPolicy, after
      })
    })
  })
})
