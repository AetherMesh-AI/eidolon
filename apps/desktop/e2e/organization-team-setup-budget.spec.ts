/** Native form review → scoped gateway → loopback inference → durable shared setup receipts. */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { appendFileSync, readFileSync, writeFileSync } from 'node:fs'
import http from 'node:http'
import path from 'node:path'

import type { Locator } from '@playwright/test'

import { buildAppEnv, type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { verifyKeyboardFocus } from './focus-visibility'
import { acknowledgeExpectedErrorBanner, expect, test } from './test'

const root = path.resolve(import.meta.dirname, '../../..')
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
let fixture: MockBackendFixture | undefined
let proxy: http.Server | undefined

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
    unsupported: 'The selected Codex subscription is not supported for AI team setup yet.'
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
    unsupported: 'اشتراك Codex المحدد غير مدعوم بعد لإعداد الفريق بالذكاء الاصطناعي.'
  }
}

test.setTimeout(180_000)
test.afterEach(async () => {
  await fixture?.cleanup()
  fixture = undefined
  await new Promise<void>(resolve => proxy ? proxy.close(() => resolve()) : resolve())
  proxy = undefined
})

function run(f: MockBackendFixture, script: string) {
  return execFileSync(python, ['-c', script], { cwd: root, env: buildAppEnv(f.sandbox), encoding: 'utf8' })
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

      const immediate = await target.evaluate(element => {
        const style = getComputedStyle(element)
        const rect = element.getBoundingClientRect()
        const outset = parseFloat(style.outlineWidth) + parseFloat(style.outlineOffset)

        const ring = {
          left: rect.left - outset,
          top: rect.top - outset,
          right: rect.right + outset,
          bottom: rect.bottom + outset
        }

        const clipping: string[] = []

        if (ring.left < 0 || ring.top < 0 || ring.right > innerWidth || ring.bottom > innerHeight) {
          clipping.push('viewport')
        }

        for (let parent = element.parentElement; parent; parent = parent.parentElement) {
          const css = getComputedStyle(parent),
            bounds = parent.getBoundingClientRect()

          const clips = (value: string) => ['auto', 'scroll', 'hidden', 'clip'].includes(value)

          if (
            (clips(css.overflowX) &&
              (ring.left < bounds.left + parent.clientLeft - 0.5 ||
                ring.right > bounds.left + parent.clientLeft + parent.clientWidth + 0.5)) ||
            (clips(css.overflowY) &&
              (ring.top < bounds.top + parent.clientTop - 0.5 ||
                ring.bottom > bounds.top + parent.clientTop + parent.clientHeight + 0.5))
          ) {
            clipping.push(parent.className)
          }
        }

        return {
          ring,
          clipping,
          width: parseFloat(style.outlineWidth),
          offset: parseFloat(style.outlineOffset),
          visible: element.matches(':focus-visible')
        }
      })

      await test.info().attach(`${prefix}-${name}-${width}-${zoom}-initial-tab`, {
        contentType: 'application/json',
        body: JSON.stringify({ steps: tabSteps, ...immediate })
      })
      expect(immediate.clipping, `${prefix}/${name}: original Tab focus has complete clearance`).toEqual([])
      expect(immediate.visible).toBe(true)
      expect(immediate.width).toBe(3)
      expect(immediate.offset).toBe(3)

      for (const description of context) {
        await expect(description, 'original Tab reveals adjacent label/help without helper scrolling').toBeInViewport({ ratio: 1 })
      }

      // The compatibility focus helper runs only after original Tab geometry passed.
      const focus = await verifyKeyboardFocus(f.page, target, `${prefix}-${name}-${width}-${zoom}`)
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
        budgetContext: { authoritative: ledger(f).state.setupBudget, visibleText: await dialog.locator('.eid-team-budget-values').allTextContents() },
        header,
        tabSteps,
        captureMethod: 'electron.capturePage',
        sha256: createHash('sha256').update(bytes).digest('hex')
      })
    }
  }

  writeFileSync(test.info().outputPath(`${prefix}-evidence.json`), JSON.stringify(evidence, null, 2))
}

function ledger(f: MockBackendFixture) {
  return JSON.parse(run(f, `
import os,json,sqlite3
from pathlib import Path
from eidolon_cli.organization_team_budget import budget_policy
home=Path(os.environ['HERMES_HOME']); p=home/'organization'/'state.db'
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as c:
 c.row_factory=sqlite3.Row
 from eidolon_cli.organization_metadata import _inspect
 identity=_inspect(c)['metadata']['organizationId']
 generations=[dict(r) for r in c.execute('SELECT idempotency_key,status,dispatched,reserved_tokens FROM organization_team_generations ORDER BY created')]
 receipts=[]
 if c.execute("SELECT 1 FROM sqlite_master WHERE name='organization_owner_policy_receipts'").fetchone():
  receipts=[json.loads(r[0])['receipt'] for r in c.execute('SELECT result_json FROM organization_owner_policy_receipts') if json.loads(r[0])['receipt']['action']=='setupBudget']
 saved=c.execute('SELECT count(*) FROM organization_team_setup').fetchone()[0]
print(json.dumps({'state':budget_policy(home,identity),'generations':generations,'receipts':receipts,'savedTeams':saved}))
`)) as {
    state: { policyGeneration: number; configuration: unknown; setupBudget: {
      policyVersion: number; status: string; usage: { legacy: number; improveTeamInput: number; generateTeam: number; total: number };
      remaining: { improveCalls: number; generationCalls: number; totalCalls: number }
    } }
    generations: { status: string; dispatched: number; reserved_tokens: number }[]
    receipts: { idempotencyKey: string; policyGeneration: number }[]
    savedTeams: number
  }
}

async function openBudget(dialog: Locator) {
  const budget = dialog.locator('.eid-team-budget')
  await budget.locator('summary').click()
  await expect(budget.getByLabel('Improve calls', { exact: true })).toBeVisible()

  return budget
}

async function reviewSuggested(budget: Locator) {
  await budget.getByRole('button', { name: 'Use suggested limits in draft', exact: true }).click()
  await expect(budget.getByLabel('Improve calls', { exact: true })).toHaveValue('4')
  await expect(budget.getByLabel('Generate / Regenerate calls', { exact: true })).toHaveValue('3')
  await expect(budget.getByLabel('Total setup calls', { exact: true })).toHaveValue('7')
  await budget.getByRole('button', { name: 'Review setup budget', exact: true }).click()
  await expect(budget.getByRole('region', { name: 'Review setup budget', exact: true })).toBeVisible()
}

async function applySuggested(budget: Locator) {
  await reviewSuggested(budget)
  await budget.getByRole('button', { name: 'Apply setup budget', exact: true }).click()
  await expect(budget.getByRole('region', { name: 'Saved budget receipt', exact: true })).toBeVisible()
}

function replies() {
  return { starterTeamReply: JSON.stringify(team), starterTeamInputReply: () => JSON.stringify({
    suggestion: 'Produce an evidence-linked report.', assumptions: ['The cadence is not yet confirmed.']
  }) }
}

test('owner explicitly adopts split setup limits while legacy debt, text and keyboard focus survive', async () => {
  fixture = await setupMockBackend({ modelContextLength: 128000, mockServer: replies() })
  const f = fixture
  await waitForAppReady(f)
  const dialog = await openSetup(f)
  const jobs = dialog.getByLabel(labels.en.jobs, { exact: true })
  const goals = dialog.getByLabel(labels.en.goals, { exact: true })
  await jobs.fill('Research product changes')
  await goals.fill('A useful source-linked report')
  await dialog.getByRole('button', { name: labels.en.improveJobs, exact: true }).click()
  await expect(dialog.getByRole('region', { name: labels.en.review, exact: true })).toBeVisible({ timeout: 60_000 })
  await dialog.getByRole('button', { name: labels.en.discard, exact: true }).click()
  const before = ledger(f)
  expect(before.state.setupBudget.usage.total).toBe(1)
  expect(before.state.configuration).toBeNull()
  const budget = await openBudget(dialog)
  await expect(budget.getByLabel('Improve calls', { exact: true })).toHaveValue('')
  expect(ledger(f)).toEqual(before)
  await budget.getByRole('button', { name: 'Use suggested limits in draft', exact: true }).click()
  expect(ledger(f)).toEqual(before)
  expect(f.mock.receivedPrompts).toHaveLength(1)

  const assertDescriptions = async () => {
    await expect(jobs).toHaveValue('Research product changes')
    await expect(goals).toHaveValue('A useful source-linked report')
  }

  await captures(f, dialog, [
    { name: 'improve-limit', target: budget.getByLabel('Improve calls', { exact: true }),
      context: [budget.getByLabel('Improve calls', { exact: true }).locator('..'), budget.locator('fieldset .eid-team-note').first()] },
    { name: 'generate-limit', target: budget.getByLabel('Generate / Regenerate calls', { exact: true }),
      context: [budget.getByLabel('Generate / Regenerate calls', { exact: true }).locator('..')] },
    { name: 'aggregate-limit', target: budget.getByLabel('Total setup calls', { exact: true }),
      context: [budget.getByLabel('Total setup calls', { exact: true }).locator('..')] }
  ], 'setup-budget-draft', assertDescriptions)
  await budget.getByRole('button', { name: 'Review setup budget', exact: true }).click()
  await expect(budget.getByRole('region', { name: 'Review setup budget', exact: true })).toBeVisible()
  expect(ledger(f)).toEqual(before)
  await captures(f, dialog, [
    { name: 'explicit-apply', target: budget.getByRole('button', { name: 'Apply setup budget', exact: true }),
      context: [budget.locator('.eid-team-budget-review > p.eid-team-note').first()] }
  ], 'setup-budget-reviewed', assertDescriptions)
  await budget.getByRole('button', { name: 'Apply setup budget', exact: true }).click()
  await expect(budget.getByRole('region', { name: 'Saved budget receipt', exact: true })).toBeVisible()
  await expect.poll(() => ledger(f).state.setupBudget.policyVersion).toBe(2)
  const adopted = ledger(f)
  expect(adopted.state.policyGeneration).toBe(before.state.policyGeneration + 1)
  expect(adopted.receipts).toHaveLength(1)
  expect(adopted.state.setupBudget.usage).toEqual({ legacy: 1, improveTeamInput: 0, generateTeam: 0, total: 1 })
  expect(adopted.state.setupBudget.remaining).toEqual({ improveCalls: 4, generationCalls: 3, totalCalls: 6 })
  expect(f.mock.receivedPrompts).toHaveLength(1)
  await dialog.getByRole('button', { name: labels.en.improveGoals, exact: true }).click()
  await expect(dialog.getByRole('region', { name: labels.en.review, exact: true })).toBeVisible({ timeout: 60_000 })
  await dialog.getByRole('button', { name: labels.en.discard, exact: true }).click()
  await dialog.getByRole('button', { name: labels.en.generate, exact: true }).click()
  await expect(dialog.getByLabel('Name', { exact: true }).first()).toHaveValue('Ash', { timeout: 60_000 })
  const after = ledger(f)
  expect(after.state.setupBudget.usage).toEqual({ legacy: 1, improveTeamInput: 1, generateTeam: 1, total: 3 })
  expect(after.state.setupBudget.remaining).toEqual({ improveCalls: 3, generationCalls: 2, totalCalls: 4 })
  expect(f.mock.receivedPrompts).toHaveLength(3)
  expect(after.savedTeams).toBe(0)
  expect(JSON.parse(f.mock.receivedPrompts[2])).toMatchObject({ jobs: 'Research product changes', goals: 'A useful source-linked report' })
})

test('lost policy acknowledgement retries the exact owner intent without duplicate receipts or inference', async () => {
  fixture = await setupMockBackend({ modelContextLength: 128000, mockServer: replies() })
  const f = fixture
  await waitForAppReady(f)
  const requests: Record<string, unknown>[] = []
  let drop = true
  await f.page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer()
    const pending = new Set<string | number>()
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as { id: string | number; method: string; params: Record<string, unknown> }

      if (request.method === 'organization.applySetupBudget') {
        requests.push(request.params)

        if (drop) {pending.add(request.id)}
      }

      server.send(message)
    })
    server.onMessage(message => {
      const response = JSON.parse(message.toString()) as { id: string | number; result?: unknown }

      if (pending.has(response.id) && response.result) {
        pending.delete(response.id)
        drop = false
        socket.send(JSON.stringify({ jsonrpc: '2.0', id: response.id, error: {
          code: 5076, message: 'Synthetic acknowledgement loss', data: { reason: 'synthetic_acknowledgement_loss' }
        } }))

        return
      }

      socket.send(message)
    })
  })
  await f.page.reload()
  await waitForAppReady(f)
  const dialog = await openSetup(f)
  await dialog.getByLabel(labels.en.jobs, { exact: true }).fill('Keep this work description')
  await dialog.getByLabel(labels.en.goals, { exact: true }).fill('Keep this success description')
  const budget = await openBudget(dialog)
  const before = ledger(f)
  await reviewSuggested(budget)
  await budget.getByRole('button', { name: 'Apply setup budget', exact: true }).click()
  await expect(budget.getByRole('button', { name: 'Check original budget change', exact: true })).toBeEnabled()
  const lost = 'The acknowledgement was lost. The change may already be saved. Reconcile the original change before editing again.'
  await expect(budget.getByRole('alert')).toHaveText(lost)
  const committed = ledger(f)
  expect(committed.receipts).toHaveLength(1)
  expect(committed.state.policyGeneration).toBe(before.state.policyGeneration + 1)
  await budget.getByRole('button', { name: 'Check original budget change', exact: true }).click()
  await expect(budget.getByRole('region', { name: 'Saved budget receipt', exact: true })).toBeVisible()
  await acknowledgeExpectedErrorBanner(f.page, lost)
  expect(requests).toHaveLength(2)
  expect(requests[1]).toEqual(requests[0])
  expect(ledger(f)).toEqual(committed)
  expect(f.mock.receivedPrompts).toHaveLength(0)
  await expect(dialog.getByLabel(labels.en.jobs, { exact: true })).toHaveValue('Keep this work description')
  await expect(dialog.getByLabel(labels.en.goals, { exact: true })).toHaveValue('Keep this success description')
})

test('source conflicts and an uncertain physical send never permit policy refill or another inference', async () => {
  let physicalPosts = 0
  fixture = await setupMockBackend({ modelContextLength: 128000, beforeLaunch: async (sandbox, mock) => {
    const upstream = new URL(mock.url)
    proxy = http.createServer((request, response) => {
      if (request.method === 'POST') {
        request.resume()
        request.on('end', () => { physicalPosts++; request.socket.destroy() })

        return
      }

      const forwarded = http.request(new URL(request.url ?? '/', upstream.origin), { method: request.method }, incoming => {
        response.writeHead(incoming.statusCode ?? 502, incoming.headers)
        incoming.pipe(response)
      })

      forwarded.on('error', () => { response.writeHead(502); response.end() })
      request.pipe(forwarded)
    })
    await new Promise<void>(resolve => proxy!.listen(0, '127.0.0.1', resolve))
    const address = proxy.address()

    if (!address || typeof address === 'string') {throw new Error('Expected numeric loopback listener')}
    const file = path.join(sandbox.hermesHome, 'config.yaml')
    writeFileSync(file, readFileSync(file, 'utf8').replaceAll(upstream.origin, `http://127.0.0.1:${address.port}`))
  } })
  const f = fixture
  await waitForAppReady(f)
  const inferenceRequests: Record<string, unknown>[] = []
  await f.page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as { method: string; params: Record<string, unknown> }

      if (request.method === 'organization.generateTeam') {inferenceRequests.push(request.params)}
      server.send(message)
    })
    server.onMessage(message => socket.send(message))
  })
  await f.page.reload()
  await waitForAppReady(f)
  const dialog = await openSetup(f)
  const jobs = dialog.getByLabel(labels.en.jobs, { exact: true })
  const goals = dialog.getByLabel(labels.en.goals, { exact: true })
  await jobs.fill('Keep the uncertain work description')
  await goals.fill('Keep the uncertain goal description')
  const budget = await openBudget(dialog)
  await reviewSuggested(budget)
  const before = ledger(f)
  appendFileSync(path.join(f.sandbox.hermesHome, 'config.yaml'), '\n# synthetic setup-budget source revision change\n')
  await budget.getByRole('button', { name: 'Apply setup budget', exact: true }).click()
  await expect(budget.getByRole('alert')).toContainText('The policy changed.')
  expect(ledger(f).receipts).toHaveLength(0)
  expect(ledger(f).state.policyGeneration).toBe(before.state.policyGeneration)
  expect(physicalPosts).toBe(0)
  await budget.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(budget.getByRole('button', { name: 'Review setup budget', exact: true })).toBeEnabled()
  await applySuggested(budget)
  await acknowledgeExpectedErrorBanner(f.page, 'The policy changed. Refresh and preview your draft again.')
  await expect(dialog.getByRole('button', { name: labels.en.generate, exact: true })).toBeEnabled()
  await dialog.getByRole('button', { name: labels.en.generate, exact: true }).click()
  await expect.poll(() => ledger(f).state.setupBudget.status, { timeout: 60_000 }).toBe('uncertain')
  const failure = 'Could not confirm setup. Retry the same action to check its outcome.'
  await expect(dialog.getByRole('alert')).toHaveText(failure)
  const uncertain = ledger(f)
  expect(physicalPosts).toBe(1)
  expect(uncertain.generations).toHaveLength(1)
  expect(uncertain.generations[0]).toMatchObject({ status: 'uncertain', dispatched: 1 })
  expect(uncertain.generations[0].reserved_tokens).toBeGreaterThan(0)
  expect(uncertain.state.setupBudget.remaining.totalCalls).toBe(6)
  await expect(budget.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled()
  await budget.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(budget.getByText('A setup request is running or its outcome is uncertain. Review it before changing the budget or starting another request.', { exact: true })).toBeVisible()
  await expect(budget.getByRole('button', { name: 'Review setup budget', exact: true })).toBeDisabled()
  // The existing intent remains available for receipt recovery, never a new send.
  const generate = dialog.getByRole('button', { name: labels.en.generate, exact: true })
  await expect(generate).toBeEnabled()
  expect(inferenceRequests).toHaveLength(1)
  await generate.click()
  await expect(dialog.getByRole('alert')).toHaveText(failure)
  await expect(generate).toBeEnabled()
  expect(inferenceRequests).toHaveLength(2)
  expect(inferenceRequests[1]).toEqual(inferenceRequests[0])
  expect(physicalPosts).toBe(1)
  expect(ledger(f)).toEqual(uncertain)
  await expect(jobs).toHaveValue('Keep the uncertain work description')
  await expect(goals).toHaveValue('Keep the uncertain goal description')
  // Editing the goal clears that old intent; an uncertain charge blocks a fresh one.
  await goals.fill('Keep the updated uncertain goal description')
  await expect(generate).toBeDisabled()
  await expect(dialog.getByRole('button', { name: labels.en.improveJobs, exact: true })).toBeDisabled()
  await expect(goals).toHaveValue('Keep the updated uncertain goal description')
  expect(ledger(f)).toEqual(uncertain)
  expect(physicalPosts).toBe(1)
  await dialog.getByRole('button', { name: labels.en.cancel, exact: true }).click()
  await acknowledgeExpectedErrorBanner(f.page, failure)
})
