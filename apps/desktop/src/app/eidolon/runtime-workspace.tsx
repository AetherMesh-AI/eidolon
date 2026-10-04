import { useMemo } from 'react'

import { useGatewayRequest } from '@/app/gateway/hooks/use-gateway-request'
import { $activeGatewayRoute, $gateway, activeGatewayConnectionId } from '@/store/gateway'
import { $activeGatewayProfile, $gatewaySwapTarget } from '@/store/profile'
import { $connection, $gatewayState } from '@/store/session'

import { createRuntimeAdapter } from './runtime-adapter'
import { OrganizationWorkspaceView } from './workspace'

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
function createScopeReader() {
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

    if (gateway && !socketIds.has(gateway)) {socketIds.set(gateway, ++nextSocketId)}

    let ownerKey = JSON.stringify([connectionId ?? (connection?.mode === 'remote' ? connection.baseUrl : 'local'), logicalProfile, connection?.mode === 'remote' ? connection.baseUrl : null])

    if (!connection && lastOwner?.profile === logicalProfile && (connectionId === lastOwner.connectionId || (!connectionId && gateway === lastOwner.gateway))) {
      ownerKey = lastOwner.key
    } else if (connection && sourceMatches && descriptorMatches) {
      lastOwner = { connectionId, gateway, profile: logicalProfile, key: ownerKey }
    }

    return {
      key: JSON.stringify([gateway ? socketIds.get(gateway) : null, connectionId, $activeGatewayRoute.get(), logicalProfile, connection?.baseUrl, connection?.profile, connection?.sharedPrimary, connection?.sharedRemote, profile]),
      ownerKey,
      connected: Boolean(connection && gateway && $gatewayState.get() === 'open' && !$gatewaySwapTarget.get() && descriptorMatches && sourceMatches),
      switching: Boolean($gatewaySwapTarget.get())
    }
  }
}

export default function RuntimeOrganizationWorkspace() {
  const { requestGateway } = useGatewayRequest()

  const adapter = useMemo(() => createRuntimeAdapter({
    request: (method, params, timeout, signal) => {
      const connection = $connection.get()
      // Dedicated sockets already select HERMES_HOME (including remote aliases).
      // Shared sockets retain the primary registry route; their logical selected
      // profile is published with the descriptor after the swap settles.
      const routed = connection?.sharedPrimary || connection?.sharedRemote ? { ...params, profile: $activeGatewayProfile.get() } : params

      return requestGateway(method, routed, timeout, signal)
    },
    getScope: createScopeReader(),
    subscribeScope(listener) {
      // listen (not subscribe) avoids starting a poll for each atom's initial
      // value. The adapter performs one initial read after all listeners bind.
      const off = [$gateway.listen(listener), $activeGatewayRoute.listen(listener), $gatewayState.listen(listener), $connection.listen(listener), $activeGatewayProfile.listen(listener), $gatewaySwapTarget.listen(listener)]

      return () => off.forEach(unsubscribe => unsubscribe())
    }
  }), [requestGateway])

  return <OrganizationWorkspaceView adapter={adapter} />
}
