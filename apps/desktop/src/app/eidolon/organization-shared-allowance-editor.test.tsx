import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useSyncExternalStore } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { ownerPolicyCopy } from '@/i18n/organization-owner-policy'
import { sharedAllowanceCopy } from '@/i18n/organization-shared-allowance'
import type { Locale } from '@/i18n/types'

import { SharedAllowanceSettings } from './organization-shared-allowance'
import { sharedAllowanceCandidate } from './organization-shared-allowance-editor'
import type { SharedAllowancePreview } from './organization-shared-allowance-types'
import { deferred } from './owner-policy-test-support'
import {
  allowanceConfiguration,
  allowanceDescriptor,
  allowanceHarness,
  allowancePreview,
  memberId,
  memberOwner
} from './shared-allowance-test-support'
import { useOrganizationSharedAllowance } from './use-organization-shared-allowance'

vi.mock('./use-organization-shared-allowance', () => ({ useOrganizationSharedAllowance: vi.fn() }))
const copy = sharedAllowanceCopy.en
const common = ownerPolicyCopy.en
type Harness = Awaited<ReturnType<typeof allowanceHarness>>

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-14T12:00:00Z'))
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

function mount(h: Harness, locale: Locale = 'en') {
  vi.mocked(useOrganizationSharedAllowance).mockImplementation(function useTestAllowance() {
    const view = useSyncExternalStore(h.client.subscribe, h.client.getSnapshot)

    return {
      client: h.client,
      view,
      scope: h.scope,
      metadata: { descriptor: null, busy: false, error: null, intent: null },
      refresh: h.client.read
    }
  })

  const element = (
    <I18nProvider configClient={null} initialLocale={locale}>
      <SharedAllowanceSettings />
    </I18nProvider>
  )

  return { ...render(element), element }
}

function open() {
  fireEvent.click(screen.getByRole('button', { name: copy.edit }))

  return within(screen.getByRole('dialog', { name: copy.edit }))
}

async function review(h: Harness) {
  const dialog = open()
  const limits = within(dialog.getByRole('group', { name: copy.limits }))
  fireEvent.change(limits.getByLabelText(copy.calls), { target: { value: '25' } })
  fireEvent.click(dialog.getByRole('button', { name: common.preview }))
  await waitFor(() => expect(h.client.getSnapshot().preview).not.toBeNull())

  return dialog
}

it('shows native review, saved sticky enrollments and only writes after explicit Apply', async () => {
  const h = await allowanceHarness()
  mount(h)
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByRole('region', { name: copy.projection })).toBeTruthy()
  const dialog = await review(h)
  expect(dialog.getAllByRole('checkbox').every(input => input.matches(':disabled'))).toBe(true)
  expect(dialog.getByLabelText(copy.label).matches(':disabled')).toBe(true)
  const region = dialog.getByRole('region', { name: common.review })
  expect(region.textContent).toContain(copy.enrollmentRequiresIdle)
  expect(region.textContent).toContain(copy.noRollover)
  const reviewed = region.querySelectorAll('pre')
  expect(JSON.parse(reviewed[0].textContent!).limits.modelCalls).toBe(20)
  expect(JSON.parse(reviewed[1].textContent!).limits.modelCalls).toBe(25)
  expect(h.request.mock.calls.some(row => row[0] === 'organization.configureSharedAllowance')).toBe(false)
  fireEvent.click(dialog.getByRole('button', { name: common.apply }))
  await waitFor(() => expect(dialog.getByText(common.saved)).toBeTruthy())
  expect(h.client.getSnapshot().descriptor?.configuration?.limits.modelCalls).toBe(25)
  expect(dialog.getByRole('button', { name: common.apply }).matches(':disabled')).toBe(true)
})

it('retains draft through close and refresh, invalidates late previews, and closes when the scope changes', async () => {
  const h = await allowanceHarness()
  const mounted = mount(h)
  let dialog = open()
  fireEvent.change(within(dialog.getByRole('group', { name: copy.limits })).getByLabelText(copy.calls), {
    target: { value: '27' }
  })
  const pending = deferred<SharedAllowancePreview>()
  h.request.mockReturnValueOnce(pending.promise)
  fireEvent.click(dialog.getByRole('button', { name: common.preview }))
  const request = h.request.mock.calls.at(-1)![1]
  fireEvent.click(dialog.getByRole('button', { name: common.close }))
  await act(async () => {
    pending.resolve(allowancePreview(h.data, 'configuration', request as never))
    await pending.promise
  })
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(h.client.getSnapshot().preview).toBeNull()
  dialog = open()
  expect(
    (within(dialog.getByRole('group', { name: copy.limits })).getByLabelText(copy.calls) as HTMLInputElement).value
  ).toBe('27')
  const ownerKey = h.scope.ownerKey
  act(() => {
    Object.assign(h.scope, {
      ownerKey: 'other-profile',
      key: 'other-socket',
      profile: 'research',
      organizationId: memberId,
      ownerId: memberOwner
    })
    mounted.rerender(
      <I18nProvider configClient={null} initialLocale="en">
        <SharedAllowanceSettings />
      </I18nProvider>
    )
  })
  expect(screen.queryByRole('dialog')).toBeNull()
  Object.assign(h.scope, {
    ownerKey,
    key: 'returned-socket',
    profile: 'default',
    organizationId: h.data.organizationId,
    ownerId: h.data.ownerId
  })
  await act(async () => {
    await h.client.read()
  })
  mounted.rerender(
    <I18nProvider configClient={null} initialLocale="en">
      <SharedAllowanceSettings />
    </I18nProvider>
  )
  dialog = open()
  expect(
    (within(dialog.getByRole('group', { name: copy.limits })).getByLabelText(copy.calls) as HTMLInputElement).value
  ).toBe('27')
  expect(dialog.getByRole('button', { name: common.apply }).matches(':disabled')).toBe(true)
})

it('keeps uncertain saved changes locked across dismissal and reconciles exactly the original intent', async () => {
  const h = await allowanceHarness()
  mount(h)
  let dialog = await review(h)
  h.request.mockImplementationOnce(async (method, params) => {
    await h.perform(method, params)
    throw { code: 5075, data: { reason: 'unreadable_or_corrupt' } }
  })
  fireEvent.click(dialog.getByRole('button', { name: common.apply }))
  await waitFor(() => expect(h.client.getSnapshot().error).toBe('uncertain'))
  const original = h.request.mock.calls.at(-1)![1]
  expect(
    within(dialog.getByRole('group', { name: copy.limits }))
      .getByLabelText(copy.calls)
      .matches(':disabled')
  ).toBe(true)
  fireEvent.click(dialog.getByRole('button', { name: common.close }))
  fireEvent.click(screen.getByRole('button', { name: common.reconcile }))
  dialog = within(screen.getByRole('dialog'))
  fireEvent.click(dialog.getByRole('button', { name: common.reconcile }))
  await waitFor(() => expect(dialog.getByText(common.saved)).toBeTruthy())
  expect(
    h.request.mock.calls.filter(row => row[0] === 'organization.configureSharedAllowance').map(row => row[1])
  ).toEqual([original, original])
})

it('shows only the served member allocation and keeps unsupported gateways read-only', async () => {
  const member = allowanceDescriptor()
  Object.assign(member, {
    authority: 'member',
    configuration: null,
    pool: null,
    candidates: [],
    capabilities: { configuration: false, observation: false }
  })
  const h = await allowanceHarness(member)
  const first = mount(h)
  expect(screen.getByText(copy.readonly)).toBeTruthy()
  expect(screen.getByRole('region', { name: copy.allocation })).toBeTruthy()
  expect(screen.queryByRole('region', { name: copy.projection })).toBeNull()
  expect(screen.queryByRole('button', { name: copy.edit })).toBeNull()
  expect(screen.queryByText('Research organization')).toBeNull()
  first.unmount()
  const old = await allowanceHarness()
  old.request.mockRejectedValueOnce({ code: -32601 })
  await old.client.read()
  mount(old)
  expect(screen.getByText(copy.unsupported)).toBeTruthy()
  expect(screen.queryByRole('button', { name: copy.edit })).toBeNull()
})

it.each(['en', 'ja', 'zh', 'zh-hant', 'ar', 'ru'] as Locale[])(
  'renders initialized-profile enrollment and labeled controls in %s',
  async locale => {
    const h = await allowanceHarness(allowanceDescriptor(false))
    mount(h, locale)
    const translated = sharedAllowanceCopy[locale]
    fireEvent.click(screen.getByRole('button', { name: translated.edit }))
    const dialog = within(screen.getByRole('dialog', { name: translated.edit }))
    expect(dialog.getByLabelText(translated.label)).toBeTruthy()
    expect(dialog.getByLabelText(translated.startsAt)).toBeTruthy()
    expect(dialog.getByLabelText(translated.endsAt)).toBeTruthy()
    const select = dialog.getByRole('checkbox', { name: `${translated.enroll}: Research organization (research)` })
    expect(select.matches(':disabled')).toBe(false)
    fireEvent.click(select)
    expect(select.getAttribute('aria-checked')).toBe('true')
    const allocation = within(dialog.getByRole('group', { name: translated.allocation }))
    expect((allocation.getByLabelText(translated.calls) as HTMLInputElement).min).toBe('1')
    fireEvent.click(select)
    expect(dialog.queryByRole('group', { name: translated.allocation })).toBeNull()
    expect(dialog.getByText(translated.disclaimer)).toBeTruthy()
  }
)

it('rebases only edited fields while preserving fresh allocation changes and never re-enrolls removed stale members', () => {
  const base = allowanceConfiguration()
  const candidate = structuredClone(base)
  candidate.limits.modelCalls = 30
  const fresh = allowanceDescriptor()
  fresh.configuration!.limits.tokens = 25000
  fresh.configuration!.allocations[0].limits.tokens = 9000
  fresh.configuration!.allocations.pop()
  const result = sharedAllowanceCandidate({ base, candidate: { configuration: candidate } }, fresh).configuration!
  expect(result.limits).toMatchObject({ modelCalls: 30, tokens: 25000 })
  expect(result.allocations).toHaveLength(1)
  expect(result.allocations[0].limits.tokens).toBe(9000)
})

it('shows enforced stage-start spacing and its recorded next start without claiming a global inflight cap', async () => {
  const data = allowanceDescriptor()
  data.pacing = {
    policy: 'shared-stage-start-v1',
    enabled: true,
    scope: 'installation',
    mode: 'critical',
    remainingPercent: 8,
    reason: 'shared_headroom',
    minIntervalSeconds: 300,
    deferUntil: '2026-10-14T12:05:00Z',
    advisory: false,
    globalInflightLimit: null,
    limitation: 'stage_start_spacing_only_no_provider_quota_inference_or_automatic_refill'
  }
  const h = await allowanceHarness(data)
  mount(h)
  const region = screen.getByRole('region', { name: copy.pacing })
  expect(region.textContent).toContain(copy.critical)
  expect(region.textContent).toContain(`${copy.spacing}: 300`)
  expect(region.querySelector('time')?.getAttribute('datetime')).toBe(data.pacing.deferUntil)
  expect(region.textContent).toContain(copy.pacingNote)
})

it('explains how to restore a missing enrolled identity and never offers to remove its saved enrollment', async () => {
  const data = allowanceDescriptor()
  data.candidates = data.candidates.filter(candidate => candidate.profile !== 'research')
  const h = await allowanceHarness(data)
  mount(h)
  const dialog = open()
  expect(dialog.getByText(copy.enrollmentConflict)).toBeTruthy()
  expect(dialog.getByRole('button', { name: `${common.remove} research` }).matches(':disabled')).toBe(true)
  fireEvent.click(dialog.getByRole('button', { name: common.preview }))
  await waitFor(() => expect(h.client.getSnapshot().error).toBe('invalid'))
  expect(h.request).toHaveBeenCalledTimes(1)
})


it.each(['en', 'ar'] as Locale[])('isolates escaped JSON preview syntax from %s labels', async locale => {
  const h = await allowanceHarness()
  mount(h, locale)
  const labels = sharedAllowanceCopy[locale]
  const controls = ownerPolicyCopy[locale]
  fireEvent.click(screen.getByRole('button', { name: labels.edit }))
  const dialog = within(screen.getByRole('dialog', { name: labels.edit }))
  fireEvent.change(within(dialog.getByRole('group', { name: labels.limits })).getByLabelText(labels.calls), {
    target: { value: '25' }
  })
  fireEvent.click(dialog.getByRole('button', { name: controls.preview }))
  await waitFor(() => expect(h.client.getSnapshot().preview).not.toBeNull())
  const region = dialog.getByRole('region', { name: controls.review })
  const json = [...region.querySelectorAll('pre')]
  expect(json).toHaveLength(2)
  expect(json.every(block => block.getAttribute('dir') === 'ltr')).toBe(true)
  expect(JSON.parse(json[1].textContent!).limits.modelCalls).toBe(25)
  expect(region.textContent).toContain(controls.after)
  expect(h.request.mock.calls.some(row => row[0] === 'organization.configureSharedAllowance')).toBe(false)
})
