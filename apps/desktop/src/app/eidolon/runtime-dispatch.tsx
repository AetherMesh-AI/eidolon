import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'

import { type DispatchChange, validDispatch } from './runtime-dispatch-types'
import type { Objective, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

export function RuntimeDispatch({ objective, adapter, snapshot }: { objective: Objective; adapter: RuntimeOrganizationAdapter; snapshot: OrganizationSnapshot }) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const state = objective.dispatchControl
  const [reviewedRevision, setReviewedRevision] = useState(state?.revision)
  const [intent, setIntent] = useState<DispatchChange | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const lock = useRef(false)
  const active = useRef(true)

  const editable = !objective.history?.archived && !['completed', 'cancelled', 'archived'].includes(objective.status)
    && validDispatch(state) && state.revision < 100

  const needsReview = validDispatch(state) && reviewedRevision !== state.revision

  const current = () => {
    const latest = adapter.getSnapshot()

    return active.current && latest.connection?.state === 'ready' && !!snapshot.runtime?.profile
      && latest.connection.scope === snapshot.connection?.scope && latest.connection.ownerScope === snapshot.connection?.ownerScope
      && latest.runtime?.profile === snapshot.runtime.profile
  }

  // eslint-disable-next-line no-restricted-syntax -- fence late receipts after navigation
  useEffect(() => {
    active.current = true

    return () => { active.current = false }
  }, [])

  const save = async () => {
    if (!current() || !adapter.setObjectivePaused || lock.current || (!intent && (!editable || needsReview))) {return}
    const request = intent ?? { id: objective.id, paused: !state!.paused, expectedRevision: state!.revision, idempotencyKey: crypto.randomUUID() }
    lock.current = true; setBusy(true); setIntent(request); setError(''); setMessage('')

    try {
      const receipt = await adapter.setObjectivePaused(request)

      if (current()) {
        setMessage(`${copy.dispatchConfirmed} · ${copy.dispatchReceiptRevision}: ${receipt.revision}`)
        // An older receipt confirms history; a newer snapshot still requires review.
        setReviewedRevision(receipt.revision); setIntent(null)
      }
    } catch (reason) {
      if (current()) {setError(reason instanceof Error ? reason.message : copy.dispatchInvalid)}
    } finally {
      lock.current = false

      if (active.current) {setBusy(false)}
    }
  }

  return <section aria-label={copy.dispatchHeading}>
    <h2>{copy.dispatchHeading}</h2>
    <p>{validDispatch(state) ? `${state.paused ? copy.dispatchPaused : copy.dispatchOpen} · ${copy.dispatchRevision}: ${state.revision}` : copy.dispatchUnavailable}</p>
    {validDispatch(state) && <p>{copy.dispatchRunning}: {state.runningCount}</p>}
    <p className="eid-note">{copy.dispatchBoundary}</p>
    {!editable && <p>{copy.dispatchRestricted}</p>}
    {(editable || intent) && adapter.setObjectivePaused && <div className="eid-inline">
      <Button disabled={busy || !current() || (!intent && needsReview)} onClick={() => void save()} size="sm" variant="secondary">{busy ? copy.dispatchSaving : intent ? copy.dispatchRetry : state?.paused ? copy.dispatchResume : copy.dispatchPause}</Button>
      <Button disabled={busy || !current()} onClick={() => { setIntent(null); setReviewedRevision(state?.revision); setError(''); setMessage('') }} size="sm" variant="secondary">{copy.dispatchReview}</Button>
    </div>}
    {needsReview && !intent && editable && <p>{copy.dispatchReviewRequired}</p>}
    {error && <p role="alert">{error} {copy.priorityUncertain}</p>}
    {message && <p role="status">{message}</p>}
  </section>
}
