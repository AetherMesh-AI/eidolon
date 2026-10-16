import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { getAllSessionMessages, getApiRequestConnection, listAllProfileSessions } from '@/eidolon'
import { $pendingConnectionId } from '@/store/connections'
import { $activeGatewayRoute, $gateway, $localOwnerProofRevision } from '@/store/gateway'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection } from '@/store/session'
import { $sourceSwitchIntent } from '@/store/source-switch-intent'

import { type ArtifactRecord, loadArtifactsForSessions } from './artifact-utils'

interface ArtifactIndexState {
  owner: string
  artifacts: ArtifactRecord[] | null
  pending: boolean
  failed: boolean
  failedSessions: number
  totalSessions: number
  failedProfiles: number
  limitedSessions: number
  retained: boolean
}

const gatewayIds = new WeakMap<object, number>()
let nextGatewayId = 0

function currentOwner(): string {
  const connection = $connection.get()
  const gateway = $gateway.get()

  if (gateway && !gatewayIds.has(gateway)) {
    gatewayIds.set(gateway, ++nextGatewayId)
  }

  return JSON.stringify([
    getApiRequestConnection(),
    connection?.connectionId,
    connection?.baseUrl,
    connection?.mode,
    connection?.profile,
    gateway ? gatewayIds.get(gateway) : null,
    $activeGatewayRoute.get(),
    $localOwnerProofRevision.get(),
    connection?.remoteIdentity,
    connection?.authenticationIdentity,
    connection?.oauthSessionIdentity,
    $activeGatewayProfile.get(),
    $sourceSwitchIntent.get(),
    $pendingConnectionId.get(),
    $gatewaySwapTarget.get()
  ])
}

function initialIndex(owner: string): ArtifactIndexState {
  return {
    owner,
    artifacts: null,
    pending: true,
    failed: false,
    failedSessions: 0,
    totalSessions: 0,
    failedProfiles: 0,
    limitedSessions: 0,
    retained: false
  }
}

/** The index belongs to the captured source, profile and foreground intent. A
 * switch revokes reads immediately, even before React runs effect cleanup. */
export function useArtifactIndex() {
  const connection = useStore($connection)
  const profile = useStore($activeGatewayProfile)
  const intent = useStore($sourceSwitchIntent)
  const gateway = useStore($gateway)
  const route = useStore($activeGatewayRoute)
  const proofRevision = useStore($localOwnerProofRevision)
  const pendingConnection = useStore($pendingConnectionId)
  const swappingProfile = useStore($gatewaySwapTarget)

  // Keep the compiler aware of every store read hidden inside currentOwner.
  const owner = useMemo(currentOwner, [
    connection,
    profile,
    intent,
    gateway,
    route,
    proofRevision,
    pendingConnection,
    swappingProfile
  ])

  const available = !!connection && !pendingConnection && !swappingProfile
  const [index, setIndex] = useState<ArtifactIndexState>(() => initialIndex(owner))
  const flight = useRef<{ owner: string } | null>(null)

  const refresh = useCallback(async () => {
    if (!available || owner !== currentOwner() || flight.current?.owner === owner) {
      return
    }

    const request = { owner }
    flight.current = request
    const isCurrent = () => flight.current === request && owner === currentOwner()

    const scope = {
      connectionId: getApiRequestConnection() ?? $connection.get()?.connectionId,
      profile: $activeGatewayProfile.get()
    }

    setIndex(previous => ({ ...(previous.owner === owner ? previous : initialIndex(owner)), pending: true }))

    try {
      const page = await listAllProfileSessions(30, 1, 'exclude', 'recent', 'all', {}, scope)

      if (!isCurrent()) {
        return
      }

      const result = await loadArtifactsForSessions(
        page.sessions,
        async session =>
          (
            await getAllSessionMessages(
              session.id,
              {
                connectionId: session.connection_id ?? scope.connectionId,
                profile: session.profile ?? scope.profile
              },
              { isCurrent }
            )
          ).messages,
        isCurrent
      )

      if (!isCurrent()) {
        return
      }

      setIndex(previous => {
        // Retain only unread portions of this owner's previous index. A
        // successfully read session may legitimately have no artifacts now.
        const unreadProfiles = new Set(page.errors?.map(error => error.profile))

        const retained =
          previous.owner === owner
            ? (previous.artifacts ?? []).filter(
                artifact =>
                  unreadProfiles.has(artifact.profile ?? 'default') ||
                  result.failures.some(
                    ({ session }) =>
                      session.id === artifact.sessionId &&
                      session.profile === artifact.profile &&
                      session.connection_id === artifact.connectionId
                  )
              )
            : []

        return {
          owner,
          artifacts: [...result.artifacts, ...retained].sort((left, right) => right.timestamp - left.timestamp),
          pending: false,
          failed: false,
          failedSessions: result.failures.length,
          totalSessions: page.sessions.length,
          failedProfiles: page.errors?.length ?? 0,
          limitedSessions: result.failures.filter(({ error }) =>
            String(error instanceof Error ? error.message : error).includes('safe-load limit')
          ).length,
          retained: retained.length > 0
        }
      })
    } catch {
      if (!isCurrent()) {
        return
      }

      setIndex(previous => ({
        ...(previous.owner === owner ? previous : initialIndex(owner)),
        pending: false,
        failed: true,
        failedSessions: 0,
        totalSessions: 0,
        failedProfiles: 0,
        limitedSessions: 0,
        retained: previous.owner === owner && (previous.artifacts?.length ?? 0) > 0
      }))
    } finally {
      if (flight.current === request) {
        flight.current = null
      }
    }
  }, [available, owner])

  // eslint-disable-next-line no-restricted-syntax -- request cancellation, not a mirrored atom value
  useEffect(() => {
    void refresh()

    return () => {
      flight.current = null
    }
  }, [refresh])

  return {
    ...(index.owner === owner ? index : initialIndex(owner)),
    available,
    switching: !!pendingConnection || !!swappingProfile,
    refresh,
    owner
  }
}
