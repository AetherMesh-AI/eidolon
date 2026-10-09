/** Paired native screenshots use the same synthetic records on both revisions.
 * The real Electron shell and temporary gateway boot normally; no work is run. */
import { createHash } from 'node:crypto'
import { writeFileSync } from 'node:fs'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

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
  objectives: [
    {
      id: 'release',
      title: 'Prepare the autumn release brief',
      description: 'Bring the launch narrative, verified changes, and review notes into one clear brief.',
      status: 'active',
      phase: 'Draft and review',
      milestone: 'Release context verified',
      progress: 60
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
      progress: 100
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
      status: 'working',
      dependsOn: []
    },
    {
      id: 'review',
      objectiveId: 'release',
      title: 'Review the acceptance evidence',
      ownerId: 'reviewer',
      managingAgentId: 'manager',
      status: 'review',
      dependsOn: ['draft']
    }
  ],
  requests: [],
  activity: [],
  knowledge: [],
  conversations: []
}

let fixture: MockBackendFixture | undefined

test.setTimeout(180_000)
test.afterEach(async () => {
  await fixture?.cleanup()
  fixture = undefined
})

test('keeps native organization navigation, roster and objective layouts usable at both window sizes', async () => {
  fixture = await setupMockBackend()
  const { app, page, mock } = fixture
  const organizationMethods: string[] = []
  await page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as { id?: number; method?: string }

      if (request.method?.startsWith('organization.')) {
        organizationMethods.push(request.method)
      }

      if (request.method === 'organization.snapshot') {
        socket.send(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: snapshot }))
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
    captures.push({
      name,
      colorScheme: await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme),
      theme: await page.locator('html').getAttribute('data-hermes-theme'),
      sha256: createHash('sha256').update(bytes).digest('hex'),
      statusColors: await workspace.locator('.eid-status').evaluateAll(elements =>
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
            color: getComputedStyle(element).color,
            backgrounds,
            opacity,
            backgroundRgba,
            foregroundRgba,
            contrastRatio: (high + 0.05) / (low + 0.05),
            bounds: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
          }
        })
      ),
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

  await resize(1220)
  await expect(page.locator('html')).toHaveClass(/\bdark\b/)
  await navigation.getByRole('link', { name: 'Command', exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Objective', exact: true })).toBeVisible()
  await capture('01-home-wide-dark')
  // The compact form's primary action must be usable without the first scroll.
  // The paired legacy build predates the runtime disclosure and remains evidence-only.
  if (await page.locator('.eid-runtime-disclosure').count()) {
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
  await navigation.getByRole('link', { name: 'Command', exact: true }).click()
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
  await resize(760)
  await capture('12-objectives-narrow-light')
  await resize(1220)
  await navigation.getByRole('link', { name: 'Organization', exact: true }).click()
  await expect(agentButton).toBeVisible()
  await resize(760)
  await capture('13-roster-narrow-light')
  await resize(1220)
  await navigation.getByRole('link', { name: 'Command', exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Objective', exact: true })).toBeVisible()
  await resize(760)
  await capture('14-home-narrow-light')

  writeEvidence()
  expect(mock.receivedPrompts).toHaveLength(0)
  expect(organizationMethods.length).toBeGreaterThan(0)
  expect(organizationMethods.filter(method => method !== 'organization.snapshot')).toEqual([])
})
