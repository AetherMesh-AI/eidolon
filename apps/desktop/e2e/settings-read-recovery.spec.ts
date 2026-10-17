import { writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'

import { verifyKeyboardFocus } from './focus-visibility'
import { acknowledgeExpectedErrorBanner, expect, test } from './test'
import { captureGallery, type GalleryFixture, galleryLocales, persistGalleryEvidence, positionGalleryAtStart, resizeGallery, setGalleryAppearance, setupGallery, writeGalleryEvidence } from './workspace-gallery-fixture'

type Dataset = 'env' | 'accounts' | 'archives' | 'endpoints'
interface FaultResult { kind: 'error' | 'pending' | 'success'; data?: unknown }
interface ReadEvidence { path: string; method: string; profile: string | null; connectionId: string | null; archived?: string | null }
interface FaultController {
  set: (path: string, result: FaultResult) => void
  release: (path: string, result: FaultResult) => number
  pending: (path: string) => number
  evidence: { reads: ReadEvidence[]; writes: ReadEvidence[] }
}

const samples = createRequire(import.meta.url)('./settings-read-state-bootstrap.cjs') as {
  empty: Record<Dataset, unknown>; populated: Record<Dataset, unknown>; model: { options: unknown }
}

const surfaces = [
  { id: 'tools', route: 'keys', path: '/api/env', dataset: 'env', populated: 'SYNTHETIC TOOL', draft: '#credential-key-SYNTHETIC_TOOL_API_KEY input' },
  { id: 'provider-keys', route: 'providers&pview=keys', path: '/api/env', dataset: 'env', populated: 'Synthetic Provider', draft: 'input[placeholder*="Synthetic Provider"]' },
  { id: 'accounts', route: 'providers&pview=accounts', path: '/api/providers/oauth', dataset: 'accounts', populated: 'Synthetic connected account' },
  { id: 'archives', route: 'sessions', path: '/api/profiles/sessions', dataset: 'archives', populated: 'Synthetic archived conversation' },
  { id: 'endpoints', route: 'providers&pview=custom-endpoints', path: '/api/providers/custom-endpoints', dataset: 'endpoints', populated: 'Synthetic endpoint', draft: 'input[placeholder="Axet Proxy"]' }
] as const

const sizes = [
  { width: 1536, height: 961, zoom: 1 },
  { width: 1280, height: 720, zoom: 1.25 },
  { width: 1280, height: 720, zoom: 2 },
  { width: 400, height: 620, zoom: 1 }
] as const

const copy = {
  en: {
    refresh: 'Refresh', accounts: 'Connect an account',
    empty: ['Nothing configured in this category yet.', 'No provider API keys available.', 'Nothing archived', 'No custom endpoints'],
    error: { env: 'API keys failed to load', accounts: 'Could not load connected accounts', archives: 'Could not load archived sessions', endpoints: 'Could not load custom endpoints' },
    stale: 'Showing the last loaded data. These values may be out of date.',
    incomplete: 'Some profiles could not be read. Available archives are shown; retained rows may be out of date.'
  },
  ru: {
    refresh: 'Обновить', accounts: 'Подключить аккаунт',
    empty: ['В этой категории пока ничего не настроено.', 'API-ключи провайдеров недоступны.', 'Архив пуст', 'Нет пользовательских эндпоинтов'],
    error: { env: 'Не удалось загрузить API-ключи', accounts: 'Не удалось загрузить подключённые аккаунты', archives: 'Не удалось загрузить архивные сеансы', endpoints: 'Не удалось загрузить пользовательские эндпоинты' },
    stale: 'Показаны последние загруженные данные. Эти значения могут быть устаревшими.',
    incomplete: 'Не удалось прочитать некоторые профили. Показаны доступные архивы; сохранённые строки могут быть устаревшими.'
  },
  ar: {
    refresh: 'تحديث', accounts: 'ربط حساب',
    empty: ['لا يوجد شيء مضبوط في هذه الفئة بعد.', 'لا توجد مفاتيح API متاحة للمزودين.', 'لا توجد محادثات مؤرشفة', 'لا توجد نقاط اتصال مخصصة'],
    error: { env: 'فشل تحميل مفاتيح API', accounts: 'تعذر تحميل الحسابات المتصلة', archives: 'تعذر تحميل الجلسات المؤرشفة', endpoints: 'تعذر تحميل نقاط الاتصال المخصصة' },
    stale: 'تُعرض آخر بيانات تم تحميلها. قد تكون هذه القيم قديمة.',
    incomplete: 'تعذرت قراءة بعض الملفات الشخصية. تُعرض المحادثات المؤرشفة المتاحة، وقد تكون الصفوف المحتفظ بها قديمة.'
  }
}

let fixture: GalleryFixture | undefined
test.setTimeout(420_000)
test.afterEach(async () => {
  try {
    if (fixture) {
      persistGalleryEvidence(fixture, 'settings-read-recovery')
      const evidence = await fixture.app.evaluate(() => (globalThis as unknown as { __eidolonReadFaults: FaultController }).__eidolonReadFaults.evidence)
      writeFileSync(test.info().outputPath('settings-read-evidence.json'), JSON.stringify(evidence, null, 2))
    }
  } finally {
    await fixture?.cleanup()
    fixture = undefined
  }
})

async function fault(current: GalleryFixture, path: string, result: FaultResult, release = false) {
  await current.app.evaluate((_, args) => {
    const control = (globalThis as unknown as { __eidolonReadFaults: FaultController }).__eidolonReadFaults
    control.set(args.path, args.result)

    if (args.release) {control.release(args.path, args.result)}
  }, { path, result, release })
}

async function reads(current: GalleryFixture, path: string) {
  return current.app.evaluate((_, target) => (globalThis as unknown as { __eidolonReadFaults: FaultController }).__eidolonReadFaults.evidence.reads
    .filter(read => read.path === target && (target !== '/api/profiles/sessions' || read.archived === 'only')).length, path)
}

for (const locale of ['en', 'ru', 'ar'] as const) {
  for (const mode of ['dark', 'light'] as const) {
    test(`Settings read failures recover safely in ${locale} ${mode}`, async () => {
      const current = fixture = await setupGallery(locale, false, false, { bootstrap: 'settings-read-state-bootstrap.cjs' })
      const { page } = current
      await setGalleryAppearance(current, mode, galleryLocales.find(entry => entry.locale === locale)![mode])

      for (const size of sizes) {
        await resizeGallery(current, size.width, size.height, size.zoom)

        for (const surface of surfaces) {
          await page.evaluate(() => { location.hash = '/settings?tab=about' })
          // A real destination switch unmounts the prior reader; no fixture
          // code replaces the React state or calls an implementation hook.
          await expect(page.locator('.eid-settings-controls [data-settings-read-state]')).toHaveCount(0)

          for (const sibling of surfaces) {await fault(current, sibling.path, { kind: 'success', data: samples.empty[sibling.dataset] })}
          await fault(current, surface.path, { kind: 'pending' })
          await page.evaluate(route => { location.hash = `/settings?tab=${route}` }, surface.route)
          const scope = page.locator('.eid-settings-page')
          const editor = scope.locator('.eid-settings-controls')
          const label = `${surface.id}-${locale}-${mode}-${size.width}-${size.zoom * 100}`
          await expect.poll(() => editor.locator('[data-slot="skeleton"]').count()).toBeGreaterThan(0)
          await expect.poll(() => current.app.evaluate((_, path) => (globalThis as unknown as { __eidolonReadFaults: FaultController }).__eidolonReadFaults.pending(path), surface.path)).toBeGreaterThan(0)
          await positionGalleryAtStart(scope)
          await captureGallery(current, `${label}-loading`, scope)

          await fault(current, surface.path, { kind: 'error' }, true)
          const status = editor.locator('[data-settings-read-state]')
          await expect(status).toHaveAttribute('data-settings-read-state', 'error')
          await expect(editor.locator('[data-slot="skeleton"]')).toHaveCount(0)
          const errorText = copy[locale].error[surface.dataset]
          await expect(editor.getByRole('alert')).toHaveText(errorText)

          for (const empty of copy[locale].empty) {await expect(editor.getByText(empty, { exact: true })).toHaveCount(0)}
          await expect(editor.locator('input')).toHaveCount(0)
          await expect(editor).not.toContainText('Synthetic read unavailable')
          await captureGallery(current, `${label}-error`, scope)
          const refresh = status.getByRole('button', { name: copy[locale].refresh, exact: true })
          current.focusChecks.push(await verifyKeyboardFocus(page, refresh, `${label} Refresh`))
          await expect(refresh).toBeInViewport({ ratio: 1 })
          await captureGallery(current, `${label}-focused-refresh`, scope)

          const beforeRetry = await reads(current, surface.path)
          await fault(current, surface.path, { kind: 'pending' })
          await page.keyboard.press('Enter')
          await page.keyboard.press('Enter')
          await expect(refresh).toBeDisabled()
          await expect.poll(() => reads(current, surface.path)).toBe(beforeRetry + 1)
          await fault(current, surface.path, { kind: 'error' }, true)
          await expect(refresh).toBeEnabled()
          expect(await reads(current, surface.path)).toBe(beforeRetry + 1)
          await expect(status).toHaveAttribute('data-settings-read-state', 'error')

          for (const empty of copy[locale].empty) {await expect(editor.getByText(empty, { exact: true })).toHaveCount(0)}

          await fault(current, surface.path, { kind: 'success', data: samples.empty[surface.dataset] })
          await refresh.click()
          await expect(status).toHaveAttribute('data-settings-read-state', 'ready')
          await expect(editor.getByRole('alert')).toHaveCount(0)
          await acknowledgeExpectedErrorBanner(page, errorText)
          const emptyIndex = { tools: 0, 'provider-keys': 1, archives: 2, endpoints: 3 }

          if (surface.id !== 'accounts') {await expect(editor.getByText(copy[locale].empty[emptyIndex[surface.id]], { exact: true })).toBeVisible()}
          else {
            await expect(editor.getByText(copy[locale].accounts, { exact: true })).toBeVisible()
            await expect(editor.getByText('Synthetic connected account', { exact: true })).toHaveCount(0)
          }

          await positionGalleryAtStart(scope)
          await captureGallery(current, `${label}-recovered-empty`, scope)

          await fault(current, surface.path, { kind: 'success', data: samples.populated[surface.dataset] })
          await refresh.click()
          await expect(editor.getByText(surface.populated, { exact: true })).toBeVisible()
          const draft = 'draft' in surface ? editor.locator(surface.draft) : null

          if (draft) {await draft.fill('Synthetic unsaved draft survives refresh')}
          await positionGalleryAtStart(scope)
          await captureGallery(current, `${label}-recovered-populated`, scope)

          if (surface.id === 'archives') {
            await fault(current, surface.path, { kind: 'success', data: {
              ...samples.empty.archives as object,
              errors: [{ profile: 'default', error: 'Synthetic unread profile detail must stay private' }]
            } })
            await refresh.click()
            await expect(status).toHaveAttribute('data-settings-read-state', 'stale')
            const incompleteText = errorText + copy[locale].incomplete
            await expect(editor.getByRole('alert')).toHaveText(incompleteText)
            await expect(editor.getByText(surface.populated, { exact: true })).toBeVisible()
            await expect(editor).not.toContainText('Synthetic unread profile detail')
            await expect(editor.getByText(copy[locale].empty[2], { exact: true })).toHaveCount(0)
            await captureGallery(current, `${label}-incomplete-archives`, scope)
            await fault(current, surface.path, { kind: 'success', data: samples.populated[surface.dataset] })
            await refresh.click()
            await expect(status).toHaveAttribute('data-settings-read-state', 'ready')
            await acknowledgeExpectedErrorBanner(page, incompleteText)
          }

          await fault(current, surface.path, { kind: 'error' })
          await refresh.click()
          await expect(status).toHaveAttribute('data-settings-read-state', 'stale')
          const staleText = errorText + copy[locale].stale
          await expect(editor.getByRole('alert')).toHaveText(staleText)
          await expect(editor.getByText(surface.populated, { exact: true })).toBeVisible()

          if (draft) {await expect(draft).toHaveValue('Synthetic unsaved draft survives refresh')}
          await positionGalleryAtStart(scope)
          await captureGallery(current, `${label}-stale`, scope)
          await fault(current, surface.path, { kind: 'success', data: samples.populated[surface.dataset] })
          await refresh.click()
          await expect(status).toHaveAttribute('data-settings-read-state', 'ready')
          await acknowledgeExpectedErrorBanner(page, staleText)

          if (draft) {await expect(draft).toHaveValue('Synthetic unsaved draft survives refresh')}
        }
      }

      const evidence = await current.app.evaluate(() => (globalThis as unknown as { __eidolonReadFaults: FaultController }).__eidolonReadFaults.evidence)
      expect(evidence.writes).toEqual([])
      expect(current.mock.receivedPrompts).toHaveLength(0)
      writeGalleryEvidence(current, 'settings-read-recovery')
    })
  }
}

test('Model reads recover without replacing drafts or writing settings at every viewport', async () => {
  const current = fixture = await setupGallery('en', false, false, { bootstrap: 'settings-read-state-bootstrap.cjs' })
  // Locale setup reads display.language from the real synthetic profile config.
  // Guard shared-config writes only after setup, within this English Model case.
  await fault(current, '/api/config', { kind: 'success', data: { display: { language: 'en' } } })
  const { page } = current
  const path = '/api/model/options'
  const errorText = 'Could not load models'

  for (const size of sizes) {
    await resizeGallery(current, size.width, size.height, size.zoom)
    await page.evaluate(() => { location.hash = '/settings?tab=about' })
    await expect(page.locator('[data-section="config:model"]')).toHaveCount(0)
    await fault(current, path, { kind: 'pending' })
    await page.evaluate(() => { location.hash = '/settings?tab=config:model' })
    const scope = page.locator('.eid-settings-page')
    const editor = scope.locator('[data-section="config:model"] .eid-settings-card-body')
    const label = `model-recovery-${size.width}-${size.zoom * 100}`
    await expect(editor.locator('[data-slot="model-settings-skeleton"]')).toBeVisible()
    await expect.poll(() => current.app.evaluate((_, target) => (globalThis as unknown as { __eidolonReadFaults: FaultController }).__eidolonReadFaults.pending(target), path)).toBeGreaterThan(0)
    await captureGallery(current, `${label}-loading`, scope)
    await fault(current, path, { kind: 'error' }, true)
    const status = editor.locator('[data-settings-read-state]')
    await expect(status).toHaveAttribute('data-settings-read-state', 'error')
    await expect(editor.getByRole('alert')).toHaveText(errorText)
    await expect(editor.getByRole('combobox')).toHaveCount(0)
    await expect(editor.getByRole('button', { name: 'Apply', exact: true })).toHaveCount(0)
    await expect(editor.locator('[data-slot="skeleton"]')).toHaveCount(0)
    const refresh = status.getByRole('button', { name: 'Refresh', exact: true })
    current.focusChecks.push(await verifyKeyboardFocus(page, refresh, `${label} Refresh`))
    await expect(refresh).toBeInViewport({ ratio: 1 })
    await captureGallery(current, `${label}-unavailable-focus`, scope)
    const before = await reads(current, path)
    await fault(current, path, { kind: 'pending' })
    await page.keyboard.press('Enter')
    await page.keyboard.press('Enter')
    await expect(refresh).toBeDisabled()
    await expect.poll(() => reads(current, path)).toBe(before + 1)
    await fault(current, path, { kind: 'success', data: samples.model.options }, true)
    await expect(status).toHaveAttribute('data-settings-read-state', 'ready')
    expect(await reads(current, path)).toBe(before + 1)
    await acknowledgeExpectedErrorBanner(page, errorText)
    const model = editor.getByRole('combobox').nth(1)
    await model.click()
    await page.getByRole('option', { name: 'synthetic-unsaved', exact: true }).click()
    await expect(model).toContainText('synthetic-unsaved')
    await fault(current, path, { kind: 'error' })
    await refresh.click()
    await expect(status).toHaveAttribute('data-settings-read-state', 'stale')
    const staleText = errorText + copy.en.stale
    await expect(editor.getByRole('alert')).toHaveText(staleText)
    await expect(model).toContainText('synthetic-unsaved')
    await expect(editor).not.toContainText('Synthetic read unavailable')
    await positionGalleryAtStart(scope)
    await captureGallery(current, `${label}-retained-draft`, scope)
    await fault(current, path, { kind: 'success', data: samples.model.options })
    await refresh.click()
    await expect(status).toHaveAttribute('data-settings-read-state', 'ready')
    await expect(model).toContainText('synthetic-unsaved')
    await acknowledgeExpectedErrorBanner(page, staleText)
    await captureGallery(current, `${label}-recovered-draft`, scope)
  }

  const evidence = await current.app.evaluate(() => (globalThis as unknown as { __eidolonReadFaults: FaultController }).__eidolonReadFaults.evidence)
  expect(evidence.writes).toEqual([])
  expect(current.mock.receivedPrompts).toHaveLength(0)
  writeGalleryEvidence(current, 'settings-read-recovery')
})
