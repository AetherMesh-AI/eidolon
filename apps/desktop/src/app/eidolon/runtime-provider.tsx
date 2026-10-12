import { createContext, type ReactNode, useContext, useMemo, useSyncExternalStore } from 'react'

import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'
import { translateNow } from '@/i18n/runtime'
import { $activeGatewayRoute, $gateway, activeGatewayConnectionId, activeGatewayLocalOwnerRoute } from '@/store/gateway'
import { $organizationObservationEpoch, $organizationObservationSwitching } from '@/store/organization-observation-mode'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection, $gatewayState } from '@/store/session'

import { validateOrganizationObservation } from './organization-observation-client'
import { organizationEndpointIdentity, organizationOwnerKey } from './organization-owner'
import { createRuntimeAdapter } from './runtime-adapter'
import type { OrganizationSnapshot, RuntimeOrganizationAdapter } from './types'

const socketIds = new WeakMap<object, number>()
let nextSocketId = 0

interface FormOwner {
  connectionId: string | null
  gateway: object | null
  profile: string
  key: string
}

/** Transport scope is invalidated immediately. Form ownership survives a
 * transient missing descriptor, so a failed reconnect cannot erase intent or
 * its admission receipt. A confirmed new source/profile/URL still re-homes it. */
export function createOrganizationScopeReader() {
  let lastOwner: FormOwner | undefined

  return () => {
    const gateway = $gateway.get()
    const connection = $connection.get()
    const connectionId = activeGatewayConnectionId()
    const sourceMatches = !connection?.connectionId || !connectionId || connection.connectionId === connectionId
    const shared = Boolean(connection?.sharedPrimary || connection?.sharedRemote)
    const logicalProfile = $activeGatewayProfile.get()
    const profile = shared ? logicalProfile : $activeGatewayRoute.get()

    const descriptorMatches = shared
      ? (connection?.profile || 'default') === profile
      : logicalProfile === $activeGatewayRoute.get()

    if (gateway && !socketIds.has(gateway)) {
      socketIds.set(gateway, ++nextSocketId)
    }

    const localRoute = !connectionId && !$gatewaySwapTarget.get() &&
      !$organizationObservationSwitching.get() && $gatewayState.get() === 'open'
      ? activeGatewayLocalOwnerRoute(connection, logicalProfile)
      : undefined

    const ownerConnectionId = connectionId ?? localRoute?.connectionId ?? null
    let ownerKey = organizationOwnerKey(ownerConnectionId, logicalProfile, connection)

    if (
      !connection &&
      lastOwner?.profile === logicalProfile &&
      (connectionId === lastOwner.connectionId || (!connectionId && gateway === lastOwner.gateway))
    ) {
      ownerKey = lastOwner.key
    } else if (connection && sourceMatches && descriptorMatches) {
      lastOwner = { connectionId, gateway, profile: logicalProfile, key: ownerKey }
    }

    return {
      key: JSON.stringify([
        $organizationObservationEpoch.get(),
        gateway ? socketIds.get(gateway) : null,
        connectionId,
        localRoute?.connectionId ?? null,
        $activeGatewayRoute.get(),
        logicalProfile,
        organizationEndpointIdentity(connection?.baseUrl),
        connection?.profile,
        connection?.sharedPrimary,
        connection?.sharedRemote,
        profile
      ]),
      ownerKey,
      ownerRoute: sourceMatches && descriptorMatches
        ? (connectionId ? { connectionId, profile: logicalProfile } : localRoute)
        : undefined,
      connected: Boolean(
        connection &&
        gateway &&
        $gatewayState.get() === 'open' &&
        !$gatewaySwapTarget.get() &&
        !$organizationObservationSwitching.get() &&
        descriptorMatches &&
        sourceMatches
      ),
      observationOnly: $organizationObservationEpoch.get() > 0,
      switching: Boolean($gatewaySwapTarget.get()) || $organizationObservationSwitching.get()
    }
  }
}

interface OrganizationRuntimeContextValue {
  adapter: RuntimeOrganizationAdapter
  snapshot: OrganizationSnapshot
}
const OrganizationRuntimeContext = createContext<OrganizationRuntimeContextValue | null>(null)

export function useRuntimeOrganization() {
  return useContext(OrganizationRuntimeContext)
}

/** Mounted once in the shell: lightweight ledger attention outlives route changes. */
export function OrganizationRuntimeProvider({ children }: { children: ReactNode }) {
  const { requestGateway } = useGatewayRequest()

  const adapter = useMemo(
    () =>
      createRuntimeAdapter({
        request: (method, params, timeout, signal) => {
          const connection = $connection.get()

          // Dedicated sockets already select HERMES_HOME (including remote aliases).
          // Shared sockets retain the primary registry route; their logical selected
          // profile is published with the descriptor after the swap settles.
          const routed =
            connection?.sharedPrimary || connection?.sharedRemote
              ? { ...params, profile: $activeGatewayProfile.get() }
              : params

          return requestGateway(method, routed, timeout, signal)
        },
        observe: async signal => {
          const gateway = $gateway.get()
          const connection = $connection.get()

          // No reconnect helper or caller-supplied profile. Shared-profile
          // destinations stay unavailable until the backend grants a scope.
          if (!gateway || !connection || connection.sharedPrimary || connection.sharedRemote || signal.aborted) {
            throw new Error(translateNow('organizationWork.observationUnavailable'))
          }

          const profile = connection.profile || 'default'
          const result = validateOrganizationObservation(await gateway.request('organization.observe', {}, 10000, signal), profile)

          if (result.state !== 'available' || !result.snapshot) {
            throw new Error(result.state === 'unconfigured' ? translateNow('organizationWork.observationUnconfigured') : translateNow('organizationWork.observationUnsupported'))
          }

          return { ...result.snapshot, runtime: { ...result.snapshot.runtime!, profile } }
        },
        observeRead: async <T,>(method: string, params: Record<string, unknown>, signal: AbortSignal): Promise<T> => {
          const gateway = $gateway.get()
          const connection = $connection.get()

          if (!gateway || !connection || connection.sharedPrimary || connection.sharedRemote || signal.aborted) {
            throw new Error(translateNow('organizationWork.observationUnavailable'))
          }

          const result = await gateway.request<{ capabilityVersion: number; state: string; scope: { kind: string; profile: string }; data?: T }>('organization.observeRead', { method, params }, 15000, signal)

          if (result?.capabilityVersion !== 1 || result.scope?.kind !== 'served-profile' || result.scope.profile !== (connection.profile || 'default')) {
            throw new Error(translateNow('organizationWork.observationUnavailable'))
          }

          if (result.state !== 'available' || !('data' in result)) {
            throw new Error(result.state === 'unconfigured' ? translateNow('organizationWork.observationUnconfigured') : translateNow('organizationWork.observationUnsupported'))
          }

          return result.data as T
        },
        getScope: createOrganizationScopeReader(),
        subscribeScope(listener) {
          // listen (not subscribe) avoids starting a poll for each atom's initial
          // value. The adapter performs one initial read after all listeners bind.
          const off = [
            $organizationObservationEpoch.listen(listener),
            $organizationObservationSwitching.listen(listener),
            $gateway.listen(listener),
            $activeGatewayRoute.listen(listener),
            $gatewayState.listen(listener),
            $connection.listen(listener),
            $activeGatewayProfile.listen(listener),
            $gatewaySwapTarget.listen(listener)
          ]

          return () => off.forEach(unsubscribe => unsubscribe())
        }
      }),
    [requestGateway]
  )

  const snapshot = useSyncExternalStore(adapter.subscribe, adapter.getSnapshot)
  const value = useMemo(() => ({ adapter, snapshot }), [adapter, snapshot])

  return <OrganizationRuntimeContext.Provider value={value}>{children}</OrganizationRuntimeContext.Provider>
}
