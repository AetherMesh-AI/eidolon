import './team-onboarding.css'

import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n/context'
import { teamOnboardingCopy, type TeamOnboardingCopy } from '@/i18n/organization-team-onboarding'
import { setupSubscriptionCopy } from '@/i18n/organization-team-setup-subscription'
import { $gateway } from '@/store/gateway'
import { $desktopOnboarding, startManualOnboarding } from '@/store/onboarding'
import { $activeSessionId } from '@/store/session'

import { queueKeyboardReveal } from './home-keyboard-focus'
import { subscribeOrganizationDirectorySource } from './organization-directory-runtime'
import { OrganizationOwnerEditor } from './organization-owner-editor'
import {
  createTeamOnboardingClient,
  type TeamDraft,
  type TeamOnboardingClient,
  type TeamPreview,
  type TeamReadiness,
  type TeamReceipt,
  type TeamSetup
} from './team-onboarding-client'
import { TeamOnboardingEditor } from './team-onboarding-editor'
import { type TeamInputReview, TeamOnboardingInputs, type TeamSetupInputs } from './team-onboarding-inputs'
import { SetupBudgetRemaining, TeamSetupBudgetEditor } from './team-setup-budget-editor'
import { TeamSetupSubscription } from './team-setup-subscription'
import {
  readSubscriptionPendingRequest,
  readSubscriptionReconciledRequest,
  readSubscriptionRecovery,
  type SubscriptionRecovery
} from './team-setup-subscription-client'
import type { RuntimeOrganizationAdapter } from './types'
import { readOrganizationMetadataScope, useOrganizationMetadata } from './use-organization-metadata'

function teamInputKeyboardContext(active: HTMLElement): HTMLElement[] {
  if (!active.matches('.eid-team-input > textarea')) {
    return []
  }

  const field = active.parentElement
  const heading = field?.querySelector<HTMLElement>(':scope > .eid-team-input-heading')
  const help = field?.querySelector<HTMLElement>(':scope > .eid-team-note')

  return heading && help ? [heading, help] : []
}

function readTeamScope() {
  const scope = readOrganizationMetadataScope()
  const gateway = $gateway.get()

  return {
    ...scope,
    request: <T,>(method: string, params: Record<string, unknown>): Promise<T> => {
      if (!gateway) {
        return Promise.reject(new Error('stale'))
      }

      return gateway.request<T>(
        method,
        params,
        ['organization.generateTeam', 'llm.oneshot'].includes(method) ? 180000 : 15000
      )
    }
  }
}

function readinessMessage(readiness: TeamReadiness, copy: TeamOnboardingCopy, saved = false) {
  if (readiness.status !== 'blocked') {
    return copy.readinessUnchecked
  }

  const reasons: [string, string][] = [
    ['model_not_configured', saved ? copy.modelBlocked : copy.modelMissing],
    ['subscription_generation_unsupported', copy.subscriptionUnsupported],
    ['codex_app_server', copy.appServerUnsupported],
    ['generation_route_unsupported', copy.generationUnsupported]
  ]

  return (
    reasons.find(([reason]) => readiness.blockers.includes(reason))?.[1] ??
    (saved ? copy.modelBlocked : copy.modelRequired)
  )
}

export function TeamOnboardingEntry({ adapter }: { adapter: RuntimeOrganizationAdapter }) {
  const { locale } = useI18n()
  const copy = teamOnboardingCopy[locale]
  const { client: metadataClient, scope, view } = useOrganizationMetadata()
  const [editingIdentity, setEditingIdentity] = useState(false)
  const [opened, setOpened] = useState(false)
  const [completed, setCompleted] = useState(false)
  const [setup, setSetup] = useState<TeamSetup | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [reload, setReload] = useState(0)
  const trigger = useRef<HTMLButtonElement>(null)
  const continueAfterIdentity = useRef(false)
  const initializeAfterIdentity = useRef(false)
  const organizationId = view.descriptor?.metadata?.organizationId ?? null
  const [client, setClient] = useState<TeamOnboardingClient | null>(null)
  // eslint-disable-next-line no-restricted-syntax -- Reset window-owned setup intent when the authenticated owner changes.
  useEffect(() => {
    continueAfterIdentity.current = false
    initializeAfterIdentity.current = false
    setCompleted(false)
    setEditingIdentity(false)
  }, [scope.key, scope.ownerKey, scope.profile])
  // eslint-disable-next-line no-restricted-syntax -- Own the cancellable setup request and explicit initialization intent.
  useEffect(() => {
    setOpened(false)
    setSetup(null)
    setLoadError(false)

    if (!scope.eligible || !organizationId) {
      return
    }

    const next = createTeamOnboardingClient(readTeamScope, subscribeOrganizationDirectorySource, () =>
      $activeSessionId.get()
    )

    setClient(next)
    let active = true

    const load = async () => {
      if (!next.current()) {
        throw new Error('stale')
      }

      // Initialization belongs only to an explicitly confirmed first identity save.
      // Ordinary entry rendering is a read and must never refresh a new owner.
      if (initializeAfterIdentity.current) {
        initializeAfterIdentity.current = false
        await adapter.refresh()

        if (!next.current()) {
          throw new Error('stale')
        }
      }

      return next.setup(organizationId)
    }

    void load()
      .then(result => {
        if (active && next.current()) {
          setSetup(result)
          setLoadError(result.state === 'unconfigured' || result.state === 'unsupported')

          if (continueAfterIdentity.current && result.state === 'available') {
            continueAfterIdentity.current = false
            setOpened(true)
          }
        }
      })
      .catch(() => {
        if (active && next.current()) {
          setLoadError(true)
        }
      })

    return () => {
      active = false
      next.dispose()
      setClient(null)
    }
  }, [scope.key, scope.ownerKey, scope.profile, scope.eligible, organizationId, adapter, reload])

  if (
    (completed && !opened) ||
    !scope.eligible ||
    !view.descriptor ||
    view.descriptor.state === 'unsupported' ||
    (organizationId && setup?.state !== 'available' && !loadError)
  ) {
    return null
  }

  return (
    <section aria-label={copy.title} className="eid-team-entry">
      <div className="eid-team-continuation">
        <strong>{copy.title}</strong>
        <ol className="eid-team-progress">
          {[copy.setupOrganization, copy.modelSettings, copy.review, copy.firstRequest].map((step, index) => (
            <li
              aria-current={
                index === (!organizationId ? 0 : setup?.readiness.status === 'blocked' ? 1 : 2) ? 'step' : undefined
              }
              key={step}
            >
              <span aria-hidden="true">{index + 1}</span>
              {step}
            </li>
          ))}
        </ol>
      </div>
      {loadError && (
        <>
          <p role="alert">{copy.unavailable}</p>
          <Button
            onClick={() => {
              // Retry may resume this owner's explicit initialization intent. Mere
              // rendering never retries it, and a scope change retires the intent.
              if (!client?.current()) {
                return
              }

              initializeAfterIdentity.current = continueAfterIdentity.current
              setReload(value => value + 1)
            }}
            variant="secondary"
          >
            {copy.retry}
          </Button>
        </>
      )}
      <Button
        disabled={loadError}
        onClick={() => (organizationId ? setOpened(true) : setEditingIdentity(true))}
        ref={trigger}
      >
        {organizationId ? copy.generate : copy.setupOrganization}
      </Button>
      {editingIdentity && (
        <OrganizationOwnerEditor
          client={metadataClient}
          draftKey={scope.ownerKey}
          onClose={() => setEditingIdentity(false)}
          onReturnFocus={saved => {
            setEditingIdentity(false)
            const currentScope = readOrganizationMetadataScope()

            if (
              saved &&
              currentScope.eligible &&
              currentScope.key === scope.key &&
              currentScope.ownerKey === scope.ownerKey &&
              currentScope.profile === scope.profile
            ) {
              continueAfterIdentity.current = true
              initializeAfterIdentity.current = true
              setReload(value => value + 1)
            } else {
              trigger.current?.focus()
            }
          }}
        />
      )}
      {opened && setup && client && (
        <TeamOnboardingDialog
          client={client}
          onClose={() => setOpened(false)}
          onCreated={() => {
            setCompleted(true)
            void adapter.refresh()
          }}
          onReturnFocus={() => trigger.current?.focus()}
          profile={scope.profile}
          setup={setup}
        />
      )}
    </section>
  )
}

interface TeamGenerationIntent {
  key: string
  setup: TeamSetup
  inputs: TeamSetupInputs
  revision: number
}

interface TeamUnsentRequest extends Pick<SubscriptionRecovery, 'idempotencyKey' | 'operation' | 'sourceRevision'> {
  organizationId: string
  inputs: TeamSetupInputs
  fingerprint: string
  revision: number
  replace: boolean
}

interface TeamOnboardingDialogProps {
  client: TeamOnboardingClient
  setup: TeamSetup
  profile: string
  onClose: () => void
  onCreated: () => void
  onReturnFocus: () => void
}

function localRequestCheckSupported(next: TeamSetup) {
  return (
    next.state === 'available' &&
    next.setupSubscriptionState === 'available' &&
    next.setupSubscriptionVersion === 1 &&
    !!next.organizationId &&
    !!next.sourceRevision &&
    next.generationAllowance?.policyVersion === 3
  )
}

function recoveryAllowance(next: TeamSetup, request: TeamUnsentRequest) {
  return (
    next.state === 'available' &&
    next.setupSubscriptionState === 'available' &&
    next.setupSubscriptionVersion === 1 &&
    next.organizationId === request.organizationId &&
    next.sourceRevision === request.sourceRevision &&
    next.readiness.status !== 'blocked' &&
    next.generationAllowance?.policyVersion === 3 &&
    next.generationAllowance.status === 'available' &&
    (next.generationAllowance.remainingByOperation?.[
      request.operation === 'generate' ? 'generateTeam' : 'improveTeamInput'
    ] ?? 0) > 0
  )
}

function useTeamOnboardingDraft({ client, setup: initialSetup, onClose, onCreated }: TeamOnboardingDialogProps) {
  const { locale } = useI18n()
  const copy = teamOnboardingCopy[locale]
  const subscriptionCopy = setupSubscriptionCopy[locale]
  const [setup, setSetup] = useState(initialSetup)
  const latestSetup = useRef(initialSetup)
  const [unsentRequest, setUnsentRequest] = useState<TeamUnsentRequest | null>(null)
  const recoveryOffer = useRef<TeamUnsentRequest | null>(null)
  const [unsentCheck, setUnsentCheck] = useState<TeamUnsentRequest | null>(null)
  const [checkedUnsent, setCheckedUnsent] = useState(false)
  const retainedCheck = useRef<TeamUnsentRequest | null>(null)
  const discoveryRevision = useRef(0)
  const [unsentOperation, setUnsentOperation] = useState<SubscriptionRecovery['operation']>('generate')

  const clearRecovery = useCallback(() => {
    recoveryOffer.current = null
    setUnsentRequest(null)
  }, [])

  const clearUnsent = useCallback(() => {
    discoveryRevision.current++
    clearRecovery()
    retainedCheck.current = null
    setUnsentCheck(null)
    setCheckedUnsent(false)
  }, [clearRecovery])

  const refreshSetup = useCallback(
    (next: TeamSetup) => {
      if (recoveryOffer.current && !recoveryAllowance(next, recoveryOffer.current)) {
        clearRecovery()
      }

      const retained = retainedCheck.current

      if (
        retained &&
        (!localRequestCheckSupported(next) ||
          next.organizationId !== retained.organizationId ||
          next.sourceRevision !== retained.sourceRevision)
      ) {
        clearUnsent()
      }

      latestSetup.current = next
      setSetup(next)
    },
    [clearRecovery, clearUnsent]
  )

  const providerSetup = useStore($desktopOnboarding)
  const returningFromProvider = useRef(false)
  const [inputs, setInputs] = useState<TeamSetupInputs>({ jobs: '', goals: '', namingStyle: '' })

  const [suggestion, setSuggestion] = useState<(TeamInputReview & { revision: number; sourceRevision: string }) | null>(
    null
  )

  const inputRevision = useRef(0)
  const draftRevision = useRef(0)
  const improvementIntents = useRef<Partial<Record<'jobs' | 'goals', { fingerprint: string; key: string }>>>({})
  const [team, setTeam] = useState<TeamDraft | null>(null)
  const [preview, setPreview] = useState<TeamPreview | null>(null)
  const [receipt, setReceipt] = useState<TeamReceipt | null>(null)

  const [pending, setPending] = useState<
    'generate' | 'regenerate' | 'improve' | 'preview' | 'create' | 'check' | 'inspect' | 'reconcile' | 'budget' | null
  >(null)

  const [error, setError] = useState('')
  const [unresolvedCreate, setUnresolvedCreate] = useState(false)
  const [hasGenerated, setHasGenerated] = useState(false)
  const [allowanceUnverified, setAllowanceUnverified] = useState(false)
  const [budgetUnverified, setBudgetUnverified] = useState(false)
  const regenerationIntent = useRef<TeamGenerationIntent | null>(null)
  const operation = useRef(0)
  const busy = useRef(false)
  const alive = useRef(true)
  const generationIntent = useRef<TeamGenerationIntent | null>(null)
  const intent = useRef<{ version: string; key: string } | null>(null)
  // eslint-disable-next-line no-restricted-syntax -- Lifecycle cancellation invalidates pending results; this is not mirrored store state.
  useEffect(() => {
    alive.current = true

    return () => {
      alive.current = false
      // eslint-disable-next-line react-hooks/exhaustive-deps -- Invalidate the latest operation on unmount.
      operation.current++
    }
  }, [])

  const requestCurrent = (request: TeamUnsentRequest) => {
    const next = latestSetup.current

    if (
      !alive.current ||
      !client.current() ||
      !localRequestCheckSupported(next) ||
      next.organizationId !== request.organizationId ||
      next.sourceRevision !== request.sourceRevision
    ) {
      return false
    }

    if (request.operation === 'generate') {
      const retained = request.replace ? regenerationIntent.current : generationIntent.current

      return (
        retained?.key === request.idempotencyKey &&
        JSON.stringify(retained.inputs) === request.fingerprint &&
        retained.setup.organizationId === request.organizationId &&
        retained.setup.sourceRevision === request.sourceRevision &&
        request.revision === draftRevision.current
      )
    }

    const field = request.operation === 'improve_jobs' ? 'jobs' : 'goals'
    const retained = improvementIntents.current[field]

    return (
      retained?.key === request.idempotencyKey &&
      retained.fingerprint === request.fingerprint &&
      request.revision === inputRevision.current
    )
  }

  const recoveryCurrent = (request: TeamUnsentRequest) =>
    request.operation === 'generate' && requestCurrent(request) && recoveryAllowance(latestSetup.current, request)

  const act = async (kind: NonNullable<typeof pending>, action: () => Promise<void>, request?: TeamUnsentRequest) => {
    if (busy.current || !client.current()) {
      if (!client.current()) {
        setError(copy.stale)
      }

      return
    }

    busy.current = true
    const token = ++operation.current
    setPending(kind)
    setError('')

    try {
      await action()
    } catch (failure) {
      if (alive.current && token === operation.current) {
        setError(
          !client.current() || (failure instanceof Error && failure.message === 'stale')
            ? copy.stale
            : kind === 'reconcile' || kind === 'inspect'
              ? subscriptionCopy.checkUnsentBlocked
              : kind === 'improve' && failure instanceof Error
                ? failure.message
                : kind === 'check'
                  ? copy.checkFailed
                  : copy.error
        )

        // A captured original request enables only local proof, not an unsent claim.
        if (request && requestCurrent(request)) {
          retainedCheck.current = request
          setUnsentCheck(request)
        }

        if (request && readSubscriptionRecovery(failure, request) && recoveryCurrent(request)) {
          recoveryOffer.current = request
          setUnsentRequest(request)
        }

        if (['generate', 'regenerate'].includes(kind) && client.current()) {
          setAllowanceUnverified(true)

          try {
            const result = await client.setup(setup.organizationId ?? null)

            if (alive.current && token === operation.current && client.current()) {
              refreshSetup(result)
              setAllowanceUnverified(false)
            }
          } catch {
            // Keep the original error and require a successful passive check before new spend.
          }
        }
      }
    } finally {
      if (alive.current && token === operation.current) {
        busy.current = false
        setPending(null)
      }
    }
  }

  const applyPreview = (result: TeamPreview) => {
    if (result.organizationId !== setup.organizationId || result.sourceRevision !== setup.sourceRevision) {
      throw new Error('stale')
    }

    if (alive.current) {
      setPreview(result)
      setTeam(result.team)

      if (result.generationAllowance) {
        refreshSetup({
          ...latestSetup.current,
          generationAllowance: result.generationAllowance
        })
        setAllowanceUnverified(false)
      }

      intent.current = null
    }
  }

  const update = (value: TeamDraft) => {
    clearUnsent()
    draftRevision.current++
    setTeam(value)
    setPreview(null)
    intent.current = null
  }

  const changeInput = (field: keyof TeamSetupInputs, value: string) => {
    clearUnsent()
    inputRevision.current++
    draftRevision.current++
    setInputs(current => ({ ...current, [field]: value }))
    setSuggestion(null)
    generationIntent.current = null
  }

  const allowanceBlocked = (operation: 'improveTeamInput' | 'generateTeam') =>
    budgetUnverified ||
    allowanceUnverified ||
    (!!setup.generationAllowance &&
      (setup.generationAllowance.status !== 'available' ||
        (setup.generationAllowance.remainingByOperation?.[operation] ?? setup.generationAllowance.remainingCalls) ===
          0))

  const canImprove = (field: 'jobs' | 'goals') => !!inputs[field].trim()

  const improve = (field: 'jobs' | 'goals') => {
    if (busy.current || !inputs[field].trim()) {
      return
    }

    const revision = inputRevision.current
    setSuggestion(null)
    void act('improve', async () => {
      const result = await client.improve(setup, { ...inputs, field, idempotencyKey: crypto.randomUUID() })

      if (alive.current && client.current() && revision === inputRevision.current) {
        setSuggestion({ ...result, original: inputs[field], revision })
      }
    })
  }

  const generate = (replace = false) => {
    if (busy.current || setup.readiness.status === 'blocked') {
      return
    }

    if (replace && !regenerationIntent.current) {
      if (allowanceBlocked('generateTeam') || !setup.generationAllowance) {
        return
      }

      regenerationIntent.current = {
        key: crypto.randomUUID(),
        setup,
        inputs: { ...inputs },
        revision: draftRevision.current
      }
    }

    if (!replace && allowanceBlocked('generateTeam') && !generationIntent.current) {
      return
    }

    const request = replace
      ? regenerationIntent.current!
      : (generationIntent.current ??= {
          key: crypto.randomUUID(),
          setup,
          inputs: { ...inputs },
          revision: draftRevision.current
        })

    clearUnsent()
    void act(
      replace ? 'regenerate' : 'generate',
      async () => {
        const result = await client.generate(request.setup, {
          ...request.inputs,
          idempotencyKey: request.key
        })

        if (alive.current && client.current()) {
          if (request.revision !== draftRevision.current) {
            if (result.generationAllowance) {
              refreshSetup({
                ...latestSetup.current,
                generationAllowance: result.generationAllowance
              })
              setAllowanceUnverified(false)
            }

            if (replace) {
              regenerationIntent.current = null
            }

            setError(copy.previousDraftPreserved)

            return
          }

          applyPreview(result)
          setHasGenerated(true)
          generationIntent.current = request
          regenerationIntent.current = null
        }
      },
      {
        idempotencyKey: request.key,
        operation: 'generate',
        sourceRevision: request.setup.sourceRevision ?? '',
        organizationId: request.setup.organizationId ?? '',
        inputs: { ...request.inputs },
        fingerprint: JSON.stringify(request.inputs),
        revision: request.revision,
        replace
      }
    )
  }

  const findUnsent = () => {
    if (busy.current || !localRequestCheckSupported(latestSetup.current)) {
      return
    }

    const capturedSetup = latestSetup.current
    const capturedInputs = { ...inputs }
    const selected = unsentOperation
    const revision = selected === 'generate' ? draftRevision.current : inputRevision.current
    clearUnsent()
    const discovery = discoveryRevision.current
    void act('inspect', async () => {
      const found = await client.inspectSubscriptionRequest(capturedSetup, { ...capturedInputs, operation: selected })
      const currentRevision = selected === 'generate' ? draftRevision.current : inputRevision.current

      if (
        !alive.current ||
        !client.current() ||
        revision !== currentRevision ||
        discovery !== discoveryRevision.current ||
        latestSetup.current.sourceRevision !== capturedSetup.sourceRevision ||
        latestSetup.current.organizationId !== capturedSetup.organizationId ||
        !localRequestCheckSupported(latestSetup.current)
      ) {
        return
      }

      if (!found) {
        setError(subscriptionCopy.unsentNotFound)

        return
      }

      if (
        !readSubscriptionPendingRequest(found, { operation: selected, sourceRevision: capturedSetup.sourceRevision! })
      ) {
        throw new Error('invalid_pending_request')
      }

      const field = selected === 'improve_jobs' ? 'jobs' : 'goals'

      const fingerprint =
        selected === 'generate'
          ? JSON.stringify(capturedInputs)
          : JSON.stringify({ ...capturedInputs, sourceRevision: capturedSetup.sourceRevision, field })

      const request: TeamUnsentRequest = {
        idempotencyKey: found.idempotencyKey,
        operation: selected,
        sourceRevision: capturedSetup.sourceRevision!,
        organizationId: capturedSetup.organizationId!,
        inputs: capturedInputs,
        fingerprint,
        revision,
        replace: false
      }

      // Adopt the exact old key returned by discovery, without minting or sending.
      if (selected === 'generate') {
        generationIntent.current = { key: found.idempotencyKey, setup: capturedSetup, inputs: capturedInputs, revision }
      } else {
        improvementIntents.current[field] = { key: found.idempotencyKey, fingerprint }
      }

      retainedCheck.current = request
      setUnsentCheck(request)
    })
  }

  const checkUnsent = () => {
    const request = retainedCheck.current

    if (!request || busy.current || !requestCurrent(request)) {
      if (request && !requestCurrent(request)) {
        clearUnsent()
      }

      return
    }

    clearRecovery()
    setCheckedUnsent(false)
    void act('reconcile', async () => {
      const result = await client.reconcileSubscriptionRequest(latestSetup.current, {
        ...request.inputs,
        operation: request.operation,
        idempotencyKey: request.idempotencyKey
      })

      if (retainedCheck.current !== request || !requestCurrent(request)) {
        return
      }

      if (!readSubscriptionReconciledRequest(result, request)) {
        throw new Error('invalid_reconciled_request')
      }

      setCheckedUnsent(true)
      setAllowanceUnverified(true)
      const refreshed = await client.setup(request.organizationId)

      if (retainedCheck.current !== request || !requestCurrent(request)) {
        return
      }

      refreshSetup(refreshed)
      setAllowanceUnverified(false)

      if (recoveryCurrent(request)) {
        recoveryOffer.current = request
        setUnsentRequest(request)
      }
    })
  }

  const retryUnsent = () => {
    const request = recoveryOffer.current

    if (!request || busy.current || budgetUnverified || allowanceUnverified) {
      return
    }

    if (!recoveryCurrent(request)) {
      clearRecovery()

      return
    }

    // Consume synchronously before creating a fresh intent. Ordinary retry and
    // configuration checks keep the old key; only this explicit action rotates it.
    clearUnsent()
    const key = crypto.randomUUID()

    if (request.operation === 'generate') {
      const retained = request.replace ? regenerationIntent : generationIntent
      retained.current = { ...retained.current!, key }
      generate(request.replace)
    } else {
      const field = request.operation === 'improve_jobs' ? 'jobs' : 'goals'
      improvementIntents.current[field] = {
        fingerprint: request.fingerprint,
        key
      }
      improve(field)
    }
  }

  const applySuggestion = () => {
    if (!suggestion || busy.current || !client.current()) {
      if (!client.current()) {
        setError(copy.stale)
      }

      return false
    }

    if (suggestion.revision !== inputRevision.current || suggestion.sourceRevision !== setup.sourceRevision) {
      setSuggestion(null)

      return false
    }

    changeInput(suggestion.field, suggestion.suggestion)

    return true
  }

  const close = () => {
    if (pending === 'create') {
      return
    }

    recoveryOffer.current = null
    retainedCheck.current = null
    alive.current = false
    operation.current++
    onClose()
  }

  // Only the explicit model setup round-trip rechecks readiness. Never generate
  // or reconnect a provider as a side effect of returning to this draft.
  // eslint-disable-next-line no-restricted-syntax -- A one-shot return latch records the explicit provider round-trip, not provider state.
  useEffect(() => {
    if (providerSetup.manual) {
      returningFromProvider.current = true

      return
    }

    if (!returningFromProvider.current) {
      return
    }

    returningFromProvider.current = false

    if (!client.current()) {
      setError(copy.stale)

      return
    }

    const token = ++operation.current
    busy.current = true
    setPending('check')
    setError('')
    void client
      .setup(setup.organizationId ?? null)
      .then(result => {
        if (alive.current && token === operation.current && client.current()) {
          refreshSetup(result)
          setAllowanceUnverified(false)
        }
      })
      .catch(() => {
        if (alive.current && token === operation.current) {
          setError(client.current() ? copy.checkFailed : copy.stale)
        }
      })
      .finally(() => {
        if (alive.current && token === operation.current) {
          busy.current = false
          setPending(null)
        }
      })
  }, [providerSetup.manual, client, setup.organizationId, copy.checkFailed, copy.stale, refreshSetup])
  const modelBlocked = setup.readiness.status === 'blocked'

  return {
    copy,
    setup,
    providerSetup,
    inputs,
    team,
    preview: preview?.sourceRevision === setup.sourceRevision ? preview : null,
    receipt,
    pending,
    error,
    canRetryUnsent: !!unsentRequest && recoveryCurrent(unsentRequest) && !budgetUnverified && !allowanceUnverified,
    retryUnsent,
    canCheckUnsent: !!unsentCheck && requestCurrent(unsentCheck),
    checkUnsent,
    checkedUnsent,
    canFindUnsent: !team && localRequestCheckSupported(setup),
    findUnsent,
    unsentOperation,
    changeUnsentOperation: (value: SubscriptionRecovery['operation']) => {
      clearUnsent()
      setUnsentOperation(value)
    },
    unresolvedCreate,
    hasGenerated,
    allowanceUnverified,
    budgetUnverified,
    modelBlocked,
    close,
    update,
    changeInput,
    improve,
    generate,
    applySuggestion,
    canGenerate: !allowanceBlocked('generateTeam') || !!generationIntent.current,
    canImprove: { jobs: canImprove('jobs'), goals: canImprove('goals') },
    canRegenerate:
      !modelBlocked &&
      (!!regenerationIntent.current || (!allowanceBlocked('generateTeam') && !!setup.generationAllowance)),
    regenerationPending: !!regenerationIntent.current,
    suggestion: suggestion?.sourceRevision === setup.sourceRevision ? suggestion : null,
    discardSuggestion: () => setSuggestion(null),
    editSuggestion: (value: string) => setSuggestion(current => (current ? { ...current, suggestion: value } : null)),
    back: () => {
      setTeam(null)
      setPreview(null)
    },
    setBudgetBusy: (value: boolean) => {
      busy.current = value
      setPending(value ? 'budget' : null)
    },
    invalidateBudget: () => {
      clearUnsent()
      setBudgetUnverified(true)
      setPreview(null)
      setSuggestion(null)
      intent.current = null
      generationIntent.current = null
      regenerationIntent.current = null
      improvementIntents.current = {}
      setAllowanceUnverified(true)
    },
    refreshBudgetSetup: (result: TeamSetup) => {
      setBudgetUnverified(false)
      refreshSetup(result)
      setAllowanceUnverified(false)
    },
    check: () =>
      void act('check', async () => {
        const result = await client.setup(setup.organizationId ?? null)

        if (alive.current && client.current()) {
          refreshSetup(result)
          setAllowanceUnverified(false)
        }
      }),
    validate: () => {
      if (team && !budgetUnverified) {
        void act('preview', async () => applyPreview(await client.preview(setup, team)))
      }
    },
    create: () => {
      if (preview && preview.sourceRevision === setup.sourceRevision && !budgetUnverified) {
        void act('create', async () => {
          setUnresolvedCreate(true)
          intent.current ??= { version: preview.previewVersion, key: crypto.randomUUID() }
          const result = await client.create(preview, intent.current.key)

          if (alive.current) {
            setReceipt(result)
            setUnresolvedCreate(false)
            onCreated()
          }
        })
      }
    }
  }
}

export function TeamOnboardingDialog(props: TeamOnboardingDialogProps) {
  const { profile, onReturnFocus } = props
  const { locale } = useI18n()
  const subscriptionCopy = setupSubscriptionCopy[locale]

  const {
    copy,
    setup,
    providerSetup,
    inputs,
    team,
    preview,
    receipt,
    pending,
    error,
    canRetryUnsent,
    retryUnsent,
    canCheckUnsent,
    checkUnsent,
    checkedUnsent,
    canFindUnsent,
    findUnsent,
    unsentOperation,
    changeUnsentOperation,
    unresolvedCreate,
    hasGenerated,
    allowanceUnverified,
    budgetUnverified,
    modelBlocked,
    close: closeDraft,
    update,
    changeInput,
    improve,
    generate,
    applySuggestion,
    canGenerate,
    canImprove,
    canRegenerate,
    regenerationPending,
    suggestion,
    discardSuggestion,
    editSuggestion,
    setBudgetBusy,
    invalidateBudget,
    refreshBudgetSetup,
    back,
    check,
    validate,
    create
  } = useTeamOnboardingDraft(props)

  const subscriptionSelected =
    setup.setupSubscriptionVersion === 1 &&
    (setup.generationAllowance?.policyVersion === 3 ||
      setup.readiness.blockers.includes('subscription_generation_unsupported'))

  const readinessId = useId()
  const cancelKeyboardReveal = useRef<(() => void) | undefined>(undefined)
  useLayoutEffect(
    () => () => cancelKeyboardReveal.current?.(),
    [
      props.client,
      profile,
      props.setup.organizationId,
      props.setup.sourceRevision,
      setup.organizationId,
      setup.sourceRevision,
      providerSetup.manual
    ]
  )

  const close = () => {
    cancelKeyboardReveal.current?.()
    closeDraft()
  }

  return (
    <Dialog
      onOpenChange={open => {
        if (!open) {
          close()
        }
      }}
      open={!providerSetup.manual}
    >
      <DialogContent
        className="eid-team-dialog"
        onCloseAutoFocus={event => {
          event.preventDefault()

          if (!$desktopOnboarding.get().manual) {
            onReturnFocus()
          }
        }}
        onEscapeKeyDown={event => {
          if (pending === 'create') {
            event.preventDefault()
          }
        }}
      >
        <DialogTitle>{receipt ? copy.ready : copy.title}</DialogTitle>
        <DialogDescription>{receipt ? copy.readyNote : copy.intro}</DialogDescription>
        <div
          className="eid-team-body"
          onKeyDownCapture={event => {
            cancelKeyboardReveal.current?.()

            if (event.key === 'Tab' && props.client.current()) {
              cancelKeyboardReveal.current = queueKeyboardReveal(
                event.currentTarget,
                event.currentTarget,
                teamInputKeyboardContext
              )
            }
          }}
        >
          {receipt ? (
            <>
              <p className="eid-team-note">{copy.inherited}</p>
              <p role="status">{readinessMessage(receipt.readiness, copy, true)}</p>
              <Button asChild>
                <Link onClick={close} to="/home?intake=assisted#eid-home-intake">
                  {copy.firstRequest}
                </Link>
              </Button>
            </>
          ) : (
            <>
              <div className="eid-team-actions">
                <Button
                  disabled={!!pending || unresolvedCreate}
                  onClick={() => startManualOnboarding(null, profile)}
                  size="sm"
                  variant="secondary"
                >
                  {copy.modelSettings}
                </Button>
                <Button disabled={!!pending || unresolvedCreate} onClick={check} size="sm" variant="text">
                  {copy.checkConfiguration}
                </Button>
              </div>
              <div id={readinessId} role="status">
                <p>
                  {subscriptionSelected
                    ? modelBlocked
                      ? setup.generationAllowance?.policyVersion === 3
                        ? subscriptionCopy.blocked
                        : subscriptionCopy.intro
                      : subscriptionCopy.readyForSetup
                    : readinessMessage(setup.readiness, copy)}
                </p>
                {(setup.generationAllowance || allowanceUnverified) && (
                  <p className="eid-team-note">
                    {allowanceUnverified
                      ? copy.allowanceBlocked
                      : {
                          available: (setup.generationAllowance?.policyVersion === 3
                            ? subscriptionCopy.allowanceAvailable
                            : copy.allowanceAvailable
                          ).replace('{count}', String(setup.generationAllowance!.remainingCalls)),
                          exhausted: copy.allowanceExhausted,
                          running: copy.allowanceRunning,
                          uncertain: copy.allowanceUncertain,
                          blocked: copy.allowanceBlocked
                        }[setup.generationAllowance!.status]}
                  </p>
                )}
              </div>
              {setup.generationAllowance?.remainingByOperation && !allowanceUnverified && (
                <SetupBudgetRemaining
                  generate={setup.generationAllowance.remainingByOperation.generateTeam}
                  improve={setup.generationAllowance.remainingByOperation.improveTeamInput}
                  total={setup.generationAllowance.remainingCalls}
                />
              )}
              {subscriptionSelected ? (
                <TeamSetupSubscription
                  client={props.client}
                  disabled={(!!pending && pending !== 'budget') || unresolvedCreate}
                  onBusyChange={setBudgetBusy}
                  onInvalidate={invalidateBudget}
                  onRefreshed={refreshBudgetSetup}
                  setup={setup}
                />
              ) : (
                <TeamSetupBudgetEditor
                  client={props.client}
                  disabled={(!!pending && pending !== 'budget') || unresolvedCreate}
                  onBusyChange={setBudgetBusy}
                  onInvalidate={invalidateBudget}
                  onRefreshed={refreshBudgetSetup}
                  setup={setup}
                />
              )}
              {!team ? (
                <>
                  <TeamOnboardingInputs
                    canGenerate={canGenerate}
                    canImprove={canImprove}
                    copy={copy}
                    disabled={!!pending || unresolvedCreate}
                    improvementSupported={true}
                    inputs={inputs}
                    modelBlocked={modelBlocked}
                    onApply={applySuggestion}
                    onChange={changeInput}
                    onDiscard={discardSuggestion}
                    onGenerate={() => generate()}
                    onImprove={improve}
                    onSuggestionChange={editSuggestion}
                    readinessId={readinessId}
                    review={suggestion}
                  />
                  <details>
                    <summary>{copy.manual}</summary>
                    <Button
                      disabled={!!pending || unresolvedCreate}
                      onClick={() =>
                        update({ executive: { name: '', purpose: '', responsibilities: [] }, members: [] })
                      }
                      variant="secondary"
                    >
                      {copy.manualStart}
                    </Button>
                    <p>{copy.presets}</p>
                    <div className="eid-team-actions">
                      {setup.presets.map(preset => (
                        <Button
                          disabled={!!pending || unresolvedCreate}
                          key={preset.id}
                          onClick={() => update(structuredClone(preset.team))}
                          size="sm"
                          variant="secondary"
                        >
                          {preset.id === 'research-writing' ? copy.researchPreset : preset.name}
                        </Button>
                      ))}
                    </div>
                  </details>
                </>
              ) : (
                <>
                  <p className="eid-team-note">{copy.reviewCoverage}</p>
                  {hasGenerated && (
                    <div>
                      <p className="eid-team-note" id={`${readinessId}-regenerate`}>
                        {copy.regenerateHelp}
                      </p>
                      <Button
                        aria-describedby={`${readinessId} ${readinessId}-regenerate`}
                        disabled={!!pending || unresolvedCreate || !canRegenerate}
                        onClick={() => generate(true)}
                        size="sm"
                        variant="secondary"
                      >
                        {regenerationPending ? copy.retryRegeneration : copy.regenerate}
                      </Button>
                    </div>
                  )}
                  <TeamOnboardingEditor
                    copy={copy}
                    disabled={!!pending || unresolvedCreate}
                    onChange={update}
                    team={team}
                  />
                </>
              )}
              <p className="eid-team-note">{copy.authority}</p>
              {preview && (
                <>
                  <p className="eid-team-note">{copy.inherited}</p>
                  <details>
                    <summary>{copy.details}</summary>
                    <p>{copy.retainedSupport}</p>
                    {!!preview.supportAgents?.length && (
                      <div>
                        <p>{copy.supportIdentities}</p>
                        <ul>
                          {preview.supportAgents.map(member => (
                            <li key={member.id}>
                              <bdi>{member.name}</bdi>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </details>
                </>
              )}
            </>
          )}
          {(receipt?.readiness ?? preview?.readiness)?.warnings?.includes('new_member_routes_unassigned') && (
            <p className="eid-team-note">{copy.inheritedRoutes}</p>
          )}
          {canFindUnsent && !canCheckUnsent && !canRetryUnsent && !receipt && (
            <details>
              <summary>{subscriptionCopy.findUnsent}</summary>
              <p className="eid-team-note" id={`${readinessId}-find-unsent`}>
                {subscriptionCopy.unsentRestartHelp}
              </p>
              <label>
                {subscriptionCopy.unsentOperation}
                <select
                  aria-describedby={`${readinessId}-find-unsent`}
                  disabled={!!pending || unresolvedCreate}
                  onChange={event => changeUnsentOperation(event.target.value as SubscriptionRecovery['operation'])}
                  value={unsentOperation}
                >
                  <option value="generate">{copy.generate}</option>
                  <option value="improve_jobs">{copy.improveJobs}</option>
                  <option value="improve_goals">{copy.improveGoals}</option>
                </select>
              </label>
              <Button disabled={!!pending || unresolvedCreate} onClick={findUnsent} size="sm" variant="secondary">
                {subscriptionCopy.findUnsent}
              </Button>
            </details>
          )}
          {checkedUnsent && !canRetryUnsent && !receipt && (
            <p role="status">
              {subscriptionCopy.unsentVerified
                .replace('{setup}', subscriptionCopy.inspect)
                .replace('{models}', subscriptionCopy.loadModels)}
            </p>
          )}
          {canCheckUnsent && !canRetryUnsent && !receipt && (
            <div>
              <p className="eid-team-note" id={`${readinessId}-check-unsent`}>
                {subscriptionCopy.checkUnsentHelp}
              </p>
              <Button
                aria-describedby={`${readinessId}-check-unsent`}
                disabled={!!pending || unresolvedCreate}
                onClick={checkUnsent}
                size="sm"
                variant="secondary"
              >
                {subscriptionCopy.checkUnsent}
              </Button>
            </div>
          )}
          {canRetryUnsent && !receipt && (
            <div>
              <p className="eid-team-note" id={`${readinessId}-retry-unsent`}>
                {subscriptionCopy.retryUnsentHelp}
              </p>
              <Button
                aria-describedby={`${readinessId}-retry-unsent`}
                disabled={!!pending || unresolvedCreate}
                onClick={retryUnsent}
                size="sm"
                variant="secondary"
              >
                {subscriptionCopy.retryUnsent}
              </Button>
            </div>
          )}
          {pending && pending !== 'budget' && (
            <Loader
              label={
                pending === 'improve'
                  ? copy.improving
                  : pending === 'generate' || pending === 'regenerate'
                    ? copy.generating
                    : pending === 'create'
                      ? copy.creating
                      : copy.checking
              }
            />
          )}
          {error && <p role="alert">{error}</p>}
        </div>
        <footer className="eid-team-actions">
          <Button disabled={pending === 'create'} onClick={close} variant="text">
            {copy.cancel}
          </Button>
          {team && !receipt && (
            <>
              <Button disabled={!!pending || unresolvedCreate} onClick={back} variant="secondary">
                {copy.back}
              </Button>
              {!preview ? (
                <Button disabled={!!pending || unresolvedCreate || budgetUnverified} onClick={validate}>
                  {copy.validate}
                </Button>
              ) : (
                <Button disabled={!!pending || budgetUnverified} onClick={create}>
                  {copy.create}
                </Button>
              )}
            </>
          )}
        </footer>
      </DialogContent>
    </Dialog>
  )
}
