import { useEffect, useRef, useState } from 'react'

import type { TeamOnboardingClient, TeamSetup } from './team-onboarding-client'
import {
  type SubscriptionCatalog,
  type SubscriptionPolicy,
  type SubscriptionPolicyState,
  type SubscriptionPreview,
  type SubscriptionStatus,
  type SubscriptionTicket,
  validateSubscriptionPolicy
} from './team-setup-subscription-client'

export interface TeamSetupSubscriptionProps {
  client: TeamOnboardingClient
  setup: TeamSetup
  disabled: boolean
  onBusyChange: (value: boolean) => void
  onInvalidate: () => void
  onRefreshed: (setup: TeamSetup) => void
}

const initialDraft = {
  improveCallLimit: '4',
  generationCallLimit: '3',
  totalCallLimit: '7',
  requestTimeoutSeconds: '120'
}

export type SubscriptionDraft = typeof initialDraft

export function useTeamSetupSubscription({
  client,
  setup,
  onBusyChange,
  onInvalidate,
  onRefreshed
}: TeamSetupSubscriptionProps) {
  const [opened, setOpened] = useState(false)
  const [status, setStatus] = useState<SubscriptionStatus | null>(null)
  const [policy, setPolicy] = useState<SubscriptionPolicyState | null>(null)
  const [catalog, setCatalog] = useState<SubscriptionCatalog | null>(null)
  const [model, setModel] = useState('')
  const [draft, setDraft] = useState(initialDraft)
  const [consent, setConsent] = useState(false)
  const [acknowledged, setAcknowledged] = useState(false)
  const [preview, setPreview] = useState<SubscriptionPreview | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(false)
  const [errorReason, setErrorReason] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [ticket, setTicket] = useState<SubscriptionTicket | null>(null)
  const initialized = useRef(false)
  const busy = useRef(false)
  const alive = useRef(true)
  const action = useRef(0)
  const applyIntent = useRef<{ preview: SubscriptionPreview; key: string } | null>(null)
  // eslint-disable-next-line no-restricted-syntax -- Retire this editor's in-flight replies when it leaves the owner dialog.
  useEffect(() => {
    alive.current = true

    return () => {
      alive.current = false
      // eslint-disable-next-line react-hooks/exhaustive-deps -- Retire the latest request token, not a DOM ref.
      action.current++
    }
  }, [])
  const current = (serial: number) => alive.current && action.current === serial && client.current()

  const act = async (run: (serial: number) => Promise<void>) => {
    if (busy.current || !client.current()) {
      return
    }

    const serial = ++action.current
    busy.current = true
    setPending(true)
    setError(false)
    setErrorReason(null)
    onBusyChange(true)

    try {
      await run(serial)
    } catch (failure) {
      if (current(serial)) {
        setError(true)
        const reason = (failure as { data?: { reason?: unknown } } | null)?.data?.reason

        if (typeof reason === 'string' && /^[a-z][a-z0-9_]{0,127}$/.test(reason)) {
          setErrorReason(reason)
        }

        // A failed mutation may have committed. Re-read setup only; never resend
        // consent, refresh, policy apply or inference to recover a missing reply.
        try {
          const next = await client.setup(setup.organizationId ?? null)

          if (current(serial) && next.state === 'available' && next.organizationId === setup.organizationId) {
            onRefreshed(next)
          }
        } catch {
          // Keep the source unverified until the owner explicitly checks again.
        }
      }
    } finally {
      if (current(serial)) {
        setPending(false)
      }

      busy.current = false
      // The parent owns its own liveness guard; release the explicit action latch.
      onBusyChange(false)
    }
  }

  const read = async (serial: number, publish = true) => {
    if (!current(serial)) {
      throw new Error('stale')
    }

    const next = await client.setup(setup.organizationId ?? null)

    if (next.state !== 'available' || next.organizationId !== setup.organizationId) {
      throw new Error('stale')
    }

    if (!current(serial)) {
      throw new Error('stale')
    }

    const connection = await client.subscriptionStatus(next)

    if (!current(serial)) {
      throw new Error('stale')
    }

    const policyState = await client.subscriptionPolicy(next)

    if (!current(serial)) {
      throw new Error('stale')
    }

    setStatus(connection)
    setTicket(existing => (existing?.priorStoreRevision === connection.connectionRevision ? existing : null))
    setPolicy(policyState)

    if (!initialized.current) {
      initialized.current = true

      if (policyState.policyVersion === 3) {
        const existing = policyState.configuration as SubscriptionPolicy
        setDraft({
          improveCallLimit: String(existing.improveCallLimit),
          generationCallLimit: String(existing.generationCallLimit),
          totalCallLimit: String(existing.totalCallLimit),
          requestTimeoutSeconds: String(existing.requestTimeoutSeconds)
        })
      }
    }

    setCatalog(existing =>
      existing &&
      existing.sourceRevision === next.sourceRevision &&
      existing.connectionRevision === connection.connectionRevision
        ? existing
        : null
    )

    if (publish) {
      onRefreshed(next)
    }

    return { next, connection, policyState }
  }

  const invalidate = () => {
    setCatalog(null)
    setPreview(null)
    setAcknowledged(false)
    setSaved(false)
    onInvalidate()
  }

  const candidate = (): SubscriptionPolicy | null => {
    if (
      !catalog ||
      catalog.sourceRevision !== setup.sourceRevision ||
      !catalog.models.some(row => row.slug === model) ||
      Object.values(draft).some(value => !/^\d+$/.test(value))
    ) {
      return null
    }

    const value = {
      version: 3,
      mode: 'subscription_calls',
      connectionId: catalog.connectionId,
      authorizationEpoch: catalog.authorizationEpoch,
      modelSlug: model,
      ...Object.fromEntries(Object.entries(draft).map(([key, value]) => [key, Number(value)]))
    }

    try {
      validateSubscriptionPolicy(value)

      return value
    } catch {
      return null
    }
  }

  const applicablePreview = preview?.sourceRevision === setup.sourceRevision ? preview : null
  const connectionPending = !!status?.attemptId && ['pending', 'exchanging'].includes(status.stage)

  return {
    opened,
    status,
    policy: policy?.sourceRevision === setup.sourceRevision ? policy : null,
    catalog: catalog?.sourceRevision === setup.sourceRevision ? catalog : null,
    model,
    draft,
    consent,
    acknowledged,
    preview: applicablePreview,
    pending,
    error,
    errorReason,
    saved,
    connectionPending,
    canReview:
      !!candidate() && !!policy && policy.sourceRevision === setup.sourceRevision && policy.state === 'available',
    setConsent,
    setAcknowledged,
    change: (field: keyof SubscriptionDraft, value: string) => {
      setDraft(previous => ({ ...previous, [field]: value }))
      setPreview(null)
      setAcknowledged(false)
      applyIntent.current = null
    },
    choose: (value: string) => {
      setModel(value)
      setPreview(null)
      setAcknowledged(false)
      applyIntent.current = null
    },
    inspect: () => {
      setOpened(true)
      void act(async serial => {
        await read(serial)
      })
    },
    check: () =>
      void act(async serial => {
        await read(serial)
      }),
    begin: () =>
      void act(async serial => {
        if (!consent || !status) {
          return
        }

        invalidate()
        await client.beginSubscription(setup, status.connectionRevision)

        if (current(serial)) {
          setConsent(false)
        }

        await read(serial)
      }),
    cancel: () =>
      void act(async serial => {
        if (!status?.attemptId) {
          return
        }

        invalidate()
        await client.cancelSubscription(setup, status.attemptId)
        await read(serial)
      }),
    models: () =>
      void act(async serial => {
        invalidate()
        setPreview(null)
        setAcknowledged(false)
        applyIntent.current = null
        const { next, connection, policyState } = await read(serial, false)

        if (!current(serial)) {
          throw new Error('stale')
        }

        const result = await client.subscriptionModels(next, connection.connectionRevision)

        if (!current(serial)) {
          throw new Error('stale')
        }

        const refreshed = await client.setup(next.organizationId ?? null)

        if (!current(serial) || refreshed.sourceRevision !== next.sourceRevision) {
          throw new Error('stale')
        }

        setCatalog(result)
        const active = policyState.policyVersion === 3 ? (policyState.configuration as SubscriptionPolicy) : null
        setModel(previous =>
          result.models.some(row => row.slug === previous)
            ? previous
            : active?.modelSlug && result.models.some(row => row.slug === active.modelSlug)
              ? active.modelSlug
              : ''
        )
        onRefreshed(refreshed)
      }),
    review: () =>
      void act(async serial => {
        const configuration = candidate()

        if (!configuration || !policy) {
          return
        }

        setPreview(null)
        setAcknowledged(false)
        applyIntent.current = null
        const result = await client.previewSubscriptionPolicy(policy, configuration)

        if (current(serial)) {
          setPreview(result)
        }
      }),
    apply: () =>
      void act(async serial => {
        if (!applicablePreview || !acknowledged) {
          return
        }

        const intent = (applyIntent.current ??= { preview: applicablePreview, key: crypto.randomUUID() })
        invalidate()
        await client.applySubscriptionPolicy(intent.preview, intent.key)
        await read(serial)

        if (current(serial)) {
          setSaved(true)
          applyIntent.current = null
        }
      }),
    operate: (kind: 'refresh' | 'revoke') =>
      void act(async serial => {
        if (!status) {
          return
        }

        invalidate()
        const prepared = await client.prepareSubscriptionOperation(setup, kind, status.connectionRevision)

        if (!current(serial)) {
          throw new Error('stale')
        }

        if (prepared.ticket && prepared.state === 'prepared') {
          if (current(serial)) {
            setTicket(prepared.ticket)
          }

          const result = await client.executeSubscriptionOperation(setup, prepared.ticket)

          if (current(serial) && ['completed', 'local_retired', 'cancelled'].includes(result.state)) {
            setTicket(null)
          }
        }

        await read(serial)
      }),
    recover: () =>
      void act(async serial => {
        invalidate()

        if (ticket) {
          const result = await client.reconcileSubscriptionOperation(setup, ticket)

          if (current(serial) && ['completed', 'local_retired', 'cancelled'].includes(result.state)) {
            setTicket(null)
          }
        } else {
          await client.recoverSubscriptionOperation(setup)
        }

        await read(serial)
      })
  }
}
