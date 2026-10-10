import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'

import { $intakeDrafts, setIntakeDraft } from './runtime-intake-drafts'
import type { OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

export function SubmissionCheck({
  adapter,
  draftKey,
  snapshot
}: {
  adapter: RuntimeOrganizationAdapter
  draftKey: string
  snapshot: OrganizationSnapshot
}) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const draft = useStore($intakeDrafts)[draftKey]
  const active = useRef(true)
  // A failed snapshot/config projection does not revoke an already verified
  // socket/profile. The read adapter still requires a live, unchanged scope.
  const ready = ['ready', 'error'].includes(snapshot.connection?.state ?? '') && !!snapshot.runtime?.profile

  // eslint-disable-next-line no-restricted-syntax -- fence detached read callbacks, not mirrored shared state
  useEffect(() => {
    active.current = true

    return () => {
      active.current = false
    }
  }, [])

  const check = async () => {
    const current = $intakeDrafts.get()[draftKey]
    const connection = snapshot.connection

    if (
      !ready ||
      !adapter.checkSubmission ||
      !current?.idempotencyKey ||
      current.busy ||
      current.checking ||
      current.receipt
    ) {
      return
    }

    const idempotencyKey = current.idempotencyKey
    setIntakeDraft(draftKey, { ...current, checking: true, submissionMissing: false, error: undefined })

    try {
      const receipt = await adapter.checkSubmission(idempotencyKey)
      const latest = $intakeDrafts.get()[draftKey]
      const scope = adapter.getSnapshot().connection

      if (
        active.current &&
        scope &&
        ['ready', 'error'].includes(scope.state) &&
        scope.scope === connection?.scope &&
        scope.ownerScope === connection?.ownerScope &&
        latest?.idempotencyKey === idempotencyKey
      ) {
        setIntakeDraft(draftKey, {
          ...latest,
          receipt: receipt.objective ? receipt : undefined,
          submissionMissing: !receipt.objective
        })
      }
    } catch (reason) {
      const latest = $intakeDrafts.get()[draftKey]

      if (active.current && latest?.idempotencyKey === idempotencyKey) {
        setIntakeDraft(draftKey, {
          ...latest,
          error: reason instanceof Error ? reason.message : copy.intakeCheckInvalid
        })
      }
    } finally {
      const latest = $intakeDrafts.get()[draftKey]

      if (latest?.idempotencyKey === idempotencyKey) {
        setIntakeDraft(draftKey, { ...latest, checking: false })
      }
    }
  }

  return (
    <div>
      {!draft?.receipt && (
        <Button disabled={!ready || draft?.busy || draft?.checking} onClick={() => void check()} variant="secondary">
          {draft?.checking ? copy.intakeChecking : copy.intakeCheck}
        </Button>
      )}
      {draft?.submissionMissing && <p role="status">{copy.intakeCheckMissing}</p>}
      {draft?.receipt?.objective && (
        <div role="status">
          <p>{copy.intakeCheckFound}</p>
          <p>
            {copy.intakeCheckProfile.replace('{profile}', draft.receipt.profile)} ·{' '}
            {draft.receipt.objective.archived ? copy.historyArchived : copy.historyCurrent}
          </p>
          <Link to={`/objectives/${encodeURIComponent(draft.receipt.objective.id)}`}>
            {draft.receipt.objective.title}
          </Link>
          <p>
            <time dateTime={draft.receipt.objective.createdAt}>{draft.receipt.objective.createdAt}</time>
          </p>
        </div>
      )}
    </div>
  )
}
