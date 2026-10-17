import { useEffect, useId, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n/context'
import { ownerPolicyCopy } from '@/i18n/organization-owner-policy'
import { teamOnboardingCopy } from '@/i18n/organization-team-onboarding'
import { setupBudgetCopy, type SetupBudgetCopy } from '@/i18n/organization-team-setup-budget'

import type { TeamOnboardingClient, TeamSetup } from './team-onboarding-client'
import {
  type SetupBudgetPolicy,
  type SetupBudgetPreview,
  type SetupBudgetReceipt,
  type SetupBudgetState,
  type SetupBudgetView,
  validateSetupBudgetPolicy
} from './team-setup-budget-client'

const fields = ['improveCallLimit', 'generationCallLimit', 'totalCallLimit'] as const
const labels = ['improve', 'generate', 'total'] as const

const setupFingerprint = (setup: TeamSetup) =>
  JSON.stringify([
    setup.organizationId,
    setup.sourceRevision,
    setup.generationAllowance?.policyVersion,
    setup.generationAllowance?.status,
    setup.generationAllowance?.remainingCalls,
    setup.generationAllowance?.remainingByOperation?.improveTeamInput,
    setup.generationAllowance?.remainingByOperation?.generateTeam
  ])

type Draft = Record<(typeof fields)[number], string>

const draftFor = (policy: SetupBudgetPolicy | null): Draft => ({
  improveCallLimit: policy ? String(policy.improveCallLimit) : '',
  generationCallLimit: policy ? String(policy.generationCallLimit) : '',
  totalCallLimit: policy ? String(policy.totalCallLimit) : ''
})

function candidateFor(draft: Draft | null): SetupBudgetPolicy | null {
  if (!draft || fields.some(field => !/^\d+$/.test(draft[field]))) {
    return null
  }

  const candidate = {
    version: 2,
    mode: 'bounded_api',
    ...Object.fromEntries(fields.map(field => [field, Number(draft[field])]))
  }

  try {
    validateSetupBudgetPolicy(candidate)

    return candidate
  } catch {
    return null
  }
}

const knownWarnings = new Set([
  'existing_charges_retained',
  'legacy_calls_aggregate_only',
  'enclosing_call_token_cost_limits_retained',
  'no_calls_or_grants_created'
])

function rejection(
  error: unknown,
  applying: boolean
): 'unsupported' | 'managed' | 'pending' | 'conflict' | 'sourceConflict' | 'invalid' | null {
  const { code, data } = (error && typeof error === 'object' ? error : {}) as {
    code?: number
    data?: { reason?: string }
  }

  const reason = data?.reason

  if (!applying && error instanceof Error && error.message === 'unsupported') {
    return 'unsupported'
  }

  if (!applying && error instanceof Error && error.message === 'invalid_setup_budget') {
    return 'invalid'
  }

  if (code === -32601 || reason === 'setup_budget_unsupported') {
    return 'unsupported'
  }

  if (reason === 'managed_configuration') {
    return 'managed'
  }

  if (reason === 'team_generation_in_progress_or_uncertain') {
    return 'pending'
  }

  if (reason === 'source_policy_conflict') {
    return 'sourceConflict'
  }

  if (
    [
      'source_conflict',
      'generation_conflict',
      'preview_conflict',
      'idempotency_conflict',
      'organization_not_empty',
      'identity_conflict'
    ].includes(reason ?? '')
  ) {
    return 'conflict'
  }

  if (code === -32602 || code === 4001) {
    return 'invalid'
  }

  return null
}

export function SetupBudgetRemaining({
  improve,
  generate,
  total
}: {
  improve: number
  generate: number
  total: number
}) {
  const { locale } = useI18n()
  const copy = setupBudgetCopy[locale]

  return (
    <p className="eid-team-note">
      {copy.remainingOperations
        .replace('{improve}', String(improve))
        .replace('{generate}', String(generate))
        .replace('{total}', String(total))}
    </p>
  )
}

function BudgetUsage({ view, copy }: { view: SetupBudgetView; copy: SetupBudgetCopy }) {
  const { locale } = useI18n()
  const common = ownerPolicyCopy[locale]

  return (
    <>
      <p className="eid-team-note">{copy.current}</p>
      <dl className="eid-team-budget-values">
        <div>
          <dt>{copy.improve}</dt>
          <dd>{view.limits.improveCalls}</dd>
        </div>
        <div>
          <dt>{copy.generate}</dt>
          <dd>{view.limits.generationCalls}</dd>
        </div>
        <div>
          <dt>{copy.total}</dt>
          <dd>{view.limits.totalCalls}</dd>
        </div>
        <div>
          <dt>{copy.tokens}</dt>
          <dd>
            {view.reservedTokens} / {view.limits.totalTokens}
          </dd>
        </div>
        <div>
          <dt>{copy.cost}</dt>
          <dd>
            <bdi>
              {view.reservedCostUsd ?? common.notEstimated} / {view.limits.maxCostUsd ?? copy.noCostCeiling}
            </bdi>
          </dd>
        </div>
      </dl>
      <p className="eid-team-note">{copy.retained}</p>
      <dl className="eid-team-budget-values">
        <div>
          <dt>{copy.legacyUsage}</dt>
          <dd>{view.usage.legacy}</dd>
        </div>
        <div>
          <dt>{copy.improve}</dt>
          <dd>{view.usage.improveTeamInput}</dd>
        </div>
        <div>
          <dt>{copy.generate}</dt>
          <dd>{view.usage.generateTeam}</dd>
        </div>
        <div>
          <dt>{copy.total}</dt>
          <dd>{view.usage.total}</dd>
        </div>
      </dl>
      <p className="eid-team-note">{copy.remaining}</p>
      <SetupBudgetRemaining
        generate={view.remaining.generationCalls}
        improve={view.remaining.improveCalls}
        total={view.remaining.totalCalls}
      />
    </>
  )
}

interface TeamSetupBudgetEditorProps {
  client: TeamOnboardingClient
  setup: TeamSetup
  disabled: boolean
  onBusyChange: (pending: boolean) => void
  onInvalidate: () => void
  onRefreshed: (setup: TeamSetup) => void
}

/** The editor retains the exact reviewed write until its outcome is known. */
function useSetupBudgetEditor({
  client,
  setup,
  disabled,
  onBusyChange,
  onInvalidate,
  onRefreshed
}: TeamSetupBudgetEditorProps) {
  const { locale } = useI18n()

  const copy = setupBudgetCopy[locale],
    common = ownerPolicyCopy[locale],
    teamCopy = teamOnboardingCopy[locale]

  const id = useId()
  const [state, setState] = useState<SetupBudgetState | null>(null)
  const [loadedFor, setLoadedFor] = useState<string | undefined>(undefined)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [preview, setPreview] = useState<SetupBudgetPreview | null>(null)
  const [receipt, setReceipt] = useState<SetupBudgetReceipt | null>(null)
  const [pending, setPending] = useState<'read' | 'preview' | 'apply' | null>(null)
  const [error, setError] = useState('')
  const [needsRefresh, setNeedsRefresh] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const intent = useRef<{ preview: SetupBudgetPreview; key: string } | null>(null)

  const busy = useRef(false),
    alive = useRef(true)

  // eslint-disable-next-line no-restricted-syntax -- Discard asynchronous responses after the dialog is dismissed.
  useEffect(() => {
    alive.current = true

    return () => {
      alive.current = false
    }
  }, [])

  const supported = setup.setupBudgetVersion === 2
  const stale = !client.current() || (!!state && loadedFor !== setupFingerprint(setup))
  const waiting = state?.setupBudget.status === 'running' || state?.setupBudget.status === 'uncertain'
  const readOnly = !state || state.managed || !state.available || waiting || stale || needsRefresh
  const candidate = candidateFor(draft)
  const locked = disabled || !!pending || uncertain
  const displayReceipt = receipt ?? state?.latestReceipt

  const refresh = async () => {
    const nextSetup = await client.setup(setup.organizationId ?? null)
    const next = await client.budget(nextSetup)

    if (!alive.current || !client.current()) {
      return
    }

    const refreshedSetup: TeamSetup = {
      ...nextSetup,
      generationAllowance: {
        status: next.setupBudget.status,
        remainingCalls: next.setupBudget.remaining.totalCalls,
        ...(next.setupBudget.policyVersion === 2
          ? {
              policyVersion: 2 as const,
              remainingByOperation: {
                improveTeamInput: next.setupBudget.remaining.improveCalls,
                generateTeam: next.setupBudget.remaining.generationCalls
              }
            }
          : {})
      }
    }

    setState(next)
    setLoadedFor(setupFingerprint(refreshedSetup))
    setDraft(current => current ?? draftFor(next.configuration))
    setPreview(null)
    setNeedsRefresh(false)
    onRefreshed(refreshedSetup)
  }

  const run = async (kind: NonNullable<typeof pending>, action: () => Promise<void>) => {
    if (busy.current || disabled || !client.current()) {
      return
    }

    busy.current = true
    setPending(kind)
    setError('')
    onBusyChange(true)

    try {
      await action()
    } catch (failure) {
      if (!alive.current) {
        return
      }

      // Only a structured server rejection proves a write did not commit.
      const reason = rejection(failure, kind === 'apply')

      const saved = kind === 'apply' && intent.current === null

      if (reason === 'conflict' || reason === 'sourceConflict' || reason === 'pending') {
        onInvalidate()
      }

      setError(
        !client.current()
          ? teamCopy.stale
          : saved
            ? copy.refreshFailed
            : reason === 'pending'
              ? copy.pending
              : reason === 'unsupported'
                ? copy.unsupported
                : reason
                  ? common[reason]
                  : kind === 'apply'
                    ? common.uncertain
                    : copy.readFailed
      )
      setNeedsRefresh(true)

      if (kind === 'apply' && intent.current && !reason) {
        setUncertain(true)
      } else {
        setPreview(null)
        intent.current = null
        setUncertain(false)
      }
    } finally {
      busy.current = false

      if (alive.current) {
        setPending(null)
        onBusyChange(false)
      }
    }
  }

  const load = () => void run('read', refresh)

  const review = () => {
    if (locked || readOnly || !candidate || !state) {
      return
    }

    void run('preview', async () => {
      const result = await client.previewBudget(state, candidate)

      if (alive.current && client.current()) {
        setPreview(result)
        intent.current = null
      }
    })
  }

  const apply = () => {
    if (
      disabled ||
      pending ||
      !preview ||
      (!uncertain && readOnly) ||
      !preview.warnings.every(warning => knownWarnings.has(warning))
    ) {
      return
    }

    intent.current ??= { preview, key: crypto.randomUUID() }
    const original = intent.current
    onInvalidate()
    void run('apply', async () => {
      const result = await client.applyBudget(original.preview, original.key)

      if (!alive.current || !client.current()) {
        return
      }

      setReceipt(result.receipt)
      setDraft(draftFor(result.receipt.configuration))
      setPreview(null)
      setUncertain(false)
      intent.current = null
      setNeedsRefresh(true)
      await refresh()
    })
  }

  return {
    copy,
    common,
    teamCopy,
    id,
    state,
    draft,
    preview,
    displayReceipt,
    pending,
    error,
    uncertain,
    supported,
    stale,
    waiting,
    readOnly,
    candidate,
    locked,
    load,
    review,
    apply,
    changeDraft: (field: keyof Draft, value: string) => {
      setDraft(current => ({ ...(current ?? draftFor(null)), [field]: value }))
      setPreview(null)
      intent.current = null
    },
    loadSuggestion: () => {
      if (state) {
        setDraft(draftFor(state.suggestedConfiguration))
        setPreview(null)
      }
    }
  }
}

export function TeamSetupBudgetEditor(props: TeamSetupBudgetEditorProps) {
  const { client, disabled } = props

  const {
    copy,
    common,
    teamCopy,
    id,
    state,
    draft,
    preview,
    displayReceipt,
    pending,
    error,
    uncertain,
    supported,
    stale,
    waiting,
    readOnly,
    candidate,
    locked,
    load,
    review,
    apply,
    changeDraft,
    loadSuggestion
  } = useSetupBudgetEditor(props)

  return (
    <details
      className="eid-team-budget"
      onToggle={event => {
        if (event.currentTarget.open && supported && !state && !pending && !error) {
          load()
        }
      }}
    >
      <summary>{copy.title}</summary>
      {!supported ? (
        <p className="eid-team-note">{copy.unsupported}</p>
      ) : (
        <div className="eid-team-budget-body">
          <p className="eid-team-note">{copy.limitsNote}</p>
          <Button
            disabled={disabled || !!pending || uncertain || !client.current()}
            onClick={load}
            size="sm"
            variant="text"
          >
            {common.refresh}
          </Button>
          {pending && <Loader label={pending === 'apply' ? common.saving : common.loading} />}
          {state && (
            <>
              {!state.configuration && <p>{copy.legacy}</p>}
              <BudgetUsage copy={copy} view={state.setupBudget} />
              <p className="eid-team-note">{copy.retainedNote}</p>
              {state.managed && <p role="status">{common.managed}</p>}
              {!state.available && <p role="status">{copy.unavailable}</p>}
              {waiting && <p role="status">{copy.pending}</p>}
              {state.setupBudget.status === 'blocked' && <p role="status">{teamCopy.allowanceBlocked}</p>}
              {stale && <p role="status">{client.current() ? common.conflict : teamCopy.stale}</p>}
              <fieldset className="eid-team-fields" disabled={locked || readOnly}>
                <legend>{copy.edit}</legend>
                <p className="eid-team-note" id={`${id}-help`}>
                  {copy.draftHelp}
                </p>
                {fields.map((field, index) => (
                  <label key={field}>
                    {copy[labels[index]]}
                    <Input
                      aria-describedby={`${id}-help`}
                      inputMode="numeric"
                      max={1000}
                      min={0}
                      onChange={event => changeDraft(field, event.target.value)}
                      step={1}
                      type="number"
                      value={draft?.[field] ?? ''}
                    />
                  </label>
                ))}
                <div className="eid-team-actions">
                  <Button onClick={loadSuggestion} size="sm" variant="secondary">
                    {copy.loadSuggestion}
                  </Button>
                  <Button disabled={!candidate} onClick={review} size="sm" variant="secondary">
                    {copy.review}
                  </Button>
                </div>
              </fieldset>
            </>
          )}
          {preview && (
            <section aria-label={copy.review} className="eid-team-budget-review">
              <p>{copy.review}</p>
              {fields.map((field, index) => (
                <p key={field}>
                  {copy[labels[index]]}: <bdi>{state?.configuration?.[field] ?? common.legacy}</bdi> →{' '}
                  <bdi>{preview.configuration[field]}</bdi>
                </p>
              ))}
              <p className="eid-team-note">{copy.applyNote}</p>
              <p className="eid-team-note">{common.closeNote}</p>
              {!preview.warnings.every(warning => knownWarnings.has(warning)) && <p role="alert">{common.invalid}</p>}
              <Button
                disabled={
                  disabled ||
                  !!pending ||
                  !client.current() ||
                  (!uncertain && readOnly) ||
                  !preview.warnings.every(warning => knownWarnings.has(warning))
                }
                onClick={apply}
                size="sm"
              >
                {uncertain ? copy.reconcile : copy.apply}
              </Button>
            </section>
          )}
          {displayReceipt && (
            <section
              aria-atomic="true"
              aria-label={copy.receipt}
              aria-live="polite"
              className="eid-team-budget-receipt"
            >
              <p>{copy.receipt}</p>
              <p>
                {common.generation}: {displayReceipt.policyGeneration}
              </p>
              {fields.map((field, index) => (
                <p key={field}>
                  {copy[labels[index]]}: {displayReceipt.configuration[field]}
                </p>
              ))}
              <p className="eid-team-note">
                <bdi>{displayReceipt.idempotencyKey}</bdi>
              </p>
            </section>
          )}
          {error && <p role="alert">{error}</p>}
        </div>
      )}
    </details>
  )
}
