import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'

import type { ObjectivePriority, PriorityChange } from './runtime-priority-types'
import type { Objective, OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

export function RuntimePriority({ objective, adapter, snapshot }: {
  objective: Objective
  adapter: RuntimeOrganizationAdapter
  snapshot: OrganizationSnapshot
}) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const [priority, setPriority] = useState(objective.priority as ObjectivePriority)
  const [revision, setRevision] = useState(objective.priorityRevision)
  const [intent, setIntent] = useState<PriorityChange | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const lock = useRef(false)
  const active = useRef(true)
  const editable = !objective.history?.archived && !['completed', 'cancelled', 'archived'].includes(objective.status)
  const supported = !!adapter.changePriority && Number.isSafeInteger(objective.priorityRevision) && objective.priorityRevision! >= 0 && objective.priorityRevision! < 100

  const current = () => {
    const latest = adapter.getSnapshot()

    return active.current && latest.connection?.state === 'ready' && !!snapshot.runtime?.profile
      && latest.connection.scope === snapshot.connection?.scope
      && latest.connection.ownerScope === snapshot.connection?.ownerScope
      && latest.runtime?.profile === snapshot.runtime.profile
  }

  // eslint-disable-next-line no-restricted-syntax -- fence completion messages after navigation
  useEffect(() => {
    active.current = true

    return () => { active.current = false }
  }, [])

  const save = async () => {
    // A retained exact retry can recover a receipt after editing becomes ineligible.
    if (!current() || !adapter.changePriority || lock.current || (!intent && (!editable || !supported))) {return}
    const request = intent ?? { id: objective.id, priority, expectedRevision: revision!, idempotencyKey: crypto.randomUUID() }
    lock.current = true
    setBusy(true)
    setIntent(request)
    setError('')
    setMessage('')

    try {
      const receipt = await adapter.changePriority!(request)

      if (current()) {
        setMessage(copy.prioritySaved.replace('{previous}', receipt.previousPriority).replace('{priority}', receipt.priority).replace('{revision}', String(receipt.revision)))
        setRevision(receipt.revision)
        setPriority(receipt.priority)
        setIntent(null)
      }
    } catch (reason) {
      if (current()) {setError(reason instanceof Error ? reason.message : copy.priorityInvalid)}
    } finally {
      lock.current = false

      if (active.current) {setBusy(false)}
    }
  }

  const review = () => {
    if (lock.current) {return}
    setIntent(null)
    setPriority(objective.priority as ObjectivePriority)
    setRevision(objective.priorityRevision)
    setError('')
    setMessage('')
  }

  return <section aria-label={copy.priorityHeading}>
    <h2>{copy.priorityHeading}</h2>
    <p>{copy.priorityCurrent}: {objective.priority} · {copy.priorityVersion}: {objective.priorityRevision ?? copy.notRecorded}</p>
    {!editable ? <p>{copy.priorityTerminal}</p> : !supported ? <p>{copy.priorityUnavailable}</p> : null}
    {((editable && supported) || (intent && adapter.changePriority)) && <>
      {editable && supported && <><label>{copy.priorityNew}<select aria-label={copy.priorityNew} disabled={busy || !!intent || !current()} onChange={event => setPriority(event.target.value as ObjectivePriority)} value={priority}>
        {(['P1', 'P2', 'P3', 'P4', 'P5'] as const).map(value => <option key={value} value={value}>{value}</option>)}
      </select></label>
      <p>{copy.priorityVersion}: {revision}</p></>}
      <div className="eid-inline">
        <Button disabled={busy || !current() || (!intent && priority === objective.priority)} onClick={() => void save()} size="sm" variant="secondary">{busy ? copy.prioritySaving : intent ? copy.priorityRetry : copy.prioritySave}</Button>
        <Button disabled={busy || !current()} onClick={review} size="sm" variant="secondary">{copy.priorityReview}</Button>
      </div>
      <p className="eid-note">{copy.prioritySemantics}</p>
    </>}
    {error && <p role="alert">{error} {copy.priorityUncertain}</p>}
    {message && <p role="status">{message}</p>}
  </section>
}
