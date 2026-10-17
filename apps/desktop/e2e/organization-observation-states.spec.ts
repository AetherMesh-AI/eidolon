import { acknowledgeExpectedErrorBanner, expect, test } from './test'
import { captureGallery, type GalleryFixture, persistGalleryEvidence, resizeGallery, setupGallery, writeGalleryEvidence } from './workspace-gallery-fixture'

let fixture: GalleryFixture | undefined
test.setTimeout(300_000)
test.afterEach(async () => {
  try { if (fixture) { persistGalleryEvidence(fixture, 'observation-states') } }
  finally { await fixture?.cleanup(); fixture = undefined }
})

const cases = [
  { locale: 'en', zoom: 1, loading: 'Loading organization data', unavailable: 'Organization data unavailable', empty: 'No records in the last received state', objectives: 'No objectives yet', agents: 'No connected organization agents', retry: 'Retry connection' },
  { locale: 'ja', zoom: 1.25, loading: '組織データを読み込み中', unavailable: '組織データを取得できません', empty: '最後に受信した状態に記録はありません', objectives: '目標はまだありません', agents: '接続中の組織のエージェントはいません', retry: '接続を再試行' },
  { locale: 'ar', zoom: 2, loading: 'جارٍ تحميل بيانات المؤسسة', unavailable: 'بيانات المؤسسة غير متاحة', empty: 'لا توجد سجلات في آخر حالة مستلمة', objectives: 'لا توجد أهداف بعد', agents: 'لا يوجد وكلاء متصلون بالمؤسسة', retry: 'إعادة محاولة الاتصال' }
] as const

for (const copy of cases) {
  test(`organization lists distinguish unobserved, confirmed empty and retained empty in ${copy.locale}`, async () => {
    const current = fixture = await setupGallery(copy.locale, false, false, { blockOrganizationMutations: true })
    const { page, observationControl, state } = current
    state.snapshot.objectives = []
    state.snapshot.agents = []
    state.snapshot.tasks = []
    state.snapshot.requests = []
    state.snapshot.activity = []
    state.snapshot.knowledge = []
    state.snapshot.outcomes = { ...state.snapshot.outcomes, total: 0, unread: 0, hasMore: false, nextCursor: null, items: [] }
    state.snapshot.runtime.rosterCount = 0
    state.snapshot.runtime.workingCount = 0

    for (const route of ['/objectives', '/organization']) {
      const name = route.slice(1)
      const emptyTitle = route === '/objectives' ? copy.objectives : copy.agents
      await resizeGallery(current, 1280, 800, copy.zoom)
      await page.evaluate(path => { location.hash = path }, route)
      observationControl.pause()
      await page.reload()
      await expect.poll(() => observationControl.pending).toBeGreaterThan(0)
      const workspace = page.locator('.eid-workspace').last()
      await expect(workspace.getByRole('heading', { name: copy.loading, exact: true })).toBeVisible()
      await expect(workspace.getByRole('heading', { name: emptyTitle, exact: true })).toHaveCount(0)
      await workspace.getByRole('heading', { name: copy.loading, exact: true }).scrollIntoViewIfNeeded()
      await captureGallery(current, `${copy.locale}-${name}-initial-loading-${copy.zoom * 100}`, workspace)
      const error = `Synthetic ${name} initial observation unavailable.`
      state.observationError = error
      observationControl.release()
      const status = workspace.locator('.eid-runtime-status')
      await expect(status.getByRole('alert')).toHaveText(error)
      await expect(workspace.getByRole('heading', { name: copy.unavailable, exact: true })).toBeVisible()
      await expect(workspace.getByRole('heading', { name: emptyTitle, exact: true })).toHaveCount(0)
      await workspace.getByRole('heading', { name: copy.unavailable, exact: true }).scrollIntoViewIfNeeded()
      await captureGallery(current, `${copy.locale}-${name}-initial-error-${copy.zoom * 100}`, workspace)
      await resizeGallery(current, 400, 620)
      await workspace.getByRole('heading', { name: copy.unavailable, exact: true }).scrollIntoViewIfNeeded()
      await captureGallery(current, `${copy.locale}-${name}-initial-error-narrow`, workspace)

      observationControl.pause()
      const beforeRetry = current.methods.filter(method => method === 'organization.observe' || method === 'organization.snapshot').length
      await status.getByRole('button', { name: copy.retry, exact: true }).click()
      await expect.poll(() => observationControl.pending).toBeGreaterThan(0)
      expect(current.methods.filter(method => method === 'organization.observe' || method === 'organization.snapshot').length).toBeGreaterThan(beforeRetry)
      await expect(workspace.getByRole('heading', { name: emptyTitle, exact: true })).toHaveCount(0)
      state.observationError = null
      observationControl.release()
      await expect(status).toHaveCount(0)
      await expect(workspace.getByRole('heading', { name: emptyTitle, exact: true })).toBeVisible()
      await acknowledgeExpectedErrorBanner(page, error)
      await expect(page).toHaveURL(new RegExp(`#${route}$`))
      await workspace.getByRole('heading', { name: emptyTitle, exact: true }).scrollIntoViewIfNeeded()
      await captureGallery(current, `${copy.locale}-${name}-confirmed-empty-narrow`, workspace)

      // Preserve the mounted successful empty snapshot through the real next read.
      observationControl.pause()
      await expect.poll(() => observationControl.pending, { timeout: 20_000 }).toBeGreaterThan(0)
      state.observationError = `Synthetic ${name} retained empty interruption.`
      observationControl.release()
      await expect(status.getByRole('alert')).toHaveText(state.observationError)
      await expect(workspace.getByRole('heading', { name: copy.empty, exact: true })).toBeVisible()
      await expect(workspace.getByRole('heading', { name: emptyTitle, exact: true })).toHaveCount(0)
      await workspace.getByRole('heading', { name: copy.empty, exact: true }).scrollIntoViewIfNeeded()
      await captureGallery(current, `${copy.locale}-${name}-retained-empty-narrow`, workspace)
      const retainedError = state.observationError
      observationControl.pause()
      await status.getByRole('button', { name: copy.retry, exact: true }).click()
      await expect.poll(() => observationControl.pending).toBeGreaterThan(0)
      state.observationError = null
      observationControl.release()
      await expect(status).toHaveCount(0)
      await expect(workspace.getByRole('heading', { name: emptyTitle, exact: true })).toBeVisible()
      await acknowledgeExpectedErrorBanner(page, retainedError)
      await expect(page).toHaveURL(new RegExp(`#${route}$`))
    }

    writeGalleryEvidence(current, `observation-empty-${copy.locale}`)
  })
}

const sizes = [[1280, 800, 1], [1280, 800, 1.25], [1280, 800, 2], [400, 620, 1]] as const

test('retained objectives and agents preserve page, filters and inspector through scoped Retry', async () => {
  const current = fixture = await setupGallery('en', false, false, { blockOrganizationMutations: true })
  const { page, state, observationControl } = current
  const original = state.snapshot.objectives.find(objective => objective.id === 'navigation')!
  expect(original.workPackages).toBeUndefined()
  state.snapshot.objectives = Array.from({ length: 30 }, (_, index) => ({ ...original, id: `observation-goal-${index}`, title: `Observation goal ${String(index).padStart(2, '0')}` }))
  await page.reload()
  const workspace = page.locator('.eid-workspace').last()
  const stale = 'Showing last-known data. The current state is not confirmed.'

  for (const [width, height, zoom] of sizes) {
    await resizeGallery(current, width, height, zoom)

    for (const route of ['/objectives', '/organization']) {
      await page.evaluate(path => { location.hash = path }, route)
      const objectives = route === '/objectives'
      const search = workspace.getByRole('textbox', { name: objectives ? 'Search objectives' : 'Find an agent', exact: true })
      const query = objectives ? 'Observation goal' : 'Morgan'
      await search.fill(query)

      if (objectives) { await workspace.getByRole('button', { name: 'Next page', exact: true }).click() }
      else { await workspace.getByRole('button', { name: 'Inspect Morgan', exact: true }).click() }

      const selected = objectives ? workspace.getByRole('link', { name: /Observation goal 25/ }) : page.getByRole('complementary', { name: 'Agent details', exact: true })
      await expect(selected).toBeVisible()
      const selectedHref = objectives ? await selected.getAttribute('href') : null
      const originalUrl = page.url()
      const label = `${route.slice(1)}-${width}-${zoom * 100}`
      observationControl.pause()
      await expect.poll(() => observationControl.pending, { timeout: 20_000 }).toBeGreaterThan(0)
      const error = `Synthetic ${label} retained read interruption.`
      state.observationError = error
      observationControl.release()
      const status = workspace.locator('.eid-runtime-status')
      await expect(status.getByRole('alert')).toHaveText(error)
      await expect(workspace.getByText(stale, { exact: true })).toHaveCount(1)
      await expect(search).toHaveValue(query)
      await expect(selected).toBeVisible()

      if (objectives) { await expect(selected).toHaveAttribute('href', selectedHref!); await expect(workspace.getByRole('button', { name: 'Previous page', exact: true })).toBeEnabled() }
      else { await expect(selected).toContainText('Morgan') }

      await expect(page).toHaveURL(originalUrl)
      await selected.scrollIntoViewIfNeeded()
      await captureGallery(current, `${label}-retained`, workspace)
      observationControl.pause()
      await status.getByRole('button', { name: 'Retry connection', exact: true }).click()
      await expect.poll(() => observationControl.pending).toBeGreaterThan(0)
      await expect(search).toHaveValue(query)
      await expect(selected).toBeVisible()
      state.observationError = null
      observationControl.release()
      await expect(status).toHaveCount(0)
      await expect(workspace.getByText(stale, { exact: true })).toHaveCount(0)
      await expect(search).toHaveValue(query)
      await expect(selected).toBeVisible()
      await expect(page).toHaveURL(originalUrl)
      await acknowledgeExpectedErrorBanner(page, error)
      await selected.scrollIntoViewIfNeeded()
      await captureGallery(current, `${label}-recovered`, workspace)

      if (objectives) { await workspace.getByRole('button', { name: 'Previous page', exact: true }).click() }
      else { await selected.getByRole('button', { name: 'Close agent details', exact: true }).click() }

      writeGalleryEvidence(current, `observation-continuity-${label}`)
    }
  }
})
