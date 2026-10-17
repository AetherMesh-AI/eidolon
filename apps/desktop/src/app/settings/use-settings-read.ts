import { useStore } from '@nanostores/react'
import { type SetStateAction, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { HermesConnection } from '@/global'
import { $activeGatewayProfile, normalizeProfileKey } from '@/store/profile'
import { $connection } from '@/store/session'

export interface SettingsReadScope {
  connection: HermesConnection | null
  profile: string
}

interface ReadState<T> {
  owner: SettingsReadScope
  data: T | null
  failed: boolean
  loading: boolean
}

// Settings reads are local snapshots, not authority to save a default. Retain
// same-owner data during recovery, but never publish an old owner's response.
export function useSettingsRead<T>(
  read: (scope: SettingsReadScope) => Promise<T>,
  profile?: string,
  options: {
    enabled?: boolean
    reconcile?: (previous: T | null, next: T) => T
  } = {}
) {
  const { enabled = true, reconcile } = options
  const connection = useStore($connection)
  const activeProfile = useStore($activeGatewayProfile)
  const resolvedProfile = normalizeProfileKey(profile ?? activeProfile)

  const owner = useMemo(() => ({ connection, profile: resolvedProfile }), [connection, resolvedProfile])

  const [state, setState] = useState<ReadState<T>>({ owner, data: null, failed: false, loading: true })
  const currentOwner = useRef(owner)
  currentOwner.current = owner
  const generation = useRef(0)
  const mounted = useRef(false)
  const inFlight = useRef<{ owner: SettingsReadScope; promise: Promise<void> } | null>(null)

  const isCurrent = useCallback(
    () =>
      mounted.current &&
      currentOwner.current === owner &&
      $connection.get() === owner.connection &&
      normalizeProfileKey(profile ?? $activeGatewayProfile.get()) === owner.profile,
    [owner, profile]
  )

  const refresh = useCallback(() => {
    if (!enabled || !isCurrent()) {
      return Promise.resolve()
    }

    if (inFlight.current?.owner === owner) {
      return inFlight.current.promise
    }

    const request = ++generation.current
    setState(previous => ({
      owner,
      data: previous.owner === owner ? previous.data : null,
      failed: previous.owner === owner && previous.failed,
      loading: true
    }))
    let settled = false

    const promise = (async () => {
      try {
        const data = await read(owner)

        if (isCurrent() && request === generation.current) {
          setState(previous => ({
            owner,
            data: reconcile ? reconcile(previous.owner === owner ? previous.data : null, data) : data,
            failed: false,
            loading: false
          }))
        }
      } catch {
        // API errors can contain credential-bearing URLs or response bodies.
        // The surface renders localized generic copy, never the raw error.
        if (isCurrent() && request === generation.current) {
          setState(previous => ({ ...previous, failed: true, loading: false }))
        }
      } finally {
        settled = true

        if (request === generation.current) {
          inFlight.current = null
        }
      }
    })()

    // A disconnected preload can throw before returning a promise. Do not
    // retain that already-settled flight and turn every later retry into a no-op.
    if (!settled) {
      inFlight.current = { owner, promise }
    }

    return promise
  }, [enabled, isCurrent, owner, read, reconcile])

  // eslint-disable-next-line no-restricted-syntax -- lifecycle/request tokens, not mirrors of store values
  useEffect(() => {
    mounted.current = true
    void refresh()

    return () => {
      mounted.current = false
      generation.current += 1
      inFlight.current = null
    }
  }, [refresh])

  const setData = useCallback(
    (update: SetStateAction<T | null>) => {
      if (!isCurrent()) {
        return
      }

      // A completed explicit write is newer authority than an earlier refresh.
      // Retire that read before publishing the returned snapshot/patch.
      generation.current += 1
      inFlight.current = null
      setState(previous =>
        previous.owner !== owner
          ? previous
          : {
              ...previous,
              loading: false,
              data: typeof update === 'function' ? (update as (data: T | null) => T | null)(previous.data) : update
            }
      )
    },
    [isCurrent, owner]
  )

  const visible = state.owner === owner ? state : { owner, data: null, failed: false, loading: true }

  return { ...visible, isCurrent, refresh, setData }
}
