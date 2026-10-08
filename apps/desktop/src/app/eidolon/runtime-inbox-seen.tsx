import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n/context'

import type { OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

export function sameConnection(adapter: RuntimeOrganizationAdapter, snapshot: OrganizationSnapshot) {
  const current = adapter.getSnapshot().connection

  return (
    current?.scope === snapshot.connection?.scope &&
    current?.ownerScope === snapshot.connection?.ownerScope &&
    current?.state === 'ready'
  )
}

interface SeenControlProps {
  adapter: RuntimeOrganizationAdapter
  snapshot: OrganizationSnapshot
  item: { id: string; revision: number; seen: boolean }
  markSeen?(input: { id: string; revision: number }): Promise<void>
  errorTitle: string
  disabled: boolean
  onChanged(): void
}

export function RevisionSeenControl({ adapter, snapshot, item, markSeen: acknowledge, errorTitle, disabled, onChanged }: SeenControlProps) {
  const { t } = useI18n()
  const copy = t.organizationWork
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const sending = useRef(false)
  const epoch = useRef(0)

  // eslint-disable-next-line no-restricted-syntax -- fence the lifetime of this exact revision control
  useEffect(() => {
    const invalidated = epoch.current + 1
    sending.current = false
    setPending(false)
    setError('')

    return () => {
      epoch.current = invalidated
    }
  }, [item.id, item.revision, snapshot.connection?.ownerScope, snapshot.connection?.scope, snapshot.connection?.state])

  const markSeen = async () => {
    if (sending.current || disabled || item.seen || !acknowledge || !sameConnection(adapter, snapshot)) {
      return
    }

    const version = epoch.current
    sending.current = true
    setPending(true)
    setError('')

    try {
      await acknowledge({ id: item.id, revision: item.revision })

      if (version === epoch.current && sameConnection(adapter, snapshot)) {
        onChanged()
      }
    } catch (reason) {
      if (version === epoch.current && sameConnection(adapter, snapshot)) {
        setError(reason instanceof Error ? reason.message : errorTitle)
      }
    } finally {
      if (version === epoch.current) {
        sending.current = false
        setPending(false)
      }
    }
  }

  return (
    <>
      <Button disabled={disabled || pending || item.seen} onClick={() => void markSeen()} size="sm" variant="secondary">
        {item.seen ? copy.attentionSeen : copy.attentionMarkSeen}
      </Button>
      {pending && <Loader label={copy.attentionSaving} />}
      {error && (
        <div role="alert">
          <ErrorState description={error} title={errorTitle}>
            <Button disabled={disabled || pending} onClick={onChanged} size="sm" variant="secondary">
              {copy.refresh}
            </Button>
          </ErrorState>
        </div>
      )}
    </>
  )
}

