import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useSyncExternalStore } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { ownerPolicyCopy } from '@/i18n/organization-owner-policy'
import type { Locale } from '@/i18n/types'

import { OwnerPolicyEntry, OwnerPolicySettings } from './organization-owner-policy'
import { createOwnerPolicyClient } from './organization-owner-policy-client'
import { ownerPolicyCandidate, type OwnerPolicyTarget } from './organization-owner-policy-editor'
import type { OwnerPolicyDescriptor, OwnerPolicyRequest } from './organization-owner-policy-types'
import { deferred, policyDescriptor, policyHarness, previewResponse, receiptResponse } from './owner-policy-test-support'
import { useOrganizationOwnerPolicy } from './use-organization-owner-policy'

vi.mock('./use-organization-owner-policy', () => ({ useOrganizationOwnerPolicy: vi.fn() }))

type Harness = Awaited<ReturnType<typeof policyHarness>>
const copy = ownerPolicyCopy.en

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
})
afterEach(() => {cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals()})

function mount(h: Harness, target?: OwnerPolicyTarget, locale: Locale = 'en') {
  vi.mocked(useOrganizationOwnerPolicy).mockImplementation(function useTestOwnerPolicy() {
    const view = useSyncExternalStore(h.client.subscribe, h.client.getSnapshot)

    return { client: h.client, view, scope: h.scope, metadata: { descriptor: null, busy: false, error: null, intent: null }, refresh: h.client.read }
  })

  return render(<MemoryRouter><I18nProvider configClient={null} initialLocale={locale}>
    {target ? <OwnerPolicyEntry target={target} /> : <OwnerPolicySettings />}
  </I18nProvider></MemoryRouter>)
}

function openBudget() {
  fireEvent.click(screen.getByRole('button', { name: copy.editBudget }))

  return within(screen.getByRole('dialog', { name: copy.editBudget }))
}

function queuePreview(h: Harness, action: 'budgetPolicy' | 'modelRouting' | 'budgetObservation' = 'budgetPolicy') {
  h.request.mockImplementationOnce(async (_method, params: OwnerPolicyRequest) => previewResponse(h.client.getSnapshot().descriptor!, action, params))
}

async function previewBudget(h: Harness, value = '12') {
  const dialog = within(screen.getByRole('dialog'))
  fireEvent.change(dialog.getByLabelText(copy.max_model_calls), { target: { value } })
  queuePreview(h)
  fireEvent.click(dialog.getByRole('button', { name: copy.preview }))
  await waitFor(() => expect(h.client.getSnapshot().preview).not.toBeNull())

  return dialog
}

it('opens the settings modal, renders the exact reviewed diff and impact, and sends only an explicit Apply', async () => {
  const h = await policyHarness()
  mount(h)
  expect(screen.queryByRole('dialog')).toBeNull()
  const dialog = openBudget()
  expect(h.request).toHaveBeenCalledExactlyOnceWith('organization.ownerPolicy', { expectedOrganizationId: h.data.organizationId })
  expect((dialog.getByRole('button', { name: copy.apply }) as HTMLButtonElement).disabled).toBe(true)
  await previewBudget(h)
  expect(h.request.mock.calls.map(call => call[0])).toEqual(['organization.ownerPolicy', 'organization.previewBudgetPolicy'])
  expect(h.request.mock.calls[1][1]).toEqual({ expectedOrganizationId: h.data.organizationId, expectedGeneration: 3, sourceRevision: h.data.sourceRevision, configuration: { max_model_calls: 12 } })
  const review = dialog.getByRole('region', { name: copy.review })
  expect([...review.querySelectorAll('pre')].map(node => node.textContent)).toEqual(['20', '12'])
  expect(review.textContent).toContain(copy.max_model_calls)
  expect(review.textContent).toContain(copy.objectiveDefaults)
  expect(review.textContent).toContain(`${copy.activeObjectives}: 7`)
  expect(review.querySelector('time')?.getAttribute('datetime')).toBe('2026-10-14T12:00:00Z')

  for (const warning of ['no_topup', 'captured_reserves_unchanged', 'configured_prices_not_invoice'] as const) {expect(review.textContent).toContain(copy[warning])}
  expect(review.textContent).toContain(copy.notEstimated)
  expect(review.textContent).toContain(`${copy.running_attempts_fenced} 2`)
  const fresh = structuredClone(h.data)
  fresh.policyGeneration++
  fresh.sourceRevision = 'c'.repeat(64)
  fresh.configuration.max_model_calls = 12
  h.request.mockImplementationOnce(async () => receiptResponse(h.data, h.client.getSnapshot().intent!)).mockResolvedValueOnce(fresh)
  fireEvent.click(dialog.getByRole('button', { name: copy.apply }))
  await waitFor(() => expect(h.client.getSnapshot().descriptor?.policyGeneration).toBe(4))
  expect(h.request.mock.calls[2]).toEqual(['organization.configureBudgetPolicy', {
    expectedOrganizationId: h.data.organizationId, expectedGeneration: 3, sourceRevision: h.data.sourceRevision,
    configuration: { max_model_calls: 12 }, previewVersion: 'b'.repeat(64), confirmed: true, idempotencyKey: h.newKey.mock.results[0].value
  }])
  expect(dialog.getByText(copy.saved)).toBeTruthy()
  expect((dialog.getByLabelText(copy.max_model_calls) as HTMLInputElement).value).toBe('12')
  expect(h.values.size).toBe(0)
})

it.each(['settings', 'entry'] as const)('keeps the %s dialog and edited draft mounted while a refresh clears the descriptor', async surface => {
  const h = await policyHarness()
  mount(h, surface === 'entry' ? { kind: 'budget' } : undefined)
  const dialog = openBudget()
  fireEvent.change(dialog.getByLabelText(copy.max_model_calls), { target: { value: '13' } })
  const pending = deferred<OwnerPolicyDescriptor>()
  h.request.mockReturnValueOnce(pending.promise)
  fireEvent.click(dialog.getByRole('button', { name: copy.refresh }))
  expect(h.client.getSnapshot().descriptor).toBeNull()
  expect(screen.getByRole('dialog')).toBeTruthy()
  expect(dialog.getByLabelText(copy.max_model_calls).matches(':disabled')).toBe(true)
  expect((dialog.getByLabelText(copy.max_model_calls) as HTMLInputElement).value).toBe('13')
  const fresh = structuredClone(h.data)
  fresh.policyGeneration = 6
  fresh.sourceRevision = 'c'.repeat(64)
  fresh.configuration.max_total_tokens = 222000
  await act(async () => {pending.resolve(fresh); await pending.promise})
  expect(screen.getByRole('dialog')).toBeTruthy()
  queuePreview(h)
  fireEvent.click(dialog.getByRole('button', { name: copy.preview }))
  await waitFor(() => expect(h.client.getSnapshot().preview).not.toBeNull())
  expect(h.request.mock.calls.at(-1)?.[1]).toEqual({ expectedOrganizationId: fresh.organizationId, expectedGeneration: 6, sourceRevision: fresh.sourceRevision, configuration: { max_model_calls: 13 } })
})

it.each(['settings', 'entry'] as const)('keeps the %s editor mounted through post-apply read and resets from the fresh descriptor', async surface => {
  const h = await policyHarness()
  mount(h, surface === 'entry' ? { kind: 'budget' } : undefined)
  openBudget()
  const dialog = await previewBudget(h)
  const pending = deferred<OwnerPolicyDescriptor>()
  h.request.mockImplementationOnce(async () => receiptResponse(h.data, h.client.getSnapshot().intent!)).mockReturnValueOnce(pending.promise)
  fireEvent.click(dialog.getByRole('button', { name: copy.apply }))
  await waitFor(() => expect(h.request.mock.calls.at(-1)?.[0]).toBe('organization.ownerPolicy'))
  expect(h.client.getSnapshot().descriptor).toBeNull()
  expect(screen.getByRole('dialog')).toBeTruthy()
  expect(dialog.getByLabelText(copy.max_model_calls).matches(':disabled')).toBe(true)
  const fresh = structuredClone(h.data)
  fresh.policyGeneration = 4
  fresh.sourceRevision = 'c'.repeat(64)
  fresh.configuration.max_model_calls = 12
  fresh.configuration.max_total_tokens = 240000
  await act(async () => {pending.resolve(fresh); await pending.promise})
  await waitFor(() => expect((dialog.getByLabelText(copy.max_total_tokens) as HTMLInputElement).value).toBe('240000'))
  expect((dialog.getByLabelText(copy.max_model_calls) as HTMLInputElement).value).toBe('12')
  expect(dialog.getByText(copy.saved)).toBeTruthy()
  expect((dialog.getByRole('button', { name: copy.apply }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(dialog.getByLabelText(copy.max_model_calls), { target: { value: '14' } })
  queuePreview(h)
  fireEvent.click(dialog.getByRole('button', { name: copy.preview }))
  await waitFor(() => expect(h.client.getSnapshot().preview?.request.expectedGeneration).toBe(4))
  expect(h.request.mock.calls.at(-1)?.[1]).toMatchObject({ sourceRevision: fresh.sourceRevision, configuration: { max_model_calls: 14 } })
})

it('preserves draft and conflict across dismissal, refreshes authority, and requires another preview', async () => {
  const h = await policyHarness()
  mount(h)
  openBudget()
  const dialog = await previewBudget(h)
  h.request.mockRejectedValueOnce({ code: 5074, data: { reason: 'generation_conflict' } })
  fireEvent.click(dialog.getByRole('button', { name: copy.apply }))
  await waitFor(() => expect(dialog.getByRole('alert').textContent).toBe(copy.conflict))
  fireEvent.click(dialog.getByRole('button', { name: copy.close }))
  expect(screen.queryByRole('dialog')).toBeNull()
  const reopened = openBudget()
  expect((reopened.getByLabelText(copy.max_model_calls) as HTMLInputElement).value).toBe('12')
  expect(reopened.getByRole('alert').textContent).toBe(copy.conflict)
  expect((reopened.getByRole('button', { name: copy.preview }) as HTMLButtonElement).disabled).toBe(true)
  const fresh = { ...h.data, policyGeneration: 8, sourceRevision: 'c'.repeat(64) }
  h.request.mockResolvedValueOnce(fresh)
  fireEvent.click(reopened.getByRole('button', { name: copy.refresh }))
  await waitFor(() => expect(h.client.getSnapshot().descriptor?.policyGeneration).toBe(8))
  expect((reopened.getByLabelText(copy.max_model_calls) as HTMLInputElement).value).toBe('12')
  expect((reopened.getByRole('button', { name: copy.apply }) as HTMLButtonElement).disabled).toBe(true)
  queuePreview(h)
  fireEvent.click(reopened.getByRole('button', { name: copy.preview }))
  await waitFor(() => expect(h.client.getSnapshot().preview?.request.expectedGeneration).toBe(8))
  expect(h.request.mock.calls.filter(call => call[0] === 'organization.configureBudgetPolicy')).toHaveLength(1)
})

it('leaves deployment-managed fields read-only while unrelated budget edits remain available', async () => {
  const data = policyDescriptor()
  data.managedFields = ['max_model_calls', 'model_routes', 'model_assignments']
  const h = await policyHarness(data)
  mount(h)
  const dialog = openBudget()
  expect(dialog.getByLabelText(new RegExp(copy.max_model_calls)).matches(':disabled')).toBe(true)
  expect(dialog.getByLabelText(copy.max_total_tokens).matches(':disabled')).toBe(false)
  fireEvent.change(dialog.getByLabelText(copy.max_total_tokens), { target: { value: '90000' } })
  queuePreview(h)
  fireEvent.click(dialog.getByRole('button', { name: copy.preview }))
  await waitFor(() => expect(h.client.getSnapshot().preview).not.toBeNull())
  expect(h.request.mock.calls.at(-1)?.[1].configuration).toEqual({ max_total_tokens: 90000 })
  fireEvent.click(dialog.getByRole('button', { name: copy.close }))
  fireEvent.click(screen.getByRole('button', { name: copy.editRoutes }))
  const routes = within(screen.getByRole('dialog'))
  expect(routes.getAllByLabelText(copy.routeId).every(input => input.matches(':disabled'))).toBe(true)
  expect(routes.getByRole('button', { name: copy.addRoute }).matches(':disabled')).toBe(true)
})

it('restricts built-in executive to Fixed but permits a custom executive Auto with checkbox selection and explicit route ordering', async () => {
  const h = await policyHarness()
  const first = mount(h, { kind: 'assignment', agentId: 'executive' })
  fireEvent.click(screen.getByRole('button', { name: copy.editAssignment }))
  let dialog = within(screen.getByRole('dialog'))
  const builtinMode = dialog.getByRole('combobox', { name: copy.mode })
  expect((builtinMode as HTMLSelectElement).value).toBe('fixed')
  expect(within(builtinMode).queryByRole('option', { name: copy.auto })).toBeNull()
  first.unmount()
  mount(h, { kind: 'assignment', agentId: 'custom-executive' })
  fireEvent.click(screen.getByRole('button', { name: copy.editAssignment }))
  dialog = within(screen.getByRole('dialog'))
  expect((dialog.getByRole('combobox', { name: copy.mode }) as HTMLSelectElement).value).toBe('auto')
  expect(dialog.getByRole('option', { name: copy.auto })).toBeTruthy()
  const fallback = dialog.getByRole('checkbox', { name: /fallback · configured-provider/ })
  expect(fallback.getAttribute('aria-checked')).toBe('false')
  fireEvent.click(fallback)
  expect(fallback.getAttribute('aria-checked')).toBe('true')
  fireEvent.click(dialog.getByRole('button', { name: `${copy.moveUp} fallback` }))
  fireEvent.change(dialog.getByLabelText(copy.fallbacks), { target: { value: '1' } })
  queuePreview(h, 'modelRouting')
  fireEvent.click(dialog.getByRole('button', { name: copy.preview }))
  await waitFor(() => expect(h.client.getSnapshot().preview).not.toBeNull())
  const candidate = h.request.mock.calls.at(-1)?.[1].configuration
  expect(candidate.model_routes).toEqual(h.data.configuration.model_routes)
  expect(candidate.model_assignments).toEqual([
    h.data.configuration.model_assignments[0],
    { agent_id: 'custom-executive', mode: 'auto', route_ids: ['fallback', 'primary'], minimum_evidence: 1, max_fallbacks: 1 }
  ])
})

it('uses configured route request-type checkboxes and invalidates a prior preview on editing', async () => {
  const h = await policyHarness()
  mount(h)
  fireEvent.click(screen.getByRole('button', { name: copy.editRoutes }))
  const dialog = within(screen.getByRole('dialog'))
  fireEvent.click(dialog.getAllByRole('checkbox', { name: 'work.analyze' })[0])
  queuePreview(h, 'modelRouting')
  fireEvent.click(dialog.getByRole('button', { name: copy.preview }))
  await waitFor(() => expect(h.client.getSnapshot().preview).not.toBeNull())
  const candidate = h.request.mock.calls.at(-1)?.[1].configuration
  expect(candidate.model_routes[0].request_types).toEqual(['work.draft', 'request.message', 'work.analyze'])
  expect(candidate.model_assignments).toEqual(h.data.configuration.model_assignments)
  fireEvent.change(dialog.getAllByLabelText(copy.model)[0], { target: { value: 'different-model' } })
  expect(h.client.getSnapshot().preview).toBeNull()
  expect((dialog.getByRole('button', { name: copy.apply }) as HTMLButtonElement).disabled).toBe(true)
  expect(h.request.mock.calls.map(call => call[0])).toEqual(['organization.ownerPolicy', 'organization.previewModelRouting'])
})

it.each(['verified', 'mismatched', 'lost'] as const)('retains sent intent across Close and handles a %s acknowledgement without an automatic retry', async outcome => {
  const h = await policyHarness()
  mount(h)
  openBudget()
  const dialog = await previewBudget(h)
  const pending = deferred<unknown>()
  h.request.mockReturnValueOnce(pending.promise)
  fireEvent.click(dialog.getByRole('button', { name: copy.apply }))
  const intent = structuredClone(h.client.getSnapshot().intent!)
  const originalParams = structuredClone(h.request.mock.calls.at(-1)?.[1])
  expect(h.values.size).toBe(1)
  fireEvent.click(dialog.getByRole('button', { name: copy.close }))
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(h.client.getSnapshot().intent).toEqual(intent)
  const receipt = receiptResponse(h.data, intent)
  const fresh = { ...h.data, policyGeneration: 4, sourceRevision: 'c'.repeat(64), configuration: { ...h.data.configuration, max_model_calls: 12 } }

  if (outcome === 'verified') {h.request.mockResolvedValueOnce(fresh)}
  await act(async () => {
    if (outcome === 'lost') {pending.reject(new Error('connection lost'))}
    else {pending.resolve(outcome === 'mismatched' ? { ...receipt, receipt: { ...receipt.receipt, idempotencyKey: 'different-intent' } } : receipt)}

    await pending.promise.catch(() => undefined)
  })

  if (outcome === 'verified') {
    await waitFor(() => expect(h.client.getSnapshot().descriptor?.policyGeneration).toBe(4))
    expect(h.client.getSnapshot().intent).toBeNull()
    expect(h.values.size).toBe(0)
    const reopened = openBudget()
    expect((reopened.getByLabelText(copy.max_model_calls) as HTMLInputElement).value).toBe('12')
    expect(reopened.getByText(copy.saved)).toBeTruthy()
  } else {
    await waitFor(() => expect(h.client.getSnapshot().error).toBe('uncertain'))
    const reopened = openBudget()
    expect(reopened.getByRole('alert').textContent).toBe(copy.uncertain)
    expect(reopened.getByLabelText(copy.max_model_calls).matches(':disabled')).toBe(true)
    expect(h.client.getSnapshot().receipt).toBeNull()
    expect(h.values.size).toBe(1)
    expect(h.request.mock.calls.filter(call => call[0] === 'organization.configureBudgetPolicy')).toHaveLength(1)
    h.request.mockResolvedValueOnce(receipt).mockResolvedValueOnce(fresh)
    fireEvent.click(reopened.getByRole('button', { name: copy.reconcile }))
    await waitFor(() => expect(h.client.getSnapshot().descriptor?.policyGeneration).toBe(4))
    const sends = h.request.mock.calls.filter(call => call[0] === 'organization.configureBudgetPolicy')
    expect(sends).toHaveLength(2)
    expect(sends[1][1]).toEqual(originalParams)
    expect(h.newKey).toHaveBeenCalledTimes(1)
    expect(h.values.size).toBe(0)
    expect(reopened.getByText(copy.saved)).toBeTruthy()
  }
})

it.each(['en', 'ja', 'zh', 'zh-hant', 'ar', 'ru'] as const)('renders localized owner policy controls, safeguards and preview impact in %s', async locale => {
  const h = await policyHarness()
  const text = ownerPolicyCopy[locale]
  mount(h, undefined, locale)
  fireEvent.click(screen.getByRole('button', { name: text.editBudget }))
  const dialog = within(screen.getByRole('dialog', { name: text.editBudget }))
  expect(dialog.getByText(text.closeNote)).toBeTruthy()
  expect(dialog.getByText(text.budgetNote)).toBeTruthy()
  expect(dialog.getByText(text.separateNote)).toBeTruthy()
  fireEvent.change(dialog.getByLabelText(text.max_model_calls), { target: { value: '11' } })
  queuePreview(h)
  fireEvent.click(dialog.getByRole('button', { name: text.preview }))
  await waitFor(() => expect(h.client.getSnapshot().preview).not.toBeNull())
  const review = within(dialog.getByRole('region', { name: text.review }))
  expect(review.getByText(text.no_topup)).toBeTruthy()
  expect(review.getByText(text.captured_reserves_unchanged)).toBeTruthy()
  expect(review.getByText(text.configured_prices_not_invoice)).toBeTruthy()
  expect(review.getByText(text.notEstimated)).toBeTruthy()
  expect(review.getByText(`${text.running_attempts_fenced} 2`)).toBeTruthy()
  expect(dialog.getByRole('button', { name: text.apply })).toBeTruthy()
  expect(window.document.documentElement.dir).toBe(locale === 'ar' ? 'rtl' : 'ltr')
})


it('blocks invalid native number fields without previewing or applying and closes on Escape', async () => {
  const h = await policyHarness()
  mount(h)
  const trigger = screen.getByRole('button', { name: copy.editBudget })
  trigger.focus()
  const dialog = openBudget()
  const input = dialog.getByLabelText(copy.max_model_calls) as HTMLInputElement
  fireEvent.change(input, { target: { value: '0' } })
  expect(input.validity.rangeUnderflow).toBe(true)
  expect(input.form?.checkValidity()).toBe(false)
  fireEvent.click(dialog.getByRole('button', { name: copy.preview }))
  expect(h.request).toHaveBeenCalledTimes(1)
  expect(h.client.getSnapshot().preview).toBeNull()
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape', code: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  await waitFor(() => expect(window.document.activeElement).toBe(trigger))
  expect(h.request).toHaveBeenCalledTimes(1)
})

it('keeps unsupported runtimes read-only without offering an Edit entry or initializing state', async () => {
  const h = await policyHarness({ ...policyDescriptor(), state: 'unsupported', capabilities: { modelRouting: false, budgetPolicy: false, budgetObservation: false } })
  const settings = mount(h)
  expect(screen.getByRole('alert').textContent).toBe(copy.unsupported)
  expect(screen.queryByRole('button', { name: copy.editBudget })).toBeNull()
  expect(screen.queryByRole('button', { name: copy.editRoutes })).toBeNull()
  settings.unmount()
  mount(h, { kind: 'assignment', agentId: 'executive' })
  expect(screen.queryByRole('button')).toBeNull()
  expect(h.request).toHaveBeenCalledExactlyOnceWith('organization.ownerPolicy', { expectedOrganizationId: h.data.organizationId })
})

it('hides the previous owner editor on profile switch and opens a clean draft for the new owner', async () => {
  const h = await policyHarness()
  mount(h)
  const oldDialog = openBudget()
  fireEvent.change(oldDialog.getByLabelText(copy.max_model_calls), { target: { value: '9' } })
  const next = structuredClone(h.data)
  next.scope.profile = 'other-profile'
  next.organizationId = '33333333-3333-4333-8333-333333333333'
  next.ownerId = '44444444-4444-4444-8444-444444444444'
  next.configuration.max_model_calls = 40
  Object.assign(h.scope, { ownerKey: `${h.scope.ownerKey}-other`, key: 'other-socket', profile: next.scope.profile, organizationId: next.organizationId, ownerId: next.ownerId })
  h.request.mockResolvedValueOnce(next)
  await act(async () => {await h.client.read()})
  expect(screen.queryByRole('dialog')).toBeNull()
  const newDialog = openBudget()
  expect((newDialog.getByLabelText(copy.max_model_calls) as HTMLInputElement).value).toBe('40')
  expect((newDialog.getByRole('button', { name: copy.apply }) as HTMLButtonElement).disabled).toBe(true)
  expect(h.request.mock.calls.map(call => call[0])).toEqual(['organization.ownerPolicy', 'organization.ownerPolicy'])
})

it('previews an objective observation with exact revision and advisory-only warnings', async () => {
  const h = await policyHarness()
  mount(h, { kind: 'observation', objectiveId: 'objective-one' })
  fireEvent.click(screen.getByRole('button', { name: copy.editObservation }))
  const dialog = within(screen.getByRole('dialog'))
  fireEvent.change(dialog.getByLabelText(copy.remainingPercent), { target: { value: '18' } })
  fireEvent.change(dialog.getByLabelText(copy.asOf), { target: { value: '2026-10-14T12:00:00Z' } })
  fireEvent.change(dialog.getByLabelText(copy.expiresAt), { target: { value: '2026-10-14T13:00:00Z' } })
  queuePreview(h, 'budgetObservation')
  fireEvent.click(dialog.getByRole('button', { name: copy.preview }))
  await waitFor(() => expect(h.client.getSnapshot().preview).not.toBeNull())
  expect(h.request.mock.calls.at(-1)).toEqual(['organization.previewBudgetObservation', {
    expectedOrganizationId: h.data.organizationId, expectedGeneration: 3, sourceRevision: h.data.sourceRevision,
    observation: { objectiveId: 'objective-one', expectedRevision: 0, remainingPercent: 18, asOf: '2026-10-14T12:00:00Z', expiresAt: '2026-10-14T13:00:00Z', resetAt: null }
  }])
  const review = dialog.getByRole('region', { name: copy.review })
  expect(review.textContent).toContain(copy.objectiveAdvisory)
  expect(review.textContent).toContain(copy.objective_advisory_only)
  expect(review.textContent).toContain(copy.no_reset_or_refill)
  expect(h.request.mock.calls.filter(call => call[0] === 'organization.recordBudgetObservation')).toHaveLength(0)
})


it('rebases only the edited surface while preserving fresh hidden assignments, routes, and managed budget values', () => {
  const data = policyDescriptor()
  const { model_routes, model_assignments, ...budget } = data.configuration
  const originalRouting = { model_routes, model_assignments }
  const fresh = structuredClone(data)
  fresh.configuration.model_routes.push({ ...model_routes[0], id: 'new-route', model: 'fresh-model' })
  fresh.configuration.model_assignments[0].route_ids = ['new-route']
  fresh.configuration.model_assignments.push({ agent_id: 'new-agent', mode: 'auto', route_ids: ['new-route'], minimum_evidence: 1, max_fallbacks: 0 })
  fresh.configuration.max_total_tokens = 222000
  fresh.configuration.max_cost_usd = '9.00'
  fresh.managedFields = ['max_cost_usd']
  const assignmentDraft = { base: { configuration: structuredClone(originalRouting) }, candidate: { configuration: structuredClone(originalRouting) } }
  assignmentDraft.candidate.configuration.model_assignments[1].route_ids = ['fallback']
  const assignment = ownerPolicyCandidate(assignmentDraft, { kind: 'assignment', agentId: 'custom-executive' }, fresh)
  expect(assignment.configuration).toEqual({
    model_routes: fresh.configuration.model_routes,
    model_assignments: [fresh.configuration.model_assignments[0], fresh.configuration.model_assignments[2], { ...model_assignments[1], route_ids: ['fallback'] }]
  })
  const routeDraft = { base: { configuration: structuredClone(originalRouting) }, candidate: { configuration: structuredClone(originalRouting) } }
  routeDraft.candidate.configuration.model_routes[0].model = 'edited-model'
  expect(ownerPolicyCandidate(routeDraft, { kind: 'routes' }, fresh).configuration).toEqual({ model_routes: routeDraft.candidate.configuration.model_routes, model_assignments: fresh.configuration.model_assignments })
  const budgetDraft = { base: { configuration: structuredClone(budget) }, candidate: { configuration: { ...budget, max_model_calls: 12 } } }
  expect(ownerPolicyCandidate(budgetDraft, { kind: 'budget' }, fresh)).toEqual({ configuration: { max_model_calls: 12 } })
})

it.each(['settings', 'entry'] as const)('does not resurrect the %s modal on A→B→A while preserving the original draft', async surface => {
  const h = await policyHarness()
  const originalScope = { ...h.scope }
  mount(h, surface === 'entry' ? { kind: 'budget' } : undefined)
  const original = openBudget()
  fireEvent.change(original.getByLabelText(copy.max_model_calls), { target: { value: '9' } })
  Object.assign(h.scope, { ownerKey: `${originalScope.ownerKey}-other`, key: 'other-socket' })
  await act(async () => {await h.client.read()})
  expect(screen.queryByRole('dialog')).toBeNull()
  Object.assign(h.scope, originalScope)
  await act(async () => {await h.client.read()})
  expect(screen.queryByRole('dialog')).toBeNull()
  const restored = openBudget()
  expect((restored.getByLabelText(copy.max_model_calls) as HTMLInputElement).value).toBe('9')
  expect((restored.getByRole('button', { name: copy.apply }) as HTMLButtonElement).disabled).toBe(true)
})

it.each(['settings', 'entry'] as const)('dismisses the %s editor on verified identity replacement without reusing its old draft', async surface => {
  const h = await policyHarness()
  mount(h, surface === 'entry' ? { kind: 'budget' } : undefined)
  fireEvent.change(openBudget().getByLabelText(copy.max_model_calls), { target: { value: '9' } })
  const next = structuredClone(h.data)
  next.organizationId = '33333333-3333-4333-8333-333333333333'
  next.ownerId = '44444444-4444-4444-8444-444444444444'
  next.configuration.max_model_calls = 40
  Object.assign(h.scope, { organizationId: next.organizationId, ownerId: next.ownerId })
  h.request.mockResolvedValueOnce(next)
  await act(async () => {await h.client.read()})
  expect(screen.queryByRole('dialog')).toBeNull()
  expect((openBudget().getByLabelText(copy.max_model_calls) as HTMLInputElement).value).toBe('40')
})


it('shows loading before the first eligible owner-policy read, then ready or a genuine RPC failure', async () => {
  const h = await policyHarness()
  const client = createOwnerPolicyClient(() => h.scope, h.newKey)
  h.request.mockClear()
  mount({ ...h, client })
  expect(screen.getByRole('status').textContent).toBe(copy.loading)
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.queryByRole('button', { name: copy.editBudget })).toBeNull()
  expect(h.request).not.toHaveBeenCalled()

  const pending = deferred<OwnerPolicyDescriptor>()
  h.request.mockReturnValueOnce(pending.promise)
  let reading!: Promise<void>
  act(() => { reading = client.read() })
  expect(screen.getByRole('status').textContent).toBe(copy.loading)
  expect(screen.queryByRole('alert')).toBeNull()
  await act(async () => { pending.resolve(h.data); await reading })
  expect(screen.queryByRole('status')).toBeNull()
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByRole('button', { name: copy.editBudget })).toBeTruthy()

  h.request.mockRejectedValueOnce({ code: 5074, data: { reason: 'locked' } })
  await act(async () => { await client.read() })
  expect(screen.queryByRole('status')).toBeNull()
  expect(screen.getByRole('alert').textContent).toBe(copy.unavailable)
  expect(screen.queryByRole('button', { name: copy.editBudget })).toBeNull()
})
