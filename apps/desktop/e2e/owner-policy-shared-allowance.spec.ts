/** Native owner controls → real served gateway → immutable shared/local ledgers → loopback HTTP. */
import type { Page } from '@playwright/test'

import { setupMockBackend, waitForAppReady } from './fixtures'
import { completionTitle, originalScope, setupCompletionFixture } from './organization-completion-fixture'
import { profileA, profileB } from './organization-dedicated-profile-fixture'
import { openOrganizationDisclosure } from './organization-disclosures'
import { firstMessage, firstReply, setupOwnerChatFixture } from './organization-owner-chat-fixture'
import { policyCopy } from './owner-policy-copy'
import { captureOwnerPolicy } from './owner-policy-evidence'
import { sharedPolicyCopy } from './owner-policy-shared-allowance-copy'
import {
  allowanceLedgers, fillSharedAllowance, initializeAllowanceOrganization, initializeEmptyAllowanceProfiles,
  observeAllowanceRpc, openSharedAllowance
} from './owner-policy-shared-allowance-fixture'
import { acknowledgeExpectedErrorBanner, expect, test } from './test'
import { galleryLocales } from './workspace-gallery-fixture'

const primary = (page: Page) => page.getByRole('navigation', { name: 'Primary', exact: true })
const configure = 'organization.configureSharedAllowance'
const read = 'organization.sharedAllowance'
test.setTimeout(360_000)

// eslint-disable-next-line no-empty-pattern -- this spec owns the native Electron lifecycle
test('shared pool preview is pure, lost acknowledgement reconciles once and an objective stops before a second provider send', async ({}, testInfo) => {
  const running = await setupCompletionFixture(false)
  const { page, app, sandbox } = running.fixture
  const transport = await observeAllowanceRpc(page)
  try {
    await page.reload()
    await waitForAppReady(running.fixture, 60_000, '.eid-workspace-header')
    await initializeAllowanceOrganization(page, 'Shared allowance root')
    const section = await openSharedAllowance(page)
    await expect.poll(() => transport.descriptor()?.authority).toBe('installation-owner')
    const before = allowanceLedgers(sandbox)
    expect(before.shared).toBeNull()
    await section.getByRole('button', { name: sharedPolicyCopy.en.edit, exact: true }).click()
    let dialog = page.getByRole('dialog', { name: sharedPolicyCopy.en.edit, exact: true })
    const candidates = transport.descriptor()!.candidates
    expect(candidates.map(candidate => candidate.profile)).toEqual(['default'])
    await expect(dialog.getByRole('checkbox', { name: 'Enroll organization: Shared allowance root (default)', exact: true })).not.toBeChecked()
    await fillSharedAllowance(dialog, candidates, { periodId: 'native-objective-period', poolCalls: 1, allocations: { default: 1 } })
    await dialog.getByRole('button', { name: 'Preview changes', exact: true }).click()
    const review = dialog.getByRole('region', { name: 'Review changes', exact: true })
    await expect(review).toContainText('native-objective-period')
    expect(transport.requests.filter(row => row.method === configure)).toHaveLength(0)
    expect(running.stages).toHaveLength(0)
    expect(allowanceLedgers(sandbox)).toEqual(before)
    await captureOwnerPolicy(page, app, [
      { name: 'period-identifier', locator: dialog.getByLabel('Period identifier', { exact: true }) },
      { name: 'pool-calls', locator: dialog.getByRole('group', { name: 'Aggregate pool limits', exact: true }).getByLabel('Model calls', { exact: true }) },
      { name: 'review-generation', locator: review.locator('p').first() },
      { name: 'explicit-apply', locator: dialog.getByRole('button', { name: 'Apply changes', exact: true }) }
    ], 'shared-allowance-preview', testInfo, transport.responses.filter(row => row.method === 'organization.previewSharedAllowance'))
    expect(allowanceLedgers(sandbox)).toEqual(before)

    transport.dropNextAcknowledgement(configure)
    await dialog.getByRole('button', { name: 'Apply changes', exact: true }).click()
    await expect.poll(() => transport.dropped.length).toBe(1)
    const original = transport.requests.find(row => row.method === configure)!.params
    expect(allowanceLedgers(sandbox).shared!.rows.allowance_configurations).toHaveLength(1)
    expect(allowanceLedgers(sandbox).shared!.rows.allowance_debits).toHaveLength(0)
    await page.reload()
    await (await openSharedAllowance(page)).getByRole('button', { name: 'Reconcile saved change', exact: true }).click()
    dialog = page.getByRole('dialog', { name: sharedPolicyCopy.en.edit, exact: true })
    const uncertain = 'The acknowledgement was lost. The change may already be saved. Reconcile the original change before editing again.'
    await expect(dialog.getByRole('alert')).toHaveText(uncertain)
    await dialog.getByRole('button', { name: 'Reconcile saved change', exact: true }).click()
    await expect(dialog).toContainText('Change verified and saved.')
    await acknowledgeExpectedErrorBanner(page, uncertain)
    const writes = transport.requests.filter(row => row.method === configure)
    expect(writes).toHaveLength(2)
    expect(writes[1].params).toEqual(original)
    const receipts = transport.responses.filter(row => row.method === configure).map(row => row.value.result?.receipt)
    expect(receipts).toHaveLength(2)
    expect(receipts[1]).toEqual(receipts[0])
    expect(allowanceLedgers(sandbox).shared!.rows.allowance_configurations).toHaveLength(1)
    expect(running.stages).toHaveLength(0)
    await dialog.getByRole('button', { name: 'Close', exact: true }).click()

    await primary(page).getByRole('link', { name: 'Home', exact: true }).click()
    await page.getByRole('button', { name: 'New objective', exact: true }).click()
    await page.getByRole('textbox', { name: 'Objective', exact: true }).fill(completionTitle)
    await openOrganizationDisclosure(page, 'Context')
    await page.getByRole('textbox', { name: 'Submitted context (optional)', exact: true }).fill(originalScope)
    await openOrganizationDisclosure(page, 'Acceptance criteria')
    await page.getByRole('textbox', { name: 'Acceptance criteria', exact: true }).fill('Retain the release gate using the supplied evidence.')
    await page.getByRole('combobox', { name: 'Delivery scope', exact: true }).selectOption('managed_artifact')
    await page.getByRole('button', { name: 'Create objective', exact: true }).click()
    const blocked = page.getByRole('button', { name: 'Inspect request: request.plan', exact: true })
    await expect(blocked).toContainText('Pending intervention', { timeout: 90_000 })
    await expect(blocked).toContainText('Shared organization allowance pool modelCalls capacity is exhausted')
    expect(running.stages.map(stage => stage.kind)).toEqual(['request.decompose'])
    const charged = allowanceLedgers(sandbox)
    expect(charged.shared!.rows.allowance_debits).toHaveLength(1)
    expect(charged.local.default!.rows.organization_model_calls).toHaveLength(1)
    expect(charged.local.default!.rows.organization_shared_call_receipts).toHaveLength(1)
    const localReceipt = charged.local.default!.rows.organization_shared_call_receipts[0]
    expect(localReceipt.model_call_id).toBe(charged.local.default!.rows.organization_model_calls[0].id)
    expect(localReceipt.owner_chat_turn_id).toBeNull()
    expect(localReceipt.call_key).toBe(charged.shared!.rows.allowance_debits[0].call_key)
    expect(JSON.parse(String(localReceipt.receipt_json))).toEqual(JSON.parse(String(charged.shared!.rows.allowance_debits[0].receipt)))

    const allowance = await openSharedAllowance(page)
    await allowance.getByRole('button', { name: 'Refresh', exact: true }).click()
    await expect.poll(() => transport.descriptor()?.pool?.ordinaryRemaining.modelCalls).toBe(0)
    expect(transport.descriptor()!.member!.ordinaryRemaining.modelCalls).toBe(0)
    const beforeObservation = allowanceLedgers(sandbox)
    await allowance.getByRole('button', { name: sharedPolicyCopy.en.observe, exact: true }).click()
    const observation = page.getByRole('dialog', { name: sharedPolicyCopy.en.observe, exact: true })
    await observation.getByLabel('Reported remaining percent', { exact: true }).fill('45')
    await observation.getByLabel('Observed at (ISO 8601 with timezone)', { exact: true }).fill(new Date(Date.now() - 1000).toISOString())
    await observation.getByLabel('Expires at (ISO 8601 with timezone)', { exact: true }).fill(new Date(Date.now() + 3600_000).toISOString())
    await observation.getByRole('button', { name: 'Preview changes', exact: true }).click()
    await expect(observation.getByRole('region', { name: 'Review changes', exact: true })).toContainText('45')
    expect(allowanceLedgers(sandbox)).toEqual(beforeObservation)
    await captureOwnerPolicy(page, app, [
      { name: 'shared-reported-percent', locator: observation.getByLabel('Reported remaining percent', { exact: true }) },
      { name: 'shared-observation-expiry', locator: observation.getByLabel('Expires at (ISO 8601 with timezone)', { exact: true }) },
      { name: 'shared-observation-apply', locator: observation.getByRole('button', { name: 'Apply changes', exact: true }) }
    ], 'shared-observation-preview', testInfo, transport.responses.filter(row => row.method === 'organization.previewSharedAllowanceObservation'))
    await observation.getByRole('button', { name: 'Apply changes', exact: true }).click()
    await expect(observation).toContainText('Change verified and saved.')
    const afterObservation = allowanceLedgers(sandbox)
    expect(afterObservation.local).toEqual(beforeObservation.local)
    expect(afterObservation.shared!.rows.allowance_debits).toEqual(beforeObservation.shared!.rows.allowance_debits)
    const configurations = afterObservation.shared!.rows.allowance_configurations
    const previousConfigurations = beforeObservation.shared!.rows.allowance_configurations
    expect(configurations).toHaveLength(previousConfigurations.length + 1)
    expect(configurations.slice(0, -1)).toEqual(previousConfigurations)
    expect(configurations.at(-1)!.configuration).toBe(previousConfigurations.at(-1)!.configuration)
    expect(configurations.at(-1)!.revision).toBe(Number(previousConfigurations.at(-1)!.revision) + 1)
    expect(afterObservation.shared!.rows.allowance_observations).toHaveLength(1)
    expect(running.stages.map(stage => stage.kind)).toEqual(['request.decompose'])
    expect(running.providerErrors).toEqual([])
  } finally {
    try {
      await testInfo.attach('shared-allowance-objective-evidence', { contentType: 'application/json', body: JSON.stringify({ requests: transport.requests, responses: transport.responses, stages: running.stages, providerErrors: running.providerErrors, ledger: allowanceLedgers(sandbox) }, null, 2) })
    } finally {
      await running.fixture.cleanup()
    }
  }
})

async function switchProfile(page: Page, profile: string) {
  await page.evaluate(() => {location.hash = '#/'})
  const label = profile === 'default' ? 'Switch to default' : profile
  await page.locator('[data-slot="profile-rail"]').getByRole('button', { name: label, exact: true }).click()
  await expect(page.locator('.eid-workspace-header').getByRole('button', { name: new RegExp(profile) })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Edit organization and owner names', exact: true })).toBeVisible()
}

async function openWorkerChat(page: Page) {
  await primary(page).getByRole('link', { name: 'Organization', exact: true }).click()
  await page.getByRole('button', { name: 'Inspect Worker 1', exact: true }).click()
  await page.getByRole('complementary', { name: 'Agent details', exact: true }).getByRole('button', { name: 'Chat', exact: true }).click()
  const conversation = page.getByRole('region', { name: 'Owner conversation', exact: true })
  await expect(conversation.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible()
  return conversation
}

async function recordSharedPercent(page: Page, remaining: number) {
  const section = await openSharedAllowance(page)
  await section.getByRole('button', { name: sharedPolicyCopy.en.observe, exact: true }).click()
  const dialog = page.getByRole('dialog', { name: sharedPolicyCopy.en.observe, exact: true })
  await dialog.getByLabel('Reported remaining percent', { exact: true }).fill(String(remaining))
  await dialog.getByLabel('Observed at (ISO 8601 with timezone)', { exact: true }).fill(new Date().toISOString())
  await dialog.getByLabel('Expires at (ISO 8601 with timezone)', { exact: true }).fill(new Date(Date.now() + 3600_000).toISOString())
  await dialog.getByRole('button', { name: 'Preview changes', exact: true }).click()
  await expect(dialog.getByRole('region', { name: 'Review changes', exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: 'Apply changes', exact: true }).click()
  await expect(dialog).toContainText('Change verified and saved.')
  await dialog.getByRole('button', { name: 'Close', exact: true }).click()
}

// eslint-disable-next-line no-empty-pattern -- this spec owns the native Electron lifecycle
test('explicit enrollment scopes profile reads and charges owner chats while ordinary profile chat stays outside the pool', async ({}, testInfo) => {
  const running = await setupOwnerChatFixture({ dedicatedProfiles: true })
  const { page, app, sandbox } = running.fixture
  const transport = await observeAllowanceRpc(page)
  const profiles = ['default', profileA, profileB]
  try {
    await page.reload()
    await waitForAppReady(running.fixture, 60_000, '.eid-workspace-header')
    await initializeAllowanceOrganization(page, 'Root shared owner')
    await initializeEmptyAllowanceProfiles(sandbox, [profileA, profileB])
    expect(running.calls).toHaveLength(0)
    for (const profile of [profileA, profileB]) {
      await switchProfile(page, profile)
      await initializeAllowanceOrganization(page, `Member ${profile}`)
    }
    await switchProfile(page, 'default')
    const section = await openSharedAllowance(page)
    await expect.poll(() => transport.descriptor()?.candidates.length).toBe(3)
    const candidates = transport.descriptor()!.candidates
    expect(new Set(candidates.map(candidate => candidate.profile))).toEqual(new Set(profiles))
    const before = allowanceLedgers(sandbox, profiles)
    await section.getByRole('button', { name: sharedPolicyCopy.en.edit, exact: true }).click()
    const dialog = page.getByRole('dialog', { name: sharedPolicyCopy.en.edit, exact: true })
    await fillSharedAllowance(dialog, candidates, { periodId: 'native-owner-chat-period', poolCalls: 3, poolProtected: 1, allocations: { default: 1, [profileA]: 1, [profileB]: 1 } })
    await dialog.getByRole('button', { name: 'Preview changes', exact: true }).click()
    await expect(dialog.getByRole('region', { name: 'Review changes', exact: true })).toContainText(profileB)
    expect(allowanceLedgers(sandbox, profiles)).toEqual(before)
    expect(running.calls).toHaveLength(0)
    await dialog.getByRole('button', { name: 'Apply changes', exact: true }).click()
    await expect(dialog).toContainText('Change verified and saved.')
    await dialog.getByRole('button', { name: 'Close', exact: true }).click()

    await switchProfile(page, profileA)
    const beforeMemberRead = allowanceLedgers(sandbox, profiles)
    const member = await openSharedAllowance(page)
    await expect.poll(() => transport.descriptor(profileA)?.authority).toBe('member')
    const memberDescriptor = transport.descriptor(profileA)!
    expect(memberDescriptor.configuration).toBeNull()
    expect(memberDescriptor.pool).toBeNull()
    expect(memberDescriptor.candidates).toEqual([])
    expect(memberDescriptor.capabilities).toEqual({ configuration: false, observation: false })
    expect(memberDescriptor.member!.ordinaryRemaining.modelCalls).toBe(1)
    const sibling = candidates.find(candidate => candidate.profile === profileB)!
    expect(JSON.stringify(memberDescriptor)).not.toContain(sibling.organizationId)
    expect(JSON.stringify(memberDescriptor)).not.toContain(sibling.ownerId)
    await expect(member.getByRole('button', { name: sharedPolicyCopy.en.edit, exact: true })).toHaveCount(0)
    await expect(member.getByRole('button', { name: sharedPolicyCopy.en.observe, exact: true })).toHaveCount(0)
    await expect(member.getByRole('region', { name: 'Recorded allowance projection', exact: true })).toHaveCount(0)
    await captureOwnerPolicy(page, app, [
      { name: 'scoped-member-allocation', locator: member.getByRole('region', { name: sharedPolicyCopy.en.allocation, exact: true }).locator('dd').first() },
      { name: 'member-refresh', locator: member.getByRole('button', { name: 'Refresh', exact: true }) }
    ], 'shared-allowance-member-readonly', testInfo, transport.responses.filter(row => row.method === read && (row.value.result?.scope as { profile?: string } | undefined)?.profile === profileA))
    expect(allowanceLedgers(sandbox, profiles)).toEqual(beforeMemberRead)
    expect(transport.requests.filter(row => row.method === configure)).toHaveLength(1)

    let conversation = await openWorkerChat(page)
    await conversation.getByRole('textbox', { name: 'Message', exact: true }).fill(firstMessage)
    await conversation.getByRole('button', { name: 'Send message', exact: true }).click()
    await expect(conversation.getByText(firstReply, { exact: true })).toBeVisible({ timeout: 90_000 })
    expect(running.calls).toHaveLength(1)
    let evidence = allowanceLedgers(sandbox, profiles)
    expect(evidence.shared!.rows.allowance_debits).toHaveLength(1)
    expect(evidence.shared!.rows.allowance_debits[0].profile).toBe(profileA)
    expect(JSON.parse(String(evidence.shared!.rows.allowance_debits[0].receipt))).toMatchObject({ classification: 'ordinary', payload: { kind: 'owner_chat' } })
    expect(evidence.local[profileA]!.rows.organization_shared_call_receipts).toHaveLength(1)
    expect(evidence.local[profileB]!.rows.organization_shared_call_receipts).toHaveLength(0)
    expect(evidence.local[profileA]!.rows.organization_model_calls).toHaveLength(0)

    // A second explicit send reaches the actual guard, but not the HTTP provider.
    await conversation.getByRole('textbox', { name: 'Message', exact: true }).fill('Refuse this call because this organization allocation is exhausted.')
    await conversation.getByRole('button', { name: 'Send message', exact: true }).click()
    await expect.poll(() => allowanceLedgers(sandbox, profiles).local[profileA]!.rows.owner_chat_turns.at(-1)?.status, { timeout: 60_000 }).toBe('blocked')
    evidence = allowanceLedgers(sandbox, profiles)
    expect(evidence.local[profileA]!.rows.owner_chat_turns.at(-1)?.dispatched).toBe(0)
    expect(evidence.local[profileA]!.rows.owner_chat_turns.at(-1)?.reason).toContain('allocation_protected_or_exhausted_modelCalls')
    await expect(conversation.getByText('Shared organization allowance: allocation_protected_or_exhausted_modelCalls', { exact: true })).toBeVisible()
    expect(evidence.shared!.rows.allowance_debits).toHaveLength(1)
    expect(running.calls).toHaveLength(1)

    await switchProfile(page, 'default')
    await recordSharedPercent(page, 5)
    conversation = await openWorkerChat(page)
    await conversation.getByRole('textbox', { name: 'Message', exact: true }).fill(firstMessage)
    await conversation.getByRole('button', { name: 'Send message', exact: true }).click()
    await expect.poll(() => allowanceLedgers(sandbox, profiles).local.default!.rows.owner_chat_turns.at(-1)?.status, { timeout: 60_000 }).toBe('blocked')
    const paced = allowanceLedgers(sandbox, profiles)
    expect(paced.local.default!.rows.owner_chat_turns.at(-1)?.dispatched).toBe(0)
    expect(paced.local.default!.rows.owner_chat_turns.at(-1)?.reason).toContain('Shared allowance pacing is waiting before the next ordinary model-backed stage.')
    expect(paced.shared!.rows.allowance_debits).toHaveLength(1)
    expect(running.calls).toHaveLength(1)
    await recordSharedPercent(page, 100)
    // Raising an advisory observation does not replay the blocked owner send.
    expect(running.calls).toHaveLength(1)
    expect(allowanceLedgers(sandbox, profiles).shared!.rows.allowance_debits).toEqual(paced.shared!.rows.allowance_debits)
    conversation = await openWorkerChat(page)
    await conversation.getByRole('textbox', { name: 'Message', exact: true }).fill(firstMessage)
    await conversation.getByRole('button', { name: 'Send message', exact: true }).click()
    await expect(conversation.getByText(firstReply, { exact: true })).toBeVisible({ timeout: 90_000 })
    expect(running.calls).toHaveLength(2)
    const rootSection = await openSharedAllowance(page)
    await rootSection.getByRole('button', { name: 'Refresh', exact: true }).click()
    await expect.poll(() => transport.descriptor()?.pool?.ordinaryRemaining.modelCalls).toBe(0)

    // B still owns its full allocation, but the aggregate ordinary cap is now spent.
    await switchProfile(page, profileB)
    await openSharedAllowance(page)
    await expect.poll(() => transport.descriptor(profileB)?.member?.ordinaryRemaining.modelCalls).toBe(1)
    conversation = await openWorkerChat(page)
    await conversation.getByRole('textbox', { name: 'Message', exact: true }).fill(firstMessage)
    await conversation.getByRole('button', { name: 'Send message', exact: true }).click()
    await expect.poll(() => allowanceLedgers(sandbox, profiles).local[profileB]!.rows.owner_chat_turns.at(-1)?.status, { timeout: 60_000 }).toBe('blocked')
    evidence = allowanceLedgers(sandbox, profiles)
    expect(evidence.local[profileB]!.rows.owner_chat_turns.at(-1)?.dispatched).toBe(0)
    expect(evidence.local[profileB]!.rows.owner_chat_turns.at(-1)?.reason).toContain('pool_protected_or_exhausted_modelCalls')
    await expect(conversation.getByText('Shared organization allowance: pool_protected_or_exhausted_modelCalls', { exact: true })).toBeVisible()
    expect(evidence.local[profileB]!.rows.organization_shared_call_receipts).toHaveLength(0)
    expect(evidence.shared!.rows.allowance_debits).toHaveLength(2)
    expect(running.calls).toHaveLength(2)
    for (const profile of ['default', profileA]) {
      const receipt = evidence.local[profile]!.rows.organization_shared_call_receipts
      expect(receipt).toHaveLength(1)
      expect(receipt[0].model_call_id).toBeNull()
      expect(evidence.local[profile]!.rows.owner_chat_turns.filter(turn => turn.dispatched === 1).map(turn => turn.id)).toEqual([receipt[0].owner_chat_turn_id])
      const shared = evidence.shared!.rows.allowance_debits.find(debit => debit.profile === profile)!
      expect(receipt[0].call_key).toBe(shared.call_key)
      expect(JSON.parse(String(receipt[0].receipt_json))).toEqual(JSON.parse(String(shared.receipt)))
    }
    await page.reload()
    await waitForAppReady(running.fixture, 60_000, '.eid-workspace-header')
    await openSharedAllowance(page)
    await expect.poll(() => transport.descriptor(profileB)?.member?.ordinaryRemaining.modelCalls).toBe(1)
    expect(allowanceLedgers(sandbox, profiles)).toEqual(evidence)
    expect(running.calls).toHaveLength(2)

    // This is the ordinary external-chat surface, with an actual mock HTTP reply.
    // A depleted organization pool must neither debit it nor disable that separate route.
    const beforeExternal = allowanceLedgers(sandbox, profiles)
    const rail = page.getByRole('complementary', { name: 'Eidolon navigation', exact: true })
    await rail.getByRole('button', { name: 'More workspace options', exact: true }).click()
    await rail.getByRole('button', { name: 'Profile chats', exact: true }).click()
    await expect(page.getByRole('tab', { name: 'Profile chats', exact: true })).toHaveAttribute('aria-selected', 'true')
    await page.evaluate(() => {location.hash = '#/'})
    const composer = page.locator('[contenteditable="true"]').first()
    await expect(composer).toBeVisible()
    await composer.click()
    await composer.pressSequentially('Hello from the independent external chat fixture.')
    await page.keyboard.press('Enter')
    await expect(page.getByText('Hello from the mock inference server! The full boot chain is working.', { exact: true })).toBeVisible({ timeout: 90_000 })
    expect(running.fixture.mock.receivedPrompts.length).toBeGreaterThan(0)
    expect(allowanceLedgers(sandbox, profiles)).toEqual(beforeExternal)
    expect(running.calls).toHaveLength(2)
    expect(running.providerErrors).toEqual([])
  } finally {
    try {
      await testInfo.attach('shared-allowance-cross-profile-evidence', { contentType: 'application/json', body: JSON.stringify({ requests: transport.requests, responses: transport.responses, calls: running.calls, providerErrors: running.providerErrors, ledger: allowanceLedgers(sandbox, profiles) }, null, 2) })
    } finally {
      await running.fixture.cleanup()
    }
  }
})

for (const language of galleryLocales) {
  // eslint-disable-next-line no-empty-pattern -- actual native fixture owned here
  test(`shared allowance preserves full labels, enrollment, focus and preview in ${language.locale}`, async ({}, testInfo) => {
    const fixture = await setupMockBackend({ extraDisplayConfig: `  language: ${language.locale}` })
    const { page, app, sandbox } = fixture
    const copy = sharedPolicyCopy[language.locale]
    const common = policyCopy[language.locale]
    const transport = await observeAllowanceRpc(page)
    try {
      await page.reload()
      await waitForAppReady(fixture, 60_000, '.eid-workspace-header')
      await expect(page.locator('html')).toHaveAttribute('lang', language.locale)
      await expect(page.locator('html')).toHaveAttribute('dir', language.locale === 'ar' ? 'rtl' : 'ltr')
      await initializeAllowanceOrganization(page, 'Shared allowance gallery', language.locale)
      await openSharedAllowance(page, language.locale)
      await expect.poll(() => transport.descriptor()?.candidates.length).toBe(1)
      for (const mode of ['dark', 'light'] as const) {
        await page.evaluate(() => {location.hash = '#/settings?tab=config:appearance'})
        const theme = mode === 'dark' ? { label: 'Eidolon', id: 'eidolon' } : { label: 'AetherMesh', id: 'nous' }
        await page.getByRole('button').filter({ has: page.getByText(theme.label, { exact: true }) }).click()
        await page.getByRole('button', { name: language[mode], exact: true }).click()
        await expect(page.locator('html')).toHaveAttribute('data-hermes-theme', theme.id)
        await expect.poll(() => page.locator('html').evaluate(element => element.classList.contains('dark'))).toBe(mode === 'dark')
        const section = await openSharedAllowance(page, language.locale)
        const before = allowanceLedgers(sandbox)
        await section.getByRole('button', { name: copy.edit, exact: true }).click()
        const dialog = page.getByRole('dialog', { name: copy.edit, exact: true })
        const enrollment = dialog.getByRole('checkbox', { name: `${copy.enroll}: Shared allowance gallery (default)`, exact: true })
        await enrollment.uncheck()
        await expect(enrollment).not.toBeChecked()
        await captureOwnerPolicy(page, app, [{ name: 'explicit-enrollment-unchecked', locator: enrollment }], `shared-enrollment-${language.locale}-${mode}`, testInfo, { locale: language.locale, mode, action: 'unsaved-draft', descriptor: transport.descriptor() })
        await fillSharedAllowance(dialog, transport.descriptor()!.candidates, { periodId: 'native-gallery-period', poolCalls: 20, poolProtected: 2, allocations: { default: 10 }, locale: language.locale })
        await dialog.getByRole('button', { name: common.preview, exact: true }).click()
        const review = dialog.getByRole('region', { name: common.review, exact: true })
        await expect(review).toContainText('native-gallery-period')
        const pool = dialog.getByRole('group', { name: copy.limits, exact: true })
        const protectedPool = dialog.getByRole('group', { name: copy.protected, exact: true }).first()
        await captureOwnerPolicy(page, app, [
          { name: 'period-id', locator: dialog.getByLabel(copy.label, { exact: true }) },
          { name: 'period-start', locator: dialog.getByLabel(copy.startsAt, { exact: true }) },
          { name: 'period-end', locator: dialog.getByLabel(copy.endsAt, { exact: true }) },
          { name: 'pool-calls', locator: pool.getByLabel(copy.calls, { exact: true }) },
          { name: 'pool-tokens', locator: pool.getByLabel(copy.tokens, { exact: true }) },
          { name: 'pool-configured-cost', locator: pool.getByLabel(copy.cost, { exact: true }) },
          { name: 'protected-calls', locator: protectedPool.getByLabel(copy.calls, { exact: true }) },
          { name: 'explicit-enrollment-checked', locator: enrollment },
          { name: 'member-allocation-calls', locator: dialog.getByRole('group', { name: copy.allocation, exact: true }).getByLabel(copy.calls, { exact: true }) },
          { name: 'preview-generation', locator: review.locator('p').first() },
          { name: 'explicit-apply', locator: dialog.getByRole('button', { name: common.apply, exact: true }) }
        ], `shared-allowance-${language.locale}-${mode}`, testInfo, transport.responses.filter(row => row.method === 'organization.previewSharedAllowance'))
        expect(allowanceLedgers(sandbox)).toEqual(before)
        expect(transport.requests.filter(row => row.method === configure)).toHaveLength(0)
        expect(fixture.mock.receivedPrompts).toHaveLength(0)
        await dialog.getByRole('button', { name: common.close, exact: true }).click()
        await expect(section.getByRole('button', { name: copy.edit, exact: true })).toBeFocused()
      }
    } finally {
      try {
        await testInfo.attach(`shared-allowance-${language.locale}-rpc`, { contentType: 'application/json', body: JSON.stringify({ requests: transport.requests, responses: transport.responses }, null, 2) })
      } finally {
        await fixture.cleanup()
      }
    }
  })
}
