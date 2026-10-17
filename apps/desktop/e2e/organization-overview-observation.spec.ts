import type { Locator } from '@playwright/test'

import { verifyKeyboardFocus } from './focus-visibility'
import { acknowledgeExpectedErrorBanner, expect, test } from './test'
import { captureGallery, type GalleryFixture, persistGalleryEvidence, resizeGallery, setupGallery, writeGalleryEvidence } from './workspace-gallery-fixture'

let fixture: GalleryFixture | undefined
test.setTimeout(300_000)
test.afterEach(async () => {
  try { if (fixture) { persistGalleryEvidence(fixture, 'overview-observation') } }
  finally { await fixture?.cleanup(); fixture = undefined }
})

const routes = ['/home', '/work', '/messages', '/activity'] as const
// Native expectations deliberately avoid importing the renderer's catalog and stores.
// Activity currently inherits the English foundation catalog in all three locales.
const cases = [
  { locale: 'en', zoom: 1, loading: 'Loading organization data', unavailable: 'Organization data unavailable', stale: 'Showing last-known data. The current state is not confirmed.', empty: 'No records in the last received state', noWork: 'No open objectives in this view.', noRecipients: 'No recipients available', noEvents: 'No organization events yet', retry: 'Retry connection', homeEmpty: ['No pending owner requests in this view.', 'No accepted deliverables in this view yet.', 'No activity recorded in this view yet.'] },
  { locale: 'ja', zoom: 1.25, loading: '組織データを読み込み中', unavailable: '組織データを取得できません', stale: '最後に受信したデータを表示しています。現在の状態は未確認です。', empty: '最後に受信した状態に記録はありません', noWork: 'この表示に未完了の目標はありません。', noRecipients: '利用可能な宛先がありません', noEvents: 'No organization events yet', retry: '接続を再試行', homeEmpty: ['対応待ちの所有者への依頼はありません。', '承認済みの成果物はまだありません。', '活動はまだ記録されていません。'] },
  { locale: 'ar', zoom: 2, loading: 'جارٍ تحميل بيانات المؤسسة', unavailable: 'بيانات المؤسسة غير متاحة', stale: 'تُعرض آخر بيانات معروفة. لم يتم تأكيد الحالة الحالية.', empty: 'لا توجد سجلات في آخر حالة مستلمة', noWork: 'لا توجد أهداف مفتوحة في هذا العرض.', noRecipients: 'لا يوجد مستلمون متاحون', noEvents: 'No organization events yet', retry: 'إعادة محاولة الاتصال', homeEmpty: ['لا توجد طلبات معلّقة للمالك في هذا العرض.', 'لا توجد مخرجات مقبولة في هذا العرض بعد.', 'لم يُسجل أي نشاط في هذا العرض بعد.'] }
] as const
const reads = (current: GalleryFixture) => current.methods.filter(method => method === 'organization.observe' || method === 'organization.snapshot').length

async function reachRetry(current: GalleryFixture, workspace: Locator, retry: Locator, route: typeof routes[number], label: string) {
  const context = route === '/home' ? workspace.locator('.eid-home-panel-body').first()
    : route === '/messages' ? workspace.locator('.eid-message-search input')
    : workspace.locator('.eid-activity-filters input')
  // Begin with a real click in the empty view, then traverse its existing tab order.
  // Nothing focuses or scrolls Retry until keyboard reachability has been established.
  await context.scrollIntoViewIfNeeded()
  await context.click()
  await expect(context).toBeFocused()
  let steps = 0
  for (; steps < 20; steps++) {
    await current.page.keyboard.press('Shift+Tab')
    if (await retry.evaluate(element => element === document.activeElement)) { break }
  }
  expect(steps, `${label}: Retry is reachable within the bounded tab order`).toBeLessThan(20)
  await expect(retry).toBeFocused()
  await expect(retry).toBeInViewport({ ratio: 1 })
  expect(await retry.evaluate(element => element.matches(':focus-visible'))).toBe(true)
  // The shared helper now measures the full ring, clipping ancestors and contrast.
  const focus = await verifyKeyboardFocus(current.page, retry, label)
  current.focusChecks.push({ ...focus, context: await context.getAttribute('class'), keyboard: 'Shift+Tab', steps: steps + 1 })
  await captureGallery(current, label, workspace)
}

for (const copy of cases) {
  const { locale, zoom } = copy
  test(`overview observations remain truthful through loading, error and Retry in ${locale}`, async () => {
    const current = fixture = await setupGallery(locale, false, false, { blockOrganizationMutations: true })
    const { page, observationControl, state } = current
    Object.assign(state.snapshot, { objectives: [], agents: [], tasks: [], requests: [], activity: [], knowledge: [] })
    state.snapshot.outcomes = { ...state.snapshot.outcomes, total: 0, unread: 0, hasMore: false, nextCursor: null, items: [] }
    state.snapshot.runtime.rosterCount = 0
    state.snapshot.runtime.workingCount = 0

    for (const route of routes) {
      const name = route.slice(1)
      const empty = route === '/messages' ? copy.noRecipients : route === '/activity' ? copy.noEvents : copy.noWork
      state.observationError = null
      await resizeGallery(current, 1280, 800, zoom)
      await page.evaluate(path => { location.hash = path }, route)
      observationControl.pause()
      await page.reload()
      await expect.poll(() => observationControl.pending).toBeGreaterThan(0)
      const workspace = page.locator('.eid-workspace').last()
      await expect(workspace.getByText(copy.loading, { exact: true }).first()).toBeVisible()
      await expect(workspace.getByText(empty, { exact: true })).toHaveCount(0)
      if (route === '/home') {
        for (const text of copy.homeEmpty) {
          await expect(workspace.getByText(text, { exact: true })).toHaveCount(0)
        }
      }
      await captureGallery(current, `${locale}-${name}-loading-${zoom * 100}`, workspace)

      const error = `Synthetic ${name} observation unavailable. ${'Keep this view and use Retry to request the current organization state. '.repeat(8)}`.trim()
      state.observationError = error
      observationControl.release()
      const status = workspace.locator('.eid-runtime-status')
      await expect(status.getByRole('alert')).toHaveText(error)
      await expect(workspace.getByText(copy.unavailable, { exact: true }).first()).toBeVisible()
      await expect(workspace.getByText(empty, { exact: true })).toHaveCount(0)
      await captureGallery(current, `${locale}-${name}-initial-error-${zoom * 100}`, workspace)
      await resizeGallery(current, 400, 620)
      await status.getByRole('alert').scrollIntoViewIfNeeded()
      await captureGallery(current, `${locale}-${name}-initial-error-narrow`, workspace)

      observationControl.pause()
      const retry = status.getByRole('button', { name: copy.retry, exact: true })
      await reachRetry(current, workspace, retry, route, `${locale}-${name}-retry-keyboard-narrow`)
      await page.keyboard.press('Enter')
      await expect.poll(() => observationControl.pending).toBe(1)
      const during = reads(current)
      await page.keyboard.press('Enter')
      await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
      expect(reads(current)).toBe(during)
      await expect(workspace.getByText(empty, { exact: true })).toHaveCount(0)
      state.observationError = null
      observationControl.release()
      await expect(status).toHaveCount(0)
      await expect(workspace.getByText(empty, { exact: true })).toBeVisible()
      await acknowledgeExpectedErrorBanner(page, error)
      await expect(page).toHaveURL(new RegExp(`#${route}$`))

      observationControl.pause()
      await expect.poll(() => observationControl.pending, { timeout: 20_000 }).toBe(1)
      const retainedError = `Synthetic ${name} interruption after confirmed empty.`
      state.observationError = retainedError
      observationControl.release()
      await expect(status.getByRole('alert')).toHaveText(retainedError)
      await expect(workspace.getByText(copy.stale, { exact: true }).first()).toBeVisible()
      await expect(workspace.getByText(copy.empty, { exact: true }).first()).toBeVisible()
      await expect(workspace.getByText(empty, { exact: true })).toHaveCount(0)
      await captureGallery(current, `${locale}-${name}-retained-empty-narrow`, workspace)
      observationControl.pause()
      await status.getByRole('button', { name: copy.retry, exact: true }).click()
      await expect.poll(() => observationControl.pending).toBe(1)
      state.observationError = null
      observationControl.release()
      await expect(status).toHaveCount(0)
      await expect(workspace.getByText(empty, { exact: true })).toBeVisible()
      await acknowledgeExpectedErrorBanner(page, retainedError)
    }
    writeGalleryEvidence(current, `overview-observation-${locale}`)
  })
}

test('retained Home drafts, member selection and activity filters survive read-only recovery', async () => {
  const current = fixture = await setupGallery('en', false, false, { blockOrganizationMutations: true })
  const { page, observationControl, state } = current
  for (const route of ['/home', '/messages', '/work'] as const) {
    await resizeGallery(current, 1280, 800)
    await page.evaluate(path => { location.hash = path }, route)
    const workspace = page.locator('.eid-workspace').last()
    const field = route === '/home' ? workspace.getByRole('textbox', { name: 'Objective', exact: true })
      : route === '/messages' ? workspace.getByRole('textbox', { name: 'Search members' })
      : workspace.getByRole('searchbox', { name: 'Search activity' })
    const value = route === '/home' ? 'Keep this unsubmitted objective' : route === '/messages' ? 'Morgan' : state.snapshot.activity[0].text
    if (route === '/home') { await workspace.getByRole('button', { name: 'New objective', exact: true }).click() }
    await field.fill(value)
    await field.evaluate(element => element.setAttribute('data-overview-retained-field', 'true'))
    if (route === '/messages') {
      await workspace.getByRole('button', { name: /Morgan/ }).click()
      await workspace.locator('.eid-message-conversation').evaluate(element => element.setAttribute('data-overview-retained-selection', 'true'))
    }
    if (route === '/work') {
      await workspace.getByRole('button', { name: `Inspect event: ${value}`, exact: true }).click()
      await page.getByRole('complementary', { name: 'Event details' }).evaluate(element => element.setAttribute('data-overview-retained-selection', 'true'))
    }
    const url = page.url()
    observationControl.pause()
    await expect.poll(() => observationControl.pending, { timeout: 20_000 }).toBe(1)
    const error = `Synthetic retained ${route} read interrupted.`
    state.observationError = error
    observationControl.release()
    const status = workspace.locator('.eid-runtime-status')
    await expect(status.getByRole('alert')).toHaveText(error)
    await expect(workspace.getByText(cases[0].stale, { exact: true }).first()).toBeVisible()
    await expect(field).toHaveValue(value)
    await expect(field).toHaveAttribute('data-overview-retained-field', 'true')
    if (route !== '/home') { await expect(page.locator('[data-overview-retained-selection]')).toHaveCount(1) }
    await resizeGallery(current, 400, 620)
    await status.scrollIntoViewIfNeeded()
    await captureGallery(current, `en-${route.slice(1)}-retained-context-narrow`, workspace)
    observationControl.pause()
    await status.getByRole('button', { name: 'Retry connection', exact: true }).click()
    await expect.poll(() => observationControl.pending).toBe(1)
    state.observationError = null
    observationControl.release()
    await expect(status).toHaveCount(0)
    await expect(field).toHaveValue(value)
    await expect(field).toHaveAttribute('data-overview-retained-field', 'true')
    if (route !== '/home') { await expect(page.locator('[data-overview-retained-selection]')).toHaveCount(1) }
    await expect(page).toHaveURL(url)
    await acknowledgeExpectedErrorBanner(page, error)
    if (route === '/work') { await page.keyboard.press('Escape') }
  }
  writeGalleryEvidence(current, 'overview-retained-continuity')
})
