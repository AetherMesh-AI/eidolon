/** Real Electron controls and owner-scoped client; all prepared RPC data is synthetic.
 * This proves UI selection/serialization only, never registration, Docker or acceptance. */
import { execFileSync } from 'node:child_process'
import { createHash, randomBytes } from 'node:crypto'

import type { Locator, TestInfo } from '@playwright/test'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { captureCausalOwnerFrame } from './organization-native-owner-frame'
import { ownerMarkerPosition } from './organization-owner-marker-position'
import { createPreparedNativeModel, PREPARED_NATIVE_ERRORS, PREPARED_NATIVE_TITLE } from './organization-prepared-validation-native-model'
import { acknowledgeExpectedErrorBanner, collectErrorBanners, expect, test } from './test'

const copy = {
  selector: 'Prepared validation for this new objective',
  enable: 'Use explicitly selected prepared validation registrations',
  read: 'Read available prepared registrations',
  reread: 'Reread and review prepared registrations',
  partial: 'Prepared mode requires an exact registration for every selected project, up to eight projects. Read availability and check each registration explicitly.',
  changed: 'The owner, profile, connection, project binding, or registration review changed. Prepared mode is still requested, but its selections are cleared. Reread and select again, or explicitly disable prepared mode.',
  activity: 'Recorded prepared validation activity',
  current: 'Current recorded generations',
  passed: 'Passed claim (unverified)',
  receipt: 'Runtime receipt unverified',
  pairReview: 'Pair review record present (unverified)',
  note: 'Read-only scalar metadata. Recorded counts and claims are unverified: they do not verify source bytes, runtime receipts, cleanup, accounting, or review, and do not authorize execution, acceptance, or source delivery.'
}

let fixture: MockBackendFixture | undefined
let model: ReturnType<typeof createPreparedNativeModel> | undefined
let methods: string[] = []

// Keep the shared 90-second scenario deadline, native capture bounds and banner guard.
// eslint-disable-next-line no-empty-pattern -- this scenario owns its temporary Electron app
test.afterEach(async ({}, info) => {
  if (!fixture) { return }

  try {
    await info.attach('prepared-native-synthetic-proof', { contentType: 'application/json', body: JSON.stringify({
      syntheticPreparedTransport: true, backendRegistrationExercised: false, dockerExercised: false,
      sourceBytesVerified: false, runtimeReceiptsVerified: false, acceptanceAuthorized: false,
      sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
      sourceDirty: execFileSync('git', ['status', '--porcelain'], { encoding: 'utf8' }).trim().length > 0,
      methods, creates: model?.creates, syntheticAdmittedCount: model?.admittedCount,
      unexpectedMethods: model?.unexpectedMethods, providerCalls: fixture.mock.receivedPrompts.length,
      runtime: await fixture.app.evaluate(() => ({ electron: process.versions.electron, chrome: process.versions.chrome, platform: process.platform }))
    }, null, 2) })
    expect(fixture.mock.receivedPrompts).toHaveLength(0)
    expect(model?.unexpectedMethods).toEqual([])
    expect(methods).not.toContain('session.create')
    expect(methods).not.toContain('chat.send')

    if (!fixture.page.isClosed()) {
      await info.attach('prepared-native-final-ui', { contentType: 'text/plain', body: await fixture.page.locator('body').ariaSnapshot() })
      expect(await collectErrorBanners(fixture.page)).toEqual([])
    }
  } finally {
    await fixture.cleanup(); fixture = undefined; model = undefined; methods = []
  }
})

async function start() {
  model = createPreparedNativeModel()
  methods = []
  fixture = await setupMockBackend({ isolateModelCatalog: true })
  const { page } = fixture
  await page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as { id?: number | string; method?: string; params?: Record<string, unknown> }

      if (!request.method) { server.send(message);

 return }

      methods.push(request.method)

      if (['session.create', 'chat.send'].includes(request.method)) {
        socket.send(JSON.stringify({ jsonrpc: '2.0', id: request.id, error: { code: -32601, message: 'Synthetic prepared UI proof forbids work dispatch' } }))

        return
      }

      const response = model!.reply(request.method, request.params ?? {})

      if (response) { socket.send(JSON.stringify({ jsonrpc: '2.0', id: request.id, ...response })) }
      else { server.send(message) }
    })
    server.onMessage(message => socket.send(message))
  })
  // The route applies to new sockets. Reopen the real authenticated connection.
  await page.reload()
  await waitForAppReady(fixture)
  await page.getByRole('complementary', { name: 'Eidolon navigation', exact: true })
    .getByRole('navigation', { name: 'Primary', exact: true }).getByRole('link', { name: 'Home', exact: true }).click()
  await page.getByRole('button', { name: 'New objective', exact: true }).click()
  await page.getByRole('textbox', { name: 'Objective', exact: true }).fill(PREPARED_NATIVE_TITLE)
  const selector = page.getByRole('region', { name: copy.selector, exact: true })
  await expect(selector.getByRole('checkbox', { name: copy.enable, exact: true })).not.toBeChecked()
  expect(model.methods.filter(method => method === 'organization.preparedValidationSetup')).toHaveLength(0)

  for (const id of ['frontend', 'backend']) {
    await page.getByRole('checkbox', { name: new RegExp(`^${id} · root[01] ·`) }).check()
  }

  await selector.getByRole('checkbox', { name: copy.enable, exact: true }).check()
  expect(model.methods.filter(method => method === 'organization.preparedValidationSetup')).toHaveLength(0)
  await expect(page.getByRole('combobox', { name: 'Delivery scope', exact: true })).toHaveValue('managed_artifact')
  await expect(page.getByRole('combobox', { name: 'Delivery scope', exact: true })).toBeDisabled()
  await selector.getByRole('button', { name: copy.read, exact: true }).click()

  for (const reference of model.currentReferences()) {
    await expect(referenceChoice(selector, reference.projectId)).not.toBeChecked()
    await expect(selector.getByText(new RegExp(reference.registrationSha256))).toBeVisible()
  }

  expect(model.methods.filter(method => method === 'organization.preparedValidationSetup')).toHaveLength(1)

  return { page, selector }
}

function referenceChoice(selector: Locator, projectId: string) {
  return selector.getByRole('checkbox', { name: new RegExp(`^Use this exact registration: ${projectId} ·`) })
}

async function capture(name: string, target: Locator, info: TestInfo) {
  const { page, app } = fixture!
  await target.scrollIntoViewIfNeeded()
  await expect(target).toBeVisible()
  await page.evaluate(async () => {
    await document.fonts.ready
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
  })
  const state = await target.evaluate(element => ({ rect: element.getBoundingClientRect().toJSON(), viewport: { width: innerWidth, height: innerHeight } }))
  const placement = await page.evaluate(ownerMarkerPosition, state.rect)
  expect(placement, 'an empty corner for the disclosed synthetic causal marker').toBeTruthy()
  const nonce = randomBytes(9)

  const marker = { id: `prepared-owner-frame-${nonce.toString('hex')}`, ...placement!,
    colors: [0, 3, 6].map(offset => [...nonce.subarray(offset, offset + 3)].map(channel => 40 + channel % 180)) }

  const window = await app.browserWindow(page)
  const frame = await window.evaluate(captureCausalOwnerFrame, { state, keyboard: null, marker })
  const hashes: Record<string, string> = {}

  for (const [kind, image] of [['first', frame.first], ['causal', frame.causal]] as const) {
    if (!image) { continue }
    const bytes = Buffer.from(image.encoded, 'base64')
    hashes[kind] = createHash('sha256').update(bytes).digest('hex')
    await info.attach(`${name}-${kind}`, { contentType: 'image/png', body: bytes })
  }

  await info.attach(`${name}-native-provenance`, { contentType: 'application/json', body: JSON.stringify({
    captureMethod: 'Electron.beginFrameSubscription', syntheticFixture: true, state, hashes, error: frame.error,
    frames: frame.frames, causalSequence: frame.causal?.sequence, windowFocused: frame.windowFocused, contentsFocused: frame.contentsFocused,
    marker: { ...marker, disclosure: 'Test-only nonce tiles in an empty viewport corner; no keyboard focus claim. Existing bounded cleanup remains enabled.' }
  }, null, 2) })
  expect(frame.error).toBeNull()
  expect(frame.causal).not.toBeNull()
  await expect(page.locator(`#${marker.id}`)).toHaveCount(0)
}

// eslint-disable-next-line no-empty-pattern -- this scenario owns its temporary Electron app
test('requires two explicit current references and retains exact create intent while displaying claims as unverified', async ({}, info) => {
  const { page, selector } = await start()
  const create = page.getByRole('button', { name: 'Create objective', exact: true })
  await referenceChoice(selector, 'frontend').check()
  await create.click()
  await expect(page.getByRole('alert')).toHaveText(copy.partial)
  expect(model!.creates).toHaveLength(0)
  await referenceChoice(selector, 'backend').check()
  // Editing the goal is the existing public way to clear a resolved form error.
  await page.getByRole('textbox', { name: 'Objective', exact: true }).fill(`${PREPARED_NATIVE_TITLE} `)
  await acknowledgeExpectedErrorBanner(page, copy.partial)
  await capture('prepared-explicit-two-project-selection', referenceChoice(selector, 'backend'), info)
  const selected = model!.currentReferences()
  model!.loseNextCreateReply()
  await create.click()
  await expect(page.getByRole('alert')).toHaveText(PREPARED_NATIVE_ERRORS.lostCreateReply)
  expect(model!.admittedCount).toBe(1)
  expect(model!.creates).toHaveLength(1)
  await create.click()
  await expect(page.getByRole('heading', { name: PREPARED_NATIVE_TITLE, exact: true })).toBeVisible()
  await acknowledgeExpectedErrorBanner(page, PREPARED_NATIVE_ERRORS.lostCreateReply)
  expect(model!.creates).toHaveLength(2)
  expect(model!.creates[1]).toEqual(model!.creates[0])
  expect(model!.creates[0]).toEqual({ title: PREPARED_NATIVE_TITLE, projectIds: ['frontend', 'backend'],
    executiveId: 'executive', managerId: 'manager', deliveryMode: 'managed_artifact', requiredChecks: ['project_typecheck', 'project_tests'],
    preparedValidations: selected, idempotencyKey: expect.any(String) })
  expect(model!.admittedCount).toBe(1)

  const activity = page.getByRole('region', { name: copy.activity, exact: true })
  await expect(activity.getByText(copy.note, { exact: true })).toBeVisible()
  const retained = activity.getByRole('list', { name: 'Immutable registrations selected for this objective', exact: true })
  await expect(retained.locator(':scope > li')).toHaveCount(2)

  for (const reference of selected) {
    const item = retained.getByRole('listitem').filter({ has: page.getByText(reference.projectId, { exact: true }) })
    await expect(item.getByText(String(reference.revision), { exact: true })).toBeVisible()
    await expect(item.getByText(reference.registrationSha256, { exact: true })).toBeVisible()
  }

  const counts = activity.locator('dl[aria-label="Recorded counts (unverified)"]')
  await expect(counts.locator('dd')).toHaveText(['Present; integrity unverified', '0', '4', '4', '2', '2'])
  const current = activity.getByRole('list', { name: copy.current, exact: true })
  await expect(current.locator(':scope > li')).toHaveCount(2)

  for (const summary of await current.locator(':scope > li > details > summary').all()) { await summary.click() }
  await expect(current.getByText(copy.passed, { exact: true })).toHaveCount(4)
  await expect(current.getByText(copy.receipt, { exact: true })).toHaveCount(4)
  await expect(current.getByText(copy.pairReview, { exact: true })).toHaveCount(2)
  await expect(activity.getByRole('link')).toHaveCount(0)
  await expect(activity.getByRole('button')).toHaveCount(0)
  await expect(page.getByRole('region', { name: /^Latest project test execution/ })).toHaveCount(0)

  for (const status of ['Passed', 'Verified', 'Approved', 'Accepted', 'Completed']) {
    await expect(activity.getByText(status, { exact: true })).toHaveCount(0)
  }

  expect(await activity.locator('[class]').evaluateAll(elements => elements.every(element => !/success|positive|green|emerald/.test(element.className)))).toBe(true)
  await capture('prepared-passed-claims-still-unverified', current.getByText(copy.passed, { exact: true }).first(), info)
})

// eslint-disable-next-line no-empty-pattern -- this scenario owns its temporary Electron app
test('blocks stale references and owner changes without promoting old selections or sending replacement creates', async ({}, info) => {
  const { page, selector } = await start()
  const create = page.getByRole('button', { name: 'Create objective', exact: true })

  for (const id of ['frontend', 'backend']) { await referenceChoice(selector, id).check() }
  model!.replaceRegistration('frontend')
  await create.click()
  await expect(page.getByRole('alert')).toHaveText(PREPARED_NATIVE_ERRORS.staleRegistration)
  expect(model!.creates).toHaveLength(1)
  expect(model!.admittedCount).toBe(0)
  await selector.getByRole('button', { name: copy.reread, exact: true }).click()

  for (const id of ['frontend', 'backend']) { await expect(referenceChoice(selector, id)).not.toBeChecked() }

  for (const reference of model!.currentReferences()) { await expect(selector.getByText(new RegExp(reference.registrationSha256))).toBeVisible() }
  await create.click()
  await expect(page.getByRole('alert')).toHaveText(copy.partial)
  expect(model!.creates).toHaveLength(1)

  for (const id of ['frontend', 'backend']) { await referenceChoice(selector, id).check() }

  model!.replaceOwner()
  await page.getByRole('button', { name: 'Edit organization and owner names', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Organization and owner', exact: true })
  await dialog.getByRole('button', { name: 'Refresh saved names', exact: true }).click()
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(selector.getByRole('checkbox', { name: copy.enable, exact: true })).toBeChecked()
  await expect(selector.getByRole('checkbox', { name: /^Use this exact registration:/ })).toHaveCount(0)
  await create.click()
  await expect(page.getByRole('alert').first()).toHaveText(copy.changed)
  expect(model!.creates).toHaveLength(1)
  expect(model!.admittedCount).toBe(0)
  await capture('prepared-owner-change-blocks-stale-choice', selector.getByRole('checkbox', { name: copy.enable, exact: true }), info)
  // Only an explicit disable releases the requested mode. Clear resolved alerts
  // through existing public controls, then acknowledge those exact expected errors.
  await selector.getByRole('checkbox', { name: copy.enable, exact: true }).uncheck()
  await page.getByRole('textbox', { name: 'Objective', exact: true }).fill(`${PREPARED_NATIVE_TITLE} after owner review`)

  for (const error of [PREPARED_NATIVE_ERRORS.staleRegistration, copy.partial, copy.changed]) { await acknowledgeExpectedErrorBanner(page, error) }
  expect(model!.creates).toHaveLength(1)
})
