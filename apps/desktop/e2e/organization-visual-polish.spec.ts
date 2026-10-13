/** Paired native screenshots use the same synthetic records on both revisions.
 * The real Electron shell and temporary gateway boot normally; no work is run. */
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { writeFileSync } from 'node:fs'

import { BUILTIN_THEME_LIST } from '../src/themes/presets'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { verifyKeyboardFocus } from './focus-visibility'
import { verifyMemberSearchGeometry } from './organization-message-search'
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
  identityId: `fixture-${agent.id}`,
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
      phaseCode: 'execution_review',
      phase: 'Execution and review',
      milestone: 'Release context verified',
      progress: 60,
      priority: 'P3',
      priorityRevision: 0,
      dispatchControl: { paused: false, revision: 0, runningCount: 1 },
      planningMode: 'executive_packages',
      acceptance: { status: 'blocked', round: 1, maxReplans: 2, criteria: ['Brief reflects verified changes'], summary: 'Conflicting source dates require resolution.', deliverableId: null, criteriaResults: [{ criterion: 'Brief reflects verified changes', satisfied: false, reason: 'Two source dates remain unresolved.', evidenceIds: ['fixture-source-a', 'fixture-source-b'] }], conflicts: ['Published launch dates disagree.'] },
      workPackages: [
        { id: 'release-package', objectiveId: 'release', round: 1, managerId: 'manager', title: 'Release documentation', description: 'Draft the narrative from verified changes.', criterionIndexes: [0], projectIds: [], dependencyIds: [], maxTasks: 3, planRequestId: 'fixture-plan', status: 'working', taskIds: ['draft'] },
        { id: 'evidence-package', objectiveId: 'release', round: 1, managerId: 'manager', title: 'Acceptance evidence', description: 'Review supporting evidence against the brief.', criterionIndexes: [0], projectIds: [], dependencyIds: ['release-package'], maxTasks: 3, planRequestId: 'fixture-evidence-plan', status: 'working', taskIds: ['review'] },
        { id: 'sources-package', objectiveId: 'release', round: 1, managerId: 'manager', title: 'Source verification', description: 'Check source material and retained context.', criterionIndexes: [0], projectIds: [], dependencyIds: [], maxTasks: 3, planRequestId: 'fixture-sources-plan', status: 'completed', taskIds: ['sources'] }
      ]
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
      workPackageId: 'evidence-package',
      currentRound: true,
      status: 'review',
      dependsOn: ['draft']
    },
    { id: 'sources', objectiveId: 'release', title: 'Verify source material', ownerId: 'researcher', managingAgentId: 'manager', workPackageId: 'sources-package', currentRound: true, status: 'completed', dependsOn: [] }
  ],
  requests: [{ id: 'fixture-question', objectiveId: 'navigation', requesterId: 'manager', type: 'request.question', team: 'Product', priority: 2, status: 'pending_intervention', reason: 'Review the proposed owner journey before the next iteration.', attempts: 0, createdAt }],
  activity: ['executive', 'manager', 'writer', 'reviewer'].map((agentId, index) => ({ id: `fixture-event-${index}`, agentId, source: 'runtime', kind: 'system', text: ['Synthetic direction recorded for the release brief.', 'Synthetic manager assignment recorded.', 'Synthetic draft prepared for review.', 'Synthetic review evidence recorded.'][index], timestamp: createdAt })),
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
  // The paired Objective focus/disclosure coverage adds 56 captures to this aggregate scenario.
  // Keep individual assertion deadlines unchanged while retaining the full theme-persistence pass.
  test.setTimeout(480_000)
  fixture = await setupMockBackend()
  const { app, page, mock } = fixture
  // A newer main can arrive during this long-running source-checkout test.
  // Dismiss only the unrelated real updater toast through its public control;
  // error banners and all notification/updater behavior remain enabled.
  const updateToast = page.getByRole('status').filter({ has: page.getByText('Update ready', { exact: true }) })
  let dismissedUpdates = 0
  await page.addLocatorHandler(updateToast, async toast => {
    await toast.getByRole('button', { name: 'Dismiss notification', exact: true }).click()
    await expect(toast).toBeHidden()
    dismissedUpdates += 1
    await test.info().attach('dismissed-source-update-toast', {
      contentType: 'text/plain', body: 'Dismissed the real Update ready toast before continuing the organization visual scenario.'
    })
  })
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
  // Exercise the real renderer poller, IPC bridge, toast and dismissal in this
  // temporary app. Only the read-only update-check response is scripted; apply,
  // progress, error banners and dedicated updater scenarios are untouched.
  await app.evaluate(({ ipcMain }) => {
    ipcMain.removeHandler('hermes:updates:check')
    ipcMain.handle('hermes:updates:check', () => ({
      supported: true, branch: 'main', targetSha: 'f'.repeat(40), behind: 1,
      updateAvailable: true, fetchedAt: Date.now()
    }))
  })
  await page.evaluate(() => localStorage.removeItem('hermes:update-toast-snooze-until'))
  await page.clock.setFixedTime(new Date('2026-10-08T12:00:00Z'))
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' })
  await page.reload()
  await waitForAppReady(fixture)
  await expect.poll(async () => {
    await page.getByRole('complementary', { name: 'Eidolon navigation', exact: true }).getByRole('link', { name: 'Home', exact: true }).click()

    return dismissedUpdates
  }).toBeGreaterThan(0)
  await expect(updateToast).toBeHidden()

  const navigation = page
    .getByRole('complementary', { name: 'Eidolon navigation' })
    .getByRole('navigation', { name: 'Primary', exact: true })

  // The organization shell has no redundant mode strip or technical sidebar prose.
  await expect(page.getByRole('tab', { name: 'Eidolon', exact: true })).toBeHidden()
  await expect(page.getByRole('tab', { name: 'Profile chats', exact: true })).toBeHidden()
  const shellRail = page.getByRole('complementary', { name: 'Eidolon navigation', exact: true })
  await expect(shellRail.getByText('Tools and configuration', { exact: true })).toHaveCount(0)
  await expect(shellRail.locator('.eid-rail-footer')).toHaveCount(0)
  await expect(shellRail.locator('.eid-rail-history > summary')).toBeHidden()
  await expect(shellRail.getByRole('button', { name: 'More workspace options', exact: true })).toBeVisible()
  const moreOptions = shellRail.getByRole('button', { name: 'More workspace options', exact: true })
  await moreOptions.click()
  await shellRail.getByRole('button', { name: 'Profile chats', exact: true }).click()
  await expect(page.getByRole('tab', { name: 'Profile chats', exact: true })).toHaveAttribute('aria-selected', 'true')
  await page.getByRole('tab', { name: 'Eidolon', exact: true }).click()
  await expect(page.getByRole('tab', { name: 'Eidolon', exact: true })).toBeHidden()
  await expect(navigation.getByRole('link', { name: 'Home', exact: true })).toBeVisible()
  await moreOptions.focus()
  await page.keyboard.press('Escape')
  await expect(moreOptions).toBeFocused()
  await expect(shellRail.getByRole('button', { name: 'Profile chats', exact: true })).toBeHidden()


  const workspace = page.getByRole('main', { name: 'Organization workspace', exact: true })
  const captures: object[] = []
  const focusChecks: object[] = []
  const columnChecks: object[] = []
  const objectiveChecks: object[] = []
  const homeChecks: object[] = []
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
          sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
          sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
          scenario: 'synthetic-read-only-organization',
          runtime,
          providerCalls: mock.receivedPrompts.length,
          organizationMethods: [...new Set(organizationMethods)],
          captures,
          focusChecks,
          columnChecks,
          objectiveChecks,
          homeChecks
        },
        null,
        2
      )
    )
  }

  async function resize(width: number, height = 900) {
    await app.evaluate(({ BrowserWindow }, size) => {
      const window = BrowserWindow.getAllWindows()[0]
      window.unmaximize()
      window.setMinimumSize(600, 600)
      window.setContentSize(size.width, size.height)
      window.setPosition(0, 0)
      window.focus()
    }, { width, height })
    await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(width)
  }

  async function capture(name: string, target = workspace, nativeWindow = false, preserveScroll = false) {
    // Matched review images are neutral-focus; keyboard checks retain their own evidence.
    if (name.startsWith('reference-')) {
      await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) {document.activeElement.blur()} })
    }

    if (!preserveScroll) {
      await target.evaluate(element => { element.scrollTop = 0 })
    }

    await page.evaluate(async () => {
      await document.fonts.ready
      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
    })

    // Chromium's page screenshot clips Electron content at non-default zoom.
    // capturePage retains the full native content surface for reflow evidence.
    const bytes = nativeWindow
      ? Buffer.from(await app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')), 'base64')
      : await page.screenshot({
          path: test.info().outputPath(`${name}.png`),
          animations: 'disabled',
          caret: 'hide'
        })

    if (nativeWindow) {
      writeFileSync(test.info().outputPath(`${name}.png`), bytes)
    }

    await test.info().attach(name, { body: bytes, contentType: 'image/png' })

    const colors = await page.locator('.eidolon').locator('.eid-status, a, button:not(:disabled), input, textarea, select, .eid-owner-chat-bubble, .eid-home-panel h2, .eid-home-record h3, .eid-home-record p, .eid-home-record small, .eid-objective-overview h3, .eid-objective-overview p, .eid-objective-overview small, .eid-objective-overview strong, .eid-messages-layout h2, .eid-messages-layout h3, .eid-messages-layout p, .eid-messages-layout small, .eid-messages-layout li').evaluateAll(elements =>
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

    for (const row of await page.locator('.eid-progress-task').all()) {
      expect(await row.evaluate(element => element.scrollWidth - element.clientWidth), 'recorded task row fits its card').toBeLessThanOrEqual(1)

      for (const content of await row.locator(':scope > strong, :scope > small, :scope > .eid-status').all()) {
        expect(await content.evaluate(element => element.scrollWidth - element.clientWidth), 'task title, assignee and translated status remain readable').toBeLessThanOrEqual(1)
      }
    }

    contrastChecks.push(...colors)
    expect(await target.evaluate(element => {
      const style = getComputedStyle(element)

      return style.getPropertyValue('--aether-500').trim() === style.getPropertyValue('--ui-accent').trim()
    })).toBe(true)
    const barBounds = await page.getByRole('contentinfo', { name: 'System health', exact: true }).boundingBox()
    expect(barBounds).not.toBeNull()
    expect(barBounds!.y + barBounds!.height).toBeLessThanOrEqual(await page.evaluate(() => window.innerHeight) + 1)
    captures.push({
      name,
      route: page.url(),
      colorScheme: await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme),
      theme: await page.locator('html').getAttribute('data-hermes-theme'),
      sha256: createHash('sha256').update(bytes).digest('hex'),
      statusColors: colors,
      viewport: await page.evaluate(() => ({
        width: window.innerWidth,
        height: window.innerHeight,
        devicePixelRatio: window.devicePixelRatio
      })),
      workspace: await target.evaluate(element => ({
        width: element.clientWidth,
        scrollWidth: element.scrollWidth,
        height: element.clientHeight,
        scrollHeight: element.scrollHeight
      }))
    })
    writeEvidence()
  }

  async function inspectMessages(mode: string) {
    await resize(1440)
    await navigation.getByRole('link', { name: 'Messages', exact: true }).click()
    const search = page.getByRole('textbox', { name: 'Search members', exact: true })
    const members = page.getByRole('navigation', { name: 'Choose an agent', exact: true })
    await capture(`messages-directory-wide-${mode}`)
    await search.fill('Robin')
    await expect(members.getByRole('button')).toHaveCount(1)
    await search.focus()
    await page.keyboard.press('Tab')
    await expect(page.getByRole('button', { name: 'Clear search', exact: true })).toBeFocused()
    await page.keyboard.press('Tab')
    await expect(members.getByRole('button', { name: /Robin/ })).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(members.getByRole('button', { name: /Robin/ })).toHaveAttribute('aria-pressed', 'true')
    const context = page.getByRole('region', { name: 'Member context', exact: true })
    await expect(context.getByRole('link', { name: 'Prepare the autumn release brief', exact: true })).toBeVisible()
    await expect(context.getByRole('link', { name: 'Document provider boundaries', exact: true })).toHaveCount(0)
    await search.fill('no matching member')
    await expect(page.getByRole('region', { name: 'Member conversation', exact: true }).getByRole('heading', { name: 'Robin', exact: true })).toBeVisible()
    await search.fill('')
    await capture(`messages-selected-wide-${mode}`)

    for (const [width, height] of [[1536, 961], [1280, 720]]) {
      await resize(width, height)

      const columns = await page.locator('.eid-messages-layout > section').evaluateAll(elements => elements.map(element => {
        const rect = element.getBoundingClientRect()

        return { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
      }))

      const bounds = await workspace.boundingBox()
      expect(columns).toHaveLength(3)

      for (const column of columns) {
        expect(Math.abs(column.y - bounds!.y), 'columns start at the workspace top').toBeLessThanOrEqual(1)
        expect(Math.abs(column.height - bounds!.height), 'columns fill the workspace height').toBeLessThanOrEqual(1)
      }

      const total = columns.reduce((sum, column) => sum + column.width, 0)
      const targetWidths = width === 1536 ? [338, 677, 318] : [.254, .507, .239].map(ratio => ratio * total)
      columns.forEach((column, index) => expect(Math.abs(column.width - targetWidths[index]), 'column width within ten pixels of reference proportion').toBeLessThanOrEqual(10))
      const headerBounds = await page.getByRole('banner', { name: 'Workspace controls', exact: true }).boundingBox()
      expect(Math.abs(bounds!.y - 64), 'Messages content origin matches the reference header').toBeLessThanOrEqual(1)
      expect(Math.abs(headerBounds!.y + headerBounds!.height - bounds!.y)).toBeLessThanOrEqual(1)
      columnChecks.push({ mode, width, height, columns, workspace: bounds, header: headerBounds, targetWidths, memberSearch: await verifyMemberSearchGeometry(page) })
      await expect(page.locator('.eid-message-members').getByRole('heading', { name: 'Messages', exact: true })).toBeVisible()
      focusChecks.push(await verifyKeyboardFocus(page, search, `Messages search ${mode} ${width}x${height}`))
      await expect(page.getByRole('contentinfo', { name: 'System health', exact: true })).toBeInViewport({ ratio: 1 })
      await capture(width === 1536 ? `reference-messages-${mode}` : `messages-selected-short-${mode}`)
      await search.fill('A long member query that must stay within the existing member column')
      await verifyMemberSearchGeometry(page)
      await search.focus()
      await page.keyboard.press('Tab')
      await expect(page.getByRole('button', { name: 'Clear search', exact: true })).toBeFocused()
      await page.keyboard.press('Enter')
      await expect(search).toHaveValue('')
      await expect(page.getByRole('region', { name: 'Member conversation', exact: true }).getByRole('heading', { name: 'Robin', exact: true })).toBeVisible()
    }

    await resize(1220)
    await capture(`messages-selected-medium-${mode}`)
    await resize(760)
    await verifyMemberSearchGeometry(page)
    await capture(`messages-selected-narrow-${mode}`)
    // Traverse the context with Tab, then follow the recorded link using Enter.
    await search.fill('Robin')
    await search.focus()
    await page.keyboard.press('Tab')
    await expect(page.getByRole('button', { name: 'Clear search', exact: true })).toBeFocused()
    await page.keyboard.press('Tab')
    await expect(members.getByRole('button', { name: /Robin/ })).toBeFocused()
    await page.keyboard.press('Tab')
    const directoryDetails = page.locator('.eid-message-members .eid-message-directory-details')
    await expect(directoryDetails.locator('summary')).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(directoryDetails).toHaveAttribute('open', '')
    await expect(directoryDetails).toContainText('Messages do not grant new permissions or renew allowances.')
    await page.keyboard.press('Enter')
    await expect(directoryDetails).not.toHaveAttribute('open', '')
    await page.keyboard.press('Tab')
    await expect(page.locator('.eid-message-conversation .eid-owner-chat-actions span[tabindex="0"]')).toBeFocused()
    await page.keyboard.press('Tab')
    await expect(context.getByRole('link', { name: 'Prepare the autumn release brief', exact: true })).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('region', { name: 'Objective overview', exact: true })).toBeVisible()
    await resize(1220)
    await navigation.getByRole('link', { name: 'Objectives', exact: true }).click()
  }

  async function inspectObjectiveOverview(mode: string) {
    await workspace.getByRole('link').filter({ has: page.getByText('Prepare the autumn release brief', { exact: true }) }).click()
    const overview = page.getByRole('region', { name: 'Objective overview', exact: true })
    await expect(overview).toBeVisible()
    await expect(overview.getByRole('heading', { name: 'Release documentation', exact: true })).toBeVisible()
    await expect(overview.getByRole('region', { name: 'Delivery', exact: true })).toContainText('has not been recorded')
    await capture(`objective-detail-wide-${mode}`)
    await resize(1536, 961)
    await workspace.evaluate(element => { element.scrollTop = 0 })
    const summary = overview.getByRole('region', { name: 'Progress overview', exact: true })
    const phase = overview.getByRole('region', { name: 'Current phase', exact: true })
    const lifecycle = phase.getByRole('list', { name: 'Current phase', exact: true })
    await expect(lifecycle.locator('[aria-current=step]')).toHaveCount(1)
    await expect(lifecycle.locator('[aria-current=step]')).toHaveText('Execution and review')
    await expect(phase.getByRole('button', { name: 'Pause new dispatch', exact: true })).toBeEnabled()
    await expect(phase.getByRole('combobox', { name: 'New priority', exact: true })).toHaveValue('P3')

    const markers = await lifecycle.locator('li').evaluateAll(items => items.map(item => {
      const marker = item.querySelector('.eid-lifecycle-marker')!.getBoundingClientRect()
      const label = item.querySelector('span:last-child')!.getBoundingClientRect()

      return { markerBottom: marker.bottom, labelTop: label.top, markerCenter: marker.x + marker.width / 2, labelCenter: label.x + label.width / 2 }
    }))

    expect(markers.every(item => item.markerBottom <= item.labelTop && Math.abs(item.markerCenter - item.labelCenter) <= 1)).toBe(true)
    const assignmentCards = overview.locator('.eid-team-work-grid > article')
    await expect(assignmentCards).toHaveCount(3)

    const geometry = await overview.evaluate(element => {
      const bounds = (node: Element) => {
        const { x, y, width, height } = node.getBoundingClientRect()

        return { x, y, width, height }
      }

      return { main: bounds(element.querySelector('.eid-objective-progress-main')!), aside: bounds(element.querySelector('.eid-objective-progress-aside')!), phase: bounds(element.querySelector('.eid-objective-phase')!), actions: bounds(element.querySelector('.eid-objective-actions')!), summary: bounds(element.querySelector('.eid-progress-summary')!), cards: [...element.querySelectorAll('.eid-team-work-grid > article')].map(bounds) }
    })

    expect(Math.abs(geometry.main.x - 222), 'Objective reference left gutter').toBeLessThanOrEqual(10)
    expect(Math.abs(geometry.main.width - 898), 'Objective reference main column').toBeLessThanOrEqual(10)
    expect(geometry.summary.height, 'compact progress retains completion and acceptance context').toBeLessThanOrEqual(190)
    expect(geometry.actions.y + geometry.actions.height, 'real controls precede the progress summary').toBeLessThan(geometry.summary.y)
    expect(geometry.summary.y + geometry.summary.height, 'progress precedes delegated work').toBeLessThan(geometry.cards[0].y)

    for (const card of geometry.cards) {
      expect(Math.abs(card.y - geometry.cards[0].y), 'three recorded assignments share a row').toBeLessThanOrEqual(1)
    }

    for (const card of await assignmentCards.all()) {
      await expect(card).toBeInViewport({ ratio: 1 })
    }

    const recordDetails = overview.getByRole('region', { name: 'Objective details', exact: true })
    await expect(recordDetails).toContainText('Avery')
    await expect(recordDetails).toContainText('Execution and review')
    await expect(recordDetails.locator('time')).toHaveAttribute('datetime', createdAt)
    await expect(recordDetails).toBeInViewport({ ratio: 1 })
    await expect(page.locator('.eid-objective-hero .eid-objective-scenery')).toHaveAttribute('aria-hidden', 'true')
    await capture(`reference-objective-${mode}`)
    const findings = overview.getByRole('region', { name: 'Recorded review findings', exact: true })
    await expect(findings.getByRole('region', { name: 'Unresolved conflicts', exact: true })).toContainText('Published launch dates disagree.')
    await expect(findings.getByText('Unmet criteria · 1', { exact: true })).toBeVisible()
    await expect(findings.locator('summary')).toBeInViewport({ ratio: 1 })
    await expect(findings).toBeInViewport({ ratio: 1 })
    const findingsBounds = await findings.boundingBox()
    const healthBounds = await page.getByRole('contentinfo', { name: 'System health', exact: true }).boundingBox()
    expect(findingsBounds!.y + findingsBounds!.height, 'complete review border clears the footer').toBeLessThanOrEqual(healthBounds!.y - 4)
    objectiveChecks.push({ mode, viewport: '1536x961', ...geometry, reviewFindings: await findings.boundingBox(), reviewDisclosure: await findings.locator('summary').boundingBox() })
    await findings.locator('summary').click()
    await expect(findings.getByText('Not met', { exact: true })).toBeVisible()
    await expect(findings.getByText('fixture-source-a, fixture-source-b', { exact: true })).toBeVisible()
    await findings.getByText('Two source dates remain unresolved.', { exact: true }).scrollIntoViewIfNeeded()
    await capture(`objective-review-findings-${mode}`, workspace, false, true)
    await findings.locator('summary').click()

    displayedSnapshot = {
      ...snapshot,
      objectives: snapshot.objectives.map(objective => objective.id !== 'release' || !objective.workPackages ? objective : {
        ...objective,
        workPackages: objective.workPackages.map(item => ({ ...item, taskIds: item.taskIds.flatMap(id => [id, `${id}-followup-1`, `${id}-followup-2`]) }))
      }),
      tasks: snapshot.tasks.flatMap(task => [task, ...[1, 2].map(index => ({ ...task, id: `${task.id}-followup-${index}`, title: `${task.title} · follow-up ${index}` }))])
    }
    await page.reload()
    await expect(assignmentCards).toHaveCount(3)

    for (const card of await assignmentCards.all()) {
      await expect(card.locator('.eid-progress-tasks > li')).toHaveCount(3)
    }

    await workspace.evaluate(element => { element.scrollTop = 0 })
    await expect(phase.getByRole('button', { name: 'Pause new dispatch', exact: true })).toBeInViewport({ ratio: 1 })
    await capture(`objective-multiple-tasks-${mode}`)
    displayedSnapshot = snapshot
    await page.reload()
    await expect(assignmentCards.first().locator('.eid-progress-tasks > li')).toHaveCount(1)
    await resize(1280, 720)
    await workspace.evaluate(element => { element.scrollTop = 0 })
    await expect(assignmentCards.first().getByText('Draft the release brief', { exact: true })).toBeInViewport({ ratio: 1 })
    await expect(assignmentCards.first().getByText('Robin', { exact: true })).toBeInViewport({ ratio: 1 })
    await expect(phase.getByRole('button', { name: 'Pause new dispatch', exact: true })).toBeInViewport({ ratio: 1 })
    await expect(phase.getByRole('combobox', { name: 'New priority', exact: true })).toBeInViewport({ ratio: 1 })
    await capture(`objective-short-${mode}`)
    await phase.getByRole('combobox', { name: 'New priority', exact: true }).selectOption('P2')

    for (const zoom of [1, 1.25, 2]) {
      await page.evaluate(value => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(value * 100), zoom)
      await expect.poll(() => page.evaluate(() => innerWidth)).toBe(Math.round(1280 / zoom))
      await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(zoom)

      for (const name of ['Inspect work and evidence', 'Review owner requests', 'Inspect acceptance and delivery']) {
        const control = overview.getByRole('button', { name, exact: true })
        await control.scrollIntoViewIfNeeded()
        focusChecks.push(await verifyKeyboardFocus(page, control, `Objective ${mode} ${zoom} ${name}`))
        await expect(control).toBeInViewport({ ratio: 1 })
        await capture(`objective-control-${mode}-${zoom}-${name.split(' ')[0]}-${name.split(' ')[1]}`, workspace, zoom !== 1, true)
      }

      for (const [name, control] of [
        ['pause', phase.getByRole('button', { name: 'Pause new dispatch', exact: true })],
        ['dispatch-review', phase.getByRole('button', { name: 'Review current dispatch', exact: true })],
        ['priority-select', phase.getByRole('combobox', { name: 'New priority', exact: true })],
        ['priority-save', phase.getByRole('button', { name: 'Save priority', exact: true })],
        ['priority-review', phase.getByRole('button', { name: 'Review current priority', exact: true })],
        ['dispatch-details', phase.locator('summary').filter({ hasText: 'Pause and resume details' })],
        ['priority-details', phase.locator('summary').filter({ hasText: 'Priority details' })]
      ] as const) {
        await control.scrollIntoViewIfNeeded()
        focusChecks.push(await verifyKeyboardFocus(page, control, `Objective action ${mode} ${zoom} ${name}`))
        await expect(control).toBeInViewport({ ratio: 1 })
        await capture(`objective-action-${mode}-${zoom}-${name}`, workspace, zoom !== 1, true)
      }

      for (const details of await phase.locator('details').all()) {
        const toggle = details.locator('summary')
        await toggle.click()
        await expect(details).toHaveAttribute('open', '')
      }

      await phase.locator('summary').first().scrollIntoViewIfNeeded()
      await capture(`objective-expanded-${mode}-${zoom}-start`, workspace, zoom !== 1, true)
      await phase.locator('details p').last().scrollIntoViewIfNeeded()
      await expect(phase.locator('details p').last()).toBeInViewport({ ratio: 1 })
      await capture(`objective-expanded-${mode}-${zoom}-end`, workspace, zoom !== 1, true)

      for (const details of await phase.locator('details').all()) {
        await details.locator('summary').click()
        await expect(details).not.toHaveAttribute('open', '')
      }

      await expect(phase).toContainText('Execution and review')
      await expect(phase.getByRole('combobox', { name: 'New priority', exact: true })).toHaveValue('P2')
      await expect(overview.getByRole('region', { name: 'Delivery', exact: true })).toContainText('has not been recorded')
    }

    await page.evaluate(() => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(100))
    await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(1)
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
    await expect(card).toContainText('3 current-round manager assignments')
    await expect(card.getByRole('list', { name: 'Recorded task states', exact: true })).toContainText('Working')
    await capture(`work-overview-wide-${mode}`)
    await resize(760)
    await capture(`work-overview-narrow-${mode}`)
    await resize(1280, 720)
    await page.evaluate(() => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(200))
    await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(2)
    const workLink = card.getByRole('link', { name: 'View objective →', exact: true })
    await workLink.scrollIntoViewIfNeeded()
    focusChecks.push(await verifyKeyboardFocus(page, workLink, `Work overview objective link ${mode} 200 percent`))
    await expect(workLink).toBeInViewport({ ratio: 1 })
    expect(await page.locator('.eid-work-overview').evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
    await capture(`work-overview-200-${mode}`, workspace, true, true)
    await page.evaluate(() => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(100))
    await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(1)
    await resize(1220)
    await navigation.getByRole('link', { name: 'Objectives', exact: true }).click()
    await workspace.getByRole('link').filter({ has: page.getByText('Review the workspace navigation', { exact: true }) }).click()
    await expect(page.getByRole('region', { name: 'Needs your input', exact: true })).toContainText('Review the proposed owner journey')
    await capture(`objective-owner-input-${mode}`)
    await resize(760)
    await expect(page.getByRole('heading', { name: 'Needs your input', exact: true })).toBeInViewport({ ratio: 1 })
    await capture(`objective-owner-input-narrow-${mode}`)
    await resize(1220)
    await navigation.getByRole('link', { name: 'Objectives', exact: true }).click()
    await workspace.getByRole('region', { name: 'Outcomes inbox', exact: true }).getByRole('link').filter({ has: page.getByText('Document provider boundaries', { exact: true }) }).click()
    await expect(page.getByRole('region', { name: 'Delivery', exact: true })).toContainText('Accepted deliverable')
    await capture(`objective-accepted-delivery-${mode}`)
    await navigation.getByRole('link', { name: 'Objectives', exact: true }).click()
  }

  await resize(1220)
  await expect(page.locator('html')).toHaveClass(/\bdark\b/)
  await expect(page.locator('html')).toHaveAttribute('data-hermes-theme', 'eidolon')
  await navigation.getByRole('link', { name: 'Home', exact: true }).click()
  await expect(page.locator('#eid-objective')).toBeAttached()
  await expect(page.getByRole('region', { name: 'What’s happening?', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'What needs me?', exact: true })).toContainText('Review the proposed owner journey')
  await expect(page.getByRole('region', { name: 'What needs me?', exact: true }).getByRole('heading', { name: 'Review the proposed owner journey before the next iteration.', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'What needs me?', exact: true })).toContainText('Requested by: Morgan')
  await expect(page.getByRole('region', { name: 'What’s ready?', exact: true })).toContainText('Synthetic reviewed scope')
  await expect(workspace.locator('.eid-runtime-status')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Open settings', exact: true })).toHaveCount(0)
  await expect(page.locator('#eid-objective')).toBeHidden()
  // Follow the owner's real accepted-results action through the registered
  // router. Do not navigate directly to the destination under test.
  await page.getByRole('region', { name: 'What’s ready?', exact: true }).getByRole('link', { name: 'View deliverables →', exact: true }).click()
  await expect.poll(() => page.evaluate(() => location.hash)).toBe('#/artifacts?source=organization')
  const deliverables = page.locator('.eid-deliverables')
  await expect(deliverables.getByRole('link', { name: 'Organization evidence', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(deliverables.getByRole('link', { name: 'Session files', exact: true })).not.toHaveAttribute('aria-current', 'page')
  await expect(deliverables.locator('.eid-delivery-outcomes').getByRole('heading', { name: 'Document provider boundaries', exact: true })).toBeVisible()
  await expect(deliverables.locator('.eid-delivery-outcomes').getByRole('link', { name: 'View objective →', exact: true })).toHaveAttribute('href', '#/objectives/research')
  const evidenceBytes = Buffer.from(await app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')), 'base64')
  writeFileSync(test.info().outputPath('home-accepted-evidence.png'), evidenceBytes)
  await test.info().attach('home-accepted-evidence', { body: evidenceBytes, contentType: 'image/png' })
  writeFileSync(test.info().outputPath('home-accepted-evidence.json'), JSON.stringify({
    sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
    sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
    route: await page.evaluate(() => location.hash),
    source: 'organization', acceptedObjectiveId: 'research', deliverableId: 'fixture-deliverable',
    providerCalls: mock.receivedPrompts.length, methods: [...new Set(organizationMethods)],
    sha256: createHash('sha256').update(evidenceBytes).digest('hex')
  }, null, 2))
  await navigation.getByRole('link', { name: 'Home', exact: true }).click()
  await capture('01-home-wide-dark')

  for (const [width, height] of [[1536, 961], [1440, 900], [1280, 720]]) {
    await resize(width, height)
    const railWidth = await page.getByRole('complementary', { name: 'Eidolon navigation', exact: true }).evaluate(element => element.getBoundingClientRect().width)
    expect(railWidth, 'primary rail matches reference proportion').toBeLessThanOrEqual(205)
    expect(railWidth, 'primary rail remains readable').toBeGreaterThanOrEqual(200)
    const fit = await workspace.evaluate(element => ({ height: element.clientHeight, content: element.scrollHeight }))
    expect(fit.content, `Home overview fits ${width} × ${height}`).toBeLessThanOrEqual(fit.height + 1)
    await expect(page.getByRole('button', { name: 'Message organization', exact: true })).toBeInViewport({ ratio: 1 })
    await expect(page.getByRole('contentinfo', { name: 'System health', exact: true })).toBeInViewport({ ratio: 1 })
    await expect(page.getByRole('navigation', { name: 'Primary', exact: true }).getByRole('link', { name: 'Settings', exact: true })).toBeInViewport({ ratio: 1 })
    const snapshotNote = workspace.locator('.eid-home-snapshot-note')
    await expect(snapshotNote).toHaveText('This overview shows retained records in the current snapshot. Open the full views for complete history.')
    await expect(snapshotNote).toBeInViewport({ ratio: 1 })
    const events = workspace.locator('.eid-home-activity ol > li')
    await expect(events).toHaveCount(4)
    await expect(workspace.locator('.eid-home-activity').getByRole('link', { name: 'View activity', exact: false })).toBeInViewport({ ratio: 1 })

    if (height >= 900) {
      for (const event of await events.all()) {
        await expect(event).toBeInViewport({ ratio: 1 })
        await expect(event.locator('time')).toBeInViewport({ ratio: 1 })
      }
    }

    const geometry = await workspace.evaluate(element => {
      const rect = (selector: string) => element.querySelector(selector)!.getBoundingClientRect().toJSON()

      return {
        hero: rect('.eid-home-hero'), cards: rect('.eid-home-grid'), activity: rect('.eid-home-activity'), note: rect('.eid-home-snapshot-note'),
        activityRows: [...element.querySelectorAll('.eid-home-activity ol > li')].map(row => ({
          bounds: row.getBoundingClientRect().toJSON(), timestamp: row.querySelector('time')!.getBoundingClientRect().toJSON()
        }))
      }
    })

    if (width === 1536) {
      expect(Math.abs(geometry.cards.bottom - 695), 'Home card bottom approaches the matched reference').toBeLessThanOrEqual(5)

      for (const event of geometry.activityRows) {
        expect(event.bounds.width, 'activity card keeps the reference width').toBeGreaterThanOrEqual(195)
        expect(event.bounds.width, 'activity card keeps the reference width').toBeLessThanOrEqual(225)
      }
    }

    const health = await page.getByRole('contentinfo', { name: 'System health', exact: true }).boundingBox()
    expect(geometry.activity.bottom).toBeLessThanOrEqual(health!.y)
    expect(geometry.cards.bottom).toBeLessThan(geometry.activity.top)
    homeChecks.push({ viewport: `${width}x${height}`, ...geometry, health })
    await capture(`home-fit-${width}-${height}`)

    if (width === 1280) {
      const body = workspace.locator('.eid-home-panel-body').first()
      const overflow = await body.evaluate(element => element.scrollHeight - element.clientHeight)
      expect(overflow).toBeGreaterThan(0)
      focusChecks.push(await verifyKeyboardFocus(page, body, 'Home dark keyboard scroll group'))
      const pageScroll = await workspace.evaluate(element => element.scrollTop)
      await page.keyboard.press('PageDown')
      await expect.poll(() => body.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
      expect(await workspace.evaluate(element => element.scrollTop)).toBe(pageScroll)
      await capture('home-keyboard-overflow-dark')
      await page.keyboard.press('Home')
      await expect.poll(() => body.evaluate(element => element.scrollTop)).toBe(0)
    }
  }

  await resize(1220)

  await resize(1280, 720)

  for (const zoom of [1.25, 2]) {
    // Use the product's persisted zoom owner; direct webContents zoom is
    // intentionally reverted by its resize/focus lifecycle reassertion.
    await page.evaluate(value => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(value * 100), zoom)
    await expect.poll(() => page.evaluate(() => innerWidth), { message: 'native zoom changes the CSS viewport before reflow verification' }).toBe(Math.round(1280 / zoom))
    await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(zoom)
    await expect(page.getByRole('button', { name: 'Message organization', exact: true })).toBeVisible()
    await expect(page.getByRole('contentinfo', { name: 'System health', exact: true })).toBeInViewport({ ratio: 1 })
    await capture(`home-reflow-${zoom}`, workspace, true)
  }

  await page.evaluate(() => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(100))
  await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(1)
  await resize(1220)


  // Browsing the explicit Home entry must not open chats or renew allowances.
  await page.getByRole('button', { name: 'Message organization', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Message organization', exact: true })).toBeVisible()
  const executiveChoices = page.getByRole('navigation', { name: 'Choose an agent', exact: true })
  await expect(executiveChoices.getByRole('button')).toHaveCount(1)
  await executiveChoices.getByRole('button', { name: /Avery/ }).click()
  await expect(page.getByRole('region', { name: 'Member conversation', exact: true }).getByRole('heading', { name: 'Avery', exact: true })).toBeVisible()
  await capture('home-message-explicit-recipient')
  expect(organizationMethods.length).toBeGreaterThan(0)
  expect(organizationMethods.every(method => ['organization.descriptor', 'organization.outcomes', 'organization.snapshot'].includes(method))).toBe(true)
  expect(mock.receivedPrompts).toHaveLength(0)
  await navigation.getByRole('link', { name: 'Home', exact: true }).click()

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
  await inspectMessages('dark')
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
  await expect(page.locator('#eid-objective')).toBeAttached()
  await resize(760)
  await capture('07-home-narrow-dark')

  // Navigation history returns to the dismissed roster, without reopening an inspector.
  await page.goBack()
  await expect(page.getByRole('heading', { name: 'Organization', exact: true })).toBeVisible()
  await expect(inspector).toHaveCount(0)
  await page.goForward()
  await expect(page.locator('#eid-objective')).toBeAttached()

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
  await expect(page.locator('#eid-objective')).toBeAttached()
  focusChecks.push(await verifyKeyboardFocus(page, workspace.locator('.eid-home-panel-body').first(), 'Home light scroll group'))
  await capture('08-home-wide-light')
  await resize(1536, 961)
  const primaryObjective = workspace.locator('.eid-home-objective-primary').first()
  await expect(primaryObjective).toContainText('Execution and review')
  await expect(primaryObjective.getByRole('list', { name: 'Recorded task states', exact: true })).toBeVisible()
  await expect(primaryObjective.getByRole('link', { name: 'View objective →', exact: true })).toBeInViewport({ ratio: 1 })
  await expect(page.getByRole('contentinfo', { name: 'System health', exact: true })).toBeInViewport({ ratio: 1 })

  const chrome = await page.evaluate(() => {
    const bounds = (selector: string) => document.querySelector(selector)!.getBoundingClientRect().toJSON()

    return { header: bounds('.eid-workspace-header'), search: bounds('.eid-workspace-search-trigger'),
      firstNavigation: bounds('.eid-rail nav a'), brand: bounds('.eid-brand'),
      headerDrag: getComputedStyle(document.querySelector('.eid-workspace-header')!).getPropertyValue('-webkit-app-region'),
      searchDrag: getComputedStyle(document.querySelector('.eid-workspace-search-trigger')!).getPropertyValue('-webkit-app-region') }
  })

  expect(chrome.header.y).toBe(0)
  expect(chrome.header.height).toBe(65)
  expect(chrome.search.width).toBe(380)
  expect(Math.abs(chrome.search.x - 887), 'wide search aligns with the reference while clearing native controls').toBeLessThanOrEqual(10)
  expect(chrome.firstNavigation.y).toBeGreaterThanOrEqual(110)
  expect(chrome.firstNavigation.y).toBeLessThanOrEqual(118)
  expect(chrome.headerDrag).toBe('drag')
  expect(chrome.searchDrag).toBe('no-drag')
  homeChecks.push({ name: 'reference-shared-chrome', ...chrome })
  focusChecks.push(await verifyKeyboardFocus(page, page.getByRole('button', { name: 'Search loaded records', exact: true }), 'Shared header search'))
  await capture('reference-home-light')
  await resize(1220)

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
  await inspectMessages('light')
  await resize(760)
  await capture('12-objectives-narrow-light')
  await resize(1220)
  await navigation.getByRole('link', { name: 'Organization', exact: true }).click()
  await expect(agentButton).toBeVisible()
  await resize(760)
  await capture('13-roster-narrow-light')
  await resize(1220)
  await navigation.getByRole('link', { name: 'Home', exact: true }).click()
  await expect(page.locator('#eid-objective')).toBeAttached()
  await resize(760)
  await capture('14-home-narrow-light')
  await page.reload()
  await waitForAppReady(fixture)
  await expect(page.locator('html')).toHaveAttribute('data-hermes-theme', 'nous')
  await expect(page.locator('html')).not.toHaveClass(/\bdark\b/)
  await capture('15-theme-choice-survives-reload')
  const goal = page.getByRole('textbox', { name: 'Objective', exact: true })
  await page.getByRole('button', { name: 'New objective', exact: true }).click()
  await goal.fill('Retain this unsubmitted objective across navigation')

  for (const destination of ['Messages', 'Work Overview', 'Home', 'Messages', 'Home']) {
    const link = navigation.getByRole('link', { name: destination, exact: true })
    await link.focus()
    await page.keyboard.press('Enter')
    await expect(link).toHaveAttribute('aria-current', 'page')
  }

  await expect(page.locator('#eid-objective')).toBeHidden()
  await page.getByRole('button', { name: 'New objective', exact: true }).click()
  await expect(goal).toHaveValue('Retain this unsubmitted objective across navigation')
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

  for (const [label, mode, key] of [['Eidolon', 'Dark', 'eidolon-dark'], ['AetherMesh', 'Light', 'nous-light']]) {
    await resize(1220)
    await page.getByRole('complementary', { name: 'Eidolon navigation' }).getByRole('link', { name: 'Settings', exact: true }).click()
    await page.getByRole('button', { name: 'Appearance', exact: true }).click()
    await page.getByRole('button').filter({ has: page.getByText(label, { exact: true }) }).click()
    await page.getByRole('button', { name: mode, exact: true }).click()
    await page.getByRole('button', { name: 'Close settings', exact: true }).click()
    await navigation.getByRole('link', { name: 'Deliverables', exact: true }).click()
    const deliverables = page.locator('.eid-deliverables')
    await expect(deliverables.getByRole('heading', { name: 'Deliverables', exact: true })).toBeVisible()
    await capture(`deliverables-session-files-${key}`, deliverables)
    await deliverables.getByRole('link', { name: 'Organization evidence', exact: true }).click()
    await expect(deliverables.getByRole('region', { name: 'Accepted outcomes', exact: true })).toBeVisible()
    await expect(deliverables.getByRole('heading', { name: 'Document provider boundaries', exact: true })).toBeVisible()
    await capture(`deliverables-accepted-evidence-${key}`, deliverables)
    await resize(760)
    await capture(`deliverables-accepted-evidence-narrow-${key}`, deliverables)
    await navigation.getByRole('link', { name: 'Home', exact: true }).click()
  }

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
  expect([...new Set(organizationMethods)].sort()).toEqual(['organization.descriptor', 'organization.outcomes', 'organization.snapshot'])
})

// A real configured locale exercises translated controls, not substituted DOM text.
test('retains translated Objective labels and opened explanations with contained scrolling', async () => {
  fixture = await setupMockBackend({ extraDisplayConfig: '  language: ja' })
  const { app, page, mock } = fixture
  const methods: string[] = []
  await page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as { id?: number; method?: string }

      if (request.method?.startsWith('organization.')) { methods.push(request.method) }

      if (request.method === 'organization.snapshot' || request.method === 'organization.outcomes') {
        socket.send(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: request.method === 'organization.snapshot' ? snapshot : snapshot.outcomes }))
      } else { server.send(message) }
    })
  })
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('lang', 'ja')
  await page.evaluate(() => { location.hash = '/objectives/release' })
  const phase = page.locator('.eid-objective-phase')
  await expect(phase.getByRole('button', { name: '新規実行を一時停止', exact: true })).toBeEnabled()
  const captures: object[] = []
  const focusChecks: object[] = []

  for (const [width, height, zoom] of [[1536, 961, 1], [1280, 720, 1], [1280, 720, 2], [760, 900, 1]]) {
    await page.evaluate(value => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(value * 100), 1)
    await app.evaluate(({ BrowserWindow }, size) => {
      const window = BrowserWindow.getAllWindows()[0]
      window.unmaximize()
      window.setMinimumSize(600, 600)
      window.setContentSize(size.width, size.height)
    }, { width, height })
    await expect.poll(() => page.evaluate(() => innerWidth)).toBe(width)
    await page.evaluate(value => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(value * 100), zoom)
    await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(zoom)

    for (const details of await phase.locator('details').all()) {
      if (await details.getAttribute('open') === null) { await details.locator('summary').click() }
      await expect(details).toHaveAttribute('open', '')
    }

    const controls = phase.locator('button:not(:disabled), select, summary')

    for (const control of await controls.all()) {
      const label = (await control.textContent())?.trim() ?? ''
      await control.scrollIntoViewIfNeeded()
      focusChecks.push(await verifyKeyboardFocus(page, control, `Japanese ${width}x${height} ${zoom} ${label}`))
      await expect(control).toBeInViewport({ ratio: 1 })
      expect(await control.evaluate(element => element.scrollWidth - element.clientWidth), label).toBeLessThanOrEqual(1)
    }

    for (const row of await page.locator('.eid-progress-task').all()) {
      expect(await row.evaluate(element => element.scrollWidth - element.clientWidth), 'recorded task row fits its card').toBeLessThanOrEqual(1)

      for (const content of await row.locator(':scope > strong, :scope > small, :scope > .eid-status').all()) {
        expect(await content.evaluate(element => element.scrollWidth - element.clientWidth), 'task title, assignee and translated status remain readable').toBeLessThanOrEqual(1)
      }
    }

    expect(await phase.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)

    for (const [position, target] of [['start', phase.locator('summary').first()], ['end', phase.locator('details p').last()]] as const) {
      await target.scrollIntoViewIfNeeded()
      await expect(target).toBeInViewport({ ratio: 1 })
      await expect(page.getByRole('banner', { name: 'ワークスペースの操作', exact: true })).toBeInViewport({ ratio: 1 })
      const name = `objective-japanese-${width}-${height}-${zoom}-${position}.png`
      await page.evaluate(async () => {
        await document.fonts.ready
        await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
      })

      const bytes = zoom !== 1
        ? Buffer.from(await app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')), 'base64')
        : await page.screenshot({ path: test.info().outputPath(name), animations: 'disabled', caret: 'hide' })

      if (zoom !== 1) { writeFileSync(test.info().outputPath(name), bytes) }
      await test.info().attach(name, { body: bytes, contentType: 'image/png' })
      captures.push({ name, width, height, zoom, sha256: createHash('sha256').update(bytes).digest('hex') })
    }
  }

  expect(mock.receivedPrompts).toHaveLength(0)
  expect([...new Set(methods)].every(method => ['organization.descriptor', 'organization.snapshot', 'organization.outcomes'].includes(method))).toBe(true)
  await test.info().attach('objective-translated-evidence', { contentType: 'application/json', body: JSON.stringify({
    sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
    sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
    locale: 'ja', providerCalls: mock.receivedPrompts.length, captures, focusChecks
  }) })
})


// Read-only header search is exercised in native Electron, with the same record scope.
test('keeps workspace controls and loaded-record inspection usable without executing work', async () => {
  fixture = await setupMockBackend()
  const { app, page, mock } = fixture
  const methods: string[] = []

  const searchSnapshot = { ...snapshot, conversations: [{
    id: 'header-conversation', subject: 'Release evidence discussion', objectiveId: 'release', taskId: null, projectId: null,
    participants: [{ id: 'writer', name: 'Robin', team: 'Product' }, { id: 'manager', name: 'Morgan', team: 'Product' }], status: 'answered', waitingAgentId: null,
    messages: [{ id: 'header-message', senderId: 'writer', recipientId: 'manager', body: 'Retained internal message; inspection leaves it unread.', createdAt, readAt: null, replyToId: null }]
  }] }

  await page.routeWebSocket(/.*/, socket => {
    const server = socket.connectToServer()
    socket.onMessage(message => {
      const request = JSON.parse(message.toString()) as { id?: number; method?: string }

      if (request.method?.startsWith('organization.')) {methods.push(request.method)}

      if (request.method === 'organization.snapshot' || request.method === 'organization.outcomes') {
        socket.send(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: request.method === 'organization.snapshot' ? searchSnapshot : snapshot.outcomes }))
      } else {server.send(message)}
    })
  })
  await page.reload()
  await waitForAppReady(fixture)
  const captures: object[] = []
  const focusChecks: object[] = []
  const header = page.getByRole('banner', { name: 'Workspace controls', exact: true })
  const search = page.getByRole('dialog', { name: 'Search loaded records', exact: true })

  const resize = async (width: number, height: number, zoom: number) => {
    await page.evaluate(() => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(100))
    await app.evaluate(({ BrowserWindow }, size) => {
      const window = BrowserWindow.getAllWindows()[0]
      window.unmaximize(); window.setMinimumSize(600, 600); window.setContentSize(size.width, size.height)
    }, { width, height })
    await expect.poll(() => page.evaluate(() => innerWidth)).toBe(width)
    await page.evaluate(value => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(value * 100), zoom)
    await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(zoom)
  }

  for (const mode of ['Dark', 'Light'] as const) {
    await resize(1536, 961, 1)
    await page.evaluate(() => { location.hash = '/settings?tab=config:appearance' })
    await page.getByRole('button').filter({ has: page.getByText('AetherMesh', { exact: true }) }).click()
    await page.getByRole('button', { name: mode, exact: true }).click()
    await expect(page.locator('html')).toHaveAttribute('data-hermes-theme', 'nous')
    await expect.poll(() => page.locator('html').evaluate(element => element.classList.contains('dark'))).toBe(mode === 'Dark')
    await expect(header.getByRole('button', { name: 'Search', exact: true })).toBeVisible()
    await header.getByRole('button', { name: 'Search', exact: true }).click()
    await expect(page.getByRole('dialog').last()).toBeVisible()
    await page.keyboard.press('Escape')
    await page.evaluate(() => { location.hash = '/home' })

    for (const [width, height, zoom] of [[1536, 961, 1], [1280, 720, 1], [1120, 720, 1], [760, 900, 1], [1280, 720, 2]]) {
      await resize(width, height, zoom)
      await expect(header).toHaveCount(1)
      await expect(page.getByRole('contentinfo', { name: 'System health', exact: true })).toHaveCount(1)
      await expect(header).toBeInViewport({ ratio: 1 })
      expect(await header.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)

      const headerGeometry = {
        header: await header.boundingBox(),
        search: await header.getByRole('button', { name: 'Search loaded records', exact: true }).boundingBox(),
        identity: await header.locator('.eid-workspace-organization-trigger').boundingBox(),
        iconTarget: await header.locator('button[data-size=icon]').boundingBox(),
        actions: await header.locator('.eid-workspace-actions').boundingBox(),
        nativeRightInset: await header.evaluate(element => Number.parseFloat(getComputedStyle(element).paddingRight))
      }

      expect(headerGeometry.iconTarget!.width, 'icon control preserves its minimum hit width').toBeGreaterThanOrEqual(36)
      expect(headerGeometry.iconTarget!.height, 'icon control preserves its minimum hit height').toBeGreaterThanOrEqual(36)

      if (width >= 1280 && zoom === 1) {
        expect(headerGeometry.header!.height, 'identity keeps the existing page origin').toBe(65)
        expect(headerGeometry.search!.width, 'responsive search retains a usable width').toBeGreaterThanOrEqual(160)
        expect(headerGeometry.search!.width).toBeLessThanOrEqual(380)
        expect(headerGeometry.search!.x + headerGeometry.search!.width).toBeLessThanOrEqual(headerGeometry.actions!.x)
        expect(headerGeometry.actions!.x + headerGeometry.actions!.width).toBeLessThanOrEqual(headerGeometry.header!.x + headerGeometry.header!.width - headerGeometry.nativeRightInset + 1)
        await expect(header.locator('.eid-workspace-organization-copy small')).toBeInViewport({ ratio: 1 })
      }

      if (width === 1536 && zoom === 1) {
        expect(headerGeometry.search!.width, 'full reference search width').toBe(380)
        expect(Math.abs(headerGeometry.search!.x - 887), 'full reference search position with native clearance').toBeLessThanOrEqual(10)
      }

      for (const button of await header.getByRole('button').all()) {
        focusChecks.push(await verifyKeyboardFocus(page, button, `Header ${mode} ${width} ${zoom} ${await button.getAttribute('aria-label')}`))
      }

      await header.getByRole('button', { name: 'Search loaded records', exact: true }).click()
      const input = search.getByRole('textbox', { name: 'Search loaded records', exact: true })
      await input.fill('Avery')
      await search.getByRole('button', { name: /Avery.*Member/ }).click()
      await expect(search.getByText('fixture-executive', { exact: true })).toBeVisible()
      await search.getByRole('button', { name: 'Back to results', exact: true }).click()
      await input.fill('Release evidence discussion')
      await search.getByRole('button', { name: /Release evidence discussion.*Internal conversation/ }).click()
      await expect(search.getByText('Retained internal message; inspection leaves it unread.', { exact: true })).toBeVisible()
      await search.getByText('Retained internal message; inspection leaves it unread.', { exact: true }).scrollIntoViewIfNeeded()
      expect(await search.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
      const name = `workspace-search-${mode.toLowerCase()}-${width}-${height}-${zoom}.png`
      await page.evaluate(async () => { await document.fonts.ready; await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))) })
      const bytes = Buffer.from(await app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')), 'base64')
      writeFileSync(test.info().outputPath(name), bytes)
      const appearance = await page.locator('html').evaluate(element => ({ theme: element.getAttribute('data-hermes-theme'), dark: element.classList.contains('dark') }))
      expect(appearance).toEqual({ theme: 'nous', dark: mode === 'Dark' })
      captures.push({ name, width, height, zoom, ...appearance, headerGeometry, sha256: createHash('sha256').update(bytes).digest('hex') })
      await test.info().attach(name, { body: bytes, contentType: 'image/png' })
      await page.keyboard.press('Escape')
      await expect(search).toBeHidden()
      await expect(header.getByRole('button', { name: 'Search loaded records', exact: true })).toBeFocused()
    }
  }

  await resize(1536, 961, 1)
  await header.getByRole('button', { name: 'Search loaded records', exact: true }).click()
  await search.getByRole('textbox', { name: 'Search loaded records', exact: true }).fill('autumn release')
  await search.getByRole('link', { name: /Prepare the autumn release brief.*Objective/ }).click()
  await expect(search).toBeHidden()
  await expect(page.locator('.eid-objective-hero')).toContainText('Prepare the autumn release brief')
  expect(mock.receivedPrompts).toHaveLength(0)
  // Outcomes are already in the snapshot: inspection must not fetch their history.
  expect([...new Set(methods)].sort()).toEqual(['organization.descriptor', 'organization.snapshot'])
  await test.info().attach('workspace-header-evidence', { contentType: 'application/json', body: JSON.stringify({
    sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
    sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
    providerCalls: mock.receivedPrompts.length, methods, captures, focusChecks
  }) })
})


for (const locale of ['ja', 'ar', 'ru'] as const) {
  test(`keeps translated workspace controls usable in ${locale} with multiple connections`, async () => {
    fixture = await setupMockBackend({ extraDisplayConfig: `  language: ${locale}` })
    const { app, page, mock } = fixture

    const copy = {
      ja: { objectives: '目標', home: 'あなたの組織', edit: '組織名と所有者名を編集', health: 'システムの状態', heading: 'ワークスペースの操作', search: '読み込み済みの記録を検索', kinds: { agent: 'メンバー' }, dark: 'ダーク', light: 'ライト', settingsSearch: '検索' },
      ar: { objectives: 'الأهداف', home: 'مؤسستك', edit: 'تعديل اسم المؤسسة والمالك', health: 'حالة النظام', heading: 'عناصر تحكم مساحة العمل', search: 'البحث في السجلات المحمّلة', kinds: { agent: 'عضو' }, dark: 'داكن', light: 'فاتح', settingsSearch: 'بحث' },
      ru: { objectives: 'Цели', home: 'Ваша организация', edit: 'Изменить названия организации и владельца', health: 'Состояние системы', heading: 'Управление рабочей областью', search: 'Поиск загруженных записей', kinds: { agent: 'Участник' }, dark: 'Тёмная', light: 'Светлая', settingsSearch: 'Поиск' }
    }[locale]

    const methods: string[] = []
    await page.routeWebSocket(/.*/, socket => {
      const server = socket.connectToServer()
      socket.onMessage(message => {
        const request = JSON.parse(message.toString()) as { id?: number; method?: string }

        if (request.method?.startsWith('organization.')) {methods.push(request.method)}

        if (request.method === 'organization.snapshot' || request.method === 'organization.outcomes') {
          socket.send(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: request.method === 'organization.snapshot' ? snapshot : snapshot.outcomes }))
        } else {server.send(message)}
      })
    })
    await page.reload()
    await waitForAppReady(fixture)
    await expect(page.locator('html')).toHaveAttribute('lang', locale)
    await expect(page.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr')
    // Only a second alias of this temporary loopback backend; no live host or credentials.
    expect(await page.evaluate(async () => {
      const bridge = (window as unknown as { hermesDesktop: {
        getConnection(): Promise<{ baseUrl: string; token: string }>
        connections: { save(input: { kind: 'remote'; label: string; url: string; token: string }): Promise<{ ok: boolean }> }
      } }).hermesDesktop

      const current = await bridge.getConnection()

      if (!/^http:\/\/(127\.0\.0\.1|localhost):/.test(current.baseUrl)) {throw new Error('Expected temporary loopback backend')}
      const result = await bridge.connections.save({ kind: 'remote', label: 'Temporary organization connection with a long descriptive name', url: current.baseUrl, token: current.token })

      return result.ok
    })).toBe(true)
    await expect(page.getByRole('navigation', { name: 'Primary', exact: true }).getByRole('link', { name: copy.objectives, exact: true })).toBeVisible()
    const header = page.getByRole('banner', { name: copy.heading, exact: true })
    await expect(header.locator('[data-slot="connection-switcher"]')).toBeVisible()
    const captures: object[] = []
    const headerCaptures: object[] = []
    const focusChecks: object[] = []

    for (const mode of ['dark', 'light'] as const) {
      await page.evaluate(() => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(100))
      await app.evaluate(({ BrowserWindow }) => { const window = BrowserWindow.getAllWindows()[0]; window.unmaximize(); window.setMinimumSize(600, 600); window.setContentSize(1536, 961) })
      await page.evaluate(() => { location.hash = '/settings?tab=config:appearance' })
      await page.getByRole('button').filter({ has: page.getByText('AetherMesh', { exact: true }) }).click()
      await page.getByRole('button', { name: copy[mode], exact: true }).click()
      await expect(page.locator('html')).toHaveAttribute('data-hermes-theme', 'nous')
      await expect.poll(() => page.locator('html').evaluate(element => element.classList.contains('dark'))).toBe(mode === 'dark')
      await expect(header.getByRole('button', { name: copy.settingsSearch, exact: true })).toBeVisible()
      await page.evaluate(() => { location.hash = '/home' })

      for (const [width, height, zoom] of [[1280, 720, 1], [760, 900, 1], [1280, 720, 2]]) {
        await page.evaluate(() => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(100))
        await app.evaluate(({ BrowserWindow }, size) => { BrowserWindow.getAllWindows()[0].setContentSize(size.width, size.height) }, { width, height })
        await expect.poll(() => page.evaluate(() => innerWidth)).toBe(width)
        await page.evaluate(value => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(value * 100), zoom)
        await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(zoom)
        await expect(header).toBeInViewport({ ratio: 1 })
        expect(await header.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)

        for (const button of await header.getByRole('button').all()) {
          focusChecks.push(await verifyKeyboardFocus(page, button, `${locale} ${mode} ${width} ${zoom} ${await button.getAttribute('aria-label')}`))
        }

        // Scroll bounds alone miss a flex child painted over its sibling. Every
        // header control must keep a separate click target at each locale/zoom.
        const controlRects = await header.getByRole('button').evaluateAll(buttons => buttons.map(button => {
          const rect = button.getBoundingClientRect()

          return { label: button.getAttribute('aria-label'), left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom }
        }))

        const sortedRects = controlRects.sort((a, b) => a.left - b.left)

        for (let index = 0; index < sortedRects.length; index++) {
          for (const other of sortedRects.slice(index + 1)) {
            const rect = sortedRects[index]
            expect(rect.right <= other.left || other.right <= rect.left || rect.bottom <= other.top || other.bottom <= rect.top,
              `${rect.label} overlaps ${other.label}`).toBe(true)
          }
        }

        // Full identity labels must reflow, not merely keep separate hit targets.
        for (const label of await header.locator('.eid-workspace-organization-name, .eid-workspace-identity button span').all()) {
          if (await label.isVisible()) {
            expect(await label.evaluate(element => ({ clipped: element.scrollWidth > element.clientWidth + 1,
              ellipsis: getComputedStyle(element).textOverflow === 'ellipsis' }))).toEqual({ clipped: false, ellipsis: false })
          }
        }

        await expect(page.getByRole('heading', { name: copy.home, exact: true })).toBeVisible()
        await expect(header.getByRole('button', { name: copy.edit, exact: true })).toBeVisible()
        await expect(page.getByRole('contentinfo', { name: copy.health, exact: true })).toBeVisible()
        // Capture the header before opening Search; the modal backdrop blurs it.
        const headerName = `workspace-header-${locale}-${mode}-${width}-${height}-${zoom}.png`
        const headerBytes = Buffer.from(await app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')), 'base64')
        writeFileSync(test.info().outputPath(headerName), headerBytes)
        headerCaptures.push({ name: headerName, width, height, zoom, controlRects: sortedRects, captureMethod: 'electron.capturePage', sha256: createHash('sha256').update(headerBytes).digest('hex') })
        await test.info().attach(headerName, { body: headerBytes, contentType: 'image/png' })
        await header.locator('[data-slot="connection-switcher"]').getByRole('button').click()
        await expect(page.getByRole('menuitemradio', { name: /Temporary organization connection/ })).toBeVisible()
        await page.keyboard.press('Escape')
        await header.getByRole('button', { name: copy.search, exact: true }).click()
        const dialog = page.getByRole('dialog', { name: copy.search, exact: true })
        await dialog.getByRole('textbox', { name: copy.search, exact: true }).fill('Avery')
        await dialog.getByRole('button', { name: `Avery · ${copy.kinds.agent} · executive`, exact: true }).click()
        await expect(dialog.getByText('fixture-executive', { exact: true })).toBeVisible()
        expect(await dialog.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
        const name = `workspace-${locale}-${mode}-${width}-${height}-${zoom}.png`
        await page.evaluate(async () => { await document.fonts.ready; await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))) })
        const bytes = Buffer.from(await app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')), 'base64')
        writeFileSync(test.info().outputPath(name), bytes)
        const appearance = await page.locator('html').evaluate(element => ({ theme: element.getAttribute('data-hermes-theme'), dark: element.classList.contains('dark') }))
        expect(appearance).toEqual({ theme: 'nous', dark: mode === 'dark' })
        captures.push({ name, width, height, zoom, ...appearance, sha256: createHash('sha256').update(bytes).digest('hex') })
        await test.info().attach(name, { body: bytes, contentType: 'image/png' })
        await page.keyboard.press('Escape')
        await expect(dialog).toBeHidden()
        await expect(header.getByRole('button', { name: copy.search, exact: true })).toBeFocused()
      }
    }

    expect(mock.receivedPrompts).toHaveLength(0)
    expect([...new Set(methods)].sort()).toEqual(['organization.descriptor', 'organization.snapshot'])
    await test.info().attach('workspace-translated-evidence', { contentType: 'application/json', body: JSON.stringify({
      sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
      sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
      locale, providerCalls: mock.receivedPrompts.length, methods, captures, headerCaptures, focusChecks
    }) })
  })
}

const translatedSurfaceExpectations = {
  "Ja": {
    workHeading: "作業の概要", workNote: "現在の目標を追跡し、作業、レビュー、証拠を確認できます。",
    reviewFindings: "記録されたレビュー所見", reviewUnmet: "未充足",
    objectiveOverview: "目標の概要",
    objectiveSearch: "目標を検索",
    objectiveRecord: "目標の詳細",
    objectivePhase: "実行とレビュー",
    "search": "メンバーを検索",
    "directoryDetails": "メンバーとの会話について",
    "directoryNote": "このプロファイルの永続メンバーです。選択しても会話は開かず、利用枠も追加されません。",
    "memberContext": "メンバーの背景情報",
    "responsibilities": "担当業務",
    "linkedWorkNote": "この表示に記録された担当者、および現在のタスクまたはマネージャーへの割り当て。",
    "executiveMessageNote": "現在の組織のエグゼクティブを選択してください。メッセージはそのメンバーとの会話に属し、送信操作を行ったときにのみ送られます。",
    "organization": "組織",
    "deliverables": "成果物",
    "deliverablesIntroduction": "承認済みの成果を確認し、元の出所を保持したファイルを調べます。",
    "findAgent": "エージェントを検索"
  },
  "Zh": {
    workHeading: "工作概览", workNote: "跟踪当前目标，并查看其工作、审核和证据。",
    reviewFindings: "已记录的审查结论", reviewUnmet: "未满足",
    objectiveOverview: "目标概览",
    objectiveSearch: "搜索目标",
    objectiveRecord: "目标详情",
    objectivePhase: "执行与审查",
    "search": "搜索成员",
    "directoryDetails": "关于成员对话",
    "directoryNote": "此配置文件中的持久成员。选择成员不会打开对话或补充其额度。",
    "memberContext": "成员背景",
    "responsibilities": "职责",
    "linkedWorkNote": "此视图中记录的负责人，以及当前的任务或经理工作分配。",
    "executiveMessageNote": "选择当前组织中的一位执行主管。消息属于与该成员的对话，只有提交后才会发送。",
    "organization": "组织",
    "deliverables": "交付物",
    "deliverablesIntroduction": "查看已验收的成果，并检查保留原始来源信息的文件。",
    "findAgent": "查找智能体"
  },
  "ZhHant": {
    workHeading: "工作概覽", workNote: "追蹤目前目標，並檢視其工作、審查與證據。",
    reviewFindings: "已記錄的審查結論", reviewUnmet: "未滿足",
    objectiveOverview: "目標概覽",
    objectiveSearch: "搜尋目標",
    objectiveRecord: "目標詳情",
    objectivePhase: "執行與審查",
    "search": "搜尋成員",
    "directoryDetails": "關於成員對話",
    "directoryNote": "此設定檔中的持久成員。選取成員不會開啟對話或補充其額度。",
    "memberContext": "成員背景",
    "responsibilities": "職責",
    "linkedWorkNote": "此檢視中記錄的負責人，以及目前的任務或經理工作指派。",
    "executiveMessageNote": "選擇目前組織中的一位執行主管。訊息屬於與該成員的對話，只有提交後才會傳送。",
    "organization": "組織",
    "deliverables": "交付項目",
    "deliverablesIntroduction": "檢視已驗收的成果，並檢查保留原始來源資訊的檔案。",
    "findAgent": "尋找代理"
  },
  "Ar": {
    workHeading: "نظرة عامة على العمل", workNote: "تابع الأهداف الحالية ثم افتح أعمالها ومراجعاتها وأدلتها.",
    reviewFindings: "نتائج المراجعة المسجلة", reviewUnmet: "غير مستوفى",
    objectiveOverview: "نظرة عامة على الهدف",
    objectiveSearch: "البحث في الأهداف",
    objectiveRecord: "تفاصيل الهدف",
    objectivePhase: "التنفيذ والمراجعة",
    "search": "البحث عن أعضاء",
    "directoryDetails": "حول محادثات الأعضاء",
    "directoryNote": "أعضاء دائمون في هذا الملف الشخصي. لا يؤدي اختيار عضو إلى فتح محادثته أو تجديد حصتها.",
    "memberContext": "سياق العضو",
    "responsibilities": "المسؤوليات",
    "linkedWorkNote": "الملكية المسجلة وتكليفات المهام أو المديرين الحالية في هذا العرض.",
    "executiveMessageNote": "اختر مسؤولاً تنفيذياً في المنظمة الحالية. تنتمي رسالتك إلى محادثة ذلك العضو، ولا تُرسل إلا عند تأكيد إرسالها.",
    "organization": "المؤسسة",
    "deliverables": "المخرجات",
    "deliverablesIntroduction": "راجع النتائج المقبولة وافحص الملفات مع معلومات مصدرها الأصلي.",
    "findAgent": "البحث عن وكيل"
  },
  "Ru": {
    workHeading: "Обзор работы", workNote: "Следите за текущими целями и открывайте их работы, проверки и подтверждения.",
    reviewFindings: "Записанные выводы проверки", reviewUnmet: "Не выполнен",
    objectiveOverview: "Обзор цели",
    objectiveSearch: "Поиск целей",
    objectiveRecord: "Сведения о цели",
    objectivePhase: "Выполнение и проверка",
    "search": "Поиск участников",
    "directoryDetails": "О диалогах с участниками",
    "directoryNote": "Постоянные участники этого профиля. Выбор участника не открывает диалог и не пополняет его лимит.",
    "memberContext": "Контекст участника",
    "responsibilities": "Обязанности",
    "linkedWorkNote": "Указанные владельцы и текущие назначения задач или работ менеджерам в этом представлении.",
    "executiveMessageNote": "Выберите руководителя текущей организации. Сообщение относится к диалогу с этим участником и отправляется только после нажатия кнопки отправки.",
    "organization": "Организация",
    "deliverables": "Результаты работы",
    "deliverablesIntroduction": "Просматривайте принятые результаты и изучайте файлы с сохранёнными сведениями об их происхождении.",
    "findAgent": "Найти агента"
  }
} as const

// Read-only native coverage of the translated slice, separate from supported
// owner-chat execution evidence. Selecting a fixture member never opens chat.
for (const [locale, suffix, dark, light] of [
  ['ja', 'Ja', 'ダーク', 'ライト'], ['zh', 'Zh', '暗色', '明亮'],
  ['zh-hant', 'ZhHant', '深色', '明亮'], ['ar', 'Ar', 'داكن', 'فاتح'],
  ['ru', 'Ru', 'Тёмная', 'Светлая']
] as const) {
  test(`renders translated organization surfaces in ${locale} without executing work`, async () => {
    fixture = await setupMockBackend({ extraDisplayConfig: `  language: ${locale}` })
    const { app, page, mock } = fixture
    const copy = translatedSurfaceExpectations[suffix]

    const retainedCopy = locale === 'ar'
      ? { identity: 'هوية دائمة', memory: 'الذاكرة الشخصية', questions: 'الأسئلة المفتوحة', history: 'سجل الهوية الحديث' }
      : locale === 'ru' ? { identity: 'Постоянная идентичность', memory: 'Личная память', questions: 'Открытые вопросы', history: 'Последняя история участника' } : null

    const localizedSnapshot = { ...snapshot, agents: snapshot.agents.map(agent => ({ ...agent, context: {
      contextSummary: 'Verify release evidence before accepting the outcome.',
      memory: { facts: [], decisions: [], lessons: [], openQuestions: ['Which audience should this brief address?'] },
      recentHistory: []
    } })), objectives: snapshot.objectives.map(objective =>
      objective.id === 'release' ? { ...objective, phaseCode: 'execution_review', phase: 'Execution and review' } : objective
    ) }

    const methods: string[] = []
    await page.routeWebSocket(/.*/, socket => {
      const server = socket.connectToServer()
      socket.onMessage(message => {
        const request = JSON.parse(message.toString()) as { id?: number; method?: string }

        if (request.method?.startsWith('organization.')) {methods.push(request.method)}

        if (request.method === 'organization.snapshot' || request.method === 'organization.outcomes') {
          socket.send(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: request.method === 'organization.snapshot' ? localizedSnapshot : localizedSnapshot.outcomes }))
        } else {server.send(message)}
      })
    })
    await page.reload()
    await waitForAppReady(fixture)
    await expect(page.locator('html')).toHaveAttribute('lang', locale)
    await expect(page.locator('html')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr')
    const captures: object[] = []

    for (const mode of ['dark', 'light'] as const) {
      await page.evaluate(() => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(100))
      await app.evaluate(({ BrowserWindow }) => { const window = BrowserWindow.getAllWindows()[0]; window.unmaximize(); window.setMinimumSize(600, 600); window.setContentSize(1536, 961) })
      await page.evaluate(() => { location.hash = '/settings?tab=config:appearance' })
      await page.getByRole('button').filter({ has: page.getByText('AetherMesh', { exact: true }) }).click()
      await page.getByRole('button', { name: mode === 'dark' ? dark : light, exact: true }).click()

      for (const [width, height, zoom] of [[1536, 961, 1], [1280, 720, 2]]) {
        await page.evaluate(() => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(100))
        await app.evaluate(({ BrowserWindow }, size) => BrowserWindow.getAllWindows()[0].setContentSize(size.width, size.height), { width, height })
        await page.evaluate(value => (window as unknown as { hermesDesktop: { zoom: { setPercent(percent: number): void } } }).hermesDesktop.zoom.setPercent(value * 100), zoom)
        await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(zoom)

        for (const route of ['/organization', '/artifacts?source=organization', '/messages', '/messages?recipient=executive', '/work', '/objectives', '/objectives/release']) {
          await page.evaluate(value => { location.hash = value }, route)

          if (route === '/work') {
            await expect(page.getByRole('heading', { name: copy.workHeading, exact: true })).toBeVisible()
            await expect(page.getByText(copy.workNote, { exact: true })).toBeVisible()
            await expect(page.locator('.eid-work-overview').getByRole('region', { name: 'Prepare the autumn release brief', exact: true })).toBeVisible()
            expect(await page.locator('.eid-work-overview').evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
          } else if (route === '/objectives') {
            const search = page.getByRole('textbox', { name: copy.objectiveSearch, exact: true })
            await expect(search).toBeVisible()
            await search.fill('autumn')
            await expect(page.locator('.eid-list').getByRole('link', { name: /Prepare the autumn release brief/ })).toBeVisible()
            await expect(page.locator('.eid-list').getByRole('link', { name: /Review the workspace navigation/ })).toHaveCount(0)
            await search.fill('')
          } else if (route === '/objectives/release') {
            await expect(page.getByRole('region', { name: copy.objectiveOverview, exact: true })).toBeVisible()
            await expect(page.getByRole('heading', { name: 'Progress overview', exact: true })).toHaveCount(0)
            await expect(page.getByRole('button', { name: 'Inspect work and evidence', exact: true })).toHaveCount(0)
            await expect(page.getByRole('region', { name: 'Objective dispatch', exact: true })).toHaveCount(0)
            await expect(page.getByRole('region', { name: 'Objective priority', exact: true })).toHaveCount(0)
            const details = page.getByRole('region', { name: copy.objectiveRecord, exact: true })
            await expect(details).toBeVisible()
            await expect(details).toContainText('Avery')
            await expect(details).toContainText(copy.objectivePhase)
            await expect(details.locator('time')).toHaveAttribute('datetime', createdAt)
            // Authored English remains LTR inside the Arabic interface.
            await expect(page.locator('.eid-objective-heading h1')).toHaveCSS('direction', 'ltr')
            await expect(page.locator('.eid-objective-hero .eid-result-text')).toHaveCSS('direction', 'ltr')
            await expect(details.getByText(copy.objectivePhase, { exact: true })).toHaveCSS('direction', locale === 'ar' ? 'rtl' : 'ltr')
          } else if (route === '/organization') {
            await expect(page.getByRole('heading', { name: copy.organization, exact: true })).toBeVisible()
            await expect(page.getByRole('textbox', { name: copy.findAgent, exact: true })).toBeVisible()
          } else if (route === '/artifacts?source=organization') {
            await expect(page.getByRole('heading', { name: copy.deliverables, exact: true })).toBeVisible()
            await expect(page.getByText(copy.deliverablesIntroduction, { exact: true })).toBeVisible()
            await expect(page.locator('.eid-delivery-outcomes').getByRole('heading', { name: 'Document provider boundaries', exact: true })).toBeVisible()
          } else {
            await expect(page.getByRole('textbox', { name: copy.search, exact: true })).toBeVisible()
            const directoryDetails = page.locator('.eid-message-directory-details > summary')
            await expect(directoryDetails).toHaveText(copy.directoryDetails)
            await directoryDetails.focus()
            await page.keyboard.press('Enter')
            await expect(page.getByText(copy.directoryNote, { exact: true })).toBeVisible()

            if (route.includes('recipient')) {
              await expect(page.getByText(copy.executiveMessageNote, { exact: true })).toBeVisible()
              await expect(page.locator('.eid-message-members nav button')).toHaveCount(1)
            }

            await directoryDetails.focus()
            await page.keyboard.press('Enter')
            await expect(page.locator('.eid-message-directory-details')).not.toHaveAttribute('open', '')
            await page.locator('.eid-message-members nav').getByRole('button', { name: /Avery/ }).click()
            await expect(page.getByRole('region', { name: copy.memberContext, exact: true })).toBeVisible()
            await expect(page.getByRole('heading', { name: copy.responsibilities, exact: true })).toBeVisible()
            await expect(page.getByText(copy.linkedWorkNote, { exact: true })).toBeVisible()
          }

          const focalCopy = route === '/work' ? copy.workNote : route === '/objectives' ? copy.objectiveSearch : route === '/objectives/release' ? copy.objectiveRecord : route === '/organization' ? copy.findAgent : route === '/artifacts?source=organization' ? copy.deliverablesIntroduction : route.includes('recipient') ? copy.executiveMessageNote : copy.linkedWorkNote
          const focal = page.getByText(focalCopy, { exact: true })

          if (route === '/messages?recipient=executive') {
            const directoryDetails = page.locator('.eid-message-directory-details > summary')
            await directoryDetails.focus()
            await page.keyboard.press('Enter')
            await expect(page.locator('.eid-message-directory-details')).toHaveAttribute('open', '')
          }

          // Center the evidence target with space on both sides. Nearest-edge
          // scrolling can leave a fractional CSS pixel clipped at 200% zoom.
          await focal.evaluate(element => element.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' }))
          await expect(focal).toBeInViewport({ ratio: 1 })
          expect(await page.locator('.eid-workspace').evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)

          if (route.startsWith('/artifacts')) {
            expect(await page.locator('.eid-deliverables').evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
          }

          await page.evaluate(async () => { await document.fonts.ready; await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))) })
          const appearance = await page.locator('html').evaluate(element => ({ theme: element.getAttribute('data-hermes-theme'), dark: element.classList.contains('dark'), dir: element.getAttribute('dir') }))
          expect(appearance).toEqual({ theme: 'nous', dark: mode === 'dark', dir: locale === 'ar' ? 'rtl' : 'ltr' })
          const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)
          expect(overflow).toBeLessThanOrEqual(1)
          const memberSearch = route.startsWith('/messages') ? await verifyMemberSearchGeometry(page) : undefined
          const name = `translated-${locale}-${mode}-${route.includes('recipient') ? 'executive' : route.startsWith('/artifacts') ? 'deliverables' : route.slice(1).replaceAll('/', '-')}-${width}-${height}-${zoom}.png`
          const bytes = Buffer.from(await app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')), 'base64')
          writeFileSync(test.info().outputPath(name), bytes)
          captures.push({ name, locale, route, width, height, zoom, ...appearance, overflow, memberSearch, sha256: createHash('sha256').update(bytes).digest('hex') })
          await test.info().attach(name, { body: bytes, contentType: 'image/png' })

          if (route === '/objectives/release') {
            const findings = page.getByRole('region', { name: copy.reviewFindings, exact: true })
            await expect(findings.getByText('Published launch dates disagree.', { exact: true })).toBeVisible()
            await findings.locator('summary').click()
            await expect(findings.getByText(copy.reviewUnmet, { exact: true })).toBeVisible()
            const reason = findings.getByText('Two source dates remain unresolved.', { exact: true })
            await reason.scrollIntoViewIfNeeded()
            await expect(reason).toBeInViewport({ ratio: 1 })
            await expect(reason).toHaveCSS('direction', 'ltr')
            expect(await findings.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
            await page.evaluate(async () => { await document.fonts.ready; await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))) })
            const reviewName = `review-${locale}-${mode}-${width}-${height}-${zoom}.png`
            const reviewBytes = Buffer.from(await app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')), 'base64')
            writeFileSync(test.info().outputPath(reviewName), reviewBytes)
            captures.push({ name: reviewName, locale, route, width, height, zoom, ...appearance, captureMethod: 'electron.capturePage', sha256: createHash('sha256').update(reviewBytes).digest('hex') })
            await test.info().attach(reviewName, { body: reviewBytes, contentType: 'image/png' })
            await findings.locator('summary').click()
          }

          if (retainedCopy && route === '/messages?recipient=executive') {
            const retained = page.locator('.eid-message-context details')
            await retained.locator(':scope > summary').click()
            await expect(retained.getByRole('heading', { name: retainedCopy.identity, exact: true })).toBeVisible()
            await expect(retained.getByRole('region', { name: retainedCopy.memory, exact: true })).toBeVisible()
            await expect(retained.getByRole('region', { name: retainedCopy.history, exact: true })).toBeVisible()
            const questions = retained.getByRole('region', { name: retainedCopy.questions, exact: true })
            const question = questions.getByText('Which audience should this brief address?', { exact: true })
            await question.scrollIntoViewIfNeeded()
            await expect(question).toBeInViewport({ ratio: 1 })
            await expect(question).toHaveCSS('direction', 'ltr')
            expect(await questions.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
            await page.evaluate(async () => { await document.fonts.ready; await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))) })
            const retainedName = `retained-${locale}-${mode}-${width}-${height}-${zoom}.png`
            const retainedBytes = Buffer.from(await app.evaluate(async ({ BrowserWindow }) => (await BrowserWindow.getAllWindows()[0].webContents.capturePage()).toPNG().toString('base64')), 'base64')
            writeFileSync(test.info().outputPath(retainedName), retainedBytes)
            captures.push({ name: retainedName, locale, route, width, height, zoom, ...appearance, captureMethod: 'electron.capturePage', sha256: createHash('sha256').update(retainedBytes).digest('hex') })
            await test.info().attach(retainedName, { body: retainedBytes, contentType: 'image/png' })
            await retained.locator(':scope > summary').click()
          }
        }
      }
    }

    expect(mock.receivedPrompts).toHaveLength(0)
    expect(methods.length).toBeGreaterThan(0)
    expect([...new Set(methods)].every(method => ['organization.descriptor', 'organization.snapshot', 'organization.outcomes'].includes(method))).toBe(true)
    writeFileSync(test.info().outputPath('translated-surfaces-evidence.json'), JSON.stringify({
      sourceCommit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
      sourceTree: execFileSync('git', ['rev-parse', 'HEAD^{tree}'], { encoding: 'utf8' }).trim(),
      locale, providerCalls: mock.receivedPrompts.length, methods, captures
    }, null, 2))
  })
}
