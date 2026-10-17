import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'
import { ownerPolicyCopy } from '@/i18n/organization-owner-policy'
import { setupBudgetCopy } from '@/i18n/organization-team-setup-budget'
import type { Locale } from '@/i18n/types'

import { createTeamOnboardingClient, type TeamOnboardingClient, type TeamSetup } from './team-onboarding-client'
import type {
  SetupBudgetPolicy,
  SetupBudgetPreview,
  SetupBudgetReceipt,
  SetupBudgetState
} from './team-setup-budget-client'
import { TeamSetupBudgetEditor } from './team-setup-budget-editor'

const copy = setupBudgetCopy.en,
  common = ownerPolicyCopy.en

const policy: SetupBudgetPolicy = {
  version: 2,
  mode: 'bounded_api',
  improveCallLimit: 4,
  generationCallLimit: 3,
  totalCallLimit: 7
}

const setup: TeamSetup = {
  capabilityVersion: 1,
  setupBudgetVersion: 2,
  state: 'available',
  organizationId: 'org',
  sourceRevision: 'team-source',
  presets: [],
  readiness: { status: 'unchecked', blockers: [], modelRouting: 'unchanged', backgroundEnabled: false }
}

const state: SetupBudgetState = {
  capabilityVersion: 1,
  state: 'ready',
  organizationId: 'org',
  ownerId: 'owner',
  policyGeneration: 2,
  sourceRevision: 'a'.repeat(64),
  configuration: null,
  suggestedConfiguration: policy,
  managed: false,
  available: true,
  latestReceipt: null,
  setupBudget: {
    policyVersion: 1,
    policy: null,
    usage: { legacy: 2, improveTeamInput: 0, generateTeam: 0, total: 2 },
    limits: { improveCalls: 3, generationCalls: 3, totalCalls: 3, totalTokens: 12000, maxCostUsd: '1' },
    remaining: { improveCalls: 1, generationCalls: 1, totalCalls: 1 },
    status: 'available',
    reservedTokens: 8000,
    reservedCostUsd: '0.25'
  }
}

const previewFor = (configuration: SetupBudgetPolicy): SetupBudgetPreview => ({
  capabilityVersion: 1,
  organizationId: 'org',
  ownerId: 'owner',
  policyGeneration: 2,
  sourceRevision: state.sourceRevision,
  configuration,
  usage: state.setupBudget,
  previewVersion: 'b'.repeat(64),
  changes: [{ field: 'setup_budget', before: null, after: configuration }],
  warnings: [
    'existing_charges_retained',
    'legacy_calls_aggregate_only',
    'enclosing_call_token_cost_limits_retained',
    'no_calls_or_grants_created'
  ]
})

function activeState(configuration = policy, generation = 3): SetupBudgetState {
  const remaining = Math.max(0, configuration.totalCallLimit - state.setupBudget.usage.total)

  return {
    ...state,
    configuration,
    policyGeneration: generation,
    sourceRevision: 'c'.repeat(64),
    setupBudget: {
      ...state.setupBudget,
      policyVersion: 2,
      policy: configuration,
      limits: {
        ...state.setupBudget.limits,
        improveCalls: configuration.improveCallLimit,
        generationCalls: configuration.generationCallLimit,
        totalCalls: configuration.totalCallLimit
      },
      remaining: {
        improveCalls: Math.min(configuration.improveCallLimit, remaining),
        generationCalls: Math.min(configuration.generationCallLimit, remaining),
        totalCalls: remaining
      }
    },
    latestReceipt: {
      organizationId: state.organizationId,
      ownerId: state.ownerId,
      action: 'setupBudget',
      idempotencyKey: 'saved-change',
      policyGeneration: generation,
      sourceRevision: state.sourceRevision,
      previewVersion: 'b'.repeat(64),
      configuration
    }
  }
}

function harness(data: SetupBudgetState = state, initialSetup = setup, locale: Locale = 'en') {
  const client = {
    current: vi.fn(() => true),
    setup: vi.fn().mockResolvedValue(initialSetup),
    budget: vi.fn().mockResolvedValue(data),
    previewBudget: vi.fn().mockImplementation(async (_state, config) => previewFor(config)),
    applyBudget: vi.fn().mockImplementation(async (preview: SetupBudgetPreview, key: string) => ({
      capabilityVersion: 1,
      receipt: { ...preview, action: 'setupBudget', idempotencyKey: key, policyGeneration: 3 }
    })),
    generate: vi.fn(),
    improve: vi.fn()
  } as unknown as TeamOnboardingClient

  const onInvalidate = vi.fn(),
    onRefreshed = vi.fn((next: TeamSetup) => {
      props.setup = next
      view.rerender(node(props))
    }),
    onBusyChange = vi.fn()

  const props = { client, setup: initialSetup, disabled: false, onInvalidate, onRefreshed, onBusyChange }

  const node = (next = props) => (
    <I18nProvider configClient={null} initialLocale={locale}>
      <TeamSetupBudgetEditor {...next} />
    </I18nProvider>
  )

  const view = render(node())

  return { client, view, props, node, onInvalidate, onRefreshed, onBusyChange }
}

async function open(locale: Locale = 'en') {
  fireEvent.click(screen.getByText(setupBudgetCopy[locale].title))
  await screen.findByText(setupBudgetCopy[locale].current)
}

async function suggestAndReview() {
  fireEvent.click(screen.getByRole('button', { name: copy.loadSuggestion }))
  fireEvent.click(screen.getByRole('button', { name: copy.review }))
  await screen.findByRole('region', { name: copy.review })
}

it('keeps legacy limits and charged usage until explicit edit, review and apply; editing revokes review', async () => {
  const h = harness()
  expect(h.client.budget).not.toHaveBeenCalled()
  await open()
  expect(h.client.applyBudget).not.toHaveBeenCalled()
  expect(h.client.previewBudget).not.toHaveBeenCalled()
  expect((screen.getByLabelText(copy.improve) as HTMLInputElement).value).toBe('')
  expect(screen.getByText(copy.legacy)).toBeTruthy()
  expect(screen.getByText(copy.legacyUsage).nextElementSibling?.textContent).toBe('2')
  expect(screen.getByText(copy.limitsNote)).toBeTruthy()
  expect((screen.getByRole('button', { name: copy.review }) as HTMLButtonElement).disabled).toBe(true)
  await suggestAndReview()
  expect(h.client.previewBudget).toHaveBeenCalledWith(state, policy)
  fireEvent.change(screen.getByLabelText(copy.improve), { target: { value: '0' } })
  expect(screen.queryByRole('button', { name: copy.apply })).toBeNull()
  fireEvent.change(screen.getByLabelText(copy.total), { target: { value: '2' } })
  expect((screen.getByRole('button', { name: copy.review }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(screen.getByLabelText(copy.total), { target: { value: '3' } })
  fireEvent.click(screen.getByRole('button', { name: copy.review }))
  await screen.findByRole('button', { name: copy.apply })
  expect(h.client.applyBudget).not.toHaveBeenCalled()
  const next = activeState({ ...policy, improveCallLimit: 0, totalCallLimit: 3 })
  vi.mocked(h.client.budget).mockResolvedValueOnce(next)
  fireEvent.click(screen.getByRole('button', { name: copy.apply }))
  await screen.findByRole('region', { name: copy.receipt })
  await waitFor(() => expect(h.onRefreshed).toHaveBeenCalledTimes(2))
  expect(h.client.applyBudget).toHaveBeenCalledTimes(1)
  expect(vi.mocked(h.client.applyBudget).mock.calls[0][0].configuration).toEqual(next.configuration)
  expect(h.onInvalidate).toHaveBeenCalledTimes(1)
  expect(screen.getByText(copy.legacyUsage).nextElementSibling?.textContent).toBe('2')
  expect(h.client.generate).not.toHaveBeenCalled()
  expect(h.client.improve).not.toHaveBeenCalled()
})

it.each(['secret transport contents', 'invalid_setup_budget', 'unsupported'])(
  'reconciles an unknown apply (%s) with the same intent and retains its receipt when refresh fails',
  async failure => {
    const h = harness()
    await open()
    await suggestAndReview()
    vi.mocked(h.client.applyBudget).mockRejectedValueOnce(new Error(failure))
    fireEvent.click(screen.getByRole('button', { name: copy.apply }))
    await screen.findByText(common.uncertain)
    expect(screen.queryByText('secret transport contents')).toBeNull()
    expect(screen.getByLabelText(copy.improve).closest('fieldset[disabled]')).not.toBeNull()
    expect((screen.getByRole('button', { name: common.refresh }) as HTMLButtonElement).disabled).toBe(true)
    vi.mocked(h.client.setup).mockRejectedValueOnce(new Error('refresh failed'))
    fireEvent.click(screen.getByRole('button', { name: copy.reconcile }))
    await screen.findByText(copy.refreshFailed)
    expect(vi.mocked(h.client.applyBudget).mock.calls[1]).toEqual(vi.mocked(h.client.applyBudget).mock.calls[0])
    const receipt = screen.getByRole('region', { name: copy.receipt })
    expect(receipt.textContent).toContain(`${copy.improve}: 4`)
    expect(screen.queryByRole('button', { name: copy.apply })).toBeNull()
    expect(screen.getByLabelText(copy.improve).closest('fieldset[disabled]')).not.toBeNull()
    vi.mocked(h.client.budget).mockResolvedValueOnce(activeState({ ...policy, improveCallLimit: 1 }, 4))
    fireEvent.click(screen.getByRole('button', { name: common.refresh }))
    await waitFor(() => expect(h.onRefreshed).toHaveBeenCalledTimes(2))
    expect(receipt.textContent).toContain(`${copy.improve}: 4`)
    expect(h.client.applyBudget).toHaveBeenCalledTimes(2)
  }
)

it.each([
  ['managed', { managed: true }, common.managed],
  ['running', { setupBudget: { ...state.setupBudget, status: 'running' } }, copy.pending],
  ['uncertain', { setupBudget: { ...state.setupBudget, status: 'uncertain' } }, copy.pending],
  ['configured', { available: false }, null]
] as const)('keeps %s policy read-only with current usage visible', async (_name, extra, message) => {
  const h = harness({ ...state, ...extra } as SetupBudgetState, {
    ...setup,
    generationAllowance: { status: 'available', remainingCalls: 1 }
  })

  await open()

  if (message) {
    expect(screen.getByText(message)).toBeTruthy()
  }

  expect(screen.getByLabelText(copy.improve).closest('fieldset[disabled]')).not.toBeNull()
  expect(screen.getByText(copy.retained)).toBeTruthy()
  expect(h.onRefreshed).toHaveBeenLastCalledWith(
    expect.objectContaining({
      generationAllowance: expect.objectContaining({
        status: ({ ...state, ...extra } as SetupBudgetState).setupBudget.status
      })
    })
  )
  expect(h.client.previewBudget).not.toHaveBeenCalled()
  expect(h.client.applyBudget).not.toHaveBeenCalled()
})

it('does not read unsupported runtimes and fences a preview after source or socket changes', async () => {
  const unsupported = harness(state, { ...setup, setupBudgetVersion: undefined })
  fireEvent.click(screen.getByText(copy.title))
  expect(screen.getByText(copy.unsupported)).toBeTruthy()
  expect(unsupported.client.budget).not.toHaveBeenCalled()
  unsupported.view.unmount()
  const h = harness()
  await open()
  await suggestAndReview()
  h.view.rerender(h.node({ ...h.props, setup: { ...setup, sourceRevision: 'new-source' } }))
  expect((screen.getByRole('button', { name: copy.apply }) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.getByText(common.conflict)).toBeTruthy()
  vi.mocked(h.client.current).mockReturnValue(false)
  h.view.rerender(h.node({ ...h.props, setup: { ...setup, sourceRevision: 'new-source' } }))
  expect((screen.getByRole('button', { name: common.refresh }) as HTMLButtonElement).disabled).toBe(true)
  expect(h.client.applyBudget).not.toHaveBeenCalled()
})

it('blocks unknown warning semantics and definitive stale rejection until a fresh review', async () => {
  const h = harness()
  await open()
  vi.mocked(h.client.previewBudget).mockResolvedValueOnce({
    ...previewFor(policy),
    warnings: ['unrecognized_sensitive_warning']
  })
  await suggestAndReview()
  expect((screen.getByRole('button', { name: copy.apply }) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.queryByText('unrecognized_sensitive_warning')).toBeNull()
  await suggestAndReview()
  vi.mocked(h.client.applyBudget).mockRejectedValueOnce({ code: 5074, data: { reason: 'source_conflict' } })
  fireEvent.click(screen.getByRole('button', { name: copy.apply }))
  await screen.findByText(common.conflict)
  expect(screen.queryByRole('button', { name: copy.reconcile })).toBeNull()
  expect(screen.queryByRole('button', { name: copy.apply })).toBeNull()
  expect((screen.getByRole('button', { name: common.refresh }) as HTMLButtonElement).disabled).toBe(false)
})

it('shows loading and ignores late reads after dismissal', async () => {
  const h = harness()
  let finish!: (value: SetupBudgetState) => void
  vi.mocked(h.client.budget).mockImplementationOnce(
    () =>
      new Promise(resolve => {
        finish = resolve
      })
  )
  fireEvent.click(screen.getByText(copy.title))
  await waitFor(() => expect(h.client.budget).toHaveBeenCalledTimes(1))
  expect(screen.getByLabelText(common.loading)).toBeTruthy()
  h.view.unmount()
  await act(async () => finish(state))
  expect(h.onRefreshed).not.toHaveBeenCalled()
  expect(h.client.applyBudget).not.toHaveBeenCalled()
})

it.each(Object.keys(setupBudgetCopy) as Locale[])(
  'localizes labelled policy inputs and retained receipt in %s',
  async locale => {
    const saved: SetupBudgetReceipt = {
      ...previewFor(policy),
      action: 'setupBudget',
      idempotencyKey: 'saved-change',
      policyGeneration: 3
    }

    const h = harness({ ...activeState(), latestReceipt: saved }, setup, locale)
    await open(locale)
    const localized = setupBudgetCopy[locale]
    expect((screen.getByLabelText(localized.improve) as HTMLInputElement).value).toBe('4')
    expect(within(screen.getByRole('region', { name: localized.receipt })).getByText('saved-change')).toBeTruthy()
    expect(h.client.applyBudget).not.toHaveBeenCalled()
  }
)

it('invalidates a budget preview when operation usage changes without changing the team source', async () => {
  const h = harness()
  await open()
  await suggestAndReview()
  h.view.rerender(
    h.node({ ...h.props, setup: { ...setup, generationAllowance: { status: 'exhausted', remainingCalls: 0 } } })
  )
  expect((screen.getByRole('button', { name: copy.apply }) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.getByLabelText(copy.improve)).toHaveProperty('value', '4')
  expect(h.client.applyBudget).not.toHaveBeenCalled()
})

it('keeps a failed read recoverable and does not manufacture current limits', async () => {
  const h = harness()
  vi.mocked(h.client.budget).mockRejectedValueOnce(new Error('sensitive upstream message'))
  fireEvent.click(screen.getByText(copy.title))
  await screen.findByRole('alert')
  expect(screen.queryByText(copy.current)).toBeNull()
  expect(screen.queryByText('sensitive upstream message')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: common.refresh }))
  await screen.findByText(copy.current)
  expect(h.client.budget).toHaveBeenCalledTimes(2)
})

it('replays a committed change once when the real client rejects its malformed acknowledgement', async () => {
  const h = harness()
  await open()
  await suggestAndReview()
  let advances = 0
  let stored: { capabilityVersion: 1; receipt: SetupBudgetReceipt } | null = null
  const requests: Record<string, unknown>[] = []

  const request = async <T,>(_method: string, params: Record<string, unknown>): Promise<T> => {
    requests.push(params)

    if (!stored) {
      advances++
      stored = {
        capabilityVersion: 1,
        receipt: {
          action: 'setupBudget',
          organizationId: state.organizationId,
          ownerId: state.ownerId,
          sourceRevision: state.sourceRevision,
          policyGeneration: state.policyGeneration + advances,
          previewVersion: 'b'.repeat(64),
          configuration: policy,
          idempotencyKey: String(params.idempotencyKey)
        }
      }

      // The saved receipt is valid; only the first transport acknowledgement is malformed.
      return { ...stored, receipt: { ...stored.receipt, previewVersion: 'malformed' } } as T
    }

    return stored as T
  }

  const validated = createTeamOnboardingClient(
    () => ({ key: 'socket', ownerKey: 'owner', profile: 'default', eligible: true, request }),
    () => () => {}
  )

  h.client.applyBudget = vi.fn(validated.applyBudget)
  fireEvent.click(screen.getByRole('button', { name: copy.apply }))
  await screen.findByText(common.uncertain)
  expect(advances).toBe(1)
  expect(screen.getByLabelText(copy.improve).closest('fieldset[disabled]')).not.toBeNull()
  vi.mocked(h.client.budget).mockResolvedValueOnce({ ...activeState(), latestReceipt: stored!.receipt })
  fireEvent.click(screen.getByRole('button', { name: copy.reconcile }))
  await screen.findByRole('region', { name: copy.receipt })
  await waitFor(() => expect(h.onRefreshed).toHaveBeenCalledTimes(2))
  expect(requests).toHaveLength(2)
  expect(requests[1]).toEqual(requests[0])
  expect(advances).toBe(1)
  expect(screen.getByRole('region', { name: copy.receipt }).textContent).toContain(`${common.generation}: 3`)
  validated.dispose()
})

it('keeps charges, remaining calls and errors visible while technical details stay optional', async () => {
  const h = harness()
  await open()
  const tokens = screen.getByText(copy.tokens)
  const details = tokens.closest('details')!
  expect(details.open).toBe(false)
  expect(screen.getByText(copy.cost).closest('details')).not.toBe(details)
  expect(screen.getByText(copy.limitsSummary).closest('details')).not.toBe(details)
  expect(screen.getByText(copy.retainedSummary).closest('details')).not.toBe(details)
  const total = screen.getByLabelText(copy.total)
  fireEvent.change(total, { target: { value: '9' } })
  fireEvent.click(within(details).getByText(copy.details))
  expect(details.open).toBe(true)
  fireEvent.click(within(details).getByText(copy.details))
  expect(details.open).toBe(false)
  expect((total as HTMLInputElement).value).toBe('9')
  await suggestAndReview()
  const preview = screen.getByRole('region', { name: copy.review })
  expect(within(preview).getByText(copy.currentRemaining)).toBeTruthy()
  expect(within(preview).getByText(copy.retainedSummary)).toBeTruthy()
  vi.mocked(h.client.applyBudget).mockRejectedValueOnce(new Error('lost acknowledgement'))
  fireEvent.click(screen.getByRole('button', { name: copy.apply }))
  const error = await screen.findByText(common.uncertain)
  expect(error.closest('details')).not.toBe(details)
  expect(screen.getByRole('button', { name: copy.reconcile })).toBeTruthy()
})

it('keeps the saved confirmation visible while receipt identifiers are collapsed', async () => {
  harness()
  await open()
  await suggestAndReview()
  fireEvent.click(screen.getByRole('button', { name: copy.apply }))
  const receipt = await screen.findByRole('region', { name: copy.receipt })
  const details = receipt.querySelector('details')!
  expect(details.open).toBe(false)
  expect(within(receipt).getByText(copy.receipt).closest('details')).not.toBe(details)
  expect(details.querySelector('bdi')?.textContent).toBeTruthy()
})
