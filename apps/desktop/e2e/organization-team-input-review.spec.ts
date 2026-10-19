/** Native form review → scoped gateway → loopback inference → durable shared setup receipts. */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { writeFileSync } from 'node:fs'
import path from 'node:path'

import type { Locator } from '@playwright/test'

import { buildAppEnv, type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expectKeyboardFocusVisible } from './focus-visibility'
import { acknowledgeExpectedErrorBanner, expect, test } from './test'

const root = path.resolve(import.meta.dirname, '../../..')
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
let fixture: MockBackendFixture | undefined

const team = {
  executive: { name: 'Ash', purpose: 'Coordinate the studio', responsibilities: ['Delegate and review outcomes'] },
  members: [
    {
      id: 'studio-lead',
      name: 'Ember',
      role: 'Manager',
      manager_id: 'executive',
      team: 'studio',
      capabilities: ['request.plan', 'request.integrate'],
      purpose: 'Plan studio work',
      responsibilities: ['Coordinate drafts']
    },
    {
      id: 'studio-writer',
      name: 'Flint',
      role: 'Worker',
      manager_id: 'studio-lead',
      team: 'studio',
      capabilities: ['work.draft'],
      purpose: 'Write scoped drafts',
      responsibilities: ['Produce requested drafts']
    }
  ]
}

const labels = {
  en: {
    setup: 'Set up organization',
    identity: 'Organization and owner',
    organization: 'Organization name',
    owner: 'Your display name',
    save: 'Save names',
    title: 'Build your starter team',
    jobs: 'Work to delegate',
    goals: 'What success looks like',
    naming: 'Team naming style (optional)',
    improveJobs: 'Improve work description',
    improveGoals: 'Improve success description',
    review: 'Review suggested wording',
    suggestion: 'Suggested text',
    apply: 'Apply suggestion',
    discard: 'Discard suggestion',
    generate: 'Generate team · Recommended',
    regenerate: 'Regenerate team',
    create: 'Create team',
    cancel: 'Cancel',
    name: 'Name',
    manual: 'Set up manually',
    preset: 'Research and writing',
    validate: 'Review changes',
    subscriptionIntro: 'Connect ChatGPT separately for Generate and Improve. Existing Codex sign-in is not used.',
    subscriptionTitle: 'ChatGPT plan for setup',
    unsupportedPlatform:
      'This requires Eidolon running locally on a Mac. Linux, Windows and remote connections are not supported.'
  },
  ar: {
    setup: 'إعداد المؤسسة',
    identity: 'المؤسسة والمالك',
    organization: 'اسم المؤسسة',
    owner: 'اسم العرض الخاص بك',
    save: 'حفظ الأسماء',
    title: 'أنشئ فريقك الأول',
    jobs: 'العمل المراد تفويضه',
    goals: 'كيف يبدو النجاح',
    naming: 'أسلوب تسمية الفريق (اختياري)',
    improveJobs: 'تحسين وصف العمل',
    improveGoals: 'تحسين وصف النجاح',
    review: 'مراجعة الصياغة المقترحة',
    suggestion: 'النص المقترح',
    apply: 'تطبيق الاقتراح',
    discard: 'تجاهل الاقتراح',
    generate: 'توليد الفريق · موصى به',
    regenerate: 'إعادة توليد الفريق',
    create: 'إنشاء الفريق',
    cancel: 'إلغاء',
    name: 'الاسم',
    manual: 'إعداد يدوي',
    preset: 'البحث والكتابة',
    validate: 'مراجعة التغييرات',
    subscriptionIntro: 'اربط ChatGPT بشكل منفصل للتوليد والتحسين. لن يُستخدم تسجيل دخول Codex الحالي.',
    subscriptionTitle: 'خطة ChatGPT للإعداد',
    unsupportedPlatform: 'يتطلب تشغيل Eidolon محلياً على Mac. لا يُدعم Linux أو Windows أو الاتصال البعيد.'
  }
}

test.setTimeout(180_000)
test.afterEach(async () => {
  await fixture?.cleanup()
  fixture = undefined
})

function run(f: MockBackendFixture, script: string) {
  return execFileSync(python, ['-c', script], { cwd: root, env: buildAppEnv(f.sandbox), encoding: 'utf8' })
}

function retained(f: MockBackendFixture) {
  return JSON.parse(
    run(
      f,
      `
import os,json,sqlite3
from pathlib import Path
p=Path(os.environ['HERMES_HOME'])/'organization'/'state.db'
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as c:
 c.row_factory=sqlite3.Row
 print(json.dumps({'generations':[dict(r) for r in c.execute('SELECT idempotency_key,status,dispatched,reserved_tokens FROM organization_team_generations ORDER BY created')],
 'savedTeams':c.execute('SELECT count(*) FROM organization_team_setup').fetchone()[0]}))
`
    )
  ) as {
    generations: { idempotency_key: string; status: string; dispatched: number; reserved_tokens: number }[]
    savedTeams: number
  }
}

async function openSetup(f: MockBackendFixture, locale: keyof typeof labels = 'en') {
  const text = labels[locale]
  await f.page.locator('.eid-team-entry').getByRole('button', { name: text.setup, exact: true }).click()
  const identity = f.page.getByRole('dialog', { name: text.identity, exact: true })
  await identity.getByLabel(text.organization, { exact: true }).fill('Synthetic Starter Studio')
  await identity.getByLabel(text.owner, { exact: true }).fill('Synthetic Owner')
  await identity.getByRole('button', { name: text.save, exact: true }).click()
  const dialog = f.page.getByRole('dialog', { name: text.title, exact: true })
  await expect(dialog).toBeVisible()

  return dialog
}

interface CapturePoint {
  name: string
  target: Locator
  context?: Locator[]
}

async function tabTo(f: MockBackendFixture, dialog: Locator, target: Locator) {
  const anchor = dialog.locator('footer').getByRole('button').first()
  await expect(anchor).toBeInViewport({ ratio: 1 })
  await anchor.focus()
  let steps = 0

  while (!(await target.evaluate(element => element === document.activeElement)) && steps < 80) {
    await f.page.keyboard.press('Tab')
    steps++
  }

  await expect(target, 'reachable by Tab from the visible dialog footer').toBeFocused()
  await expect(target, 'native keyboard scrolling reveals the full control').toBeInViewport({ ratio: 1 })

  return steps
}

async function captures(
  f: MockBackendFixture,
  dialog: Locator,
  points: CapturePoint[],
  prefix: string,
  assertDraft: () => Promise<void>
) {
  const evidence: object[] = []

  const scrollportBounds = () => dialog.locator('.eid-team-body').evaluate(element => {
    const bounds = element.getBoundingClientRect()
    const top = bounds.top + element.clientTop

    return { top, bottom: top + element.clientHeight, scrollTop: element.scrollTop, height: element.clientHeight }
  })

  for (const [width, height, zoom] of [
    [1280, 900, 1],
    [1280, 900, 2],
    [400, 820, 1]
  ]) {
    await f.app.evaluate(
      ({ BrowserWindow }, dimensions) => {
        const window = BrowserWindow.getAllWindows()[0]
        window.unmaximize()
        window.setMinimumSize(320, 400)
        window.setContentSize(dimensions.width, dimensions.height)
      },
      { width, height }
    )
    await f.page.evaluate(
      value =>
        (
          window as unknown as { hermesDesktop: { zoom: { setPercent: (value: number) => void } } }
        ).hermesDesktop.zoom.setPercent(value),
      zoom * 100
    )
    await expect
      .poll(() => f.app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.getZoomFactor()))
      .toBe(zoom)

    for (const { name, target, context = [] } of [
      { name: 'close', target: dialog.locator('[data-slot=dialog-close-button]') },
      ...points
    ]) {
      const tabSteps = await tabTo(f, dialog, target)
      // Allow the production one-shot reveal to finish, without helper focus or scrolling.
      await f.page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())))

      const immediate = await target.evaluate(element => {
        const style = getComputedStyle(element)
        const rect = element.getBoundingClientRect()
        const outset = parseFloat(style.outlineWidth) + parseFloat(style.outlineOffset)
        const ring = { left: rect.left - outset, top: rect.top - outset, right: rect.right + outset, bottom: rect.bottom + outset }
        const clipping: string[] = []

        if (ring.left < 0 || ring.top < 0 || ring.right > innerWidth || ring.bottom > innerHeight) {clipping.push('viewport')}

        for (let parent = element.parentElement; parent; parent = parent.parentElement) {
          const css = getComputedStyle(parent), bounds = parent.getBoundingClientRect()
          const clips = (value: string) => ['auto', 'scroll', 'hidden', 'clip'].includes(value)

          if ((clips(css.overflowX) && (ring.left < bounds.left + parent.clientLeft - 0.5 || ring.right > bounds.left + parent.clientLeft + parent.clientWidth + 0.5)) ||
              (clips(css.overflowY) && (ring.top < bounds.top + parent.clientTop - 0.5 || ring.bottom > bounds.top + parent.clientTop + parent.clientHeight + 0.5))) {
            clipping.push(parent.className)
          }
        }

        return { ring, clipping, visible: element.matches(':focus-visible'), width: parseFloat(style.outlineWidth), offset: parseFloat(style.outlineOffset) }
      })

      const initialContext = await Promise.all(context.map(description => description.boundingBox()))
      const initialScrollport = await scrollportBounds()
      await test.info().attach(`${prefix}-${name}-${width}-${zoom}-initial-tab`, {
        contentType: 'application/json', body: JSON.stringify({ steps: tabSteps, ...immediate, context: initialContext, scrollport: initialScrollport })
      })
      expect(immediate.clipping, 'original Tab reveals the complete focused outline').toEqual([])
      expect(immediate.visible).toBe(true)
      expect(immediate.width).toBe(3)
      expect(immediate.offset).toBe(3)

      for (const description of context) {
        await expect(description, 'original Tab reveals full adjacent wording before helper traversal').toBeInViewport({ ratio: 1 })
      }

      // Retain the round-trip checks too; Shift+Tab must preserve the same context.
      await f.page.keyboard.press('Tab')
      await f.page.keyboard.press('Shift+Tab')
      // Team's return-focus reveal uses the same one-shot frame as the original Tab.
      await f.page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())))
      const focus = await expectKeyboardFocusVisible(target, `${prefix}-${name}-${width}-${zoom}`)
      const roundTripContext = await Promise.all(context.map(description => description.boundingBox()))
      const roundTripScrollport = await scrollportBounds()
      await test.info().attach(`${prefix}-${name}-${width}-${zoom}-round-trip`, {
        contentType: 'application/json', body: JSON.stringify({ ...focus, context: roundTripContext, scrollport: roundTripScrollport })
      })
      await assertDraft()

      for (const description of context) {
        await expect(description).toBeVisible()
        await expect(description, 'adjacent wording remains readable with its control').toBeInViewport({ ratio: 1 })
        expect(await description.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
      }

      expect(await dialog.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
      await f.page.evaluate(async () => {
        await document.fonts.ready
        await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
      })
      let header

      if (name === 'close') {
        const titleText = await dialog.locator('[data-slot=dialog-title]').evaluate(element => {
          const range = document.createRange()
          range.selectNodeContents(element)

          return Array.from(range.getClientRects(), rect => ({
            left: rect.left,
            top: rect.top,
            right: rect.right,
            bottom: rect.bottom
          }))
        })

        const closeTarget = await target.boundingBox()
        expect(closeTarget?.width).toBeGreaterThanOrEqual(36)
        expect(closeTarget?.height).toBeGreaterThanOrEqual(36)
        expect(titleText.length).toBeGreaterThan(0)
        expect(
          titleText.filter(rect =>
            rect.left < focus.ring.right && rect.right > focus.ring.left &&
            rect.top < focus.ring.bottom && rect.bottom > focus.ring.top
          ),
          'the complete close focus ring does not cover the rendered title'
        ).toEqual([])
        header = { titleText, closeTarget }
      }

      const viewport = await f.page.evaluate(() => ({
        width: innerWidth,
        height: innerHeight,
        ratio: devicePixelRatio,
        dir: document.documentElement.dir
      }))

      const bytes = Buffer.from(
        await f.app.evaluate(async ({ BrowserWindow }) =>
          (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')
        ),
        'base64'
      )

      expect([bytes.readUInt32BE(16), bytes.readUInt32BE(20)]).toEqual([
        Math.round(viewport.width * viewport.ratio),
        Math.round(viewport.height * viewport.ratio)
      ])
      const filename = `${prefix}-${name}-${width}-${zoom}.png`
      writeFileSync(test.info().outputPath(filename), bytes)
      evidence.push({
        filename,
        width,
        height,
        zoom,
        viewport,
        focus,
        immediate,
        initialContext,
        initialScrollport,
        roundTripContext,
        roundTripScrollport,
        header,
        tabSteps,
        captureMethod: 'electron.capturePage',
        sha256: createHash('sha256').update(bytes).digest('hex')
      })
    }
  }

  writeFileSync(test.info().outputPath(`${prefix}-evidence.json`), JSON.stringify(evidence, null, 2))
}

for (const locale of ['en'] as const) {
  test(`reviews two improvements before generating within the three-call allowance in ${locale}`, async () => {
    const text = labels[locale]
    fixture = await setupMockBackend({
      modelContextLength: 128000,
      extraDisplayConfig: `  language: ${locale}`,
      mockServer: {
        starterTeamReply: JSON.stringify(team),
        starterTeamInputReply: raw => {
          const input = JSON.parse(raw) as { field: string }

          return JSON.stringify({
            suggestion:
              input.field === 'jobs'
                ? 'Research product changes weekly.'
                : 'Produce a source-linked report ready for review.',
            assumptions: ['Weekly delivery is suggested, not confirmed.']
          })
        }
      }
    })
    const f = fixture
    await waitForAppReady(f)
    await expect(f.page.locator('html')).toHaveAttribute('dir', 'ltr')
    const dialog = await openSetup(f, locale)
    const jobs = dialog.getByLabel(text.jobs, { exact: true })
    const goals = dialog.getByLabel(text.goals, { exact: true })
    await jobs.fill('Research product changes')
    await goals.fill('A useful report')
    await dialog.getByLabel(text.naming, { exact: true }).fill('Short astronomy names')
    expect(f.mock.receivedPrompts).toHaveLength(0)
    await dialog.getByRole('button', { name: text.improveJobs, exact: true }).click()
    await expect(dialog.getByRole('region', { name: text.review, exact: true })).toBeVisible({ timeout: 60_000 })
    await expect(jobs).toHaveValue('Research product changes')
    await expect(dialog.getByText('Weekly delivery is suggested, not confirmed.', { exact: true })).toBeVisible()
    expect(JSON.parse(f.mock.receivedPrompts[0])).toMatchObject({
      field: 'jobs',
      jobs: 'Research product changes',
      goals: 'A useful report',
      namingStyle: 'Short astronomy names'
    })
    const assumption = dialog.getByText('Weekly delivery is suggested, not confirmed.', { exact: true })
    await captures(
      f,
      dialog,
      [
        {
          name: 'suggestion',
          target: dialog.getByLabel(text.suggestion, { exact: true }),
          context: [dialog.locator('.eid-team-original')]
        },
        { name: 'apply', target: dialog.getByRole('button', { name: text.apply, exact: true }), context: [assumption] },
        {
          name: 'discard',
          target: dialog.getByRole('button', { name: text.discard, exact: true }),
          context: [assumption]
        }
      ],
      `improve-review-${locale}`,
      async () => {
        await expect(jobs).toHaveValue('Research product changes')
        await expect(goals).toHaveValue('A useful report')
        await expect(dialog.getByLabel(text.naming, { exact: true })).toHaveValue('Short astronomy names')
        await expect(dialog.getByLabel(text.suggestion, { exact: true })).toHaveValue(
          'Research product changes weekly.'
        )
      }
    )
    await expect(dialog.getByRole('button', { name: text.discard, exact: true })).toBeFocused()
    await f.page.keyboard.press('Enter')
    await expect(jobs).toBeFocused()
    await expect(jobs).toHaveValue('Research product changes')
    await dialog.getByRole('button', { name: text.improveGoals, exact: true }).click()
    const suggestion = dialog.getByLabel(text.suggestion, { exact: true })
    await expect(suggestion).toHaveValue('Produce a source-linked report ready for review.', { timeout: 60_000 })
    await expect(goals).toHaveValue('A useful report')
    await suggestion.fill('A source-linked monthly report for my review.')
    await tabTo(f, dialog, dialog.getByRole('button', { name: text.apply, exact: true }))
    await f.page.keyboard.press('Enter')
    await expect(goals).toBeFocused()
    await expect(goals).toHaveValue('A source-linked monthly report for my review.')
    expect(f.mock.receivedPrompts).toHaveLength(2)
    expect(retained(f).savedTeams).toBe(0)
    await dialog.getByRole('button', { name: text.generate, exact: true }).click()
    await expect(dialog.getByLabel(text.name, { exact: true }).first()).toHaveValue('Ash', { timeout: 60_000 })
    await expect(dialog.getByRole('button', { name: text.create, exact: true })).toBeEnabled()
    await expect(dialog.getByRole('button', { name: text.regenerate, exact: true })).toBeDisabled()
    expect(f.mock.receivedPrompts).toHaveLength(3)
    expect(JSON.parse(f.mock.receivedPrompts[2])).toMatchObject({
      jobs: 'Research product changes',
      goals: 'A source-linked monthly report for my review.'
    })
    const receipt = retained(f)
    expect(receipt.savedTeams).toBe(0)
    expect(receipt.generations).toHaveLength(3)
    expect(
      receipt.generations.every(row => row.status === 'completed' && row.dispatched === 1 && row.reserved_tokens > 0)
    ).toBe(true)
    await captures(
      f,
      dialog,
      [
        {
          name: 'executive',
          target: dialog.getByLabel(text.name, { exact: true }).first(),
          context: [dialog.locator('.eid-team-executive > strong')]
        },
        { name: 'manager-disclosure', target: dialog.locator('.eid-team-member > summary').nth(0) },
        { name: 'worker-disclosure', target: dialog.locator('.eid-team-member > summary').nth(1) },
        { name: 'create', target: dialog.getByRole('button', { name: text.create, exact: true }) }
      ],
      `full-team-review-${locale}`,
      async () => {
        await expect(dialog.getByLabel(text.name, { exact: true }).first()).toHaveValue('Ash')
      }
    )
    await expect(dialog.getByLabel(text.name, { exact: true }).first()).toHaveValue('Ash')
    await expect(dialog.getByRole('button', { name: text.create, exact: true })).toBeFocused()
    await f.page.keyboard.press('Enter')
    await expect(f.page.getByRole('dialog', { name: 'Team saved', exact: true })).toBeVisible()
    const saved = retained(f)
    expect(saved.savedTeams).toBe(1)
    expect(saved.generations).toEqual(receipt.generations)
    expect(f.mock.receivedPrompts).toHaveLength(3)
    writeFileSync(
      test.info().outputPath(`team-improve-${locale}-receipts.json`),
      JSON.stringify(
        { providerCalls: f.mock.receivedPrompts.length, beforeCreate: receipt, afterCreate: saved },
        null,
        2
      )
    )
  })
}

test('deliberate regeneration has one new durable send and exact recovery preserves newer edits', async () => {
  fixture = await setupMockBackend({
    modelContextLength: 128000,
    mockServer: { starterTeamReply: JSON.stringify(team) }
  })
  const f = fixture
  await waitForAppReady(f)
  const requests: Record<string, unknown>[] = []
  let loseReply = false
  await f.page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer()
    const pending = new Set<string | number>()
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as {
        id: string | number
        method: string
        params: Record<string, unknown>
      }

      if (request.method === 'organization.generateTeam') {
        requests.push(request.params)

        if (loseReply) {pending.add(request.id)}
      }

      server.send(message)
    })
    server.onMessage(message => {
      const response = JSON.parse(message.toString()) as { id: string | number; result?: unknown }

      if (pending.has(response.id) && response.result) {
        pending.delete(response.id)
        loseReply = false
        socket.send(
          JSON.stringify({
            jsonrpc: '2.0',
            id: response.id,
            error: {
              code: 5076,
              message: 'Synthetic acknowledgement loss',
              data: { reason: 'synthetic_acknowledgement_loss' }
            }
          })
        )

        return
      }

      socket.send(message)
    })
  })
  await f.page.reload()
  await waitForAppReady(f)
  const dialog = await openSetup(f)
  await dialog.getByLabel(labels.en.jobs, { exact: true }).fill('Research')
  await dialog.getByLabel(labels.en.goals, { exact: true }).fill('Evidence')
  await dialog.getByRole('button', { name: labels.en.generate, exact: true }).click()
  await expect(dialog.getByLabel('Name', { exact: true }).first()).toHaveValue('Ash', { timeout: 60_000 })
  loseReply = true
  await dialog.getByRole('button', { name: labels.en.regenerate, exact: true }).click()
  const failure = 'Could not confirm setup. Retry the same action to check its outcome.'
  await expect(dialog.getByRole('alert')).toHaveText(failure, { timeout: 60_000 })
  await expect(dialog.getByRole('button', { name: 'Check regeneration', exact: true })).toBeEnabled()
  expect(f.mock.receivedPrompts).toHaveLength(2)
  const before = retained(f)
  expect(before.generations).toHaveLength(2)
  expect(before.generations.every(row => row.status === 'completed' && row.dispatched === 1)).toBe(true)
  expect(before.generations.map(row => row.idempotency_key)).toEqual(requests.map(request => request.idempotencyKey))
  expect(requests[0].idempotencyKey).not.toBe(requests[1].idempotencyKey)
  await dialog.getByLabel('Name', { exact: true }).first().fill('My newer executive name')
  await dialog.getByRole('button', { name: 'Check regeneration', exact: true }).click()

  const preserved =
    'The recovered proposal used an earlier draft. Your newer edits are unchanged. Review this team, or request a new proposal if setup allowance remains.'

  await expect(dialog.getByRole('alert')).toHaveText(preserved)
  await expect(dialog.getByLabel('Name', { exact: true }).first()).toHaveValue('My newer executive name')
  expect(requests).toHaveLength(3)
  expect(requests[2]).toEqual(requests[1])
  expect(f.mock.receivedPrompts).toHaveLength(2)
  expect(retained(f)).toEqual(before)
  expect(before.savedTeams).toBe(0)
  await expect(dialog.getByRole('button', { name: 'Create team', exact: true })).toHaveCount(0)
  await dialog.getByRole('button', { name: 'Review changes', exact: true }).click()
  await expect(dialog.getByRole('button', { name: 'Create team', exact: true })).toBeEnabled()
  await expect(dialog.getByRole('alert')).toHaveCount(0)
  await acknowledgeExpectedErrorBanner(f.page, failure)
  await acknowledgeExpectedErrorBanner(f.page, preserved)
  expect(f.mock.receivedPrompts).toHaveLength(2)
  await captures(
    f,
    dialog,
    [
      {
        name: 'executive',
        target: dialog.getByLabel('Name', { exact: true }).first(),
        context: [dialog.locator('.eid-team-executive > strong')]
      },
      { name: 'create', target: dialog.getByRole('button', { name: 'Create team', exact: true }) }
    ],
    'regenerated-preserved-draft',
    async () => {
      await expect(dialog.getByLabel('Name', { exact: true }).first()).toHaveValue('My newer executive name')
    }
  )
  writeFileSync(
    test.info().outputPath('team-regeneration-receipts.json'),
    JSON.stringify({ requests, receipt: before, providerCalls: f.mock.receivedPrompts.length }, null, 2)
  )
})

for (const locale of ['en', 'ar'] as const) {
  test(`selected unsupported Codex subscription keeps descriptions and manual setup usable in ${locale} without a send`, async () => {
    const text = labels[locale]
    fixture = await setupMockBackend({ modelContextLength: 128000, extraDisplayConfig: `  language: ${locale}` })
    const f = fixture
    await waitForAppReady(f)
    // Change only this isolated profile's selected route. No sign-in, credentials or subscription calls.
    run(
      f,
      `
import os,yaml
from pathlib import Path
p=Path(os.environ['HERMES_HOME'])/'config.yaml'
config=yaml.safe_load(p.read_text())
config['model']={'provider':'openai-codex','default':'synthetic-codex'}
p.write_text(yaml.safe_dump(config))
`
    )
    await expect(f.page.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr')
    const dialog = await openSetup(f, locale)
    await expect(dialog.getByRole('status')).toContainText(text.subscriptionIntro)

    if (process.platform !== 'darwin') {
      const subscription = dialog.getByRole('region', { name: text.subscriptionTitle, exact: true })
      await expect(subscription.getByText(text.unsupportedPlatform, { exact: true })).toBeVisible()
    }

    const jobs = dialog.getByLabel(text.jobs, { exact: true })
    await expect(jobs).toBeEnabled()
    await jobs.fill('Keep my research description')
    await dialog.getByLabel(text.goals, { exact: true }).fill('Keep my desired result')
    await expect(dialog.getByRole('button', { name: text.generate, exact: true })).toBeDisabled()
    await expect(dialog.getByRole('button', { name: text.improveJobs, exact: true })).toBeDisabled()
    await captures(
      f,
      dialog,
      [
        {
          name: 'jobs',
          target: jobs,
          context: [
            dialog.locator('.eid-team-input').first().locator('.eid-team-input-heading'),
            dialog.locator('.eid-team-input').first().locator('.eid-team-note').first()
          ]
        }
      ],
      `codex-subscription-form-${locale}`,
      async () => {
        await expect(jobs).toHaveValue('Keep my research description')
        await expect(dialog.getByLabel(text.goals, { exact: true })).toHaveValue('Keep my desired result')
      }
    )
    await expect(jobs).toHaveValue('Keep my research description')
    await dialog.getByText(text.manual, { exact: true }).click()
    await dialog.getByRole('button', { name: text.preset, exact: true }).click()
    await dialog.getByRole('button', { name: text.validate, exact: true }).click()
    await expect(dialog.getByRole('button', { name: text.create, exact: true })).toBeEnabled()
    expect(f.mock.receivedPrompts).toHaveLength(0)
    expect(retained(f).savedTeams).toBe(0)
    expect(retained(f).generations).toHaveLength(0)
  })
}
