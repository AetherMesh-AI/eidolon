/** Paired native screenshots use the same synthetic records on both revisions.
 * The real Electron shell and temporary gateway boot normally; no work is run. */
import { createHash } from 'node:crypto'
import { writeFileSync } from 'node:fs'

import { BUILTIN_THEME_LIST } from '../src/themes/presets'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { acknowledgeExpectedErrorBanner, expect, test } from './test'

const createdAt = '2026-10-08T09:00:00Z'

const agents = [
  {
    id: 'executive',
    name: 'Avery',
    role: 'Executive',
    team: 'Leadership',
    status: 'reviewing',
    capabilities: ['request.accept', 'request.decompose'],
    responsibilities: ['Set direction and accept reviewed outcomes']
  },
  {
    id: 'manager',
    name: 'Morgan',
    role: 'Manager',
    team: 'Product',
    managerId: 'executive',
    status: 'working',
    capabilities: ['request.plan', 'request.integrate'],
    responsibilities: ['Plan delivery and coordinate specialists']
  },
  {
    id: 'writer',
    name: 'Robin',
    role: 'Worker',
    team: 'Product',
    managerId: 'manager',
    status: 'working',
    capabilities: ['work.draft'],
    responsibilities: ['Draft clear release documentation']
  },
  {
    id: 'reviewer',
    name: 'Sage',
    role: 'Worker',
    team: 'Quality',
    managerId: 'manager',
    status: 'reviewing',
    capabilities: ['request.review'],
    responsibilities: ['Review evidence and acceptance criteria']
  },
  {
    id: 'researcher',
    name: 'Rowan',
    role: 'Worker',
    team: 'Research',
    managerId: 'manager',
    status: 'idle',
    capabilities: ['work.analyze'],
    responsibilities: ['Verify source material and context']
  }
].map(agent => ({
  ...agent,
  persistent: true,
  lifecycle: 'active',
  provider: null,
  model: null,
  tools: [],
  summary: 'Synthetic identity for native layout inspection. No provider work is running.'
}))

const snapshot = {
  source: 'runtime',
  runtime: {
    state: 'idle',
    profile: 'default',
    capabilities: ['work.draft', 'work.analyze'],
    maxWorkers: 2,
    maxInflight: 2,
    rosterCount: agents.length,
    workingCount: 0,
    scope: 'Synthetic visual fixture. No work, external tools, or provider calls are executed.',
    setup: {
      version: 1,
      provider: { status: 'unchecked', blockers: [], inheritedMembers: agents.length, overriddenMembers: 0 },
      backgroundOptIn: false
    }
  },
  outcomes: { generation: 'fixture-1', total: 1, unread: 1, hasMore: false, nextCursor: null, items: [{ objectiveId: 'research', revision: 1, seen: false, status: 'accepted', title: 'Document provider boundaries', summary: 'Synthetic reviewed scope and provider boundary summary.', round: 1, deliverableId: 'fixture-deliverable', acceptanceRequestId: 'fixture-accept', evidenceIds: ['fixture-deliverable'], createdAt, updatedAt: createdAt, archived: false }] },
  objectives: [
    {
      id: 'release',
      title: 'Prepare the autumn release brief',
      description: 'Bring the launch narrative, verified changes, and review notes into one clear brief.',
      status: 'active',
      phase: 'Draft and review',
      milestone: 'Release context verified',
      progress: 60,
      planningMode: 'executive_packages',
      acceptance: { status: 'reviewing', round: 1, maxReplans: 2, criteria: ['Brief reflects verified changes'], summary: null, deliverableId: null },
      workPackages: [{ id: 'release-package', objectiveId: 'release', round: 1, managerId: 'manager', title: 'Release documentation', description: 'Draft the narrative and review its supporting evidence.', criterionIndexes: [0], projectIds: [], dependencyIds: [], maxTasks: 3, planRequestId: 'fixture-plan', status: 'working', taskIds: ['draft', 'review'] }]
    },
    {
      id: 'navigation',
      title: 'Review the workspace navigation',
      description: 'Compare the owner journey across a standard desktop and a narrow window.',
      status: 'waiting',
      phase: 'Owner review',
      milestone: 'Navigation proposal ready',
      progress: 80
    },
    {
      id: 'research',
      title: 'Document provider boundaries',
      description: 'Record the supported runtime responsibilities and evidence requirements.',
      status: 'completed',
      phase: 'Complete',
      milestone: 'Reviewed summary retained',
      progress: 100,
      acceptance: { status: 'accepted', round: 1, maxReplans: 2, criteria: ['Boundaries documented'], summary: 'Synthetic reviewed scope and provider boundary summary.', deliverableId: 'fixture-deliverable' }
    }
  ].map(objective => ({ ...objective, createdAt, source: 'runtime', ownerId: 'executive', managerId: 'manager' })),
  agents,
  tasks: [
    {
      id: 'draft',
      objectiveId: 'release',
      title: 'Draft the release brief',
      ownerId: 'writer',
      managingAgentId: 'manager',
      assignedById: 'manager',
      workPackageId: 'release-package',
      currentRound: true,
      status: 'working',
      dependsOn: []
    },
    {
      id: 'review',
      objectiveId: 'release',
      title: 'Review the acceptance evidence',
      ownerId: 'reviewer',
      managingAgentId: 'manager',
      workPackageId: 'release-package',
      currentRound: true,
      status: 'review',
      dependsOn: ['draft']
    }
  ],
  requests: [{ id: 'fixture-question', objectiveId: 'navigation', type: 'request.question', team: 'Product', priority: 2, status: 'pending_intervention', reason: 'Review the proposed owner journey before the next iteration.', attempts: 0, createdAt }],
  activity: [],
  knowledge: [],
  conversations: []
}

let fixture: MockBackendFixture | undefined

test.setTimeout(360_000)
test.afterEach(async () => {
  await fixture?.cleanup()
  fixture = undefined
})

test('keeps native organization navigation, roster and objective layouts usable at both window sizes', async () => {
  fixture = await setupMockBackend()
  const { app, page, mock } = fixture
  const organizationMethods: string[] = []
  let displayedSnapshot: Omit<typeof snapshot, 'requests'> & { requests?: typeof snapshot.requests } = snapshot
  let readinessUnknown = false
  await page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as { id?: number; method?: string }

      if (request.method?.startsWith('organization.')) {
        organizationMethods.push(request.method)
      }

      if (request.method === 'organization.snapshot') {
        socket.send(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: displayedSnapshot }))
      } else if (request.method === 'setup.runtime_check' || request.method === 'setup.status') {
        socket.send(JSON.stringify({ jsonrpc: '2.0', id: request.id, ...(readinessUnknown ? { error: { code: -32000, message: 'Scripted unavailable readiness probe' } } : { result: request.method === 'setup.status' ? { provider_configured: true } : { ok: true } }) }))
      } else if (request.method === 'organization.outcomes') {
        socket.send(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: snapshot.outcomes }))
      } else {
        server.send(message)
      }
    })
  })
  await page.clock.setFixedTime(new Date('2026-10-08T12:00:00Z'))
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' })
  await page.reload()
  await waitForAppReady(fixture)

  const navigation = page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })

  const workspace = page.getByRole('main', { name: 'Organization workspace', exact: true })
  const captures: object[] = []
  const contrastChecks: Array<{ text: string | null; contrastRatio: number; opacity: number }> = []

  const runtime = await app.evaluate(() => ({
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    platform: process.platform
  }))

  function writeEvidence() {
    writeFileSync(
      test.info().outputPath('native-visual-evidence.json'),
      JSON.stringify(
        {
          sourceCommit: process.env.EIDOLON_VISUAL_SOURCE_SHA ?? null,
          scenario: 'synthetic-read-only-organization',
          runtime,
          providerCalls: mock.receivedPrompts.length,
          organizationMethods: [...new Set(organizationMethods)],
          captures
        },
        null,
        2
      )
    )
  }

  async function resize(width: number) {
    await app.evaluate(({ BrowserWindow }, requestedWidth) => {
      const window = BrowserWindow.getAllWindows()[0]
      window.unmaximize()
      window.setMinimumSize(600, 600)
      window.setContentSize(requestedWidth, 900)
      window.setPosition(0, 0)
      window.focus()
    }, width)
    await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(width)
  }

  async function capture(name: string) {
    await workspace.evaluate(element => {
      element.scrollTop = 0
    })
    await page.evaluate(async () => {
      await document.fonts.ready
      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
    })

    const bytes = await page.screenshot({
      path: test.info().outputPath(`${name}.png`),
      animations: 'disabled',
      caret: 'hide'
    })

    await test.info().attach(name, { body: bytes, contentType: 'image/png' })

    const colors = await page.locator('.eidolon').locator('.eid-status, a, button:not(:disabled), input, textarea, select, .eid-owner-chat-bubble, .eid-home-panel h2, .eid-home-record h3, .eid-home-record p, .eid-home-record small, .eid-objective-overview h3, .eid-objective-overview p, .eid-objective-overview small, .eid-objective-overview strong').evaluateAll(elements =>
        elements.map(element => {
          const backgrounds: string[] = []
          let opacity = 1
          let ancestor: Element | null = element

          while (ancestor) {
            const style = getComputedStyle(ancestor)
            backgrounds.push(style.backgroundColor)
            opacity *= Number(style.opacity)
            ancestor = ancestor.parentElement
          }

          const canvas = document.createElement('canvas')
          canvas.width = canvas.height = 1
          const context = canvas.getContext('2d')!
          // Resolve CSS color-mix and alpha in Chromium, without sampling glyph antialiasing.
          context.fillStyle = '#fff'
          context.fillRect(0, 0, 1, 1)

          for (const background of [...backgrounds].reverse()) {
            context.fillStyle = background
            context.fillRect(0, 0, 1, 1)
          }

          const backgroundRgba = [...context.getImageData(0, 0, 1, 1).data]
          context.fillStyle = getComputedStyle(element).color
          context.fillRect(0, 0, 1, 1)
          const foregroundRgba = [...context.getImageData(0, 0, 1, 1).data]

          const luminance = (rgba: number[]) => {
            const linear = rgba.slice(0, 3).map(channel => {
              const value = channel / 255

              return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
            })

            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
          }

          const [low, high] = [luminance(backgroundRgba), luminance(foregroundRgba)].sort((left, right) => left - right)
          const rect = element.getBoundingClientRect()

          return {
            text: element.textContent,
            label: element.getAttribute('aria-label') || element.getAttribute('title'),
            tag: element.tagName,
            color: getComputedStyle(element).color,
            backgrounds,
            opacity,
            backgroundRgba,
            foregroundRgba,
            contrastRatio: (high + 0.05) / (low + 0.05),
            bounds: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
          }
        })
      )

    contrastChecks.push(...colors)
    expect(await workspace.evaluate(element => {
      const style = getComputedStyle(element)

      return style.getPropertyValue('--aether-500').trim() === style.getPropertyValue('--ui-accent').trim()
    })).toBe(true)
    const barBounds = await page.getByRole('contentinfo', { name: 'System health', exact: true }).boundingBox()
    expect(barBounds).not.toBeNull()
    expect(barBounds!.y + barBounds!.height).toBeLessThanOrEqual(await page.evaluate(() => window.innerHeight) + 1)
    captures.push({
      name,
      colorScheme: await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme),
      theme: await page.locator('html').getAttribute('data-hermes-theme'),
      sha256: createHash('sha256').update(bytes).digest('hex'),
      statusColors: colors,
      viewport: await page.evaluate(() => ({
        width: window.innerWidth,
        height: window.innerHeight,
        devicePixelRatio: window.devicePixelRatio
      })),
      workspace: await workspace.evaluate(element => ({
        width: element.clientWidth,
        scrollWidth: element.scrollWidth,
        height: element.clientHeight,
        scrollHeight: element.scrollHeight
      }))
    })
    writeEvidence()
  }

  async function inspectObjectiveOverview(mode: string) {
    await workspace.getByRole('link').filter({ has: page.getByText('Prepare the autumn release brief', { exact: true }) }).click()
    const overview = page.getByRole('region', { name: 'Objective overview', exact: true })
    await expect(overview).toBeVisible()
    await expect(overview.getByRole('heading', { name: 'Release documentation', exact: true })).toBeVisible()
    await expect(overview.getByRole('region', { name: 'Delivery', exact: true })).toContainText('has not been recorded')
    await capture(`objective-detail-wide-${mode}`)
    await resize(760)
    await capture(`objective-detail-narrow-${mode}`)
    await page.getByRole('button', { name: 'Review owner requests', exact: true }).click()
    await expect(page.getByRole('region', { name: 'Objective request controls', exact: true })).toBeFocused()
    await page.getByRole('tab', { name: 'Activity', exact: true }).click()
    await page.getByRole('button', { name: 'Inspect acceptance and delivery', exact: true }).click()
    await expect(page.getByRole('region', { name: 'Objective work details', exact: true })).toBeFocused()
    await expect(page.getByRole('tab', { name: 'Work', exact: true })).toHaveAttribute('aria-selected', 'true')
    await resize(1220)
    await navigation.getByRole('link', { name: 'Work Overview', exact: true }).click()
    const card = page.getByRole('region', { name: 'Prepare the autumn release brief', exact: true })
    await expect(card).toContainText('1 current-round manager assignment')
    await expect(card.getByRole('list', { name: 'Recorded task states', exact: true })).toContainText('Working')
    await capture(`work-overview-wide-${mode}`)
    await resize(760)
    await capture(`work-overview-narrow-${mode}`)
    await resize(1220)
    await navigation.getByRole('link', { name: 'Objectives', exact: true }).click()
    await workspace.getByRole('link').filter({ has: page.getByText('Review the workspace navigation', { exact: true }) }).click()
    await expect(page.getByRole('region', { name: 'Needs your input', exact: true })).toContainText('Review the proposed owner journey')
    await capture(`objective-owner-input-${mode}`)
    await navigation.getByRole('link', { name: 'Objectives', exact: true }).click()
    await workspace.locator('a[href="/objectives/research"]').first().click()
    await expect(page.getByRole('region', { name: 'Delivery', exact: true })).toContainText('Accepted deliverable')
    await capture(`objective-accepted-delivery-${mode}`)
    await navigation.getByRole('link', { name: 'Objectives', exact: true }).click()
  }

  await resize(1220)
  await expect(page.locator('html')).toHaveClass(/\bdark\b/)
  await expect(page.locator('html')).toHaveAttribute('data-hermes-theme', 'eidolon')
  await navigation.getByRole('link', { name: 'Home', exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Objective', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'What’s happening?', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'What needs me?', exact: true })).toContainText('Review the proposed owner journey')
  await expect(page.getByRole('region', { name: 'What’s ready?', exact: true })).toContainText('Synthetic reviewed scope')
  await expect(workspace.locator('.eid-runtime-status')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Open settings', exact: true })).toHaveCount(0)
  await capture('01-home-wide-dark')

  // Home puts the overview first; explicit New objective reaches the retained form.
  {
    await page.getByRole('button', { name: 'New objective', exact: true }).click()
    await expect(page.getByRole('textbox', { name: 'Objective', exact: true })).toBeFocused()
    await expect(page.getByRole('textbox', { name: 'Objective', exact: true })).toBeInViewport({ ratio: 1 })
    await expect(page.getByRole('button', { name: 'Create objective', exact: true })).toBeInViewport({ ratio: 1 })
  }

  await navigation.getByRole('link', { name: 'Organization', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Organization', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /^Inspect (Avery|Morgan|Robin|Sage|Rowan)$/ })).toHaveCount(
    agents.length
  )
  await capture('02-roster-wide-dark')
  await page.getByRole('button', { name: 'Grid', exact: true }).click()
  await capture('03-roster-grid-wide-dark')
  await page.getByRole('button', { name: 'List', exact: true }).click()

  const agentButton = page.getByRole('button', { name: 'Inspect Robin', exact: true })
  await agentButton.click()
  const inspector = page.getByRole('complementary', { name: 'Agent details', exact: true })
  await expect(inspector).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(inspector).toHaveCount(0)
  await expect(agentButton).toBeFocused()

  await navigation.getByRole('link', { name: 'Objectives', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Objectives', exact: true })).toBeVisible()
  await expect(
    workspace.getByRole('link').filter({ has: page.getByText('Prepare the autumn release brief', { exact: true }) })
  ).toBeVisible()
  await capture('04-objectives-wide-dark')
  await inspectObjectiveOverview('dark')
  await page.getByRole('textbox', { name: 'Search objectives', exact: true }).fill('autumn')
  await expect(
    workspace.getByRole('link').filter({ has: page.getByText('Review the workspace navigation', { exact: true }) })
  ).toHaveCount(0)
  await page.getByRole('textbox', { name: 'Search objectives', exact: true }).fill('')
  await expect(
    workspace.getByRole('link').filter({ has: page.getByText('Review the workspace navigation', { exact: true }) })
  ).toBeVisible()

  await resize(760)
  await capture('05-objectives-narrow-dark')
  await resize(1220)
  await navigation.getByRole('link', { name: 'Organization', exact: true }).click()
  await expect(agentButton).toBeVisible()
  await resize(760)
  await capture('06-roster-narrow-dark')
  await resize(1220)
  await navigation.getByRole('link', { name: 'Home', exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Objective', exact: true })).toBeVisible()
  await resize(760)
  await capture('07-home-narrow-dark')

  // Navigation history returns to the dismissed roster, without reopening an inspector.
  await page.goBack()
  await expect(page.getByRole('heading', { name: 'Organization', exact: true })).toBeVisible()
  await expect(inspector).toHaveCount(0)
  await page.goForward()
  await expect(page.getByRole('textbox', { name: 'Objective', exact: true })).toBeVisible()

  // Eidolon's built-in palette deliberately stays dark in both mode settings.
  // Exercise a genuine shared light palette through the same Appearance UI on both builds.
  await resize(1220)
  await page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('link', { name: 'Settings', exact: true })
    .click()
  await page.getByRole('button', { name: 'Appearance', exact: true }).click()
  await page
    .getByRole('button')
    .filter({ has: page.getByText('AetherMesh', { exact: true }) })
    .click()
  await page.getByRole('button', { name: 'Light', exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-hermes-theme', 'nous')
  await expect(page.locator('html')).not.toHaveClass(/\bdark\b/)
  await page.getByRole('button', { name: 'Close settings', exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Objective', exact: true })).toBeVisible()
  await capture('08-home-wide-light')
  await navigation.getByRole('link', { name: 'Organization', exact: true }).click()
  await expect(agentButton).toBeVisible()
  await capture('09-roster-wide-light')
  await page.getByRole('button', { name: 'Grid', exact: true }).click()
  await capture('10-roster-grid-wide-light')
  await page.getByRole('button', { name: 'List', exact: true }).click()
  await navigation.getByRole('link', { name: 'Objectives', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Objectives', exact: true })).toBeVisible()
  await capture('11-objectives-wide-light')
  await inspectObjectiveOverview('light')
  await resize(760)
  await capture('12-objectives-narrow-light')
  await resize(1220)
  await navigation.getByRole('link', { name: 'Organization', exact: true }).click()
  await expect(agentButton).toBeVisible()
  await resize(760)
  await capture('13-roster-narrow-light')
  await resize(1220)
  await navigation.getByRole('link', { name: 'Home', exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Objective', exact: true })).toBeVisible()
  await resize(760)
  await capture('14-home-narrow-light')
  await page.reload()
  await waitForAppReady(fixture)
  await expect(page.locator('html')).toHaveAttribute('data-hermes-theme', 'nous')
  await expect(page.locator('html')).not.toHaveClass(/\bdark\b/)
  await capture('15-theme-choice-survives-reload')
  const goal = page.getByRole('textbox', { name: 'Objective', exact: true })
  await goal.fill('Retain this unsubmitted objective across navigation')

  for (const destination of ['Messages', 'Work Overview', 'Home', 'Messages', 'Home']) {
    const link = navigation.getByRole('link', { name: destination, exact: true })
    await link.focus()
    await page.keyboard.press('Enter')
    await expect(link).toHaveAttribute('aria-current', 'page')
  }

  await expect(goal).toHaveValue('Retain this unsubmitted objective across navigation')
  await page.getByRole('button', { name: 'New objective', exact: true }).click()
  await expect(goal).toBeFocused()
  await capture('16-home-retained-intake')
  await navigation.getByRole('link', { name: 'Objectives', exact: true }).click()
  await workspace.getByRole('link', { name: 'New objective', exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Objective', exact: true })).toBeFocused()
  await expect(page.getByRole('textbox', { name: 'Objective', exact: true })).toHaveValue('Retain this unsubmitted objective across navigation')
  const health = page.getByRole('contentinfo', { name: 'System health', exact: true })
  await expect(health.getByRole('button', { name: 'System health: Background work disabled', exact: true })).toBeVisible()
  await resize(1220)
  await capture('health-disabled')

  for (const [runtimeState, expected] of [['error', 'Needs attention'], ['not_reported', 'Unknown'], ['ready', 'Input needed']] as const) {
    displayedSnapshot = { ...snapshot, runtime: { ...snapshot.runtime, state: runtimeState, setup: { ...snapshot.runtime.setup, backgroundOptIn: true } } }
    await page.reload()
    await waitForAppReady(fixture)
    await expect(health.getByRole('button', { name: `System health: ${expected}`, exact: true })).toBeVisible()
    await capture(`health-runtime-${runtimeState}`)
  }

  displayedSnapshot = { ...displayedSnapshot, requests: [] }
  await page.reload()
  await waitForAppReady(fixture)
  await expect(health.getByRole('button', { name: 'System health: Ready', exact: true })).toBeVisible()
  await capture('health-ready')
  displayedSnapshot = { ...displayedSnapshot, requests: undefined }
  await page.reload()
  await waitForAppReady(fixture)
  // Missing requests violates the wire contract: keep the adapter fail-closed.
  const invalidSnapshot = 'This backend did not return a supported organization snapshot. Update the runtime and reconnect.'
  await expect(health.getByRole('button', { name: 'System health: Needs attention', exact: true })).toBeVisible()
  await expect(page.getByRole('alert')).toHaveText(invalidSnapshot)
  await expect(health).toContainText('Organization activity unavailable')
  await expect(health).not.toContainText('Organization: 0 running')
  await capture('health-missing-queue-rejected')
  displayedSnapshot = { ...displayedSnapshot, requests: [] }
  await page.getByRole('button', { name: 'Retry connection', exact: true }).click()
  await expect(health.getByRole('button', { name: 'System health: Ready', exact: true })).toBeVisible()
  await acknowledgeExpectedErrorBanner(page, invalidSnapshot)
  readinessUnknown = true
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect(health.getByRole('button', { name: 'System health: Last known · refresh pending', exact: true })).toBeVisible()
  await capture('health-stale')
  readinessUnknown = false
  displayedSnapshot = snapshot
  await page.reload()
  await waitForAppReady(fixture)
  await health.getByRole('button', { name: /^System health:/ }).click()
  const details = page.getByRole('dialog', { name: 'System health details', exact: true })
  await expect(details.getByRole('link', { name: 'Gateway settings', exact: true })).toHaveAttribute('href', /settings\?tab=gateway$/)
  await details.getByText('Advanced application controls', { exact: true }).click()
  await expect(details.getByRole('button', { name: 'Open settings', exact: true })).toBeVisible()
  await capture('health-advanced-controls')
  await resize(760)
  await capture('health-advanced-controls-narrow')
  await page.keyboard.press('Escape')
  await expect(details).toHaveCount(0)
  await expect(health.getByRole('button', { name: /^System health:/ })).toBeFocused()

  // Exercise every built-in Settings palette in both explicit appearances.
  // Each choice is reloaded before capture; no redesign-only theme state exists.
  await resize(1220)

  for (const theme of BUILTIN_THEME_LIST) {
    for (const mode of ['Dark', 'Light'] as const) {
      await page.getByRole('complementary', { name: 'Eidolon navigation' }).getByRole('link', { name: 'Settings', exact: true }).click()
      await page.getByRole('button', { name: 'Appearance', exact: true }).click()
      await page.getByRole('button').filter({ has: page.getByText(theme.label, { exact: true }) }).click()
      await page.getByRole('button', { name: mode, exact: true }).click()
      await expect(page.locator('html')).toHaveAttribute('data-hermes-theme', theme.name)
      await page.getByRole('button', { name: 'Close settings', exact: true }).click()
      await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())))
      const scheme = await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)
      await page.reload()
      await waitForAppReady(fixture)
      await expect(page.locator('html')).toHaveAttribute('data-hermes-theme', theme.name)
      expect(await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)).toBe(scheme)
      await capture(`theme-${theme.name}-${mode.toLowerCase()}-persisted`)
      await page.getByRole('complementary', { name: 'Eidolon navigation' }).getByRole('link', { name: 'Settings', exact: true }).click()
      await page.getByRole('button', { name: 'Appearance', exact: true }).click()
      await expect(page.getByRole('button', { name: mode, exact: true })).toHaveAttribute('aria-pressed', 'true')
      await page.getByRole('button', { name: 'Close settings', exact: true }).click()
    }
  }

  // System appearance uses the same Settings preference and responds to OS changes.
  await page.getByRole('complementary', { name: 'Eidolon navigation' }).getByRole('link', { name: 'Settings', exact: true }).click()
  await page.getByRole('button', { name: 'Appearance', exact: true }).click()
  await page.getByRole('button').filter({ has: page.getByText('AetherMesh', { exact: true }) }).click()
  await page.getByRole('button', { name: 'System', exact: true }).click()
  await page.getByRole('button', { name: 'Close settings', exact: true }).click()

  for (const colorScheme of ['dark', 'light'] as const) {
    await page.emulateMedia({ colorScheme })
    await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)).toBe(colorScheme)
    await page.reload()
    await waitForAppReady(fixture)
    await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)).toBe(colorScheme)
    await capture(`theme-nous-system-${colorScheme}-persisted`)
  }

  expect(contrastChecks.length).toBeGreaterThan(0)

  for (const sample of contrastChecks.filter(item => item.opacity === 1)) {
    expect(sample.contrastRatio, sample.text ?? 'control text').toBeGreaterThanOrEqual(4.5)
  }

  writeEvidence()
  expect(mock.receivedPrompts).toHaveLength(0)
  expect(organizationMethods.length).toBeGreaterThan(0)
  expect([...new Set(organizationMethods)].sort()).toEqual(['organization.outcomes', 'organization.snapshot'])
})
