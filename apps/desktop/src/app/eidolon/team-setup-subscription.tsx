import { useId } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n/context'
import { setupBudgetCopy } from '@/i18n/organization-team-setup-budget'
import { setupSubscriptionCopy } from '@/i18n/organization-team-setup-subscription'

import { SetupBudgetRemaining } from './team-setup-budget-editor'
import { type SubscriptionPolicy, type SubscriptionPreview } from './team-setup-subscription-client'
import { type TeamSetupSubscriptionProps, useTeamSetupSubscription } from './use-team-setup-subscription'

const format = (value: string, values: Record<string, string | number>) =>
  Object.entries(values).reduce(
    (result, [key, replacement]) => result.replaceAll(`{${key}}`, String(replacement)),
    value
  )

function PolicyReview({ preview }: { preview: SubscriptionPreview }) {
  const { locale } = useI18n()

  const copy = setupSubscriptionCopy[locale],
    common = setupBudgetCopy[locale]

  return (
    <section aria-label={copy.review} className="eid-team-fields">
      <p>{copy.nonHard}</p>
      <p>
        {format(copy.limits, {
          improve: preview.effectiveCallLimits.improveCallLimit,
          generate: preview.effectiveCallLimits.generationCallLimit,
          total: preview.effectiveCallLimits.totalCallLimit,
          timeout: preview.effectiveTimeoutSeconds
        })}
      </p>
      <p>
        {format(copy.retained, {
          total: preview.retainedCallCounts.total,
          legacy: preview.retainedCallCounts.legacy,
          ceiling: preview.retainedInheritanceCallCeiling
        })}
      </p>
      <SetupBudgetRemaining
        generate={preview.remainingCalls.generateTeam}
        improve={preview.remainingCalls.improveTeamInput}
        total={preview.remainingCalls.total}
      />
      <details>
        <summary>{common.details}</summary>
        <p className="eid-team-note">{copy.sourceWarning}</p>
        <dl className="eid-team-note">
          <dt>{common.current}</dt>
          <dd>
            <bdi>{JSON.stringify(preview.priorPolicy)}</bdi>
          </dd>
          {preview.retiredGuarantees.map(row => (
            <div key={row.guarantee}>
              <dt>
                <bdi>{row.guarantee}</bdi>
              </dt>
              <dd>
                <bdi>{JSON.stringify(row.value)}</bdi> · {row.scope} · {row.source}
                {row.historicalOrigin ? ` · ${row.historicalOrigin}` : ''}
              </dd>
            </div>
          ))}
        </dl>
      </details>
    </section>
  )
}

export function TeamSetupSubscription(props: TeamSetupSubscriptionProps) {
  const { locale } = useI18n()

  const copy = setupSubscriptionCopy[locale],
    budget = setupBudgetCopy[locale]

  const state = useTeamSetupSubscription(props)
  const id = useId()

  if (props.setup.setupSubscriptionVersion !== 1) {
    return null
  }

  const unavailable = props.setup.setupSubscriptionState !== 'available'
  const disabled = props.disabled || state.pending
  const connected = state.status?.connectionState === 'active'
  const absent = ['absent', 'disconnected'].includes(state.status?.connectionState ?? '')
  const activePolicy = state.policy?.policyVersion === 3 ? (state.policy.configuration as SubscriptionPolicy) : null
  const currentPreview = state.preview
  const limits = state.policy?.setupBudget.limits
  const timeout = state.policy?.setupBudget.requestTimeoutSeconds
  const usage = props.setup.generationAllowance?.setupUsage ?? state.policy?.setupBudget.setupUsage

  const usageObserved =
    usage?.tokenStatus === 'observed' &&
    [usage.inputTokens, usage.outputTokens, usage.totalTokens].every(
      value => Number.isSafeInteger(value) && Number(value) >= 0
    )

  return (
    <section aria-label={copy.title} className="eid-team-fields">
      <strong>{copy.title}</strong>
      <p className="eid-team-note">{unavailable ? copy.unsupported : copy.intro}</p>
      {!unavailable && !state.opened && (
        <Button disabled={disabled} onClick={state.inspect} size="sm" variant="secondary">
          {copy.inspect}
        </Button>
      )}
      {!unavailable && state.opened && (
        <>
          <div className="eid-team-actions">
            <Button disabled={disabled} onClick={state.check} size="sm" variant="secondary">
              {copy.check}
            </Button>
            {connected && (
              <Button disabled={disabled} onClick={state.models} size="sm" variant="secondary">
                {copy.loadModels}
              </Button>
            )}
            {(state.connectionPending || state.status?.canCancel) && (
              <Button disabled={disabled} onClick={state.cancel} size="sm" variant="text">
                {copy.cancelConsent}
              </Button>
            )}
          </div>
          {state.connectionPending && <p role="status">{copy.pending}</p>}
          {connected && !state.catalog && <p role="status">{copy.stored}</p>}
          {absent && !state.connectionPending && (
            <>
              <p className="eid-team-note" id={`${id}-consent`}>
                {copy.consent}
              </p>
              <label>
                <input
                  aria-describedby={`${id}-consent`}
                  checked={state.consent}
                  disabled={disabled}
                  onChange={event => state.setConsent(event.target.checked)}
                  type="checkbox"
                />
                {copy.consentCheck}
              </label>
              <Button
                disabled={disabled || !state.consent || state.status?.cleanupPending}
                onClick={state.begin}
                size="sm"
              >
                {copy.continue}
              </Button>
            </>
          )}
          {state.catalog && (
            <>
              <div className="grid min-w-0 gap-1.5">
                <label htmlFor={`${id}-model`}>{copy.model}</label>
                <select
                  disabled={disabled}
                  id={`${id}-model`}
                  onChange={event => state.choose(event.target.value)}
                  value={state.model}
                >
                  <option value="">{copy.choose}</option>
                  {state.catalog.models.map(model => (
                    <option key={model.slug} value={model.slug}>
                      {model.displayName}
                    </option>
                  ))}
                </select>
              </div>
              {!state.catalog.models.length && <p role="status">{copy.noModels}</p>}
              <fieldset className="eid-team-fields" disabled={disabled}>
                {(['improveCallLimit', 'generationCallLimit', 'totalCallLimit', 'requestTimeoutSeconds'] as const).map(
                  (field, index) => (
                    <label key={field}>
                      {[budget.improve, budget.generate, budget.total, copy.timeout][index]}
                      <Input
                        inputMode="numeric"
                        max={index === 3 ? 120 : 1000}
                        min={index === 3 ? 1 : 0}
                        onChange={event => state.change(field, event.target.value)}
                        type="number"
                        value={state.draft[field]}
                      />
                    </label>
                  )
                )}
              </fieldset>
              <Button disabled={disabled || !state.canReview} onClick={state.review} size="sm" variant="secondary">
                {copy.review}
              </Button>
            </>
          )}
          {currentPreview && (
            <>
              <PolicyReview preview={currentPreview} />
              <label>
                <input
                  checked={state.acknowledged}
                  disabled={disabled}
                  onChange={event => state.setAcknowledged(event.target.checked)}
                  type="checkbox"
                />
                {copy.ack}
              </label>
              <Button disabled={disabled || !state.acknowledged} onClick={state.apply} size="sm">
                {copy.apply}
              </Button>
            </>
          )}
          {activePolicy && limits && timeout && (
            <p className="eid-team-note">
              {format(copy.limits, {
                improve: limits.improveCalls,
                generate: limits.generationCalls,
                total: limits.totalCalls,
                timeout
              })}
            </p>
          )}
          {activePolicy && (
            <p className="eid-team-note">
              {copy.model}: <bdi>{activePolicy.modelSlug}</bdi>
            </p>
          )}
          <p className="eid-team-note">
            {usageObserved
              ? format(copy.observed, {
                  input: usage!.inputTokens!,
                  output: usage!.outputTokens!,
                  total: usage!.totalTokens!
                })
              : copy.unknown}
          </p>
          {(state.policy?.state === 'blocked' ||
            state.status?.cleanupPending ||
            ['running', 'uncertain'].includes(props.setup.generationAllowance?.status ?? '')) && (
            <p role="status">
              {copy.blocked}
              {state.policy?.reason && (
                <>
                  {' '}
                  <bdi>{state.policy.reason}</bdi>
                </>
              )}
            </p>
          )}
          {state.saved && <p role="status">{copy.changed}</p>}
          <details>
            <summary>{budget.details}</summary>
            <p className="eid-team-note">{copy.disconnectHelp}</p>
            <div className="eid-team-actions">
              {connected && (
                <Button disabled={disabled} onClick={() => state.operate('refresh')} size="sm" variant="secondary">
                  {copy.refresh}
                </Button>
              )}
              {(connected || state.status?.connectionState === 'refreshing') && (
                <Button disabled={disabled} onClick={() => state.operate('revoke')} size="sm" variant="secondary">
                  {copy.disconnect}
                </Button>
              )}
              <Button disabled={disabled} onClick={state.recover} size="sm" variant="text">
                {copy.recover}
              </Button>
            </div>
          </details>
        </>
      )}
      {state.pending && <Loader label={copy.working} />}
      {state.error && (
        <p role="alert">
          {copy.failed}
          {state.errorReason && (
            <>
              {' '}
              <bdi>{state.errorReason}</bdi>
            </>
          )}
        </p>
      )}
    </section>
  )
}
